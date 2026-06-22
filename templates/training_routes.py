"""
training_routes.py — Módulo Plan de Entrenamiento para CosmoTools
Gestión del plan semanal, log de sesiones y análisis Claude.
"""

import os, io, json, uuid, sqlite3, logging, urllib.request
from datetime import datetime, date, timedelta
from flask import Blueprint, render_template, request, jsonify, session

training_bp = Blueprint("training", __name__)

HIST_DB = "/data/historial.db"

# ── BD ────────────────────────────────────────────────────────────────────────
def init_training_db():
    con = sqlite3.connect(HIST_DB)
    con.execute("""CREATE TABLE IF NOT EXISTS entrenamiento_plan (
        id          TEXT PRIMARY KEY,
        semana_num  INTEGER,
        fecha_inicio TEXT,
        fase        TEXT,
        es_descarga INTEGER DEFAULT 0,
        dia_semana  TEXT,
        turno       TEXT,
        descripcion TEXT,
        notas       TEXT DEFAULT '',
        creado      TEXT DEFAULT (datetime('now')),
        modificado  TEXT DEFAULT (datetime('now'))
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS entrenamiento_semanas (
        semana_num   INTEGER PRIMARY KEY,
        fecha_inicio TEXT,
        fase         TEXT,
        es_descarga  INTEGER DEFAULT 0,
        objetivo     TEXT DEFAULT '',
        notas        TEXT DEFAULT ''
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS entrenamiento_log (
        id           TEXT PRIMARY KEY,
        fecha        TEXT,
        tipo         TEXT,
        descripcion  TEXT,
        duracion_min INTEGER,
        notas        TEXT DEFAULT '',
        completado   INTEGER DEFAULT 1,
        garmin_id    TEXT,
        plan_id      TEXT,
        creado       TEXT DEFAULT (datetime('now'))
    )""")
    con.commit(); con.close()

