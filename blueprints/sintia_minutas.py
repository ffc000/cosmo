"""
blueprints/sintia_minutas.py — Panel de Minutas para SINTIA.

Parte de la unificación del panel de Minutas en los 4 módulos que lo
tienen (VUA, SENASA, Pad Acuático, SINTIA), 23/07/2026. Mismo esquema y
mismos endpoints que blueprints/pad_acuatico.py (que a su vez sigue el
patrón original de blueprints/senasa.py), salvo por una diferencia real:
SINTIA no tiene el concepto de "Cronología" (es un módulo de consulta de
datos, no de seguimiento de proyecto), así que acá no existe la
sugerencia de "agregar a cronología" que sí tienen los otros tres.

Se separa en su propio archivo (en vez de meterlo en app.py, donde viven
el resto de las rutas de SINTIA) siguiendo el mismo criterio que
training_plan.py/training_antro.py: una sub-funcionalidad autocontenida
no tiene por qué vivir en el archivo gigante.
"""
import os
import json
import uuid

from actas import generar_acta_word, importar_minuta_desde_docx
from datetime import datetime

from flask import Blueprint, request, jsonify, session, send_file

from core import HIST_DB, login_required, modulo_required, get_api_key, contexto_repositorio, get_db

sintia_minutas_bp = Blueprint("sintia_minutas", __name__)


@sintia_minutas_bp.route("/api/sintia/minutas", methods=["GET"])
@login_required
@modulo_required("sintia")
def sintia_minutas_list():
    with get_db(HIST_DB, row_factory=True) as con:
        rows = [dict(r) for r in con.execute(
            "SELECT id,fecha,asunto,lugar,creado_por,creado FROM sintia_minutas "
            "ORDER BY creado DESC LIMIT 50").fetchall()]
    return jsonify({"ok": True, "rows": rows})


@sintia_minutas_bp.route("/api/sintia/minuta", methods=["POST"])
@login_required
@modulo_required("sintia")
def sintia_minuta_create():
    data = request.json or {}
    asunto = (data.get("asunto") or "").strip()
    if not asunto:
        return jsonify({"ok": False, "error": "El asunto es obligatorio."}), 400
    minuta_id = str(uuid.uuid4())[:8]
    fecha         = (data.get("fecha") or "").strip() or datetime.today().strftime("%d/%m/%Y")
    lugar         = data.get("lugar", "")
    participantes = data.get("participantes", [])
    temas         = data.get("temas", [])
    conclusiones  = data.get("conclusiones", [])
    compromisos   = data.get("compromisos", [])
    proximos      = data.get("proximos", [])
    notas_completas = data.get("notas_completas", "")

    doc = generar_acta_word(
        "ACTA DE REUNIÓN — SINTIA / ARCA", fecha, asunto, lugar, participantes,
        secciones=[("Temas tratados", temas), ("Conclusiones", conclusiones),
                   ("Compromisos", compromisos), ("Próximos pasos", proximos)],
        notas_completas=notas_completas,
    )

    os.makedirs("/data/minutas_sintia", exist_ok=True)
    fname = f"Acta_SINTIA_{fecha.replace('/','_')}_{minuta_id}.docx"
    ruta = os.path.join("/data/minutas_sintia", fname)
    doc.save(ruta)

    with get_db(HIST_DB) as con:
        con.execute("INSERT INTO sintia_minutas VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
            (minuta_id, fecha, asunto, lugar,
             json.dumps(participantes), json.dumps(temas), json.dumps(conclusiones),
             json.dumps(compromisos), json.dumps(proximos), ruta, session.get("username", "?")))

    return jsonify({
        "ok": True, "minuta_id": minuta_id,
        "download_url": f"/api/sintia/minutas/{minuta_id}/download",
        "fname": fname,
    })


