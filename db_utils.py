"""
db_utils.py — Helper de conexión SQLite compartido, SIN dependencia de Flask.

Existe separado de core.py a propósito: algunos módulos (finanzas_datos.py,
generar.py y sus generar_*.py) están diseñados para poder importarse y
testearse sin levantar Flask (ver sus propios docstrings — "para poder
testearla sin levantar Flask"). core.py, en cambio, crea la app Flask y exige
SECRET_KEY apenas se lo importa. Si get_db() viviera en core.py, cualquier
módulo "sin Flask" que lo usara dejaría de poder importarse solo — rompería
justamente la propiedad que esos módulos buscan preservar.

core.py importa HIST_DB/DB_PATH/get_db de acá y los re-exporta, así que
`from core import get_db` sigue funcionando igual que antes para app.py y
los blueprints — este archivo es un detalle de implementación interno.
"""
import os
import sqlite3
import contextlib

HIST_DB = os.environ.get("HIST_DB", "/data/historial.db")
DB_PATH = os.environ.get("DB_PATH", "/data/pad.db")


@contextlib.contextmanager
def get_db(db_path=None, timeout=10, row_factory=False):
    """Context manager compartido para abrir una conexión SQLite.

    Antes cada función (~200 lugares repartidos entre app.py, los blueprints,
    finanzas_datos.py y generar.py) hacía su propio `con = sqlite3.connect(...)`
    con timeout copiado a mano, y su propio `con.commit(); con.close()` al
    final. Funciona, pero cualquier cambio que deba aplicar a TODAS las
    conexiones (agregar `PRAGMA foreign_keys=ON`, cambiar el timeout, sumar
    logging de queries lentas) implica tocar esos ~200 lugares uno por uno.

    Este helper no cambia el modelo de concurrencia (sigue siendo una
    conexión nueva por operación, no un pool) — solo centraliza la apertura.
    Uso:
        with get_db() as con:
            con.execute("INSERT INTO ...", (...))
        # commit automático si no hubo excepción, rollback si la hubo,
        # con.close() siempre se ejecuta.

        with get_db(DB_PATH, row_factory=True) as con:
            rows = con.execute("SELECT * FROM ...").fetchall()  # sqlite3.Row

    Migrar los call-sites existentes a esto es mecánico pero no automático
    (a propósito no se hizo de una sola vez, para no tocar ~40 archivos en
    un solo cambio sin poder correr los tests de cada uno) — conviene ir
    módulo por módulo, corriendo los tests de ese módulo después de cada
    migración, igual que se hizo al extraer los blueprints de app.py."""
    con = sqlite3.connect(db_path or HIST_DB, timeout=timeout)
    if row_factory:
        con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ── Deduplicación de historial en DAT_<año> ──────────────────────────────────
# FECHA_ULT_INT no viene en un formato único: se vieron "22/12/2025 11:26"
# (día/mes de ancho variable, separador "/") y también "DD-MM-YYYY HH:MM:SS"
# (ancho fijo, separador "-", el formato que ya asumía _FECHA_ULT_INT_ISO en
# app.py/generar_queries.py). Un primer intento de reusar esa expresión acá
# (basada en substr por POSICIÓN fija) ordenaba mal los casos "D/M/YYYY" sin
# cero a la izquierda -- encontrado en pruebas, 17/07/2026 -- así que esta
# versión parsea por posición del separador, no por offset fijo, y cubre
# los 3 formatos vistos (ISO, DD/M/YYYY variable, DD-MM-YYYY fijo).
def _fecha_ult_int_iso_expr(col="FECHA_ULT_INT"):
    p1 = f"INSTR({col}, '/')"
    dia = f"SUBSTR({col}, 1, {p1} - 1)"
    resto1 = f"SUBSTR({col}, {p1} + 1)"
    p2 = f"INSTR({resto1}, '/')"
    mes = f"SUBSTR({resto1}, 1, {p2} - 1)"
    resto2 = f"SUBSTR({resto1}, {p2} + 1)"
    anio = f"SUBSTR({resto2}, 1, 4)"
    hora = f"SUBSTR({resto2}, 6)"
    iso_slash = (f"({anio} || '-' || printf('%02d', CAST({mes} AS INTEGER)) || '-' || "
                 f"printf('%02d', CAST({dia} AS INTEGER)) || ' ' || {hora})")
    return (
        f"(CASE"
        f" WHEN {col} LIKE '____-__-__%' THEN {col}"
        f" WHEN {col} LIKE '%/%/%' THEN {iso_slash}"
        f" WHEN {col} LIKE '__-__-____%' THEN"
        f" substr({col},7,4) || '-' || substr({col},4,2) || '-' || substr({col},1,2) || substr({col},11)"
        f" ELSE NULL END)"
    )


