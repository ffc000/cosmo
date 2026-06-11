import ast

RUTAS = '''

# ── VUA Riesgos ───────────────────────────────────────────────────────────────
@app.route("/api/vua/riesgos", methods=["GET"])
@login_required
def vua_riesgos_list():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM vua_riesgos WHERE activo=1 ORDER BY orden").fetchall()]
    con.close()
    return jsonify({"ok": True, "rows": rows})

@app.route("/api/vua/riesgos/<int:rid>", methods=["PUT"])
@login_required
def vua_riesgos_update(rid):
    data = request.json or {}
    con = sqlite3.connect(HIST_DB)
    fields = []; params = []
    for f in ["titulo", "descripcion", "mitigacion", "probabilidad", "impacto", "activo"]:
        if f in data:
            fields.append(f + "=?")
            params.append(data[f])
    if fields:
        params.append(rid)
        con.execute("UPDATE vua_riesgos SET " + ", ".join(fields) + " WHERE id=?", params)
        con.commit()
    con.close()
    return jsonify({"ok": True})

# ── VUA Correos rapidos ───────────────────────────────────────────────────────
@app.route("/api/vua/correos_rapidos", methods=["GET"])
@login_required
def vua_correos_rapidos_list():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM vua_correos_rapidos WHERE activo=1 ORDER BY orden").fetchall()]
    con.close()
    return jsonify({"ok": True, "rows": rows})

@app.route("/api/vua/correos_rapidos", methods=["POST"])
@login_required
def vua_correos_rapidos_create():
    data = request.json or {}
    con = sqlite3.connect(HIST_DB)
    con.execute("INSERT INTO vua_correos_rapidos (etiqueta, instruccion, activo) VALUES (?,?,1)",
        (data.get("etiqueta",""), data.get("instruccion","")))
    con.commit(); con.close()
    return jsonify({"ok": True})

@app.route("/api/vua/correos_rapidos/<int:cid>", methods=["PUT"])
@login_required
def vua_correos_rapidos_update(cid):
    data = request.json or {}
    con = sqlite3.connect(HIST_DB)
    fields = []; params = []
    for f in ["etiqueta", "instruccion", "activo"]:
        if f in data:
            fields.append(f + "=?")
            params.append(data[f])
    if fields:
        params.append(cid)
        con.execute("UPDATE vua_correos_rapidos SET " + ", ".join(fields) + " WHERE id=?", params)
        con.commit()
    con.close()
    return jsonify({"ok": True})

@app.route("/api/vua/correos_rapidos/<int:cid>", methods=["DELETE"])
@login_required
def vua_correos_rapidos_delete(cid):
    con = sqlite3.connect(HIST_DB)
    con.execute("UPDATE vua_correos_rapidos SET activo=0 WHERE id=?", (cid,))
    con.commit(); con.close()
    return jsonify({"ok": True})

# ── VUA Info (normativa, bpmn descripciones) ──────────────────────────────────
@app.route("/api/vua/info/<clave>", methods=["GET"])
@login_required
def vua_info_get(clave):
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM vua_info WHERE clave=?", (clave,)).fetchone()
    con.close()
    if not row:
        return jsonify({"ok": False, "error": "No encontrado"})
    return jsonify({"ok": True, "item": dict(row)})

@app.route("/api/vua/info/<clave>", methods=["PUT"])
@login_required
def vua_info_update(clave):
    data = request.json or {}
    contenido = data.get("contenido", "").strip()
    if not contenido:
        return jsonify({"ok": False, "error": "Contenido vacio"})
    con = sqlite3.connect(HIST_DB)
    con.execute("UPDATE vua_info SET contenido=?, modificado=datetime('now') WHERE clave=?",
        (contenido, clave))
    con.commit(); con.close()
    return jsonify({"ok": True})
'''

try:
    ast.parse(RUTAS)
    print("OK sintaxis")
except SyntaxError as e:
    print(f"ERROR: {e}"); exit(1)

content = open('/opt/sintia/app.py').read()
if 'vua_riesgos_list' not in content:
    with open('/opt/sintia/app.py', 'a') as f:
        f.write(RUTAS)
    print("OK rutas agregadas")
else:
    print("Las rutas ya existen")

try:
    ast.parse(open('/opt/sintia/app.py').read())
    print("OK app.py sin errores")
except SyntaxError as e:
    print(f"ERROR app.py linea {e.lineno}: {e.msg}")

RUTAS2 = '''

# ── VUA Consultas frecuentes ──────────────────────────────────────────────────
@app.route("/api/vua/consultas_frecuentes", methods=["GET"])
@login_required
def vua_consultas_frecuentes_list():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM vua_consultas_frecuentes WHERE activo=1 ORDER BY orden").fetchall()]
    con.close()
    return jsonify({"ok": True, "rows": rows})
'''

try:
    ast.parse(RUTAS2)
    print("OK sintaxis RUTAS2")
except SyntaxError as e:
    print(f"ERROR: {e}"); exit(1)

content = open('/opt/sintia/app.py').read()
if 'vua_consultas_frecuentes_list' not in content:
    with open('/opt/sintia/app.py', 'a') as f:
        f.write(RUTAS2)
    print("OK RUTAS2 agregadas")
else:
    print("RUTAS2 ya existen")
