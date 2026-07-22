"""
blueprints/pad_acuatico.py — Módulo Pad Acuático.

Creado a pedido (22/07/2026) reutilizando la misma estructura que ya
funciona en producción para VUA y SENASA: Cronología, Ejes de trabajo y
Minutas están tomados de blueprints/senasa.py (mismo esquema de datos,
mismos endpoints, solo cambia el prefijo de tabla/URL); Glosario está
tomado de blueprints/vua.py. Repositorio NO tiene endpoints acá porque ya
es genérico en app.py (/api/repositorio/<modulo>) -- alcanza con que
"pad_acuatico" esté en MODULOS_REPOSITORIO (core.py).

A propósito NO se incluyeron "Acuerdos/Compromisos" ni "Informe" ni
"Integrantes" (SENASA y VUA sí los tienen) porque no fueron pedidos para
este módulo -- si hacen falta más adelante, agregarlos siguiendo el mismo
patrón que senasa.py.
"""
import os
import json
import uuid

from actas import generar_acta_word
from datetime import datetime

from flask import Blueprint, request, jsonify, render_template, session, send_file

from core import (
    HIST_DB, login_required, modulo_required,
    get_api_key, contexto_repositorio,
    _normalizar_fecha_a_ddmmaaaa, _validar_fecha_ddmmaaaa,
    validar_enum, ESTADOS_TAREA, get_db,
)

pad_acuatico_bp = Blueprint("pad_acuatico", __name__)

# MÓDULO PAD ACUÁTICO
# ══════════════════════════════════════════════════════════════════════════════

@pad_acuatico_bp.route("/pad_acuatico")
@login_required
@modulo_required("pad_acuatico")
def pad_acuatico_index():
    return render_template("pad_acuatico.html", username=session.get("username", ""),
        role=session.get("role", "admin"))

# ── Cronología ────────────────────────────────────────────────────────────────
@pad_acuatico_bp.route("/api/pad_acuatico/cronologia", methods=["GET"])
@login_required
@modulo_required("pad_acuatico")
def pad_acuatico_crono_list():
    with get_db(HIST_DB, row_factory=True) as con:
        rows = [dict(r) for r in con.execute(
            "SELECT *, CASE WHEN fecha GLOB '[0-9][0-9]/[0-9][0-9]/[0-9][0-9][0-9][0-9]' "
            "THEN substr(fecha,7,4)||substr(fecha,4,2)||substr(fecha,1,2) ELSE '00000000' END as _ord "
            "FROM pad_acuatico_cronologia ORDER BY (estado='Pendiente') DESC, _ord DESC, id ASC").fetchall()]
    for r in rows: r.pop("_ord", None)
    return jsonify({"ok": True, "rows": rows})

@pad_acuatico_bp.route("/api/pad_acuatico/cronologia", methods=["POST"])
@login_required
@modulo_required("pad_acuatico")
def pad_acuatico_crono_add():
    data = request.json or {}
    ok, err = validar_enum(data.get("estado"), ESTADOS_TAREA, "estado")
    if not ok: return jsonify({"ok": False, "error": err}), 400
    fecha = _normalizar_fecha_a_ddmmaaaa(data.get("fecha","")) if data.get("fecha") else "A definir"
    with get_db(HIST_DB) as con:
        cur = con.cursor()
        cur.execute("SELECT MAX(orden) FROM pad_acuatico_cronologia")
        max_o = cur.fetchone()[0] or 0
        cur.execute("INSERT INTO pad_acuatico_cronologia (fecha,actividad,participantes,estado,orden) VALUES (?,?,?,?,?)",
            (fecha, data.get("actividad",""),
             data.get("participantes",""), data.get("estado","Pendiente"), max_o+1))
        new_id = cur.lastrowid
    return jsonify({"ok": True, "id": new_id})

@pad_acuatico_bp.route("/api/pad_acuatico/cronologia/<int:iid>", methods=["PUT"])
@login_required
@modulo_required("pad_acuatico")
def pad_acuatico_crono_update(iid):
    data = request.json or {}
    if "fecha" in data and not _validar_fecha_ddmmaaaa(data["fecha"]):
        return jsonify({"ok": False, "error": f"Fecha inválida: '{data['fecha']}'. Formato requerido: dd/mm/aaaa."}), 400
    ok, err = validar_enum(data.get("estado"), ESTADOS_TAREA, "estado")
    if not ok: return jsonify({"ok": False, "error": err}), 400
    campos = {k: v for k, v in data.items() if k in ("fecha","actividad","participantes","estado")}
    if not campos:
        return jsonify({"ok": False, "error": "Nada para actualizar"}), 400
    set_clause = ", ".join(f"{k}=?" for k in campos)
    with get_db(HIST_DB) as con:
        con.execute(f"UPDATE pad_acuatico_cronologia SET {set_clause}, modificado=datetime('now') WHERE id=?",
            (*campos.values(), iid))
    return jsonify({"ok": True})