def _seed_plan():
    """Importa el plan Post-Hyrox 2026 desde los datos del xlsx."""
    con = sqlite3.connect(HIST_DB)
    n = con.execute("SELECT COUNT(*) FROM entrenamiento_semanas").fetchone()[0]
    if n > 0:
        con.close(); return

    FASES = {
        **{i: ("FASE 1 — Foco 21K", 0) for i in range(1, 8)},
        8:  ("FASE 2 — Transición (descarga)", 1),
        9:  ("FASE 2 — Transición", 0),
        10: ("FASE 3 — Descarga pre-21K", 0),
        **{i: ("FASE 4 — Foco Hybrid Race", 0) for i in range(11, 15)},
    }
    FECHAS = {
        1: "2026-06-14", 2: "2026-06-21", 3: "2026-06-28", 4: "2026-07-05",
        5: "2026-07-12", 6: "2026-07-19", 7: "2026-07-26", 8: "2026-08-02",
        9: "2026-08-09", 10: "2026-08-16", 11: "2026-08-23", 12: "2026-08-30",
        13: "2026-09-06", 14: "2026-09-13",
    }
    OBJETIVOS = {
        1: "Arranque del plan. Calibrar intensidad del bloque HX matutino.",
        4: "DESCARGA 65% — Recuperación activa.",
        8: "DESCARGA 65% — Inicio transición hacia fase Hybrid Race.",
        10: "Descarga total pre-21K. Domingo 23/08: 21K Buenos Aires 8hs.",
        11: "Post 21K. Evaluar recuperación antes de retomar máxima intensidad HX.",
        14: "Semana de carrera. Sábado 12/09: HYBRID RACE INDIVIDUAL.",
    }

    # Plantilla base semanal (turno, dia, descripción)
    PLANTILLA_NORMAL = [
        ("mañana",    "lunes",     "Fuerza A + 4 rounds HX: SkiErg 500m+200m run / Remo 500m+200m run / 20 estocadas+200m run"),
        ("mediodía",  "lunes",     "Bici 45' Z2"),
        ("mañana",    "miércoles", "Movilidad 25' hombros / caderas / cadena posterior"),
        ("mediodía",  "miércoles", "Bici 45' Z2"),
        ("noche",     "miércoles", "Run Z2 30' recuperación activa"),
        ("mañana",    "jueves",    "Fuerza B + Peso muerto 3x5 + 4 rounds HX: Farmer 50m+200m run / Remo 500m+200m run / WallBall 20+200m run"),
        ("mediodía",  "jueves",    "Bici 45' Z2"),
        ("noche",     "martes",    "Run suave 30' → HX grupal"),
        ("noche",     "jueves",    "HX grupal"),
        ("mediodía",  "viernes",   "Run estructurado 65': 15' Z2 + 20' @ 5:40/km + 15' Z2"),
        ("mañana",    "sábado",    "Run grupal"),
        ("mediodía",  "sábado",    "HX grupal"),
        ("mañana",    "domingo",   "Run 90' Z2 progresivo + 2-3 estaciones HX"),
        ("mediodía",  "domingo",   "Run Z2 extra 20-30'"),
    ]
    PLANTILLA_DESCARGA = [
        ("mañana",    "lunes",     "Fuerza A + 2 rounds HX: SkiErg 500m+200m run / Remo 500m+200m run / 10 estocadas+200m run"),
        ("mediodía",  "lunes",     "Bici 45' Z2"),
        ("mañana",    "miércoles", "Movilidad 25' hombros / caderas / cadena posterior"),
        ("mediodía",  "miércoles", "Bici 45' Z2"),
        ("noche",     "miércoles", "Run Z2 30' recuperación activa"),
        ("mañana",    "jueves",    "Fuerza B + Peso muerto 3x3 + 2 rounds HX: Farmer 50m+200m run / Remo 500m+200m run / WallBall 20+200m run"),
        ("mediodía",  "jueves",    "Bici 45' Z2"),
        ("noche",     "martes",    "Run suave 20' → HX grupal"),
        ("noche",     "jueves",    "HX grupal"),
        ("mediodía",  "viernes",   "Run Z2 40' sin ritmo"),
        ("mañana",    "sábado",    "Run grupal"),
        ("mediodía",  "sábado",    "HX grupal"),
        ("mañana",    "domingo",   "Run 50' Z2 (descarga)"),
    ]
    PLANTILLA_FASE4_DOM = [
        ("mañana",    "domingo",   "Simulación Hyrox completa: 8x(1km run + estación) + 5km run final Z2"),
    ]
    PLANTILLA_FASE4_VIE = [
        ("mediodía",  "viernes",   "Run estructurado 65': 15' Z2 + 35' @ 5:20/km + 15' Z2"),
    ]
    ESPECIALES = {
        10: [("mañana", "domingo", "21K BUENOS AIRES 23/08 — 8hs Av. Figueroa Alcorta")],
        14: [("mañana", "sábado",  "HYBRID RACE INDIVIDUAL 12/09 — 8 estaciones completas")],
    }

    semanas_data = []
    sesiones_data = []

    for sem, fecha in FECHAS.items():
        fase, es_desc = FASES[sem]
        obj = OBJETIVOS.get(sem, f"Fase {sem}/14.")
        semanas_data.append((sem, fecha, fase, es_desc, obj, ""))

        plantilla = PLANTILLA_DESCARGA if es_desc else PLANTILLA_NORMAL

        # Ajustar domingo en Fase 4
        if sem >= 11 and not es_desc:
            plantilla = [s for s in plantilla if not (s[0] == "mañana" and s[1] == "domingo")]
            plantilla += PLANTILLA_FASE4_DOM
            plantilla = [s for s in plantilla if not (s[0] == "mediodía" and s[1] == "viernes")]
            plantilla += PLANTILLA_FASE4_VIE

        for turno, dia, desc in plantilla:
            sesiones_data.append((
                str(uuid.uuid4())[:12], sem, fecha, fase, es_desc, dia, turno, desc, obj
            ))

        # Sesiones especiales (race day)
        for turno, dia, desc in ESPECIALES.get(sem, []):
            sesiones_data.append((
                str(uuid.uuid4())[:12], sem, fecha, fase, es_desc, dia, turno, desc, ""
            ))

    con.executemany(
        "INSERT OR IGNORE INTO entrenamiento_semanas VALUES (?,?,?,?,?,?)", semanas_data)
    con.executemany(
        "INSERT OR IGNORE INTO entrenamiento_plan (id,semana_num,fecha_inicio,fase,es_descarga,dia_semana,turno,descripcion,notas) VALUES (?,?,?,?,?,?,?,?,?)",
        sesiones_data)
    con.commit(); con.close()

init_training_db()
_seed_plan()

