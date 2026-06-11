import sqlite3, ast

HIST_DB = "/data/historial.db"
con = sqlite3.connect(HIST_DB)

# Verificar tablas y columnas
for tabla in ['vua_ejes', 'vua_glosario', 'vua_riesgos', 'vua_correos_rapidos',
              'vua_info', 'vua_consultas_frecuentes', 'vua_equipo', 'vua_config']:
    try:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({tabla})").fetchall()]
        count = con.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]
        print(f"  {tabla}: {count} filas, cols={cols}")
    except Exception as e:
        print(f"  {tabla}: ERROR — {e}")

con.close()
