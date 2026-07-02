"""
finanzas.py — Persistencia, categorización y presupuesto del módulo de
economía personal. Aislado de app.py por el mismo motivo que garmin_auth.py:
mantener la lógica de un módulo concreto fuera del archivo gigante, y poder
testearla sin levantar Flask.

Tablas (todas en HIST_DB, mismo sqlite que el resto de CosmoTools):
  fin_tarjetas              — Santander Visa / Santander Amex / Galicia Visa
  fin_categorias            — categorías + presupuesto mensual por categoría
  fin_reglas_categorizacion — palabra clave -> categoría (se aprende solo)
  fin_resumenes             — un registro por cada PDF subido (auditoría)
  fin_movimientos           — cada gasto/pago/cargo, ya categorizado
  fin_presupuesto           — presupuesto total por mes (independiente del
                               presupuesto por categoría, que vive en fin_categorias)
  fin_ddjj                  — una fila por año de declaración jurada (bienes
                               personales), con el valor del dólar de cierre
  fin_ddjj_dinero           — cuentas bancarias declaradas en esa DDJJ
  fin_ddjj_propiedades      — inmuebles declarados en esa DDJJ
  fin_ddjj_tarjetas         — tarjetas declaradas (número cifrado en reposo)

Decisiones de diseño que importan:
  - El id de fin_movimientos es un hash determinístico de
    (tarjeta, fecha, descripción, monto, comprobante, cuota). Si el usuario
    sube el mismo PDF dos veces, INSERT OR REPLACE pisa el mismo registro en
    vez de duplicarlo.
  - compra_clave agrupa las distintas cuotas de UNA MISMA compra entre
    resúmenes de distintos meses, para no tener que categorizar la misma
    compra 6 o 12 veces.
  - El valor del dólar de una DDJJ vive en `fin_ddjj.valor_dolar`, UNA sola
    vez por año — nunca se guarda repetido por fila de `fin_ddjj_dinero`.
    "Valor a declarar" se calcula al vuelo (importe si es ARS, importe*dólar
    si es USD), nunca se persiste, así corregir el dólar de cierre no deja
    filas viejas desincronizadas.
  - El número de tarjeta se guarda completo (a pedido explícito, es lo que
    exige la DDJJ) pero SIEMPRE cifrado en reposo con Fernet, igual que
    garmin_auth.py cifra la contraseña de Garmin. Nunca se loguea en texto
    plano. Se devuelve enmascarado por default; el texto plano solo sale si
    se pide explícitamente `revelar=True`.
"""

import base64
import hashlib
import re
import sqlite3
import uuid
from datetime import datetime

try:
    from cryptography.fernet import Fernet, InvalidToken
    _CRYPTO_OK = True
except ImportError:
    _CRYPTO_OK = False


class DDJJCifradoNoDisponible(RuntimeError):
    """Falta el paquete 'cryptography', o cambió SECRET_KEY y no se puede
    descifrar un número guardado con la clave anterior."""

CATEGORIAS_DEFAULT = [
    # (id, nombre, presupuesto_mensual, color)
    ("transporte",       "Transporte",                     0, "#3B82F6"),
    ("comida_delivery",  "Comida / Delivery",               0, "#F59E0B"),
    ("supermercado",     "Supermercado",                    0, "#10B981"),
    ("suscripciones",    "Suscripciones",                   0, "#8B5CF6"),
    ("salidas",          "Salidas / Entretenimiento",       0, "#EC4899"),
    ("indumentaria_dep", "Indumentaria / Deporte",          0, "#06B6D4"),
    ("salud",            "Salud / Farmacia",                0, "#EF4444"),
    ("servicios",        "Servicios / Hogar",               0, "#84CC16"),
    ("mascotas",         "Mascotas",                        0, "#F97316"),
    ("adelantos",        "Adelantos de efectivo",           0, "#DC2626"),
    ("cargos_tarjeta",   "Intereses / Impuestos tarjeta",   0, "#6B7280"),
    ("otros",            "Otros",                           0, "#9CA3AF"),
]

