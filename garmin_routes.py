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
def get_actividades(limit=50, tipo=None, desde=None, hasta=None):
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    q = "SELECT * FROM garmin_actividades WHERE 1=1"
    params = []
    if tipo and tipo != "todas":
        q += " AND tipo=?"; params.append(tipo)
    if desde:
        q += " AND fecha >= ?"; params.append(desde)
    if hasta:
        q += " AND fecha <= ?"; params.append(hasta + "T23:59:59")
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

    cad_run  = act.get("averageRunningCadenceInStepsPerMinute")
    cad_bike = act.get("avgBikeCadence")
    cad_swim = act.get("averageSwimmingCadenceInStrokesPerMinute")
    cadencia = cad_run or cad_bike or cad_swim

    # Zonas FC — dos formatos posibles
    zonas_fc = {}
    hrz = act.get("heartRateZones") or []
    if hrz:
        for z in hrz:
            nombre = z.get("zoneName") or z.get("name") or f"Z{z.get('number','')}"
            pct    = _r(z.get("percentOfMax") or z.get("percent"), 1)
            seg    = z.get("secsInZone") or z.get("seconds")
            zonas_fc[nombre] = {"pct": pct, "seg": seg}
    else:
        # Formato plano hrTimeInZone_1..5
        for i in range(1, 6):
            seg = act.get(f"hrTimeInZone_{i}")
            if seg is not None:
                zonas_fc[f"Z{i}"] = {"pct": None, "seg": _r(seg, 1)}

    # Splits
    splits = []
    for s in (act.get("splitSummaries") or []):
        splits.append({
            "tipo":           s.get("splitType"),
            "n":              s.get("noOfSplits"),
            "duracion_seg":   _r(s.get("duration"), 1),
            "distancia_m":    _r(s.get("distance"), 1),
            "vel_media":      _r(s.get("averageSpeed"), 4),
            "vel_max":        _r(s.get("maxSpeed"), 4),
            "desnivel_pos":   _r(s.get("totalAscent"), 1),
            "desnivel_neg":   _r(s.get("elevationLoss"), 1),
        })

    # Ejercicios de fuerza
    ejercicios = []
    for e in (act.get("summarizedExerciseSets") or []):
        ejercicios.append({
            "categoria":  e.get("category"),
            "subcategoria": e.get("subCategory"),
            "sets":       e.get("sets"),
            "reps":       e.get("reps"),
            "peso_g":     e.get("maxWeight"),   # en gramos
            "volumen":    e.get("volume"),
            "duracion":   _r((e.get("duration") or 0) / 1000, 0),
        })

    metadata = {
        # Clasificación
        "tipo_original":        tipo_raw,
        "subtipo":              subtipo,
        "ubicacion":            act.get("locationName"),
        # Fisiológico
        "vo2max":               _r(act.get("vO2MaxValue"), 1),
        "aerobic_te":           _r(act.get("aerobicTrainingEffect"), 1),
        "anaerobic_te":         _r(act.get("anaerobicTrainingEffect"), 1),
        "aerobic_te_label":     act.get("trainingEffectLabel") or act.get("aerobicTrainingEffectLabel"),
        "anaerobic_te_msg":     act.get("anaerobicTrainingEffectMessage"),
        "aerobic_te_msg":       act.get("aerobicTrainingEffectMessage"),
        "hrv_weekly_avg":       _r(act.get("hrvWeeklyAverage"), 1),
        "hrv_status":           act.get("hrvStatus"),
        "body_battery_drained": act.get("bodyBatteryDrained"),
        "body_battery_delta":   act.get("differenceBodyBattery"),
        "stress_durante":       act.get("avgStress"),
        "water_ml":             _r(act.get("waterEstimated"), 0),
        # Intensidad
        "min_moderada":         act.get("moderateIntensityMinutes"),
        "min_vigorosa":         act.get("vigorousIntensityMinutes"),
        # Running específico
        "cadencia_tipo":        "spm" if cad_run else ("rpm" if cad_bike else "s/min"),
        "velocidad_max":        _r(act.get("maxSpeed"), 4),
        "vel_ajustada_pend":    _r(act.get("avgGradeAdjustedSpeed"), 4),
        "paso_mejor":           _r(act.get("bestPace"), 2),
        "faster_split_1k":      _r(act.get("fastestSplit_1000"), 1),
        "faster_split_1609":    _r(act.get("fastestSplit_1609"), 1),
        "ground_contact":       _r(act.get("groundContactTime"), 1),
        "vertical_osc":         _r(act.get("verticalOscillation"), 2),
        "vertical_ratio":       _r(act.get("verticalRatio"), 2),
        "stride_length":        _r(act.get("strideLength"), 2),
        "running_power":        _r(act.get("avgRunningPowerInWatts"), 1),
        "steps":                act.get("steps"),
        # Ciclismo específico
        "potencia_max":         _r(act.get("maxPower"), 1),
        "ftp":                  _r(act.get("functionalThresholdPower"), 1),
        "if_factor":            _r(act.get("intensityFactor"), 2),
        # Natación
        "swolf":                _r(act.get("avgSwolf"), 1),
        "brazadas_largo":       _r(act.get("avgStrokeDistance"), 2),
        "estilo_nado":          act.get("strokes"),
        # Altimetría
        "altitud_min":          _r(act.get("minElevation"), 1),
        "altitud_max":          _r(act.get("maxElevation"), 1),
        "desnivel_neg":         _r(act.get("elevationLoss"), 1),
        # GPS
        "tiene_gps":            act.get("hasPolyline", False),
        "start_lat":            _r(act.get("startLatitude"), 5),
        "start_lon":            _r(act.get("startLongitude"), 5),
        "end_lat":              _r(act.get("endLatitude"), 5),
        "end_lon":              _r(act.get("endLongitude"), 5),
        # Clima
        "temp_inicio":          _r(act.get("startTemperature"), 1),
        "temp_media":           _r(act.get("avgTemperature"), 1),
        # Carga y recuperación
        "load_primario":        _r(act.get("activityTrainingLoad"), 1),
        "tiempo_recuperacion":  act.get("recoveryTime"),
        "performance_cond":     _r(act.get("avgRunningPerformanceCondition"), 1),
        # Vueltas
        "lap_count":            act.get("lapCount"),
        "min_lap_dur":          _r(act.get("minActivityLapDuration"), 1),
        # Splits y ejercicios
        "splits":               splits,
        "ejercicios":           ejercicios,
        # Calorías extra
        "calorias_bmr":         _r(act.get("bmrCalories"), 0),
        # Dispositivo
        "dispositivo":          act.get("deviceId"),
        "manufacturer":         act.get("manufacturer"),
    }

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
        "laps":            [],
        "metadata":        metadata,
        "archivo_path":    None,
    }
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
def _limpiar_label(txt):
    """MINOR_ANAEROBIC_BENEFIT_15 → Minor Anaerobic Benefit"""
    if not txt: return None
    import re
    txt = re.sub(r'_\d+$', '', txt)          # quitar número final
    return txt.replace('_', ' ').title()

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
    meta   = json.loads(act.get("metadata") or "{}")
    zonas  = json.loads(act.get("zonas_fc") or "{}")
    splits = meta.get("splits") or []
    ejercicios = meta.get("ejercicios") or []
    dist_km = (act.get("distancia_m") or 0) / 1000
    tipo = act.get("tipo", "")

    # Zonas FC formateadas
    zonas_txt = ""
    if zonas:
        zonas_txt = "Distribución zonas FC:\n" + "\n".join([
            f"  {z}: {d.get('seg',0)//60:.0f}' ({d.get('pct') or '?'}%)"
            for z, d in zonas.items()
        ])

    # Splits formateados
    splits_txt = ""
    if splits:
        splits_txt = "Splits:\n" + "\n".join([
            f"  {s.get('tipo','').replace('RWD_','').replace('_',' ')} — "
            f"N:{s.get('n','?')} | {_fmt_duracion(s.get('duracion_seg'))} | "
            f"{(s.get('distancia_m') or 0)/1000:.2f}km | {_fmt_ritmo(s.get('vel_media'))}"
            for s in splits
        ])

    # Ejercicios formateados (fuerza)
    ej_txt = ""
    if ejercicios:
        ej_txt = "Ejercicios:\n" + "\n".join([
            f"  {(e.get('subcategoria') or e.get('categoria') or '?').replace('_',' ')} — "
            f"{e.get('sets','?')} sets x {e.get('reps','?')} reps"
            f"{' @ '+str(e.get('peso_g')//1000)+'kg' if e.get('peso_g') else ''}"
            for e in ejercicios
        ])

    # Sección específica por deporte
    deporte_txt = ""
    if tipo == "running":
        deporte_txt = f"""Running:
  Ritmo medio: {_fmt_ritmo(act.get('velocidad_media'))}
  Ritmo aj. pendiente: {_fmt_ritmo(meta.get('vel_ajustada_pend'))}
  Split más rápido 1km: {meta.get('faster_split_1k') or '—'}s
  Cadencia: {act.get('cadencia_media') or '—'} spm
  Desnivel +: {act.get('desnivel_pos') or '—'} m / -: {meta.get('desnivel_neg') or '—'} m
  Contacto suelo: {meta.get('ground_contact') or '—'} ms
  Oscilación vertical: {meta.get('vertical_osc') or '—'} cm
  Ratio vertical: {meta.get('vertical_ratio') or '—'} %
  Long. zancada: {meta.get('stride_length') or '—'} m
  Running power: {meta.get('running_power') or '—'} W"""
    elif tipo == "cycling":
        deporte_txt = f"""Ciclismo:
  Potencia media: {act.get('potencia_media') or '—'} W
  Potencia normalizada: {act.get('normalizada_w') or '—'} W
  Potencia máx: {meta.get('potencia_max') or '—'} W
  IF: {meta.get('if_factor') or '—'}
  FTP: {meta.get('ftp') or '—'} W
  Cadencia: {act.get('cadencia_media') or '—'} rpm"""
    elif tipo == "swimming":
        deporte_txt = f"""Natación:
  SWOLF: {meta.get('swolf') or '—'}
  Dist/brazada: {meta.get('brazadas_largo') or '—'} m
  Estilo: {meta.get('estilo_nado') or '—'}"""
    elif tipo == "strength":
        deporte_txt = f"Fuerza/Hyrox — {len(ejercicios)} ejercicios registrados"

    te_label = _limpiar_label(meta.get('aerobic_te_label')) or ''
    te_msg   = _limpiar_label(meta.get('anaerobic_te_msg')) or ''

    return f"""Analizá esta sesión de entrenamiento. Soy triatleta y competidor de Hyrox, actualmente en preparación para 21K (23/08/2026) y Hybrid Race Individual (12/09/2026).

ACTIVIDAD: {act.get('nombre','')} ({tipo})
Fecha: {act.get('fecha','')[:10]} | Ubicación: {meta.get('ubicacion') or '—'}
Duración: {_fmt_duracion(act.get('duracion_seg'))} | Distancia: {dist_km:.2f} km

FC media: {act.get('fc_media') or '—'} bpm | FC máx: {act.get('fc_max') or '—'} bpm
Calorías: {act.get('calorias') or '—'} kcal | TSS: {act.get('tss') or '—'}
Carga: {meta.get('load_primario') or '—'} | Recuperación estimada: {meta.get('tiempo_recuperacion') or '—'} h
VO2max: {meta.get('vo2max') or '—'} | TE aeróbico: {act.get('aerobic_te') if hasattr(act,'get') else meta.get('aerobic_te') or '—'} ({te_label}) | TE anaeróbico: {meta.get('anaerobic_te') or '—'} ({te_msg})
Body Battery Δ: {meta.get('body_battery_delta') or '—'} | Min vigorosa: {meta.get('min_vigorosa') or '—'}

{deporte_txt}

{zonas_txt}

{splits_txt}

{ej_txt}

Respondé en español con:
1. Resumen ejecutivo (2-3 líneas)
2. Puntos positivos
3. Puntos a mejorar o señales de alerta
4. Recomendación concreta para la próxima sesión del mismo tipo
Sé técnico y directo. Máximo 300 palabras.
"""