# ── Helpers ────────────────────────────────────────────────────────────────────
DIAS_ORDER = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]
TURNOS_ORDER = ["mañana","mediodía","noche"]

def _semana_actual():
    hoy = date.today()
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM entrenamiento_semanas ORDER BY semana_num").fetchall()
    con.close()
    sem_actual = 1
    for r in rows:
        fi = datetime.strptime(r["fecha_inicio"], "%Y-%m-%d").date()
        if hoy >= fi:
            sem_actual = r["semana_num"]
    return sem_actual

def _get_semana(num):
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    sem = con.execute("SELECT * FROM entrenamiento_semanas WHERE semana_num=?", (num,)).fetchone()
    sesiones = con.execute(
        "SELECT * FROM entrenamiento_plan WHERE semana_num=? ORDER BY dia_semana, turno",
        (num,)).fetchall()
    con.close()
    if not sem: return None
    s = dict(sem)
    s["sesiones"] = [dict(r) for r in sesiones]
    return s

def _get_log(fecha_desde=None, fecha_hasta=None):
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    q = "SELECT l.*, g.tipo as garmin_tipo, g.fc_media, g.distancia_m, g.duracion_seg FROM entrenamiento_log l LEFT JOIN garmin_actividades g ON l.garmin_id = g.id"
    params = []
    if fecha_desde and fecha_hasta:
        q += " WHERE l.fecha BETWEEN ? AND ?"; params = [fecha_desde, fecha_hasta]
    q += " ORDER BY l.fecha DESC LIMIT 100"
    rows = [dict(r) for r in con.execute(q, params).fetchall()]
    con.close()
    return rows

def _api_key():
    return os.environ.get("ANTHROPIC_API_KEY", "")

# ── Rutas ─────────────────────────────────────────────────────────────────────
@training_bp.route("/training")
def training_index():
    return render_template("training.html", username=session.get("username", ""))

@training_bp.route("/api/training/semana/actual")
def api_semana_actual():
    num = _semana_actual()
    sem = _get_semana(num)
    return jsonify({"ok": True, "semana_num": num, "semana": sem})

@training_bp.route("/api/training/semana/<int:num>")
def api_semana(num):
    sem = _get_semana(num)
    if not sem: return jsonify({"ok": False, "error": "Semana no encontrada"})
    return jsonify({"ok": True, "semana": sem})

@training_bp.route("/api/training/semanas")
def api_semanas_lista():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT semana_num, fecha_inicio, fase, es_descarga, objetivo FROM entrenamiento_semanas ORDER BY semana_num"
    ).fetchall()]
    con.close()
    return jsonify({"ok": True, "rows": rows, "actual": _semana_actual()})

@training_bp.route("/api/training/sesion/<sid>", methods=["PUT"])
def api_sesion_update(sid):
    data = request.json or {}
    con = sqlite3.connect(HIST_DB)
    con.execute(
        "UPDATE entrenamiento_plan SET descripcion=?, notas=?, modificado=datetime('now') WHERE id=?",
        (data.get("descripcion",""), data.get("notas",""), sid))
    con.commit(); con.close()
    return jsonify({"ok": True})

@training_bp.route("/api/training/sesion", methods=["POST"])
def api_sesion_nueva():
    data = request.json or {}
    sid = str(uuid.uuid4())[:12]
    con = sqlite3.connect(HIST_DB)
    sem = con.execute("SELECT * FROM entrenamiento_semanas WHERE semana_num=?",
                      (data.get("semana_num"),)).fetchone()
    if not sem:
        con.close(); return jsonify({"ok": False, "error": "Semana inválida"})
    con.execute(
        "INSERT INTO entrenamiento_plan (id,semana_num,fecha_inicio,fase,es_descarga,dia_semana,turno,descripcion,notas) VALUES (?,?,?,?,?,?,?,?,?)",
        (sid, data["semana_num"], sem["fecha_inicio"], sem["fase"], sem["es_descarga"],
         data.get("dia_semana",""), data.get("turno","mañana"), data.get("descripcion",""), data.get("notas","")))
    con.commit(); con.close()
    return jsonify({"ok": True, "id": sid})

@training_bp.route("/api/training/sesion/<sid>", methods=["DELETE"])
def api_sesion_delete(sid):
    con = sqlite3.connect(HIST_DB)
    con.execute("DELETE FROM entrenamiento_plan WHERE id=?", (sid,))
    con.commit(); con.close()
    return jsonify({"ok": True})

