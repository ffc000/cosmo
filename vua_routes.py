# Agregar rutas VUA extra al app.py

RUTAS = '''

# ── VUA Config ────────────────────────────────────────────────────────────────
@app.route("/api/vua/config", methods=["GET"])
@login_required
def vua_config_list():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM vua_config ORDER BY clave").fetchall()]
    con.close()
    return jsonify({"ok": True, "rows": rows})

@app.route("/api/vua/config/<clave>", methods=["PUT"])
@login_required
def vua_config_update(clave):
    data = request.json or {}
    contenido = data.get("contenido", "").strip()
    if not contenido:
        return jsonify({"ok": False, "error": "Contenido vacío"})
    con = sqlite3.connect(HIST_DB)
    con.execute("UPDATE vua_config SET contenido=?, modificado=datetime('now') WHERE clave=?", (contenido, clave))
    con.commit(); con.close()
    return jsonify({"ok": True})

@app.route("/api/vua/config/<clave>/mejorar", methods=["POST"])
@login_required
def vua_config_mejorar(clave):
    api_key = get_api_key()
    if not api_key:
        return jsonify({"ok": False, "error": "API key no configurada"})
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM vua_config WHERE clave=?", (clave,)).fetchone()
    con.close()
    if not row: return jsonify({"ok": False, "error": "Sección no encontrada"})
    try:
        import anthropic, httpx
        client = anthropic.Anthropic(api_key=api_key, http_client=httpx.Client(follow_redirects=True))
        msg = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=1500,
            messages=[{"role":"user","content":f"""Sos un analista de comercio exterior de ARCA.
Mejorá el texto de la sección "{row['titulo']}" del proyecto VUA manteniendo todos los datos concretos.
Estilo: formal, técnico, español rioplatense. Devolvé SOLO el texto mejorado.

TEXTO ACTUAL:
{row['contenido']}"""}])
        return jsonify({"ok": True, "texto": msg.content[0].text.strip()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

# ── VUA Equipo ────────────────────────────────────────────────────────────────
@app.route("/api/vua/equipo", methods=["GET"])
@login_required
def vua_equipo_list():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM vua_equipo WHERE activo=1 ORDER BY orden, organismo, nombre").fetchall()]
    con.close()
    return jsonify({"ok": True, "rows": rows})

@app.route("/api/vua/equipo", methods=["POST"])
@login_required
def vua_equipo_create():
    data = request.json or {}
    con = sqlite3.connect(HIST_DB)
    con.execute("INSERT INTO vua_equipo (nombre, cargo, organismo, email, activo) VALUES (?,?,?,?,1)",
        (data.get("nombre",""), data.get("cargo",""), data.get("organismo",""), data.get("email","")))
    con.commit(); con.close()
    return jsonify({"ok": True})

@app.route("/api/vua/equipo/<int:uid>", methods=["PUT"])
@login_required
def vua_equipo_update(uid):
    data = request.json or {}
    con = sqlite3.connect(HIST_DB)
    fields = []; params = []
    for f in ["nombre","cargo","organismo","email","activo"]:
        if f in data: fields.append(f"{f}=?"); params.append(data[f])
    if fields:
        params.append(uid)
        con.execute(f"UPDATE vua_equipo SET {', '.join(fields)} WHERE id=?", params)
        con.commit()
    con.close()
    return jsonify({"ok": True})

@app.route("/api/vua/equipo/<int:uid>", methods=["DELETE"])
@login_required
def vua_equipo_delete(uid):
    con = sqlite3.connect(HIST_DB)
    con.execute("UPDATE vua_equipo SET activo=0 WHERE id=?", (uid,))
    con.commit(); con.close()
    return jsonify({"ok": True})

# ── VUA Glosario ──────────────────────────────────────────────────────────────
@app.route("/api/vua/glosario", methods=["GET"])
@login_required
def vua_glosario_list():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM vua_glosario ORDER BY orden, termino").fetchall()]
    con.close()
    return jsonify({"ok": True, "rows": rows})

@app.route("/api/vua/glosario", methods=["POST"])
@login_required
def vua_glosario_create():
    data = request.json or {}
    con = sqlite3.connect(HIST_DB)
    con.execute("INSERT INTO vua_glosario (termino, definicion, categoria) VALUES (?,?,?)",
        (data.get("termino",""), data.get("definicion",""), data.get("categoria","general")))
    con.commit(); con.close()
    return jsonify({"ok": True})

@app.route("/api/vua/glosario/<int:gid>", methods=["PUT"])
@login_required
def vua_glosario_update(gid):
    data = request.json or {}
    con = sqlite3.connect(HIST_DB)
    fields = []; params = []
    for f in ["termino","definicion","categoria"]:
        if f in data: fields.append(f"{f}=?"); params.append(data[f])
    if fields:
        params.append(gid)
        con.execute(f"UPDATE vua_glosario SET {', '.join(fields)} WHERE id=?", params)
        con.commit()
    con.close()
    return jsonify({"ok": True})

@app.route("/api/vua/glosario/<int:gid>", methods=["DELETE"])
@login_required
def vua_glosario_delete(gid):
    con = sqlite3.connect(HIST_DB)
    con.execute("DELETE FROM vua_glosario WHERE id=?", (gid,))
    con.commit(); con.close()
    return jsonify({"ok": True})

# ── VUA Ejes — mejorar con IA ─────────────────────────────────────────────────
@app.route("/api/vua/ejes/<int:eje_id>/mejorar", methods=["POST"])
@login_required
def vua_eje_mejorar(eje_id):
    api_key = get_api_key()
    if not api_key: return jsonify({"ok": False, "error": "API key no configurada"})
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    eje = con.execute("SELECT * FROM vua_ejes WHERE id=?", (eje_id,)).fetchone()
    con.close()
    if not eje: return jsonify({"ok": False, "error": "Eje no encontrado"})
    try:
        import anthropic, httpx, json as _json
        client = anthropic.Anthropic(api_key=api_key, http_client=httpx.Client(follow_redirects=True))
        msg = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=400,
            messages=[{"role":"user","content":f"""Mejorá la redacción del nombre y estado del siguiente eje de trabajo del proyecto VUA (Ventanilla Única Aeroportuaria — DGA Argentina). Mantené el sentido exacto.
Respondé SOLO con JSON: {{"nombre":"...","estado":"..."}}

NOMBRE: {eje['nombre']}
ESTADO: {eje['estado']}"""}])
        resultado = _json.loads(msg.content[0].text.strip().replace("```json","").replace("```",""))
        return jsonify({"ok": True, "nombre": resultado.get("nombre",""), "estado": resultado.get("estado","")})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

# ── VUA Minuta con IA ─────────────────────────────────────────────────────────
@app.route("/api/vua/minuta_ia", methods=["POST"])
@login_required
def vua_minuta_ia():
    api_key = get_api_key()
    if not api_key: return jsonify({"ok": False, "error": "API key no configurada"})
    data = request.json or {}
    asunto = data.get("asunto","")
    participantes = data.get("participantes",[])
    temas = data.get("temas",[])
    try:
        import anthropic, httpx, json as _json
        client = anthropic.Anthropic(api_key=api_key, http_client=httpx.Client(follow_redirects=True))
        partic_txt = "; ".join([f"{p['nombre']} ({p['cargo']} — {p['organismo']})" for p in participantes])
        temas_txt = "\n".join([f"- {t}" for t in temas])
        msg = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=1500,
            messages=[{"role":"user","content":f"""Sos un analista de DI REPA (ARCA). Generá el borrador de un acta de reunión del proyecto VUA.
Contexto: Mesa de trabajo interinstitucional DGA (DI REPA, DI SADU, DI ADEZ) y VUCEA sobre integración de sistemas para carga aérea en Ezeiza.

ASUNTO: {asunto}
PARTICIPANTES: {partic_txt}
TEMAS: {temas_txt}

Devolvé SOLO un JSON válido:
{{"temas_tratados":["párrafo 1","párrafo 2"],"acuerdos":["acuerdo 1","acuerdo 2"],"proximos_pasos":["paso 1","paso 2"]}}

Estilo: formal, técnico, español rioplatense. Temas tratados: 2-3 oraciones cada uno. Acuerdos y próximos pasos: concisos y accionables."""}])
        texto = msg.content[0].text.strip().replace("```json","").replace("```","").strip()
        resultado = _json.loads(texto)
        return jsonify({"ok": True, **resultado})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
'''

content = open('/opt/sintia/app.py').read()
if 'vua_config_list' not in content:
    with open('/opt/sintia/app.py', 'a') as f:
        f.write(RUTAS)
    print("OK — rutas VUA extra agregadas")
else:
    print("Las rutas ya existen — sin cambios")
