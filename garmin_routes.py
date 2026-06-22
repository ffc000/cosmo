"""
garmin_routes.py — Módulo Garmin para CosmoTools
Sincronización de actividades, almacenamiento local y análisis Claude.
"""

import os, io, json, uuid, sqlite3, logging, threading, time, struct
from datetime import datetime, timezone
from flask import Blueprint, render_template, request, jsonify, session, send_file

garmin_bp = Blueprint("garmin", __name__)

HIST_DB   = "/data/historial.db"
GARMIN_DIR = "/data/garmin"
os.makedirs(GARMIN_DIR, exist_ok=True)

# ── Tipos de actividad normalizados ───────────────────────────────────────────
TIPO_MAP = {
    "running": "running", "trail_running": "running",
    "cycling": "cycling", "indoor_cycling": "cycling", "road_biking": "cycling",
    "swimming": "swimming", "open_water_swimming": "swimming",
    "triathlon": "triathlon",
    "strength_training": "strength", "hiit": "strength",
    "fitness_equipment": "strength",
}

def normalizar_tipo(tipo_garmin):
    if not tipo_garmin:
        return "other"
    t = tipo_garmin.lower().replace(" ", "_")
    return TIPO_MAP.get(t, "other")

# ── BD ────────────────────────────────────────────────────────────────────────
def init_garmin_db():
    con = sqlite3.connect(HIST_DB)
    con.execute("""CREATE TABLE IF NOT EXISTS garmin_config (
        clave TEXT PRIMARY KEY,
        valor TEXT DEFAULT '',
        modificado TEXT DEFAULT (datetime('now'))
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS garmin_actividades (
        id            TEXT PRIMARY KEY,
        tipo          TEXT,
        fecha         TEXT,
        nombre        TEXT,
        duracion_seg  INTEGER,
        distancia_m   REAL,
        fc_media      INTEGER,
        fc_max        INTEGER,
        calorias      INTEGER,
        tss           REAL,
        cadencia_media INTEGER,
        velocidad_media REAL,
        desnivel_pos  REAL,
        potencia_media INTEGER,
        normalizada_w INTEGER,
        zonas_fc      TEXT,
        laps          TEXT,
        metadata      TEXT,
        archivo_path  TEXT,
        sincronizado  TEXT
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS garmin_analisis (
        id            TEXT PRIMARY KEY,
        tipo          TEXT,
        fecha_desde   TEXT,
        fecha_hasta   TEXT,
        actividad_id  TEXT,
        prompt_usado  TEXT,
        respuesta     TEXT,
        creado        TEXT
    )""")
    con.commit()
    con.close()

init_garmin_db()

# ── Helpers BD ────────────────────────────────────────────────────────────────
def get_actividades(limit=50, tipo=None):
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    q = "SELECT * FROM garmin_actividades"
    params = []
    if tipo and tipo != "todas":
        q += " WHERE tipo=?"; params.append(tipo)
    q += " ORDER BY fecha DESC LIMIT ?"
    params.append(limit)
    rows = [dict(r) for r in con.execute(q, params).fetchall()]
    con.close()
    return rows

def get_actividad(act_id):
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM garmin_actividades WHERE id=?", (act_id,)).fetchone()
    con.close()
    return dict(row) if row else None

