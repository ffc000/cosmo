"""
training_antro.py — Composición corporal (antropometría ISAK) (Fase 11)

Separado de training.py por el mismo motivo que training_plan.py: extiende
el mismo training_bp, pero es un tema aparte (mediciones periódicas de un
antropometrista, no datos de Garmin).

Por qué esto existe: Garmin solo reporta peso total (un número). Los
informes de composición corporal (protocolo ISAK, fraccionamiento en 5
masas de Kerr 1988, somatotipo de Heath & Carter) dan la COMPOSICIÓN de
ese peso -- cuánto es grasa, cuánto músculo -- que es mucho más relevante
para trackear progreso real entrenando fuerza. Se cargan cada 2-3 meses
(no a diario como el peso de Garmin), en PDF, siempre desde la misma
plantilla de Excel (mismo antropometrista, mismo protocolo).

Se probó el parseo automático contra un informe real antes de escribir
esto (no se asumió que iba a funcionar) -- el texto del PDF es
consistente y parseable con regex para casi todos los campos, EXCEPTO
algunas partes con texto rotado (somatotipo X/Y, datos adicionales) que
salen desordenadas en la extracción. Por eso: se parsea todo lo que se
puede extraer con confianza, y SIEMPRE se muestra una pantalla de
revisión antes de guardar -- nunca se guarda directo lo que devuelve el
parser, así que un informe futuro con formato distinto no puede meter
datos mal parseados sin que el usuario los vea primero.
"""
import re
import os
import uuid
import logging
from datetime import datetime, date

from flask import request, jsonify, session

from core import HIST_DB, login_required, modulo_required, get_db, notificar_telegram
from blueprints.training import training_bp, _api_key

ANTRO_DIR = "/data/antropometria"

