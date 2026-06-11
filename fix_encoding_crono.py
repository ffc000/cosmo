import sqlite3

HIST_DB = "/data/historial.db"
con = sqlite3.connect(HIST_DB)
con.row_factory = sqlite3.Row

rows = con.execute("SELECT id, fecha, actividad, participantes FROM vua_cronologia").fetchall()
fixed = 0
for row in rows:
    updates = {}
    for field in ['fecha', 'actividad', 'participantes']:
        val = row[field] or ''
        try:
            fixed_val = val.encode('latin1').decode('utf-8')
            if fixed_val != val:
                updates[field] = fixed_val
        except:
            pass
    if updates:
        sets = ', '.join(f + '=?' for f in updates)
        params = list(updates.values()) + [row['id']]
        con.execute(f"UPDATE vua_cronologia SET {sets} WHERE id=?", params)
        fixed += 1

con.commit()
con.close()
print(f"OK — {fixed} filas corregidas de {len(rows)} totales")