def _build_prompt_progresion(actividades: list, tipo: str, rango: str) -> str:
    resumen = []
    for a in actividades:
        meta = json.loads(a.get("metadata") or "{}")
        dist_km = (a.get("distancia_m") or 0) / 1000
        zonas = json.loads(a.get("zonas_fc") or "{}")
        z5_pct = (zonas.get("Z5") or {}).get("pct") or (zonas.get("Z5") or {}).get("seg", 0)
        resumen.append(
            f"- {a.get('fecha','')[:10]} | {_fmt_duracion(a.get('duracion_seg'))} | "
            f"{dist_km:.1f}km | FC:{a.get('fc_media') or '—'} | {_fmt_ritmo(a.get('velocidad_media'))} | "
            f"TSS:{a.get('tss') or '—'} | Carga:{meta.get('load_primario') or '—'} | "
            f"TE:{meta.get('aerobic_te') or '—'} | Z5:{z5_pct or '—'}"
        )

    return f"""Analizá la progresión de entrenamiento. Soy triatleta/Hyrox competidor preparando 21K (23/08) y Hybrid Race (12/09/2026).

Deporte: {tipo} | Período: {rango}
Sesiones ({len(actividades)}):
{chr(10).join(resumen)}

Respondé en español con:
1. Tendencia de volumen, intensidad y recuperación
2. Progresión de rendimiento (ritmo/potencia/FC a igual esfuerzo)
3. Sesión más destacada y por qué
4. Señales de fatiga o sobreentrenamiento
5. Recomendación para las próximas 2 semanas considerando los objetivos de carrera
Sé técnico y directo. Máximo 400 palabras.
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
    desde = request.args.get("desde")
    hasta = request.args.get("hasta")
    rows  = get_actividades(limit=limit, tipo=tipo, desde=desde, hasta=hasta)
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
        "usuario": u,
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

@garmin_bp.route("/api/garmin/carga_semanal")
def api_carga_semanal():
    """ATL (fatiga aguda 7d) y CTL (forma crónica 42d) basados en carga diaria."""
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT DATE(fecha) as dia,
               SUM(COALESCE(CAST(json_extract(metadata,'$.load_primario') AS REAL), 0)) as carga,
               COUNT(*) as sesiones
        FROM garmin_actividades
        WHERE fecha >= DATE('now', '-90 days')
        GROUP BY dia ORDER BY dia
    """).fetchall()
    con.close()

    from datetime import date, timedelta
    datos = {r["dia"]: {"carga": r["carga"], "sesiones": r["sesiones"]} for r in rows}

    semanas = []
    hoy = date.today()
    for w in range(12, -1, -1):
        lunes = hoy - timedelta(days=hoy.weekday()) - timedelta(weeks=w)
        carga_sem = sum(
            datos.get((lunes + timedelta(days=d)).isoformat(), {}).get("carga", 0)
            for d in range(7)
        )
        sesiones_sem = sum(
            datos.get((lunes + timedelta(days=d)).isoformat(), {}).get("sesiones", 0)
            for d in range(7)
        )
        semanas.append({
            "semana": lunes.isoformat(),
            "carga": round(carga_sem, 1),
            "sesiones": sesiones_sem,
        })

    # ATL (7d) y CTL (42d) — promedio móvil
    dias_ordenados = sorted(datos.keys())
    if dias_ordenados:
        ultimo = dias_ordenados[-1]
        cargas_7  = [datos.get((date.fromisoformat(ultimo) - timedelta(days=i)).isoformat(), {}).get("carga", 0) for i in range(7)]
        cargas_42 = [datos.get((date.fromisoformat(ultimo) - timedelta(days=i)).isoformat(), {}).get("carga", 0) for i in range(42)]
        atl = round(sum(cargas_7) / 7, 1)
        ctl = round(sum(cargas_42) / 42, 1)
        tsb = round(ctl - atl, 1)  # Training Stress Balance
    else:
        atl = ctl = tsb = 0

    return jsonify({"ok": True, "semanas": semanas, "atl": atl, "ctl": ctl, "tsb": tsb})