# ── Esquema ────────────────────────────────────────────────────────────────────
def init_antro_db():
    with get_db(HIST_DB) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS antropometria (
            id                      TEXT PRIMARY KEY,
            fecha_medicion          TEXT NOT NULL,
            numero_medicion         INTEGER,
            peso_kg                 REAL,
            talla_cm                REAL,
            talla_sentado_cm        REAL,
            diam_biacromial         REAL, diam_torax_transverso REAL, diam_torax_anteroposterior REAL,
            diam_biiliocrestideo    REAL, diam_humeral REAL, diam_femoral REAL,
            perim_cabeza            REAL, perim_brazo_relajado REAL, perim_brazo_flexionado REAL,
            perim_antebrazo         REAL, perim_torax REAL, perim_cintura REAL, perim_caderas REAL,
            perim_muslo_superior    REAL, perim_muslo_medial REAL, perim_pantorrilla REAL,
            pliegue_triceps         REAL, pliegue_subescapular REAL, pliegue_supraespinal REAL,
            pliegue_abdominal       REAL, pliegue_muslo_medial REAL, pliegue_pantorrilla REAL,
            masa_adiposa_pct        REAL, masa_adiposa_kg REAL,
            masa_muscular_pct       REAL, masa_muscular_kg REAL,
            masa_residual_pct       REAL, masa_residual_kg REAL,
            masa_osea_pct           REAL, masa_osea_kg REAL,
            masa_piel_pct           REAL, masa_piel_kg REAL,
            somato_endo             REAL, somato_meso REAL, somato_ecto REAL,
            imc                     REAL,
            archivo_pdf             TEXT,
            creado                  TEXT DEFAULT (datetime('now'))
        )""")
init_antro_db()

# ── Parseo del PDF ─────────────────────────────────────────────────────────────
_MAPA_CAMPOS = {
    "Peso (kg)": "peso_kg", "Talla (cm)": "talla_cm", "Talla sentado (cm)": "talla_sentado_cm",
    "Biacromial": "diam_biacromial", "Tórax Transverso": "diam_torax_transverso",
    "Tórax Anteroposterior": "diam_torax_anteroposterior", "Bi-iliocrestídeo": "diam_biiliocrestideo",
    "Humeral (biepicondilar)": "diam_humeral", "Femoral (biepicondilar)": "diam_femoral",
    "Cabeza": "perim_cabeza", "Brazo Relajado": "perim_brazo_relajado",
    "Brazo Flexionado en Tensión": "perim_brazo_flexionado", "Antebrazo": "perim_antebrazo",
    "Tórax Mesoesternal": "perim_torax", "Cintura (mínima)": "perim_cintura",
    "Caderas (máxima)": "perim_caderas", "Muslo (superior)": "perim_muslo_superior",
    "Pantorrilla (máxima)": "perim_pantorrilla",
    "Tríceps": "pliegue_triceps", "Subescapular": "pliegue_subescapular",
    "Supraespinal": "pliegue_supraespinal", "Abdominal": "pliegue_abdominal",
}
# Etiquetas que el propio PDF repite con significados distintos según la
# sección (ej. "Muslo (medial)" es un perímetro Y un pliegue) -- se
# resuelven por orden de aparición, que es siempre el mismo en la plantilla.
_AMBIGUAS = {
    "Muslo (medial)": ["perim_muslo_medial", "pliegue_muslo_medial"],
    "Pantorrilla": ["pliegue_pantorrilla"],
}
_PATRON_MEDIDA = re.compile(
    r'^([A-Za-zÁÉÍÓÚÑáéíóúñ][A-Za-zÁÉÍÓÚÑáéíóúñ\s\(\)\-]*?)\s+(\d+,\d+)(?:\s+(\d+,\d+))?(?:\s+(-?\d+,\d+))?\s*$')
_PATRON_MASA = re.compile(r'^(Masa (?:de la )?\w+)\s+([\d,]+)%\s+([\d,]+)')
_NOMBRE_MASA = {"Masa Adiposa": "masa_adiposa", "Masa Muscular": "masa_muscular",
                "Masa Residual": "masa_residual", "Masa Ósea": "masa_osea", "Masa de la Piel": "masa_piel"}
_PATRON_SOMATO = re.compile(
    r'RATING DE SOMATOTIPO\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+\(Posicionamiento actual\)')
_PATRON_FECHA = re.compile(r'Fecha de medici[oó]n:\s*(\d{1,2}/\d{1,2}/\d{2,4})')
_PATRON_NUMERO = re.compile(r'N[uú]mero de medici[oó]n:\s*(\d+)')


def _num(s):
    return float(s.replace(",", ".")) if s else None


def parsear_antropometria_pdf(ruta_pdf):
    """Devuelve (campos, no_reconocidos, meta) -- campos es un dict con
    todo lo que se pudo extraer con confianza (ver mapa arriba), listo
    para precargar en el formulario de revisión. no_reconocidos es lo que
    el parser encontró pero no supo dónde poner (para mostrar como aviso,
    no se pierde silenciosamente). Nunca levanta excepción por un formato
    inesperado -- devuelve lo que haya podido sacar."""
    import pdfplumber
    campos, no_reconocidos, meta = {}, [], {}
    try:
        with pdfplumber.open(ruta_pdf) as pdf:
            textos = [p.extract_text() or "" for p in pdf.pages]
    except Exception as e:
        logging.exception("Antropometría: no se pudo abrir el PDF")
        return {}, [], {"error": str(e)}

    texto_todo = "\n".join(textos)

    m = _PATRON_FECHA.search(texto_todo)
    if m:
        try:
            d, mo, y = m.group(1).split("/")
            y = "20" + y if len(y) == 2 else y
            meta["fecha_medicion"] = f"{y}-{int(mo):02d}-{int(d):02d}"
        except Exception:
            pass
    m = _PATRON_NUMERO.search(texto_todo)
    if m:
        meta["numero_medicion"] = int(m.group(1))

    # Medidas básicas/diámetros/perímetros/pliegues -- suelen estar en las
    # primeras 2-3 páginas, pero se busca en todo el texto por las dudas.
    contador_ambiguas = {}
    for linea in texto_todo.split("\n"):
        m = _PATRON_MEDIDA.match(linea.strip())
        if not m:
            continue
        etiqueta, valor = m.group(1).strip(), _num(m.group(2))
        if etiqueta in _AMBIGUAS:
            i = contador_ambiguas.get(etiqueta, 0)
            if i < len(_AMBIGUAS[etiqueta]):
                campos[_AMBIGUAS[etiqueta][i]] = valor
            contador_ambiguas[etiqueta] = i + 1
        elif etiqueta in _MAPA_CAMPOS:
            campos[_MAPA_CAMPOS[etiqueta]] = valor
        elif etiqueta not in ("Score-Z", "Resultados", "Valor Ajustado"):  # ruido de encabezados de tabla
            no_reconocidos.append({"etiqueta": etiqueta, "valor": valor})

    # 5 masas (Kerr 1988)
    for linea in texto_todo.split("\n"):
        m = _PATRON_MASA.match(linea.strip())
        if m and m.group(1) in _NOMBRE_MASA:
            clave = _NOMBRE_MASA[m.group(1)]
            campos[f"{clave}_pct"] = _num(m.group(2))
            campos[f"{clave}_kg"] = _num(m.group(3))

    # Somatotipo (Heath & Carter) -- solo la posición actual, el resto de
    # esa página sale desordenado en la extracción de texto (ver docstring).
    m = _PATRON_SOMATO.search(texto_todo)
    if m:
        campos["somato_endo"] = _num(m.group(1))
        campos["somato_meso"] = _num(m.group(2))
        campos["somato_ecto"] = _num(m.group(3))

    # IMC -- "Indice masa corporal: Kg/m2" y el valor salen separados en
    # esa página (texto rotado), pero se puede calcular directo si hay
    # peso y talla, que es más confiable que intentar extraerlo del PDF.
    if campos.get("peso_kg") and campos.get("talla_cm"):
        campos["imc"] = round(campos["peso_kg"] / ((campos["talla_cm"] / 100) ** 2), 2)

    return campos, no_reconocidos, meta


# ── Endpoints ──────────────────────────────────────────────────────────────────
@training_bp.route("/api/training/antropometria/parsear", methods=["POST"])
@login_required
@modulo_required("training")
def api_antro_parsear():
    """Sube un PDF y lo parsea -- NO guarda nada todavía. El frontend
    muestra los campos extraídos en un formulario editable; recién al
    confirmar se llama a /guardar. Así un informe con formato distinto al
    esperado nunca puede meter datos mal parseados sin pasar por revisión."""
    if "archivo" not in request.files:
        return jsonify({"ok": False, "error": "Falta el archivo PDF."}), 400
    f = request.files["archivo"]
    if not f.filename.lower().endswith(".pdf"):
        return jsonify({"ok": False, "error": "Tiene que ser un PDF."}), 400

    os.makedirs(ANTRO_DIR, exist_ok=True)
    nombre = f"{uuid.uuid4().hex[:12]}_{f.filename}"
    ruta = os.path.join(ANTRO_DIR, nombre)
    f.save(ruta)

    campos, no_reconocidos, meta = parsear_antropometria_pdf(ruta)
    if meta.get("error"):
        return jsonify({"ok": False, "error": f"No se pudo leer el PDF: {meta['error']}"})

    return jsonify({"ok": True, "campos": campos, "no_reconocidos": no_reconocidos,
                     "meta": meta, "archivo_pdf": nombre})


@training_bp.route("/api/training/antropometria", methods=["POST"])
@login_required
@modulo_required("training")
def api_antro_guardar():
    """Guarda una medición ya revisada/confirmada por el usuario (puede
    venir de parsear_antropometria_pdf y haber sido editada a mano, o
    cargada 100% manual sin subir PDF)."""
    data = request.json or {}
    fecha = data.get("fecha_medicion") or date.today().isoformat()
    aid = str(uuid.uuid4())[:12]

    campos_validos = [
        "peso_kg", "talla_cm", "talla_sentado_cm",
        "diam_biacromial", "diam_torax_transverso", "diam_torax_anteroposterior",
        "diam_biiliocrestideo", "diam_humeral", "diam_femoral",
        "perim_cabeza", "perim_brazo_relajado", "perim_brazo_flexionado", "perim_antebrazo",
        "perim_torax", "perim_cintura", "perim_caderas", "perim_muslo_superior",
        "perim_muslo_medial", "perim_pantorrilla",
        "pliegue_triceps", "pliegue_subescapular", "pliegue_supraespinal", "pliegue_abdominal",
        "pliegue_muslo_medial", "pliegue_pantorrilla",
        "masa_adiposa_pct", "masa_adiposa_kg", "masa_muscular_pct", "masa_muscular_kg",
        "masa_residual_pct", "masa_residual_kg", "masa_osea_pct", "masa_osea_kg",
        "masa_piel_pct", "masa_piel_kg", "somato_endo", "somato_meso", "somato_ecto", "imc",
    ]
    valores = {c: data.get(c) for c in campos_validos}

    with get_db(HIST_DB) as con:
        con.execute(
            f"INSERT INTO antropometria (id, fecha_medicion, numero_medicion, archivo_pdf, {', '.join(campos_validos)}) "
            f"VALUES (?,?,?,?,{', '.join('?' for _ in campos_validos)})",
            [aid, fecha, data.get("numero_medicion"), data.get("archivo_pdf", "")] + [valores[c] for c in campos_validos])

    notificar_telegram(f"📏 Nueva medición de composición corporal cargada ({fecha}).")

    # Fase 11: dispara un análisis de IA comparando contra la medición
    # anterior -- no es el análisis diario (esto es cada 2-3 meses), va
    # aparte. Falla en silencio si no hay API key o hay solo 1 medición
    # (nada contra qué comparar todavía) -- no debe romper el guardado.
    try:
        _analizar_antropometria(aid)
    except Exception:
        logging.exception("Antropometría: no se pudo generar el análisis de IA")

    return jsonify({"ok": True, "id": aid})


@training_bp.route("/api/training/antropometria")
@login_required
@modulo_required("training")
def api_antro_listar():
    with get_db(HIST_DB, row_factory=True) as con:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM antropometria ORDER BY fecha_medicion DESC").fetchall()]
    return jsonify({"ok": True, "rows": rows})


@training_bp.route("/api/training/antropometria/<aid>", methods=["DELETE"])
@login_required
@modulo_required("training")
def api_antro_borrar(aid):
    with get_db(HIST_DB) as con:
        con.execute("DELETE FROM antropometria WHERE id=?", (aid,))
    return jsonify({"ok": True})


def _analizar_antropometria(aid):
    """Compara la medición recién guardada contra la anterior y le pide a
    Claude un análisis corto. Reusa _llamar_ia_haiku de training_plan.py
    en vez de duplicarlo -- import perezoso para evitar el ciclo de
    imports (training_plan importa de training, no al revés)."""
    api_key = _api_key()
    if not api_key:
        return
    with get_db(HIST_DB, row_factory=True) as con:
        actual = con.execute("SELECT * FROM antropometria WHERE id=?", (aid,)).fetchone()
        anterior = con.execute(
            "SELECT * FROM antropometria WHERE fecha_medicion < ? ORDER BY fecha_medicion DESC LIMIT 1",
            (actual["fecha_medicion"],)).fetchone()
    if not anterior:
        return  # primera medición, nada contra qué comparar

    actual, anterior = dict(actual), dict(anterior)
    campos_clave = [
        ("Peso", "peso_kg", "kg"), ("% Grasa", "masa_adiposa_pct", "%"),
        ("Masa muscular", "masa_muscular_kg", "kg"), ("Cintura", "perim_cintura", "cm"),
        ("Somatotipo (endo/meso/ecto)", None, None),
    ]
    lineas = [f"Comparación de composición corporal: medición del {anterior['fecha_medicion']} vs "
              f"{actual['fecha_medicion']} (protocolo ISAK).\n"]
    for nombre, campo, unidad in campos_clave:
        if campo:
            v_ant, v_act = anterior.get(campo), actual.get(campo)
            if v_ant is not None and v_act is not None:
                lineas.append(f"- {nombre}: {v_ant}{unidad} → {v_act}{unidad} (diferencia: {round(v_act-v_ant,2)}{unidad})")
        else:
            lineas.append(
                f"- Somatotipo: {anterior.get('somato_endo')}/{anterior.get('somato_meso')}/{anterior.get('somato_ecto')} "
                f"→ {actual.get('somato_endo')}/{actual.get('somato_meso')}/{actual.get('somato_ecto')}")
    lineas.append(
        "\nAnalizá el cambio en 3-4 líneas: ¿la composición mejoró (más músculo, menos grasa) o empeoró? "
        "¿el somatotipo se movió hacia más mesomorfo (más apropiado para Hyrox) o no? Sé concreto con los "
        "números, no genérico. No inventes datos que no te di.")
    prompt = "\n".join(lineas)

    from blueprints.training_plan import _llamar_ia_haiku
    try:
        respuesta = _llamar_ia_haiku(prompt, api_key)
        aidr = str(uuid.uuid4())[:12]
        with get_db(HIST_DB) as con:
            con.execute(
                "INSERT INTO garmin_analisis (id,tipo,fecha_desde,fecha_hasta,prompt_usado,respuesta,creado) "
                "VALUES (?,?,?,?,?,?,datetime('now'))",
                (aidr, "antropometria", anterior["fecha_medicion"], actual["fecha_medicion"], prompt, respuesta))
        notificar_telegram(f"📏 Análisis de composición corporal:\n\n{respuesta[:3500]}")
    except Exception:
        logging.exception("Antropometría: fall\u00f3 la llamada a la IA")