@sintia_minutas_bp.route("/api/sintia/minutas/<minuta_id>/download")
@login_required
@modulo_required("sintia")
def sintia_minuta_download(minuta_id):
    with get_db(HIST_DB, row_factory=True) as con:
        row = con.execute("SELECT archivo FROM sintia_minutas WHERE id=?", (minuta_id,)).fetchone()
    if not row or not os.path.exists(row["archivo"]):
        return jsonify({"ok": False, "error": "Archivo no encontrado"}), 404
    return send_file(row["archivo"], as_attachment=True,
        download_name=os.path.basename(row["archivo"]),
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@sintia_minutas_bp.route("/api/sintia/minutas/<minuta_id>", methods=["DELETE"])
@login_required
@modulo_required("sintia")
def sintia_minuta_delete(minuta_id):
    with get_db(HIST_DB) as con:
        row = con.execute("SELECT archivo FROM sintia_minutas WHERE id=?", (minuta_id,)).fetchone()
        if row and row[0] and os.path.exists(row[0]):
            try: os.remove(row[0])
            except Exception: pass
        con.execute("DELETE FROM sintia_minutas WHERE id=?", (minuta_id,))
    return jsonify({"ok": True})


@sintia_minutas_bp.route("/api/sintia/minuta_ia", methods=["POST"])
@login_required
@modulo_required("sintia")
def sintia_minuta_ia():
    api_key = get_api_key()
    if not api_key: return jsonify({"ok": False, "error": "API key no configurada"})
    data = request.json or {}
    notas = (data.get("notas") or "").strip()
    if not notas: return jsonify({"ok": False, "error": "Sin notas"})
    try:
        import anthropic, httpx
        client = anthropic.Anthropic(api_key=api_key, http_client=httpx.Client(follow_redirects=True))
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=3000,
            system=(
                "Sos asistente de DI REPA (ARCA Argentina). Trabajás con notas de reunión del "
                "circuito SINTIA tomadas al vuelo durante la reunión -- suelen tener errores "
                "de tipeo, abreviaturas, y frases cortadas o telegráficas. Tu trabajo es CORREGIR "
                "y COMPLETAR esas notas para armar una minuta prolija, no solo repartirlas en "
                "campos tal cual están escritas. Redactás en español rioplatense institucional, "
                "en oraciones completas y claras. No inventás información que no esté presente o "
                "claramente implícita en las notas -- si algo quedó ambiguo o incompleto, "
                "redactalo de la forma más razonable sin agregar hechos, nombres, fechas o "
                "compromisos que no estén en el original. Respondés solo con JSON válido."
                + contexto_repositorio("sintia")
            ),
            messages=[{"role":"user","content":(
                f"Notas de la reunión, tal como se tomaron (pueden tener errores de tipeo, "
                f"abreviaturas o frases cortadas):\n\n{notas}\n\n"
                "A partir de estas notas: corregí ortografía y gramática, completá las frases "
                "truncadas o abreviadas en oraciones claras y prolijas, y organizá todo en la "
                "estructura pedida abajo. Cada tema/conclusión/compromiso/paso debe quedar "
                "redactado como una oración completa y entendible por alguien que no estuvo en "
                "la reunión, no como una nota telegráfica.\n\n"
                'Devolvé: {"notas_corregidas":"las notas originales reescritas como texto '
                'corrido, en párrafos, ya corregidas y completadas -- esto es lo que el usuario '
                'va a leer para VER qué le corregiste, así que tiene que reflejar el contenido '
                'completo de las notas, no un resumen",'
                '"asunto":"...","temas":["..."],"conclusiones":["..."],'
                '"compromisos":["ORG — compromiso..."],"proximos":["..."]}'
            )}])
        texto = msg.content[0].text.strip().replace("```json","").replace("```","").strip()
        return jsonify({"ok": True, "resultado": json.loads(texto)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@sintia_minutas_bp.route("/api/sintia/minuta/importar", methods=["POST"])
@login_required
@modulo_required("sintia")
def sintia_minuta_importar():
    """Recibe un .docx, extrae el texto y usa la IA para estructurarlo en
    campos de minuta -- lógica compartida con los otros 3 módulos, ver
    actas.importar_minuta_desde_docx."""
    api_key = get_api_key()
    resultado = importar_minuta_desde_docx(
        request.files.get("archivo"), api_key, "SINTIA", contexto_repositorio("sintia"))
    return jsonify(resultado)
