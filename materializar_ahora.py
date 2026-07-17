#!/usr/bin/env python3
"""
materializar_ahora.py — Script de UN SOLO USO para crear DAT_2025_actual y
DAT_2026_actual ya mismo en el servidor, sin esperar al deploy completo del
fix (db_utils.py/app.py/generar_queries.py actualizados).

Por qué existe: el 502 actual es porque el dashboard/Consultar DAT recalculan
la deduplicación (ROW_NUMBER() OVER ...) en cada request -- medido en ~2-4s
por query en una tabla de tamaño real, con ~9 queries solo en el dashboard.
Este script hace ese cálculo UNA vez y lo deja guardado en una tabla real
(DAT_<año>_actual), que es lo que el código ya actualizado va a leer en vez
de recalcular. Corriendo esto ahora, el sitio puede volver a andar incluso
antes de terminar de desplegar el resto de los archivos (aunque para que
quede realmente resuelto hace falta desplegarlos igual -- este script no
reemplaza eso, solo destraba la situación actual).

Uso:
    python3 materializar_ahora.py

Corre contra /data/pad.db (o $DB_PATH si está seteada). Idempotente: se
puede correr de nuevo sin problema (DROP TABLE IF EXISTS antes de crear).
"""
import os
import sys
import time
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db_utils import DB_PATH, _FECHA_ULT_INT_ISO_EXPR  # usa la MISMA expresión ya probada


def materializar(con, tabla):
    tabla_actual = f"{tabla}_actual"
    existe = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tabla,)).fetchone()
    if not existe:
        print(f"  {tabla}: no existe, se salta.")
        return

    columnas = [r[1] for r in con.execute(f"PRAGMA table_info({tabla})").fetchall()]
    faltan = {"OPERACION_PAD_EXT", "MIC", "TIPO_REGISTRO", "FECHA_ULT_INT"} - set(columnas)
    if faltan:
        print(f"  {tabla}: le faltan columnas {faltan}, no se puede materializar. Se salta.")
        return
    cols_sql = ", ".join(f'"{c}"' for c in columnas)

    t0 = time.time()
    con.execute(f"DROP TABLE IF EXISTS {tabla_actual}")
    con.execute(f"""
        CREATE TABLE {tabla_actual} AS
        SELECT {cols_sql} FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY OPERACION_PAD_EXT, MIC, TIPO_REGISTRO
                ORDER BY {_FECHA_ULT_INT_ISO_EXPR} DESC, rowid DESC
            ) AS _rn FROM {tabla}
        ) WHERE _rn = 1
    """)
    for nombre, col in [("fecha", "FECHA_INGRESO_ISO"), ("estado", "EST_MIC"), ("aduana", "ADUANA")]:
        if col in columnas:
            con.execute(f"CREATE INDEX IF NOT EXISTS idx_{tabla_actual}_{nombre} ON {tabla_actual}({col})")
    con.commit()

    total_crudo = con.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]
    total_actual = con.execute(f"SELECT COUNT(*) FROM {tabla_actual}").fetchone()[0]
    print(f"  {tabla}: {total_crudo:,} filas de historial -> {total_actual:,} operaciones "
          f"únicas en {tabla_actual} ({time.time()-t0:.1f}s)")


def main():
    print(f"Conectando a {DB_PATH}...")
    con = sqlite3.connect(DB_PATH, timeout=30)
    anios = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name GLOB 'DAT_[0-9][0-9][0-9][0-9]'"
    ).fetchall()]
    print(f"Tablas DAT_<año> encontradas: {anios}")
    for tabla in anios:
        materializar(con, tabla)
    con.close()
    print("Listo.")


if __name__ == "__main__":
    main()