# patron (busca en la descripción en mayúsculas) -> categoria_id, prioridad
REGLAS_DEFAULT = [
    ("SUBE", "transporte", 0), ("EMOVA", "transporte", 0),
    ("CABIFY", "transporte", 0), ("UBER", "transporte", 0),
    ("RAPPI", "comida_delivery", 0), ("PEDIDOSYA", "comida_delivery", 0),
    ("DLO*", "comida_delivery", 0), ("MCDONALDS", "comida_delivery", 0),
    ("NETFLIX", "suscripciones", 0), ("CLAUDE.AI", "suscripciones", 0),
    ("AMAZON PRIME", "suscripciones", 0), ("XBOX GAME", "suscripciones", 0),
    ("BUMBLE", "suscripciones", 0), ("INNER CIRCLE", "suscripciones", 0),
    ("YOUTUBE", "suscripciones", 0), ("SPOTIFY", "suscripciones", 0),
    ("ADIDAS", "indumentaria_dep", 0), ("ASICS", "indumentaria_dep", 0),
    ("GARMIN", "indumentaria_dep", 0),
    ("EDENOR", "servicios", 0), ("EDESUR", "servicios", 0),
    ("METROGAS", "servicios", 0), ("AYSA", "servicios", 0),
    ("MOVISTAR ARENA", "salidas", 0), ("LAS VIOLETAS", "salidas", 0),
    ("RUTINI", "salidas", 0),
    ("PHARMACIE", "salud", 0), ("FARMA", "salud", 0),
    ("AHORROPET", "mascotas", 0), ("VETERINAR", "mascotas", 0),
    ("(ADEL.)", "adelantos", 10),  # prioridad alta: que no lo pise otra regla
]


# ── Setup ──────────────────────────────────────────────────────────────────
def init_finanzas_db(db_path: str):
    con = sqlite3.connect(db_path)
    con.execute("""CREATE TABLE IF NOT EXISTS fin_tarjetas (
        id TEXT PRIMARY KEY, nombre TEXT, motor TEXT,
        dia_cierre INTEGER, activa INTEGER DEFAULT 1
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS fin_categorias (
        id TEXT PRIMARY KEY, nombre TEXT,
        presupuesto_mensual REAL DEFAULT 0, color TEXT, orden INTEGER DEFAULT 0
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS fin_reglas_categorizacion (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patron TEXT, categoria_id TEXT, prioridad INTEGER DEFAULT 0, creado TEXT
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS fin_resumenes (
        id TEXT PRIMARY KEY, tarjeta_id TEXT, archivo_nombre TEXT,
        periodo_desde TEXT, periodo_hasta TEXT,
        total_consumos_declarado REAL, total_consumos_calculado REAL,
        validado INTEGER, subido_por TEXT, creado TEXT
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS fin_movimientos (
        id TEXT PRIMARY KEY, tarjeta_id TEXT, resumen_id TEXT,
        compra_clave TEXT, fecha TEXT, descripcion TEXT, comprobante TEXT,
        monto_ars REAL, monto_usd REAL,
        cuota_actual INTEGER, cuota_total INTEGER,
        tipo TEXT, categoria_id TEXT, categoria_manual INTEGER DEFAULT 0,
        origen TEXT DEFAULT 'pdf', creado TEXT
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS fin_presupuesto (
        mes TEXT, categoria_id TEXT, monto REAL,
        PRIMARY KEY (mes, categoria_id)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS fin_ddjj (
        id TEXT PRIMARY KEY, anio INTEGER UNIQUE NOT NULL, fecha_cierre TEXT,
        valor_dolar REAL NOT NULL, estado TEXT DEFAULT 'borrador',
        fecha_presentacion TEXT, creado TEXT
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS fin_ddjj_dinero (
        id TEXT PRIMARY KEY, ddjj_id TEXT NOT NULL, fecha TEXT, banco TEXT,
        cuenta TEXT, cbu TEXT, moneda TEXT NOT NULL, importe REAL NOT NULL, creado TEXT
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS fin_ddjj_propiedades (
        id TEXT PRIMARY KEY, ddjj_id TEXT NOT NULL, direccion TEXT,
        fecha_adquisicion TEXT, superficie REAL, base_imponible REAL,
        valor_compra_actualizado REAL, creado TEXT
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS fin_ddjj_tarjetas (
        id TEXT PRIMARY KEY, ddjj_id TEXT NOT NULL, emisor TEXT,
        numero_cifrado TEXT, creado TEXT
    )""")
    con.commit()

    # Seed solo si está vacío — no pisar nombres/presupuestos que el usuario ya editó
    if not con.execute("SELECT 1 FROM fin_tarjetas WHERE id='manual'").fetchone():
        con.execute("INSERT INTO fin_tarjetas (id,nombre,motor,dia_cierre,activa) VALUES ('manual','Transferencia / Efectivo','manual',NULL,1)")
    if not con.execute("SELECT 1 FROM fin_categorias LIMIT 1").fetchone():
        con.executemany(
            "INSERT INTO fin_categorias (id,nombre,presupuesto_mensual,color,orden) VALUES (?,?,?,?,?)",
            [(c[0], c[1], c[2], c[3], i) for i, c in enumerate(CATEGORIAS_DEFAULT)])
    if not con.execute("SELECT 1 FROM fin_reglas_categorizacion LIMIT 1").fetchone():
        ahora = datetime.now().isoformat()
        con.executemany(
            "INSERT INTO fin_reglas_categorizacion (patron,categoria_id,prioridad,creado) VALUES (?,?,?,?)",
            [(p, c, prio, ahora) for p, c, prio in REGLAS_DEFAULT])
    con.commit()
    con.close()