@training_bp.route("/api/training/log", methods=["GET"])
def api_log_list():
    fd = request.args.get("desde")
    fh = request.args.get("hasta")
    rows = _get_log(fd, fh)
    return jsonify({"ok": True, "rows": rows})

@training_bp.route("/api/training/log", methods=["POST"])
def api_log_nuevo():
    data = request.json or {}
    lid = str(uuid.uuid4())[:12]
    con = sqlite3.connect(HIST_DB)
    con.execute(
        "INSERT INTO entrenamiento_log (id,fecha,tipo,descripcion,duracion_min,notas,completado,garmin_id,plan_id) VALUES (?,?,?,?,?,?,?,?,?)",
        (lid, data.get("fecha"), data.get("tipo"), data.get("descripcion"),
         data.get("duracion_min"), data.get("notas",""), data.get("completado",1),
         data.get("garmin_id"), data.get("plan_id")))
    con.commit(); con.close()
    return jsonify({"ok": True, "id": lid})

@training_bp.route("/api/training/log/<lid>", methods=["DELETE"])
def api_log_delete(lid):
    con = sqlite3.connect(HIST_DB)
    con.execute("DELETE FROM entrenamiento_log WHERE id=?", (lid,))
    con.commit(); con.close()
    return jsonify({"ok": True})

@training_bp.route("/api/training/claude", methods=["POST"])
def api_claude():
    data = request.json or {}
    pregunta = data.get("pregunta","").strip()
    semana_num = data.get("semana_num")
    if not pregunta:
        return jsonify({"ok": False, "error": "Pregunta vacía"})

    key = _api_key()
    if not key:
        return jsonify({"ok": False, "error": "API key no configurada"})

    # Contexto del plan
    contexto = ""
    if semana_num:
        sem = _get_semana(semana_num)
        if sem:
            sesiones_txt = "\n".join([
                f"  {s['dia_semana']} {s['turno']}: {s['descripcion']}"
                for s in sem.get("sesiones", [])
            ])
            contexto = f"""
PLAN ACTUAL — Semana {semana_num}: {sem['fase']}
Fecha inicio: {sem['fecha_inicio']}
{'⚠ SEMANA DE DESCARGA (65% volumen)' if sem['es_descarga'] else ''}
{sem.get('objetivo','')}

Sesiones planificadas:
{sesiones_txt}
"""

    prompt = f"""Sos un asistente de entrenamiento deportivo para un triatleta/Hyrox competidor.

CONTEXTO DEL PLAN (Post-Hyrox 2026, 14 semanas):
- Objetivo 1: 21K Buenos Aires 23/08/2026
- Objetivo 2: Hybrid Race Individual 12/09/2026
- Entrena triatlón, running, ciclismo, fuerza y Hyrox
{contexto}

Pregunta: {pregunta}

Respondé en español de forma concisa y práctica."""

    try:
        payload = json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}]
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
            }
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
        return jsonify({"ok": True, "respuesta": result["content"][0]["text"]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@training_bp.route("/api/training/stats")
def api_stats():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    total_plan = con.execute("SELECT COUNT(*) FROM entrenamiento_plan").fetchone()[0]
    total_log  = con.execute("SELECT COUNT(*) FROM entrenamiento_log").fetchone()[0]
    completadas = con.execute("SELECT COUNT(*) FROM entrenamiento_log WHERE completado=1").fetchone()[0]
    con.close()
    return jsonify({
        "ok": True,
        "total_plan": total_plan,
        "total_log": total_log,
        "completadas": completadas,
        "semana_actual": _semana_actual(),
    })

@training_bp.route("/api/training/garmin_dia")
def api_garmin_dia():
    """Actividades Garmin del día indicado — para vincular con el plan."""
    fecha = request.args.get("fecha")  # YYYY-MM-DD
    if not fecha:
        return jsonify({"ok": False, "error": "Fecha requerida"})
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id, nombre, tipo, duracion_seg, distancia_m, fc_media, calorias "
        "FROM garmin_actividades WHERE DATE(fecha) = ? ORDER BY fecha",
        (fecha,)
    ).fetchall()
    con.close()
    return jsonify({"ok": True, "rows": [dict(r) for r in rows]})
