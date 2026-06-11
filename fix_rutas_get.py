import ast, re

RUTAS = '''

# ── VUA GET routes faltantes ──────────────────────────────────────────────────

@app.route("/api/vua/ejes", methods=["GET"])
@login_required
def vua_ejes_list():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM vua_ejes ORDER BY orden").fetchall()]
    con.close()
    return jsonify({"ok": True, "rows": rows})

@app.route("/api/vua/cronologia", methods=["GET"])
@login_required
def vua_cronologia_list_get():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM vua_cronologia ORDER BY orden, id").fetchall()]
    con.close()
    return jsonify({"ok": True, "rows": rows})

@app.route("/api/vua/equipo", methods=["GET"])
@login_required
def vua_equipo_list_get():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM vua_equipo WHERE activo=1 ORDER BY orden, organismo, nombre").fetchall()]
    con.close()
    return jsonify({"ok": True, "rows": rows})

@app.route("/api/vua/glosario", methods=["GET"])
@login_required
def vua_glosario_list_get():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM vua_glosario ORDER BY orden, termino").fetchall()]
    con.close()
    return jsonify({"ok": True, "rows": rows})

@app.route("/api/vua/config", methods=["GET"])
@login_required
def vua_config_list_get():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM vua_config ORDER BY clave").fetchall()]
    con.close()
    return jsonify({"ok": True, "rows": rows})

@app.route("/api/vua/riesgos", methods=["GET"])
@login_required
def vua_riesgos_list_get():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM vua_riesgos WHERE activo=1 ORDER BY orden").fetchall()]
    con.close()
    return jsonify({"ok": True, "rows": rows})

@app.route("/api/vua/correos_rapidos", methods=["GET"])
@login_required
def vua_correos_rapidos_list_get():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM vua_correos_rapidos WHERE activo=1 ORDER BY orden").fetchall()]
    con.close()
    return jsonify({"ok": True, "rows": rows})

@app.route("/api/vua/consultas_frecuentes", methods=["GET"])
@login_required
def vua_consultas_frecuentes_list_get():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM vua_consultas_frecuentes WHERE activo=1 ORDER BY orden").fetchall()]
    con.close()
    return jsonify({"ok": True, "rows": rows})

@app.route("/api/vua/info/<clave>", methods=["GET"])
@login_required
def vua_info_get_v2(clave):
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM vua_info WHERE clave=?", (clave,)).fetchone()
    con.close()
    if not row:
        return jsonify({"ok": False, "error": "No encontrado"})
    return jsonify({"ok": True, "item": dict(row)})
'''

# Verificar sintaxis del bloque nuevo
try:
    ast.parse(RUTAS)
    print("OK sintaxis rutas nuevas")
except SyntaxError as e:
    print(f"ERROR sintaxis: {e}"); exit(1)

# Leer app.py
content = open('/opt/sintia/app.py').read()

# Verificar cuáles faltan (por nombre de función)
funciones = [
    'vua_ejes_list',
    'vua_cronologia_list_get',
    'vua_equipo_list_get',
    'vua_glosario_list_get',
    'vua_config_list_get',
    'vua_riesgos_list_get',
    'vua_correos_rapidos_list_get',
    'vua_consultas_frecuentes_list_get',
    'vua_info_get_v2',
]

# También verificar las originales con nombres diferentes
nombres_alternativos = {
    'vua_ejes_list': ['def vua_ejes_list'],
    'vua_cronologia_list_get': ['def vua_cronologia_list', 'def vua_cronologia_list_get'],
    'vua_equipo_list_get': ['def vua_equipo_list', 'def vua_equipo_list_get'],
    'vua_glosario_list_get': ['def vua_glosario_list', 'def vua_glosario_list_get'],
    'vua_config_list_get': ['def vua_config_list', 'def vua_config_list_get'],
    'vua_riesgos_list_get': ['def vua_riesgos_list', 'def vua_riesgos_list_get'],
    'vua_correos_rapidos_list_get': ['def vua_correos_rapidos_list', 'def vua_correos_rapidos_list_get'],
    'vua_consultas_frecuentes_list_get': ['def vua_consultas_frecuentes_list', 'def vua_consultas_frecuentes_list_get'],
    'vua_info_get_v2': ['def vua_info_get', 'def vua_info_get_v2'],
}

# Verificar rutas GET existentes
routes = re.findall(r"@app\.route\(['\"]([^'\"]+)['\"].*?methods=\[([^\]]+)\]", content)
get_routes = {p for p, m in routes if 'GET' in m}
print(f"\nRutas GET VUA existentes: {sorted(r for r in get_routes if 'vua' in r)}")

faltantes = []
for fn, alternativas in nombres_alternativos.items():
    existe = any(alt in content for alt in alternativas)
    if not existe:
        faltantes.append(fn)
        print(f"  FALTA: {fn}")
    else:
        print(f"  OK: {fn}")

if faltantes:
    with open('/opt/sintia/app.py', 'a') as f:
        f.write(RUTAS)
    print(f"\nAgregadas {len(faltantes)} rutas faltantes")
else:
    print("\nTodas las rutas ya existen")

# Verificar sintaxis final
try:
    ast.parse(open('/opt/sintia/app.py').read())
    print("OK app.py sin errores de sintaxis")
except SyntaxError as e:
    print(f"ERROR app.py línea {e.lineno}: {e.msg}")