# ── Tarjetas ───────────────────────────────────────────────────────────────
def get_tarjetas(db_path: str):
    con = sqlite3.connect(db_path); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM fin_tarjetas WHERE activa=1").fetchall()]
    con.close()
    return rows


def crear_tarjeta(db_path: str, nombre: str, motor: str, dia_cierre: int = None):
    tid = str(uuid.uuid4())[:12]
    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO fin_tarjetas (id,nombre,motor,dia_cierre,activa) VALUES (?,?,?,?,1)",
                (tid, nombre, motor, dia_cierre))
    con.commit(); con.close()
    return tid


# ── Categorías ─────────────────────────────────────────────────────────────
def crear_categoria(db_path: str, nombre: str, color: str = "#9CA3AF", presupuesto_mensual: float = 0):
    """id determinístico a partir del nombre (slug, sin acentos), para evitar
    duplicados obvios si el usuario intenta crear 'Mascotas' dos veces."""
    import unicodedata
    sin_acentos = unicodedata.normalize("NFKD", nombre.strip().lower()).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "_", sin_acentos).strip("_") or str(uuid.uuid4())[:8]
    con = sqlite3.connect(db_path); con.row_factory = sqlite3.Row
    existente = con.execute("SELECT id FROM fin_categorias WHERE id=?", (slug,)).fetchone()
    if existente:
        con.close()
        raise ValueError(f"Ya existe una categoría con ese nombre")
    orden = con.execute("SELECT COALESCE(MAX(orden),0)+1 FROM fin_categorias").fetchone()[0]
    con.execute("INSERT INTO fin_categorias (id,nombre,presupuesto_mensual,color,orden) VALUES (?,?,?,?,?)",
                (slug, nombre.strip(), presupuesto_mensual, color, orden))
    con.commit(); con.close()
    return slug


# ── Categorización ─────────────────────────────────────────────────────────
def _normalizar(desc: str) -> str:
    return re.sub(r"\s+", " ", desc.upper()).strip()