def guardar_actividad(data: dict):
    con = sqlite3.connect(HIST_DB)
    con.execute("""INSERT OR REPLACE INTO garmin_actividades
        (id,tipo,fecha,nombre,duracion_seg,distancia_m,fc_media,fc_max,
         calorias,tss,cadencia_media,velocidad_media,desnivel_pos,
         potencia_media,normalizada_w,zonas_fc,laps,metadata,archivo_path,sincronizado)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (data.get("id"), data.get("tipo"), data.get("fecha"), data.get("nombre"),
         data.get("duracion_seg"), data.get("distancia_m"), data.get("fc_media"),
         data.get("fc_max"), data.get("calorias"), data.get("tss"),
         data.get("cadencia_media"), data.get("velocidad_media"), data.get("desnivel_pos"),
         data.get("potencia_media"), data.get("normalizada_w"),
         json.dumps(data.get("zonas_fc") or {}),
         json.dumps(data.get("laps") or []),
         json.dumps(data.get("metadata") or {}),
         data.get("archivo_path"), datetime.now().isoformat()))
    con.commit(); con.close()

def get_analisis(actividad_id=None, tipo=None):
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    if actividad_id:
        rows = con.execute(
            "SELECT * FROM garmin_analisis WHERE actividad_id=? ORDER BY creado DESC",
            (actividad_id,)).fetchall()
    else:
        q = "SELECT * FROM garmin_analisis WHERE tipo=? ORDER BY creado DESC LIMIT 20"
        rows = con.execute(q, (tipo or "sesion",)).fetchall()
    con.close()
    return [dict(r) for r in rows]

def guardar_analisis(data: dict):
    aid = str(uuid.uuid4())[:12]
    con = sqlite3.connect(HIST_DB)
    con.execute("""INSERT INTO garmin_analisis
        (id,tipo,fecha_desde,fecha_hasta,actividad_id,prompt_usado,respuesta,creado)
        VALUES (?,?,?,?,?,?,?,?)""",
        (aid, data.get("tipo","sesion"), data.get("fecha_desde"), data.get("fecha_hasta"),
         data.get("actividad_id"), data.get("prompt_usado",""), data.get("respuesta",""),
         datetime.now().isoformat()))
    con.commit(); con.close()
    return aid

# ── Credenciales Garmin (guardadas en BD) ─────────────────────────────────────
def _xor(text: str, key: str = "cosmotools2026") -> str:
    """Ofuscación simple XOR — no es cifrado fuerte, pero evita texto plano en BD."""
    key_bytes = (key * (len(text) // len(key) + 1)).encode()
    return ''.join(chr(ord(c) ^ key_bytes[i]) for i, c in enumerate(text))

def get_credenciales_garmin():
    try:
        con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
        u = con.execute("SELECT valor FROM garmin_config WHERE clave='garmin_user'").fetchone()
        p = con.execute("SELECT valor FROM garmin_config WHERE clave='garmin_pass'").fetchone()
        con.close()
        usuario = _xor(u["valor"]) if u and u["valor"] else ""
        passwd  = _xor(p["valor"]) if p and p["valor"] else ""
        return usuario, passwd
    except:
        return "", ""

def set_credenciales_garmin(usuario: str, passwd: str):
    con = sqlite3.connect(HIST_DB)
    con.execute("INSERT OR REPLACE INTO garmin_config (clave,valor,modificado) VALUES (?,?,datetime('now'))",
                ("garmin_user", _xor(usuario)))
    con.execute("INSERT OR REPLACE INTO garmin_config (clave,valor,modificado) VALUES (?,?,datetime('now'))",
                ("garmin_pass", _xor(passwd)))
    con.commit(); con.close()

def credenciales_configuradas():
    u, p = get_credenciales_garmin()
    return bool(u and p)

# ── Sincronización Garmin ─────────────────────────────────────────────────────
_sync_status = {}

def _r(val, decimals=1):
    """Redondea un valor numérico, devuelve None si no es número."""
    try:
        return round(float(val), decimals) if val is not None else None
    except (TypeError, ValueError):
        return None

def _parsear_actividad(act: dict) -> dict:
    """Extrae TODOS los campos disponibles del JSON de garminconnect."""
    act_id   = str(act.get("activityId", ""))
    tipo_raw = (act.get("activityType", {}) or {}).get("typeKey", "")
    subtipo  = (act.get("activityType", {}) or {}).get("parentTypeId", "")
    fecha    = act.get("startTimeGMT") or act.get("startTimeLocal", "")

    duracion = act.get("duration", 0) or act.get("movingDuration", 0) or 0
    if duracion > 100000:
        duracion = duracion / 1000

    distancia = act.get("distance", 0) or 0

    # Cadencia: running usa spm (pasos/min), cycling usa rpm
    cad_run  = act.get("averageRunningCadenceInStepsPerMinute")
    cad_bike = act.get("avgBikeCadence")
    cad_swim = act.get("averageSwimmingCadenceInStrokesPerMinute")
    cadencia = cad_run or cad_bike or cad_swim

    metadata = {
        # Clasificación
        "tipo_original":       tipo_raw,
        "subtipo":             subtipo,
        "deporte":             act.get("activityType", {}).get("typeKey", ""),
        # Fisiológico
        "vo2max":              _r(act.get("vO2MaxValue"), 1),
        "aerobic_te":          _r(act.get("aerobicTrainingEffect"), 1),
        "anaerobic_te":        _r(act.get("anaerobicTrainingEffect"), 1),
        "aerobic_te_label":    act.get("aerobicTrainingEffectLabel"),
        "anaerobic_te_label":  act.get("anaerobicTrainingEffectLabel"),
        "hrv_weekly_avg":      _r(act.get("hrvWeeklyAverage"), 1),
        "hrv_status":          act.get("hrvStatus"),
        "body_battery_drained":act.get("bodyBatteryDrained"),
        "stress_durante":      act.get("avgStress"),
        # Running específico
        "cadencia_tipo":       "spm" if cad_run else ("rpm" if cad_bike else "s/min"),
        "velocidad_max":       _r(act.get("maxSpeed"), 3),
        "paso_medio":          _r(act.get("averagePace"), 2),     # min/km en decimal
        "paso_mejor":          _r(act.get("bestPace"), 2),
        "ground_contact":      _r(act.get("groundContactTime"), 1),
        "vertical_osc":        _r(act.get("verticalOscillation"), 2),
        "vertical_ratio":      _r(act.get("verticalRatio"), 2),
        "stride_length":       _r(act.get("strideLength"), 2),
        "running_power":       _r(act.get("avgRunningPowerInWatts"), 1),
        # Ciclismo específico
        "potencia_max":        _r(act.get("maxPower"), 1),
        "ftp":                 _r(act.get("functionalThresholdPower"), 1),
        "if_factor":           _r(act.get("intensityFactor"), 2),
        # Natación
        "swolf":               _r(act.get("avgSwolf"), 1),
        "brazadas_largo":      _r(act.get("avgStrokeDistance"), 2),
        "estilo_nado":         act.get("strokes"),
        # Altimetría
        "altitud_min":         _r(act.get("minElevation"), 1),
        "altitud_max":         _r(act.get("maxElevation"), 1),
        "desnivel_neg":        _r(act.get("elevationLoss"), 1),
        # GPS / ruta
        "tiene_gps":           act.get("hasPolyline", False),
        "start_lat":           _r(act.get("startLatitude"), 5),
        "start_lon":           _r(act.get("startLongitude"), 5),
        # Clima
        "temp_inicio":         _r(act.get("startTemperature"), 1),
        "temp_media":          _r(act.get("avgTemperature"), 1),
        # Carga y recuperación
        "load_primario":       _r(act.get("activityTrainingLoad"), 1),
        "aerobic_load":        _r(act.get("aerobicEffect"), 1),
        "tiempo_recuperacion": act.get("recoveryTime"),          # horas
        "performance_cond":    _r(act.get("avgRunningPerformanceCondition"), 1),
        # Dispositivo
        "dispositivo":         act.get("deviceId"),
        # Zonas FC raw (si vienen en el resumen)
        "zonas_fc_raw":        act.get("heartRateZones"),
    }

    # Zonas FC normalizadas
    zonas_fc = {}
    hrz = act.get("heartRateZones") or []
    for z in hrz:
        nombre = z.get("zoneName") or z.get("name") or f"Z{z.get('number','')}"
        pct    = _r(z.get("percentOfMax") or z.get("percent"), 1)
        seg    = z.get("secsInZone") or z.get("seconds")
        zonas_fc[nombre] = {"pct": pct, "seg": seg}

    return {
        "id":              act_id,
        "tipo":            normalizar_tipo(tipo_raw),
        "fecha":           fecha,
        "nombre":          act.get("activityName", ""),
        "duracion_seg":    int(duracion),
        "distancia_m":     _r(distancia, 2),
        "fc_media":        _r(act.get("averageHR") or act.get("avgHr"), 0),
        "fc_max":          _r(act.get("maxHR") or act.get("maxHr"), 0),
        "calorias":        _r(act.get("calories"), 0),
        "tss":             _r(act.get("trainingStressScore"), 1),
        "cadencia_media":  _r(cadencia, 0),
        "velocidad_media": _r(act.get("averageSpeed"), 4),
        "desnivel_pos":    _r(act.get("elevationGain"), 1),
        "potencia_media":  _r(act.get("avgPower"), 1),
        "normalizada_w":   _r(act.get("normPower"), 1),
        "zonas_fc":        zonas_fc,
        "laps":            [],   # se pueden bajar por separado si se necesitan
        "metadata":        metadata,
        "archivo_path":    None,
    }

def _sync_worker(job_id: str, user: str, modo: str, semana_offset: int = 0):
    """
    modo: 'todo' — baja todo el histórico (paginado de a 100)
          'semana' — baja actividades de la semana N (0=actual, 1=anterior, etc.)
    """
    _sync_status[job_id] = {"estado": "iniciando", "progreso": 0, "total": 0, "nuevas": 0, "errores": []}
    try:
        from garminconnect import Garmin
        from datetime import date, timedelta

        g_user, g_pass = get_credenciales_garmin()
        if not g_user:
            g_user = os.environ.get("GARMIN_USER", "")
        if not g_pass:
            g_pass = os.environ.get("GARMIN_PASS", "")
        if not g_user or not g_pass:
            _sync_status[job_id] = {"estado": "error", "error": "Credenciales Garmin no configuradas. Ingresalas en Configuración."}
            return

        _sync_status[job_id]["estado"] = "conectando"
        client = Garmin(g_user, g_pass)
        client.login()
        _sync_status[job_id]["estado"] = "descargando"

        if modo == "semana":
            hoy = date.today()
            lunes = hoy - timedelta(days=hoy.weekday()) - timedelta(weeks=semana_offset)
            domingo = lunes + timedelta(days=6)
            actividades = client.get_activities_by_date(
                lunes.isoformat(), domingo.isoformat()
            )
        else:
            # Histórico completo — paginado de a 100
            actividades = []
            start = 0
            batch = 100
            while True:
                lote = client.get_activities(start, batch)
                if not lote:
                    break
                actividades.extend(lote)
                _sync_status[job_id]["total"] = len(actividades)
                _sync_status[job_id]["progreso"] = len(actividades)
                if len(lote) < batch:
                    break
                start += batch

        total = len(actividades)
        _sync_status[job_id]["total"] = total
        nuevas = 0

        for i, act in enumerate(actividades):
            act_id = str(act.get("activityId", ""))
            _sync_status[job_id]["progreso"] = i + 1

            con = sqlite3.connect(HIST_DB)
            existe = con.execute("SELECT 1 FROM garmin_actividades WHERE id=?", (act_id,)).fetchone()
            con.close()
            if existe:
                continue

            datos = _parsear_actividad(act)

            try:
                mes_dir = os.path.join(GARMIN_DIR, datos["fecha"][:7] if datos["fecha"] else "unknown")
                os.makedirs(mes_dir, exist_ok=True)
                fit_path = os.path.join(mes_dir, f"{act_id}.fit")
                fit_data = client.download_activity(int(act_id), dl_fmt=client.ActivityDownloadFormat.ORIGINAL)
                with open(fit_path, "wb") as f:
                    f.write(fit_data)
                datos["archivo_path"] = fit_path
            except Exception as e:
                _sync_status[job_id]["errores"].append(f"FIT {act_id}: {e}")

            guardar_actividad(datos)
            nuevas += 1
            _sync_status[job_id]["nuevas"] = nuevas

        _sync_status[job_id] = {
            "estado": "ok",
            "total": total,
            "nuevas": nuevas,
            "progreso": total,
            "errores": _sync_status[job_id]["errores"],
        }
        logging.info(f"GARMIN SYNC | user={user} | modo={modo} | total={total} | nuevas={nuevas}")

    except Exception as e:
        _sync_status[job_id] = {"estado": "error", "error": str(e)}
        logging.error(f"GARMIN SYNC ERROR | {e}")

# ── Análisis Claude ───────────────────────────────────────────────────────────
def _fmt_duracion(seg):
    if not seg: return "—"
    h, m = divmod(int(seg) // 60, 60)
    s = int(seg) % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

def _fmt_ritmo(vel_ms):
    if not vel_ms or vel_ms == 0: return "—"
    ritmo_seg = 1000 / float(vel_ms)
    m, s = divmod(int(ritmo_seg), 60)
    return f"{m}:{s:02d} /km"

def _build_prompt_sesion(act: dict) -> str:
    meta = json.loads(act.get("metadata") or "{}")
    laps = json.loads(act.get("laps") or "[]")
    zonas = json.loads(act.get("zonas_fc") or "{}")
    dist_km = (act.get("distancia_m") or 0) / 1000
    return f"""Analizá esta sesión de entrenamiento y dá feedback técnico conciso.

ACTIVIDAD: {act.get('nombre','')} ({act.get('tipo','')})
Fecha: {act.get('fecha','')}
Duración: {_fmt_duracion(act.get('duracion_seg'))}
Distancia: {dist_km:.2f} km
FC media: {act.get('fc_media') or '—'} bpm | FC máx: {act.get('fc_max') or '—'} bpm
Calorías: {act.get('calorias') or '—'}
Ritmo medio: {_fmt_ritmo(act.get('velocidad_media'))}
Cadencia media: {act.get('cadencia_media') or '—'} rpm/spm
Desnivel +: {act.get('desnivel_pos') or '—'} m
Potencia media: {act.get('potencia_media') or '—'} W
TSS: {act.get('tss') or '—'}
VO2max estimado: {meta.get('vo2max') or '—'}
Efecto aeróbico: {meta.get('aerobic_te') or '—'} | Efecto anaeróbico: {meta.get('anaerobic_te') or '—'}
Zonas FC: {json.dumps(zonas) if zonas else 'No disponible'}
Vueltas: {len(laps)} registradas

Respondé en español con:
1. Resumen ejecutivo de la sesión (2-3 líneas)
2. Puntos positivos
3. Puntos a mejorar o atención
4. Recomendación para la próxima sesión del mismo tipo
"""

def _build_prompt_progresion(actividades: list, tipo: str, rango: str) -> str:
    resumen = []
    for a in actividades:
        dist_km = (a.get("distancia_m") or 0) / 1000
        resumen.append(
            f"- {a.get('fecha','')[:10]} | {_fmt_duracion(a.get('duracion_seg'))} | "
            f"{dist_km:.1f}km | FC:{a.get('fc_media') or '—'} | ritmo:{_fmt_ritmo(a.get('velocidad_media'))} | TSS:{a.get('tss') or '—'}"
        )
    return f"""Analizá la progresión de entrenamiento de las últimas sesiones de {tipo}.

Período: {rango}
Sesiones ({len(actividades)}):
{chr(10).join(resumen)}

Respondé en español con:
1. Tendencia general (volumen, intensidad, recuperación)
2. Sesión más destacada y por qué
3. Señales de fatiga o sobreentrenamiento si las hay
4. Recomendación para las próximas 2 semanas
"""

# ── Rutas ─────────────────────────────────────────────────────────────────────
def _api_key():
    return os.environ.get("ANTHROPIC_API_KEY", "")

@garmin_bp.route("/garmin")
def garmin_index():
    from flask import session as s
    return render_template("garmin.html", username=s.get("username", ""))

@garmin_bp.route("/api/garmin/actividades")
def api_actividades():
    tipo  = request.args.get("tipo", "todas")
    limit = int(request.args.get("limit", 50))
    rows  = get_actividades(limit=limit, tipo=tipo)
    return jsonify({"ok": True, "rows": rows})

@garmin_bp.route("/api/garmin/actividades/<act_id>")
def api_actividad_detalle(act_id):
    act = get_actividad(act_id)
    if not act: return jsonify({"ok": False, "error": "No encontrada"})
    # Adjuntar análisis previos
    act["analisis"] = get_analisis(actividad_id=act_id)
    return jsonify({"ok": True, "actividad": act})

@garmin_bp.route("/api/garmin/sync", methods=["POST"])
def api_sync():
    data = request.json or {}
    modo   = data.get("modo", "todo")          # 'todo' | 'semana'
    offset = int(data.get("semana_offset", 0)) # 0=actual, 1=anterior...
    job_id = str(uuid.uuid4())[:8]
    t = threading.Thread(
        target=_sync_worker,
        args=(job_id, session.get("username","?"), modo, offset),
        daemon=True
    )
    t.start()
    return jsonify({"ok": True, "job_id": job_id})

@garmin_bp.route("/api/garmin/sync/status/<job_id>")
def api_sync_status(job_id):
    return jsonify(_sync_status.get(job_id, {"estado": "no_encontrado"}))

@garmin_bp.route("/api/garmin/analizar", methods=["POST"])
def api_analizar():
    import urllib.request
    data = request.json or {}
    act_id  = data.get("actividad_id")
    tipo_an = data.get("tipo", "sesion")  # sesion | progresion

    # Verificar caché
    existentes = get_analisis(actividad_id=act_id) if act_id else []
    if existentes and tipo_an == "sesion":
        return jsonify({"ok": True, "respuesta": existentes[0]["respuesta"], "cached": True})

    key = _api_key()
    if not key:
        return jsonify({"ok": False, "error": "API key no configurada"})

    if tipo_an == "sesion":
        act = get_actividad(act_id)
        if not act: return jsonify({"ok": False, "error": "Actividad no encontrada"})
        prompt = _build_prompt_sesion(act)
        fecha_desde = act.get("fecha","")[:10]
        fecha_hasta = fecha_desde
    else:
        tipo_fil = data.get("tipo_actividad", "running")
        rows = get_actividades(limit=10, tipo=tipo_fil)
        if not rows: return jsonify({"ok": False, "error": "Sin actividades para analizar"})
        rango = f"{rows[-1]['fecha'][:10]} al {rows[0]['fecha'][:10]}"
        prompt = _build_prompt_progresion(rows, tipo_fil, rango)
        fecha_desde = rows[-1]["fecha"][:10]
        fecha_hasta = rows[0]["fecha"][:10]

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
        respuesta = result["content"][0]["text"]
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

    aid = guardar_analisis({
        "tipo": tipo_an,
        "fecha_desde": fecha_desde,
        "fecha_hasta": fecha_hasta,
        "actividad_id": act_id,
        "prompt_usado": prompt,
        "respuesta": respuesta,
    })

    return jsonify({"ok": True, "respuesta": respuesta, "analisis_id": aid, "cached": False})

@garmin_bp.route("/api/garmin/analisis/progresion")
def api_analisis_progresion():
    rows = get_analisis(tipo="progresion")
    return jsonify({"ok": True, "rows": rows})

@garmin_bp.route("/api/garmin/stats")
def api_stats():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    total = con.execute("SELECT COUNT(*) FROM garmin_actividades").fetchone()[0]
    por_tipo = [dict(r) for r in con.execute(
        "SELECT tipo, COUNT(*) as n FROM garmin_actividades GROUP BY tipo ORDER BY n DESC"
    ).fetchall()]
    ultima = con.execute(
        "SELECT sincronizado FROM garmin_actividades ORDER BY sincronizado DESC LIMIT 1"
    ).fetchone()
    con.close()
    return jsonify({
        "ok": True,
        "total": total,
        "por_tipo": por_tipo,
        "ultima_sync": ultima["sincronizado"] if ultima else None,
        "credenciales_ok": credenciales_configuradas(),
    })

@garmin_bp.route("/api/garmin/config", methods=["GET"])
def api_config_get():
    u, _ = get_credenciales_garmin()
    return jsonify({
        "ok": True,
        "configurado": credenciales_configuradas(),
        "usuario": u,  # devuelve usuario pero no contraseña
    })

@garmin_bp.route("/api/garmin/config", methods=["POST"])
def api_config_set():
    data = request.json or {}
    usuario = data.get("usuario", "").strip()
    passwd  = data.get("passwd", "").strip()
    if not usuario or not passwd:
        return jsonify({"ok": False, "error": "Usuario y contraseña son requeridos"})
    set_credenciales_garmin(usuario, passwd)
    return jsonify({"ok": True})