@garmin_bp.route("/api/garmin/comparar")
def api_comparar():
    """Últimas N sesiones del mismo tipo para comparación."""
    tipo  = request.args.get("tipo", "running")
    limit = int(request.args.get("limit", 8))
    rows  = get_actividades(limit=limit, tipo=tipo)
    # Extraer métricas clave para gráfico
    datos = []
    for a in rows:
        meta = json.loads(a.get("metadata") or "{}")
        datos.append({
            "fecha":        a.get("fecha","")[:10],
            "nombre":       a.get("nombre",""),
            "duracion_min": round((a.get("duracion_seg") or 0) / 60, 1),
            "distancia_km": round((a.get("distancia_m") or 0) / 1000, 2),
            "fc_media":     a.get("fc_media"),
            "ritmo":        round(1000 / a["velocidad_media"], 1) if a.get("velocidad_media") else None,
            "tss":          a.get("tss"),
            "carga":        meta.get("load_primario"),
            "vo2max":       meta.get("vo2max"),
            "cadencia":     a.get("cadencia_media"),
        })
    return jsonify({"ok": True, "rows": datos[::-1]})  # cronológico

@garmin_bp.route("/api/garmin/export_csv")
def api_export_csv():
    import csv, io as sio
    tipo  = request.args.get("tipo", "todas")
    limit = int(request.args.get("limit", 500))
    rows  = get_actividades(limit=limit, tipo=tipo)

    output = sio.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id","fecha","nombre","tipo","duracion_min","distancia_km",
        "fc_media","fc_max","calorias","tss","cadencia","ritmo_min_km",
        "desnivel_pos","potencia_media","normalizada_w","vo2max",
        "aerobic_te","anaerobic_te","carga","recuperacion_h","ubicacion"
    ])
    for a in rows:
        meta = json.loads(a.get("metadata") or "{}")
        dist_km = round((a.get("distancia_m") or 0) / 1000, 2)
        dur_min = round((a.get("duracion_seg") or 0) / 60, 1)
        vel = a.get("velocidad_media")
        ritmo = round(1000 / vel, 1) if vel else ""
        writer.writerow([
            a.get("id"), a.get("fecha","")[:10], a.get("nombre",""), a.get("tipo",""),
            dur_min, dist_km, a.get("fc_media",""), a.get("fc_max",""),
            a.get("calorias",""), a.get("tss",""), a.get("cadencia_media",""), ritmo,
            a.get("desnivel_pos",""), a.get("potencia_media",""), a.get("normalizada_w",""),
            meta.get("vo2max",""), meta.get("aerobic_te",""), meta.get("anaerobic_te",""),
            meta.get("load_primario",""), meta.get("tiempo_recuperacion",""), meta.get("ubicacion","")
        ])

    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"garmin_actividades_{tipo}.csv"
    )