def categorizar(db_path: str, descripcion: str):
    """Devuelve categoria_id según las reglas guardadas (prioridad desc, luego
    la regla más específica/larga gana en empate). None si no matchea nada."""
    con = sqlite3.connect(db_path); con.row_factory = sqlite3.Row
    reglas = con.execute(
        "SELECT patron, categoria_id, prioridad FROM fin_reglas_categorizacion "
        "ORDER BY prioridad DESC, LENGTH(patron) DESC"
    ).fetchall()
    con.close()
    desc_norm = _normalizar(descripcion)
    for r in reglas:
        if r["patron"].upper() in desc_norm:
            return r["categoria_id"]
    return None


def aprender_regla(db_path: str, patron: str, categoria_id: str):
    """Se llama cuando el usuario corrige la categoría de un movimiento a mano:
    la próxima vez que aparezca ese comercio, ya lo categoriza solo."""
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO fin_reglas_categorizacion (patron,categoria_id,prioridad,creado) VALUES (?,?,?,?)",
        (patron.upper(), categoria_id, 1, datetime.now().isoformat()))
    con.commit(); con.close()


# ── Movimientos ────────────────────────────────────────────────────────────
def _id_movimiento(tarjeta_id, fecha, descripcion, monto_ars, monto_usd, comprobante, cuota_actual):
    """Hash determinístico: subir el mismo PDF dos veces actualiza, no duplica."""
    base = f"{tarjeta_id}|{fecha}|{_normalizar(descripcion)}|{monto_ars}|{monto_usd}|{comprobante}|{cuota_actual}"
    return hashlib.sha1(base.encode()).hexdigest()[:20]


def _compra_clave(tarjeta_id, descripcion, comprobante, cuota_total):
    """Agrupa las distintas cuotas de una misma compra entre resúmenes de
    distintos meses. Preferimos el comprobante (más estable); si no hay,
    caemos a la descripción normalizada + cantidad total de cuotas."""
    if not cuota_total:
        return None
    base = f"{tarjeta_id}|{comprobante or _normalizar(descripcion)}|{cuota_total}"
    return hashlib.sha1(base.encode()).hexdigest()[:16]


def guardar_resumen(db_path: str, tarjeta_id: str, archivo_nombre: str,
                     periodo_desde: str, periodo_hasta: str,
                     total_declarado: float, total_calculado: float,
                     validado: bool, usuario: str):
    rid = str(uuid.uuid4())[:12]
    con = sqlite3.connect(db_path)
    con.execute("""INSERT INTO fin_resumenes
        (id,tarjeta_id,archivo_nombre,periodo_desde,periodo_hasta,
         total_consumos_declarado,total_consumos_calculado,validado,subido_por,creado)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (rid, tarjeta_id, archivo_nombre, periodo_desde, periodo_hasta,
         total_declarado, total_calculado, int(validado), usuario, datetime.now().isoformat()))
    con.commit(); con.close()
    return rid


def guardar_movimientos(db_path: str, tarjeta_id: str, resumen_id: str, movimientos: list,
                         origen: str = "pdf"):
    """Inserta/actualiza movimientos ya parseados.

    Prioridad para decidir la categoría de cada movimiento:
      1. Si ya existía y el usuario la había corregido a mano (categoria_manual=1),
         no se toca — aunque se vuelva a subir el mismo resumen.
      2. Si el caller ya trae una categoria_id elegida (ej. la que el usuario
         seleccionó en la previsualización antes de confirmar), se respeta.
      3. Si no, se hereda la de otra cuota de la misma compra.
      4. Si no, se auto-categoriza por reglas.
      5. Los cargos (intereses/impuestos) siempre van a 'cargos_tarjeta'.

    Devuelve la lista enriquecida con id/categoria para la previsualización.
    """
    con = sqlite3.connect(db_path); con.row_factory = sqlite3.Row
    ahora = datetime.now().isoformat()
    resultado = []
    for m in movimientos:
        mid = _id_movimiento(tarjeta_id, m["fecha"], m["descripcion"],
                              m["monto_ars"], m["monto_usd"], m.get("comprobante", ""),
                              m.get("cuota_actual"))
        compra_clave = _compra_clave(tarjeta_id, m["descripcion"], m.get("comprobante", ""),
                                      m.get("cuota_total"))

        existente = con.execute(
            "SELECT categoria_id, categoria_manual FROM fin_movimientos WHERE id=?", (mid,)
        ).fetchone()

        if existente and existente["categoria_manual"]:
            categoria_id = existente["categoria_id"]
        else:
            categoria_id = m.get("categoria_id") or None
            if m["tipo"] == "consumo":
                if not categoria_id and compra_clave:
                    prev = con.execute(
                        "SELECT categoria_id FROM fin_movimientos WHERE compra_clave=? AND categoria_id IS NOT NULL LIMIT 1",
                        (compra_clave,)).fetchone()
                    if prev:
                        categoria_id = prev["categoria_id"]
                if not categoria_id:
                    categoria_id = categorizar(db_path, m["descripcion"])
            elif m["tipo"] == "cargo":
                categoria_id = "cargos_tarjeta"

        con.execute("""INSERT OR REPLACE INTO fin_movimientos
            (id,tarjeta_id,resumen_id,compra_clave,fecha,descripcion,comprobante,
             monto_ars,monto_usd,cuota_actual,cuota_total,tipo,categoria_id,
             categoria_manual,origen,creado)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (mid, tarjeta_id, resumen_id, compra_clave, m["fecha"], m["descripcion"],
             m.get("comprobante", ""), m["monto_ars"], m["monto_usd"],
             m.get("cuota_actual"), m.get("cuota_total"), m["tipo"], categoria_id,
             int(existente["categoria_manual"]) if existente else 0, origen, ahora))

        resultado.append({**m, "id": mid, "compra_clave": compra_clave, "categoria_id": categoria_id})

    con.commit(); con.close()
    return resultado