@pad_acuatico_bp.route("/api/pad_acuatico/cronologia/<int:iid>", methods=["DELETE"])
@login_required
@modulo_required("pad_acuatico")
def pad_acuatico_crono_delete(iid):
    with get_db(HIST_DB) as con:
        con.execute("DELETE FROM pad_acuatico_cronologia WHERE id=?", (iid,))
    return jsonify({"ok": True})

# ── Ejes de trabajo ───────────────────────────────────────────────────────────
@pad_acuatico_bp.route("/api/pad_acuatico/ejes", methods=["GET"])
@login_required
@modulo_required("pad_acuatico")
def pad_acuatico_ejes_list():
    with get_db(HIST_DB, row_factory=True) as con:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM pad_acuatico_ejes ORDER BY orden ASC").fetchall()]
    return jsonify({"ok": True, "rows": rows})

@pad_acuatico_bp.route("/api/pad_acuatico/ejes/<int:iid>", methods=["PUT"])
@login_required
@modulo_required("pad_acuatico")
def pad_acuatico_ejes_update(iid):
    data = request.json or {}
    with get_db(HIST_DB) as con:
        con.execute("UPDATE pad_acuatico_ejes SET nombre=?,descripcion=?,estado=? WHERE id=?",
            (data.get("nombre",""), data.get("descripcion",""), data.get("estado",""), iid))
    return jsonify({"ok": True})

@pad_acuatico_bp.route("/api/pad_acuatico/ejes", methods=["POST"])
@login_required
@modulo_required("pad_acuatico")
def pad_acuatico_ejes_create():
    data = request.json or {}
    with get_db(HIST_DB, row_factory=True) as con:
        max_orden = con.execute("SELECT MAX(orden) FROM pad_acuatico_ejes").fetchone()[0] or 0
        con.execute("INSERT INTO pad_acuatico_ejes (nombre, descripcion, estado, orden) VALUES (?,?,?,?)",
            (data.get("nombre",""), data.get("descripcion",""), data.get("estado","Pendiente"), max_orden + 1))
    return jsonify({"ok": True})

@pad_acuatico_bp.route("/api/pad_acuatico/ejes/<int:iid>", methods=["DELETE"])
@login_required
@modulo_required("pad_acuatico")
def pad_acuatico_ejes_delete(iid):
    with get_db(HIST_DB) as con:
        con.execute("DELETE FROM pad_acuatico_ejes WHERE id=?", (iid,))
    return jsonify({"ok": True})

# ── Minutas ───────────────────────────────────────────────────────────────────
@pad_acuatico_bp.route("/api/pad_acuatico/minutas", methods=["GET"])
@login_required
@modulo_required("pad_acuatico")
def pad_acuatico_minutas_list():
    with get_db(HIST_DB, row_factory=True) as con:
        rows = [dict(r) for r in con.execute(
            "SELECT id,fecha,asunto,lugar,creado_por,creado FROM pad_acuatico_minutas ORDER BY creado DESC").fetchall()]
    return jsonify({"ok": True, "rows": rows})

@pad_acuatico_bp.route("/api/pad_acuatico/minuta", methods=["POST"])
@login_required
@modulo_required("pad_acuatico")
def pad_acuatico_minuta_create():
    data = request.json or {}
    minuta_id = str(uuid.uuid4())[:8]
    fecha       = data.get("fecha","")
    asunto      = data.get("asunto","")
    lugar       = data.get("lugar","")
    participantes = data.get("participantes",[])
    temas         = data.get("temas",[])
    conclusiones  = data.get("conclusiones",[])
    compromisos   = data.get("compromisos",[])
    proximos      = data.get("proximos",[])

    doc = generar_acta_word(
        "ACTA DE REUNIÓN — PAD ACUÁTICO / ARCA", fecha, asunto, lugar, participantes,
        secciones=[("Temas tratados", temas), ("Conclusiones", conclusiones),
                   ("Compromisos", compromisos), ("Próximos pasos", proximos)],
    )

    os.makedirs("/data/minutas_pad_acuatico", exist_ok=True)
    fname = f"Acta_PadAcuatico_{fecha.replace('/','_')}_{minuta_id}.docx"
    ruta  = os.path.join("/data/minutas_pad_acuatico", fname)
    doc.save(ruta)

    with get_db(HIST_DB) as con:
        con.execute("INSERT INTO pad_acuatico_minutas VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
            (minuta_id, fecha, asunto, lugar,
             json.dumps(participantes), json.dumps(temas), json.dumps(conclusiones),
             json.dumps(compromisos), json.dumps(proximos), ruta, session.get("username","?")))

    partic_str = ", ".join(
        p.get("nombre",p) if isinstance(p,dict) else str(p) for p in participantes)
    return jsonify({
        "ok": True, "minuta_id": minuta_id,
        "download_url": f"/api/pad_acuatico/minutas/{minuta_id}/download",
        "fname": fname,
        "cronologia_sugerida": {
            "fecha": fecha, "actividad": asunto,
            "participantes": partic_str, "estado": "Completado"
        }
    })

