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