def recategorizar_movimiento(db_path: str, movimiento_id: str, categoria_id: str, aprender: bool = True):
    """El usuario corrige a mano. Si aprender=True, también propaga la
    categoría a otras cuotas de la misma compra y guarda una regla nueva."""
    con = sqlite3.connect(db_path); con.row_factory = sqlite3.Row
    mov = con.execute("SELECT * FROM fin_movimientos WHERE id=?", (movimiento_id,)).fetchone()
    if not mov:
        con.close()
        return False

    con.execute("UPDATE fin_movimientos SET categoria_id=?, categoria_manual=1 WHERE id=?",
                (categoria_id, movimiento_id))
    if aprender and mov["compra_clave"]:
        con.execute("UPDATE fin_movimientos SET categoria_id=? WHERE compra_clave=? AND categoria_manual=0",
                    (categoria_id, mov["compra_clave"]))
    con.commit()

    if aprender:
        # palabra clave = comercio real, no el procesador de pago
        # (ej. de "MERPAGO*GARMIN" o "PAYU*AR*ADIDAS" aprender "GARMIN"/"ADIDAS", no el prefijo)
        normalizado = _normalizar(mov["descripcion"])
        partes = normalizado.split("*")
        candidato = partes[-1].strip() if len(partes) > 1 else normalizado
        primer_token = candidato.split(" ")[0]
        con.close()
        if len(primer_token) >= 4:
            aprender_regla(db_path, primer_token, categoria_id)
        return True

    con.close()
    return True


def eliminar_movimiento(db_path: str, movimiento_id: str) -> bool:
    con = sqlite3.connect(db_path)
    cur = con.execute("DELETE FROM fin_movimientos WHERE id=?", (movimiento_id,))
    con.commit()
    borrado = cur.rowcount > 0
    con.close()
    return borrado


