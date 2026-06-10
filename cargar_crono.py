import sqlite3, urllib.request, os

# Descargar CSV desde GitHub
CSV_URL = "https://raw.githubusercontent.com/ffc000/cosmo/main/VUA_-_Cronología_de_actividades.csv"
CSV_TMP = "/tmp/crono_vua.csv"

print(f"Descargando CSV desde GitHub...")
urllib.request.urlretrieve(CSV_URL, CSV_TMP)
print(f"OK — descargado en {CSV_TMP}")

with open(CSV_TMP, 'rb') as f:
    raw = f.read().replace(b'\r\n', b'\n').replace(b'\r', b'\n')
lines = raw.decode('latin1').split('\n')

con = sqlite3.connect('/data/historial.db')

# Verificar si tiene columna 'orden', si no agregarla
cols = [r[1] for r in con.execute("PRAGMA table_info(vua_cronologia)").fetchall()]
if 'orden' not in cols:
    con.execute("ALTER TABLE vua_cronologia ADD COLUMN orden INTEGER DEFAULT 99")
    print("Columna 'orden' agregada")

con.execute("DELETE FROM vua_cronologia")

orden = 1
for line in lines[1:]:
    if not line.strip(): continue
    partes = line.split(';')
    if len(partes) < 4: continue
    fecha = partes[0].strip()
    actividad = partes[1].strip()
    participantes = partes[2].strip()
    estado = partes[3].strip()
    con.execute(
        "INSERT INTO vua_cronologia (orden, fecha, actividad, participantes, estado) VALUES (?,?,?,?,?)",
        (orden, fecha, actividad, participantes, estado)
    )
    orden += 1

con.commit(); con.close()
print(f"OK — {orden-1} actividades cargadas")

os.remove(CSV_TMP)