@pad_acuatico_bp.route("/api/pad_acuatico/minutas/<minuta_id>/download")
@login_required
@modulo_required("pad_acuatico")
def pad_acuatico_minuta_download(minuta_id):
    with get_db(HIST_DB, row_factory=True) as con:
        row = con.execute("SELECT archivo FROM pad_acuatico_minutas WHERE id=?", (minuta_id,)).fetchone()
    if not row or not os.path.exists(row["archivo"]):
        return jsonify({"ok": False, "error": "Archivo no encontrado"}), 404
    return send_file(row["archivo"], as_attachment=True,
        download_name=os.path.basename(row["archivo"]),
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

# ── IA (estructurar notas de reunión) ─────────────────────────────────────────
@pad_acuatico_bp.route("/api/pad_acuatico/minuta_ia", methods=["POST"])
@login_required
@modulo_required("pad_acuatico")
def pad_acuatico_minuta_ia():
    api_key = get_api_key()
    if not api_key: return jsonify({"ok": False, "error": "API key no configurada"})
    data = request.json or {}
    notas = data.get("notas","").strip()
    if not notas: return jsonify({"ok": False, "error": "Sin notas"})
    try:
        import anthropic, httpx
        client = anthropic.Anthropic(api_key=api_key, http_client=httpx.Client(follow_redirects=True))
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=1500,
            system="Sos asistente de DI REPA (ARCA Argentina). Estructurás minutas de reuniones del proyecto Pad Acuático. Respondés solo con JSON válido." + contexto_repositorio("pad_acuatico"),
            messages=[{"role":"user","content":(
                f"Estructurá estas notas de reunión de Pad Acuático en JSON:\n{notas}\n\n"
                'Devolvé: {"asunto":"...","temas":["..."],"conclusiones":["..."],"compromisos":["ORG — compromiso..."],"proximos":["..."]}'
            )}])
        texto = msg.content[0].text.strip().replace("```json","").replace("```","").strip()
        return jsonify({"ok": True, "resultado": json.loads(texto)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

# ── Glosario ──────────────────────────────────────────────────────────────────
@pad_acuatico_bp.route("/api/pad_acuatico/glosario", methods=["GET"])
@login_required
@modulo_required("pad_acuatico")
def pad_acuatico_glosario_list():
    with get_db(HIST_DB, row_factory=True) as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM pad_acuatico_glosario ORDER BY orden, termino").fetchall()]
    return jsonify({"ok": True, "rows": rows})

@pad_acuatico_bp.route("/api/pad_acuatico/glosario", methods=["POST"])
@login_required
@modulo_required("pad_acuatico")
def pad_acuatico_glosario_create():
    data = request.json or {}
    with get_db(HIST_DB) as con:
        con.execute("INSERT INTO pad_acuatico_glosario (termino, definicion, categoria) VALUES (?,?,?)",
            (data.get("termino",""), data.get("definicion",""), data.get("categoria","general")))
    return jsonify({"ok": True})

@pad_acuatico_bp.route("/api/pad_acuatico/glosario/<int:gid>", methods=["PUT"])
@login_required
@modulo_required("pad_acuatico")
def pad_acuatico_glosario_update(gid):
    data = request.json or {}
    with get_db(HIST_DB) as con:
        fields = []; params = []
        for f in ["termino","definicion","categoria"]:
            if f in data: fields.append(f + "=?"); params.append(data[f])
        if fields:
            params.append(gid)
            con.execute("UPDATE pad_acuatico_glosario SET " + ", ".join(fields) + " WHERE id=?", params)
    return jsonify({"ok": True})

@pad_acuatico_bp.route("/api/pad_acuatico/glosario/<int:gid>", methods=["DELETE"])
@login_required
@modulo_required("pad_acuatico")
def pad_acuatico_glosario_delete(gid):
    with get_db(HIST_DB) as con:
        con.execute("DELETE FROM pad_acuatico_glosario WHERE id=?", (gid,))
    return jsonify({"ok": True})