_FECHA_ULT_INT_ISO_EXPR = _fecha_ult_int_iso_expr()


def dat_actual_subquery(tabla, alias=None, con=None, db_path=None):
    """Subquery SQL: "última fila" por operación (OPERACION_PAD_EXT + MIC +
    TIPO_REGISTRO) de una tabla DAT_<año> -- para usar en cualquier FROM
    donde antes se ponía el nombre de tabla directo.

    Por qué existe (decisión 17/07/2026): desde que _procesar_csv pasó de
    INSERT OR REPLACE a INSERT OR IGNORE por huella de contenido (para
    conservar el historial de estados de cada operación en vez de pisarlo
    en cada carga semanal), una tabla DAT_<año> puede tener varias filas
    para la misma operación -- una por cada estado distinto que tuvo. Eso
    es intencional (permite analizar cuánto tarda una operación en pasar de
    un estado a otro), pero significa que cualquier COUNT(*) o agregación
    que antes asumía "1 fila = 1 operación" ahora cuenta de más. Esta
    subquery resuelve eso quedándose con una sola fila por operación (la de
    FECHA_ULT_INT más reciente, con rowid más alto como desempate), así
    cualquier query que la use por debajo sigue viendo "1 fila = 1
    operación actual" -- el comportamiento de siempre. Para el informe de
    historial/tiempo-entre-estados en sí (que sí necesita TODAS las filas)
    no se usa esto, se consulta la tabla directamente.

    Proyecta EXPLÍCITAMENTE las columnas reales de la tabla (consultadas
    con PRAGMA table_info) en vez de un SELECT * a secas -- un SELECT *
    ingenuo sobre "SELECT *, ROW_NUMBER() ... AS _rn ... WHERE _rn=1" deja
    pasar la columna interna _rn hacia afuera (encontrado en producción,
    17/07/2026: aparecía como columna fantasma en "Consultar DAT").

    Para el PRAGMA hace falta una conexión: si el caller ya tiene una
    abierta (con), se reusa esa -- IMPORTANTE pasarla cuando se tenga,
    porque generar_queries.py recibe `ruta_db` como parámetro (puede no
    ser el mismo archivo que db_utils.DB_PATH, ej. en tests con una base
    temporal); usar el DB_PATH global ahí consultaría el schema de la base
    equivocada. Si no se pasa `con` (ej. algunos call-sites de app.py que
    todavía no abrieron una conexión en ese punto), se abre una propia de
    vida corta contra `db_path` (o DB_PATH si tampoco se especifica) --
    ahí sí es seguro porque en app.py DB_PATH siempre es el global real.

    `tabla` tiene que ser el nombre real de una tabla (ej. "DAT_2026"), no
    ya una subquery -- si se necesita combinar varios años, aplicar esto a
    CADA tabla individual antes de unirlas (rowid solo existe en tablas
    reales, no en subqueries).

    alias: nombre con el que queda expuesta la subquery resultante
    (default: el mismo `tabla`, para que las queries existentes que hacen
    `FROM {tabla}` sigan funcionando igual solo cambiando ese valor, sin
    tocar el resto de su SQL)."""
    alias = alias or tabla
    if con is not None:
        columnas = [r[1] for r in con.execute(f"PRAGMA table_info({tabla})").fetchall()]
    else:
        with get_db(db_path or DB_PATH) as con2:
            columnas = [r[1] for r in con2.execute(f"PRAGMA table_info({tabla})").fetchall()]
    cols_sql = ", ".join(f'"{c}"' for c in columnas)
    return (
        f"(SELECT {cols_sql} FROM ("
        f"SELECT *, ROW_NUMBER() OVER ("
        f"PARTITION BY OPERACION_PAD_EXT, MIC, TIPO_REGISTRO "
        f"ORDER BY {_FECHA_ULT_INT_ISO_EXPR} DESC, rowid DESC"
        f") AS _rn FROM {tabla}"
        f") WHERE _rn = 1) AS {alias}"
    )