# ── Presupuesto y resumen mensual ──────────────────────────────────────────
def gasto_por_categoria(db_path: str, mes: str):
    """mes: 'YYYY-MM'. Solo cuenta tipo='consumo' (los cargos van aparte,
    los pagos no son gasto)."""
    con = sqlite3.connect(db_path); con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT c.id, c.nombre, c.presupuesto_mensual, c.color,
               COALESCE(SUM(m.monto_ars), 0) as gastado
        FROM fin_categorias c
        LEFT JOIN fin_movimientos m
            ON m.categoria_id = c.id AND m.tipo='consumo' AND substr(m.fecha,1,7)=?
        GROUP BY c.id ORDER BY c.orden
    """, (mes,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def resumen_mes(db_path: str, mes: str):
    con = sqlite3.connect(db_path); con.row_factory = sqlite3.Row
    consumos = con.execute(
        "SELECT COALESCE(SUM(monto_ars),0) FROM fin_movimientos WHERE tipo='consumo' AND substr(fecha,1,7)=?",
        (mes,)).fetchone()[0]
    cargos = con.execute(
        "SELECT COALESCE(SUM(monto_ars),0) FROM fin_movimientos WHERE tipo='cargo' AND substr(fecha,1,7)=?",
        (mes,)).fetchone()[0]
    sin_categoria = con.execute(
        "SELECT COUNT(*) FROM fin_movimientos WHERE tipo='consumo' AND categoria_id IS NULL AND substr(fecha,1,7)=?",
        (mes,)).fetchone()[0]
    presupuesto_total = con.execute(
        "SELECT monto FROM fin_presupuesto WHERE mes=? AND categoria_id IS NULL", (mes,)).fetchone()
    con.close()
    presupuesto_total = presupuesto_total[0] if presupuesto_total else None
    return {
        "mes": mes,
        "consumos": round(consumos, 2),
        "cargos_intereses_impuestos": round(cargos, 2),
        "movimientos_sin_categoria": sin_categoria,
        "presupuesto_total": presupuesto_total,
        "disponible": round(presupuesto_total - consumos, 2) if presupuesto_total is not None else None,
    }


def get_presupuesto_total(db_path: str, mes: str):
    con = sqlite3.connect(db_path)
    row = con.execute("SELECT monto FROM fin_presupuesto WHERE mes=? AND categoria_id IS NULL", (mes,)).fetchone()
    con.close()
    return row[0] if row else None


def suma_presupuesto_categorias(db_path: str, excluir_categoria_id: str = None):
    con = sqlite3.connect(db_path)
    if excluir_categoria_id:
        row = con.execute("SELECT COALESCE(SUM(presupuesto_mensual),0) FROM fin_categorias WHERE id != ?",
                           (excluir_categoria_id,)).fetchone()
    else:
        row = con.execute("SELECT COALESCE(SUM(presupuesto_mensual),0) FROM fin_categorias").fetchone()
    con.close()
    return row[0]


def set_presupuesto_total(db_path: str, mes: str, monto: float):
    con = sqlite3.connect(db_path)
    con.execute("INSERT OR REPLACE INTO fin_presupuesto (mes,categoria_id,monto) VALUES (?,NULL,?)",
                (mes, monto))
    con.commit(); con.close()


def set_presupuesto_categoria(db_path: str, categoria_id: str, monto: float):
    con = sqlite3.connect(db_path)
    con.execute("UPDATE fin_categorias SET presupuesto_mensual=? WHERE id=?", (monto, categoria_id))
    con.commit(); con.close()


# ── Declaraciones Juradas (Bienes Personales) ───────────────────────────────
# El valor del dólar es UNO por año/DDJJ (fin_ddjj.valor_dolar). "Valor a
# declarar" de cada cuenta se calcula al vuelo en listar_dinero_ddjj, nunca
# se persiste, para que corregir el dólar no deje filas viejas desincronizadas.

EMISORES_TARJETA = ["Visa", "Amex", "Galicia", "Otros"]


def _get_fernet(secret_key: str) -> "Fernet":
    if not _CRYPTO_OK:
        raise DDJJCifradoNoDisponible(
            "El paquete 'cryptography' no está instalado (pip install cryptography)."
        )
    key = hashlib.sha256(secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def _cifrar_numero(numero: str, secret_key: str) -> str:
    if not numero:
        return numero
    return _get_fernet(secret_key).encrypt(numero.encode()).decode()


def _descifrar_numero(numero_cifrado: str, secret_key: str) -> str:
    if not numero_cifrado:
        return numero_cifrado
    try:
        return _get_fernet(secret_key).decrypt(numero_cifrado.encode()).decode()
    except InvalidToken:
        raise DDJJCifradoNoDisponible(
            "No se pudo descifrar el número guardado (¿cambió SECRET_KEY?)."
        )


def _mask_numero(numero: str) -> str:
    """'4111111111111234' -> '•••• •••• •••• 1234'. Si no se pudo descifrar, oculta todo."""
    if not numero:
        return '•••• •••• •••• ••••'
    limpio = re.sub(r'\D', '', numero)
    if len(limpio) <= 4:
        return '•' * len(limpio)
    oculto = '•' * (len(limpio) - 4)
    visible = limpio[-4:]
    junto = oculto + visible
    return ' '.join(junto[i:i+4] for i in range(0, len(junto), 4))


# ── DDJJ (cabecera por año) ──────────────────────────────────────────────
def crear_ddjj(db_path: str, anio: int, fecha_cierre: str, valor_dolar: float) -> str:
    did = str(uuid.uuid4())[:12]
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO fin_ddjj (id,anio,fecha_cierre,valor_dolar,estado,creado) VALUES (?,?,?,?,?,?)",
        (did, anio, fecha_cierre, valor_dolar, 'borrador', datetime.now().isoformat()))
    con.commit(); con.close()
    return did


def listar_ddjj(db_path: str):
    con = sqlite3.connect(db_path); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM fin_ddjj ORDER BY anio DESC").fetchall()]
    con.close()
    return rows


def actualizar_ddjj(db_path: str, ddjj_id: str, **campos):
    """campos permitidos: fecha_cierre, valor_dolar, estado, fecha_presentacion."""
    permitidos = ('fecha_cierre', 'valor_dolar', 'estado', 'fecha_presentacion')
    claves = [k for k in campos if k in permitidos]
    if not claves:
        return
    con = sqlite3.connect(db_path)
    con.execute(f"UPDATE fin_ddjj SET {', '.join(k+'=?' for k in claves)} WHERE id=?",
                [campos[k] for k in claves] + [ddjj_id])
    con.commit(); con.close()


def borrar_ddjj(db_path: str, ddjj_id: str):
    con = sqlite3.connect(db_path)
    con.execute("DELETE FROM fin_ddjj_dinero WHERE ddjj_id=?", (ddjj_id,))
    con.execute("DELETE FROM fin_ddjj_propiedades WHERE ddjj_id=?", (ddjj_id,))
    con.execute("DELETE FROM fin_ddjj_tarjetas WHERE ddjj_id=?", (ddjj_id,))
    con.execute("DELETE FROM fin_ddjj WHERE id=?", (ddjj_id,))
    con.commit(); con.close()


# ── Dinero (cuentas bancarias) ────────────────────────────────────────────
def crear_dinero_ddjj(db_path: str, ddjj_id: str, fecha: str, banco: str, cuenta: str,
                       cbu: str, moneda: str, importe: float) -> str:
    if moneda not in ('ARS', 'USD'):
        raise ValueError("moneda debe ser 'ARS' o 'USD'")
    rid = str(uuid.uuid4())[:12]
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO fin_ddjj_dinero (id,ddjj_id,fecha,banco,cuenta,cbu,moneda,importe,creado) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (rid, ddjj_id, fecha, banco, cuenta, str(cbu), moneda, importe, datetime.now().isoformat()))
    con.commit(); con.close()
    return rid


def listar_dinero_ddjj(db_path: str, ddjj_id: str):
    con = sqlite3.connect(db_path); con.row_factory = sqlite3.Row
    ddjj = con.execute("SELECT valor_dolar FROM fin_ddjj WHERE id=?", (ddjj_id,)).fetchone()
    valor_dolar = ddjj["valor_dolar"] if ddjj else 0
    rows = []
    for r in con.execute("SELECT * FROM fin_ddjj_dinero WHERE ddjj_id=? ORDER BY fecha", (ddjj_id,)).fetchall():
        d = dict(r)
        d["valor_a_declarar"] = d["importe"] if d["moneda"] == "ARS" else round(d["importe"] * valor_dolar, 2)
        rows.append(d)
    con.close()
    return rows


def borrar_dinero_ddjj(db_path: str, reg_id: str):
    con = sqlite3.connect(db_path)
    con.execute("DELETE FROM fin_ddjj_dinero WHERE id=?", (reg_id,))
    con.commit(); con.close()


# ── Propiedades ────────────────────────────────────────────────────────────
def crear_propiedad_ddjj(db_path: str, ddjj_id: str, direccion: str, fecha_adquisicion: str,
                          superficie: float, base_imponible: float, valor_compra_actualizado: float) -> str:
    rid = str(uuid.uuid4())[:12]
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO fin_ddjj_propiedades "
        "(id,ddjj_id,direccion,fecha_adquisicion,superficie,base_imponible,valor_compra_actualizado,creado) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (rid, ddjj_id, direccion, fecha_adquisicion, superficie, base_imponible,
         valor_compra_actualizado, datetime.now().isoformat()))
    con.commit(); con.close()
    return rid


def listar_propiedades_ddjj(db_path: str, ddjj_id: str):
    con = sqlite3.connect(db_path); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM fin_ddjj_propiedades WHERE ddjj_id=? ORDER BY direccion", (ddjj_id,)).fetchall()]
    con.close()
    return rows


def borrar_propiedad_ddjj(db_path: str, reg_id: str):
    con = sqlite3.connect(db_path)
    con.execute("DELETE FROM fin_ddjj_propiedades WHERE id=?", (reg_id,))
    con.commit(); con.close()


# ── Tarjetas declaradas (número completo, cifrado en reposo) ────────────────
def crear_tarjeta_ddjj(db_path: str, secret_key: str, ddjj_id: str, emisor: str, numero: str) -> str:
    if emisor not in EMISORES_TARJETA:
        raise ValueError(f"emisor debe ser uno de {EMISORES_TARJETA}")
    numero_limpio = re.sub(r'\D', '', numero or '')
    if not numero_limpio:
        raise ValueError("número de tarjeta vacío")
    tid = str(uuid.uuid4())[:12]
    numero_cifrado = _cifrar_numero(numero_limpio, secret_key)
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO fin_ddjj_tarjetas (id,ddjj_id,emisor,numero_cifrado,creado) VALUES (?,?,?,?,?)",
        (tid, ddjj_id, emisor, numero_cifrado, datetime.now().isoformat()))
    con.commit(); con.close()
    return tid


def listar_tarjetas_ddjj(db_path: str, secret_key: str, ddjj_id: str, revelar: bool = False):
    """Por default devuelve el número enmascarado (•••• •••• •••• 1234).
    revelar=True devuelve el número completo en texto plano — usar solo cuando
    el usuario lo pide explícitamente (ej. botón "mostrar" o export), nunca
    para listados generales, y logueando quién lo pidió desde app.py."""
    con = sqlite3.connect(db_path); con.row_factory = sqlite3.Row
    rows = []
    for r in con.execute("SELECT * FROM fin_ddjj_tarjetas WHERE ddjj_id=? ORDER BY emisor", (ddjj_id,)).fetchall():
        d = dict(r)
        cifrado = d.pop("numero_cifrado")
        try:
            numero = _descifrar_numero(cifrado, secret_key)
        except DDJJCifradoNoDisponible:
            numero = None
        d["numero"] = numero if (revelar and numero) else _mask_numero(numero)
        rows.append(d)
    con.close()
    return rows


def borrar_tarjeta_ddjj(db_path: str, tarjeta_id: str):
    con = sqlite3.connect(db_path)
    con.execute("DELETE FROM fin_ddjj_tarjetas WHERE id=?", (tarjeta_id,))
    con.commit(); con.close()
