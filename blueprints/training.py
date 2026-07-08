"""
blueprints/training.py — Módulos Garmin + Training (entrenamiento personal:
sync de actividades Garmin, análisis con IA, planificador semanal, log de
sesiones de fuerza).

Segundo blueprint extraído de app.py (después de stock_bp) en la Fase 2 de
profesionalización. Siguen vua, senasa, finanzas.
"""
import os
import re
import json
import uuid
import time
import logging
import sqlite3
import threading
import threading as _threading
import urllib.request
import urllib.parse
from datetime import datetime, date, timedelta

from flask import Blueprint, request, jsonify, render_template, session, redirect, url_for, send_file

from core import HIST_DB, login_required, modulo_required, limiter, app
import garmin_auth

training_bp = Blueprint("training", __name__)

# ══════════════════════════════════════════════════════════════════════════════
# MÓDULO GARMIN
# ══════════════════════════════════════════════════════════════════════════════

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
    con.execute("""CREATE TABLE IF NOT EXISTS garmin_detalle (
        actividad_id  TEXT PRIMARY KEY,
        serie_tiempo  TEXT,
        ruta_gps      TEXT,
        obtenido      TEXT
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

def get_detalle_cache(act_id):
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM garmin_detalle WHERE actividad_id=?", (act_id,)).fetchone()
    con.close()
    return dict(row) if row else None

def guardar_detalle_cache(act_id, serie_tiempo, ruta_gps):
    con = sqlite3.connect(HIST_DB)
    con.execute("""INSERT OR REPLACE INTO garmin_detalle (actividad_id,serie_tiempo,ruta_gps,obtenido)
        VALUES (?,?,?,?)""",
        (act_id, json.dumps(serie_tiempo or []), json.dumps(ruta_gps or []), datetime.now().isoformat()))
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

# ── Credenciales Garmin (cifrado/almacenamiento aislado en garmin_auth.py) ────
import garmin_auth

def get_credenciales_garmin():
    """Wrapper fino: inyecta HIST_DB/SECRET_KEY y no rompe a los ~10 call sites
    existentes. Si las credenciales guardadas no se pueden descifrar (p.ej.
    cambió SECRET_KEY), loguea el motivo y devuelve vacío en vez de explotar
    en medio de un sync — pero ver api_config_get para el caso en que el
    usuario necesita que se le avise explícitamente."""
    try:
        return garmin_auth.get_credenciales_garmin(HIST_DB, app.secret_key)
    except garmin_auth.CredencialesNoDisponibles as e:
        logging.error(f"GARMIN CREDS | {e}")
        return "", ""

def set_credenciales_garmin(usuario: str, passwd: str):
    garmin_auth.set_credenciales_garmin(HIST_DB, app.secret_key, usuario, passwd)

def credenciales_configuradas():
    return garmin_auth.credenciales_configuradas(HIST_DB, app.secret_key)

# ── Sincronización Garmin ─────────────────────────────────────────────────────
_sync_status = {}

def _limpiar_sync_viejos():
    """Elimina sync jobs de más de 2 horas para evitar memory leak."""
    import time
    while True:
        time.sleep(3600)
        ahora = time.time()
        viejos = [k for k, v in list(_sync_status.items())
                  if v.get('_ts', ahora) < ahora - 7200]
        for k in viejos:
            _sync_status.pop(k, None)
_threading.Thread(target=_limpiar_sync_viejos, daemon=True).start()

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

def _sync_worker(job_id: str, user: str, modo: str, semana_offset: int = 0):
    """
    modo: 'todo' — baja todo el histórico (paginado de a 100)
          'semana' — baja actividades de la semana N (0=actual, 1=anterior, etc.)
    """
    _sync_status[job_id] = {"estado": "iniciando", "progreso": 0, "total": 0, "nuevas": 0, "errores": [], "_ts": __import__("time").time()}
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

@training_bp.route("/garmin")
@login_required
def garmin_index():
    from flask import redirect
    return redirect("/training")

@training_bp.route("/api/garmin/actividades")
@login_required
@modulo_required("training")
def api_actividades():
    tipo  = request.args.get("tipo", "todas")
    limit = int(request.args.get("limit", 50))
    desde = request.args.get("desde")
    hasta = request.args.get("hasta")
    rows  = get_actividades(limit=limit, tipo=tipo, desde=desde, hasta=hasta)
    return jsonify({"ok": True, "rows": rows})

@training_bp.route("/api/garmin/actividades/<act_id>")
@login_required
@modulo_required("training")
def api_actividad_detalle(act_id):
    act = get_actividad(act_id)
    if not act: return jsonify({"ok": False, "error": "No encontrada"})
    # Adjuntar análisis previos
    act["analisis"] = get_analisis(actividad_id=act_id)
    return jsonify({"ok": True, "actividad": act})

@training_bp.route("/api/garmin/sync", methods=["POST"])
@login_required
@modulo_required("training")
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

@training_bp.route("/api/garmin/sync/status/<job_id>")
@login_required
@modulo_required("training")
def api_sync_status(job_id):
    return jsonify(_sync_status.get(job_id, {"estado": "no_encontrado"}))

@training_bp.route("/api/garmin/analizar", methods=["POST"])
@login_required
@modulo_required("training")
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

@training_bp.route("/api/garmin/analisis/progresion")
@login_required
@modulo_required("training")
def api_analisis_progresion():
    rows = get_analisis(tipo="progresion")
    return jsonify({"ok": True, "rows": rows})

@training_bp.route("/api/garmin/stats")
@login_required
@modulo_required("training")
def api_garmin_stats():
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

@training_bp.route("/api/garmin/config", methods=["GET"])
@login_required
@modulo_required("training")
def api_config_get():
    try:
        u, _ = garmin_auth.get_credenciales_garmin(HIST_DB, app.secret_key)
        logging.info(f"GARMIN CONFIG VIEW | user={session.get('username')}")
        return jsonify({"ok": True, "configurado": bool(u), "usuario": u})
    except garmin_auth.CredencialesNoDisponibles as e:
        return jsonify({"ok": True, "configurado": False, "usuario": "", "error": str(e)})

@training_bp.route("/api/garmin/config", methods=["POST"])
@login_required
@modulo_required("training")
@limiter.limit("10 per 15 minutes", error_message="Demasiados intentos de guardar credenciales.")
def api_config_set():
    data = request.json or {}
    usuario = data.get("usuario", "").strip()
    passwd  = data.get("passwd", "").strip()
    if not usuario or not passwd:
        return jsonify({"ok": False, "error": "Usuario y contraseña son requeridos"})
    set_credenciales_garmin(usuario, passwd)
    logging.info(f"GARMIN CONFIG SET | user={session.get('username')} | garmin_usuario={usuario}")
    return jsonify({"ok": True})

@training_bp.route("/api/garmin/carga_semanal")
@login_required
@modulo_required("training")
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

@training_bp.route("/api/garmin/comparar")
@login_required
@modulo_required("training")
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

@training_bp.route("/api/garmin/export_csv")
@login_required
@modulo_required("training")
def api_export_csv():
    import csv, io as sio
    tipo  = request.args.get("tipo", "todas")
    limit = int(request.args.get("limit", 500))
    logging.info(f"GARMIN EXPORT | user={session.get('username')} | tipo={tipo} | limit={limit}")
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

# ── Historial en calendario (actividades REALIZADAS, no plan) ─────────────────
@training_bp.route("/api/garmin/historial_calendario")
@login_required
@modulo_required("training")
def api_historial_calendario():
    """
    Devuelve actividades agrupadas por día para un mes dado, más un mapa
    'heatmap' simplificado (día -> carga total) para pintar el calendario.
    Params: anio, mes (1-12). Si no se pasan, usa el mes actual.
    """
    hoy = date.today()
    anio = int(request.args.get("anio", hoy.year))
    mes  = int(request.args.get("mes", hoy.month))
    desde = f"{anio:04d}-{mes:02d}-01"
    if mes == 12:
        hasta = f"{anio+1:04d}-01-01"
    else:
        hasta = f"{anio:04d}-{mes+1:02d}-01"

    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id,tipo,fecha,nombre,duracion_seg,distancia_m,fc_media,tss,metadata "
        "FROM garmin_actividades WHERE fecha >= ? AND fecha < ? ORDER BY fecha",
        (desde, hasta)
    ).fetchall()
    con.close()

    dias = {}
    for r in rows:
        d = dict(r)
        meta = json.loads(d.get("metadata") or "{}")
        dia = (d.get("fecha") or "")[:10]
        if not dia:
            continue
        dias.setdefault(dia, {"actividades": [], "carga_total": 0})
        dias[dia]["actividades"].append({
            "id": d["id"], "tipo": d["tipo"], "nombre": d["nombre"],
            "duracion_seg": d["duracion_seg"], "distancia_m": d["distancia_m"],
            "fc_media": d["fc_media"], "tss": d["tss"],
        })
        dias[dia]["carga_total"] += meta.get("load_primario") or 0

    return jsonify({"ok": True, "anio": anio, "mes": mes, "dias": dias})

# ── Tendencias multi-parámetro (todas las actividades, todos los campos) ──────
TENDENCIA_CAMPOS = [
    "vo2max", "aerobic_te", "anaerobic_te", "hrv_weekly_avg", "body_battery_drained",
    "stress_durante", "load_primario", "tiempo_recuperacion", "performance_cond",
    "ground_contact", "vertical_osc", "vertical_ratio", "stride_length", "running_power",
    "potencia_max", "ftp", "if_factor", "swolf",
]

@training_bp.route("/api/garmin/tendencias")
@login_required
@modulo_required("training")
def api_tendencias():
    """
    Serie temporal de cualquier combinación de parámetros (de garmin_actividades
    o de su metadata), para graficar evolución a lo largo del tiempo.
    Params: tipo (running|cycling|...), desde, hasta, limit
    """
    tipo  = request.args.get("tipo", "todas")
    desde = request.args.get("desde")
    hasta = request.args.get("hasta")
    limit = int(request.args.get("limit", 200))
    rows = get_actividades(limit=limit, tipo=tipo, desde=desde, hasta=hasta)
    rows = rows[::-1]  # cronológico

    serie = []
    for a in rows:
        meta = json.loads(a.get("metadata") or "{}")
        punto = {
            "fecha": (a.get("fecha") or "")[:10],
            "id": a.get("id"),
            "tipo": a.get("tipo"),
            "duracion_min": round((a.get("duracion_seg") or 0) / 60, 1),
            "distancia_km": round((a.get("distancia_m") or 0) / 1000, 2),
            "fc_media": a.get("fc_media"),
            "fc_max": a.get("fc_max"),
            "tss": a.get("tss"),
            "cadencia": a.get("cadencia_media"),
            "potencia_media": a.get("potencia_media"),
            "ritmo": round(1000 / a["velocidad_media"], 1) if a.get("velocidad_media") else None,
        }
        for campo in TENDENCIA_CAMPOS:
            punto[campo] = meta.get(campo)
        serie.append(punto)

    return jsonify({"ok": True, "campos_disponibles": TENDENCIA_CAMPOS, "rows": serie})

# ── Detalle de actividad: curvas (FC/ritmo/potencia/altitud) y ruta GPS ───────
def _descargar_detalle_garmin(act_id):
    """Pide a Garmin Connect la serie de métricas punto a punto y la ruta GPS.
    Se cachea en garmin_detalle porque es una llamada cara y no cambia."""
    from garminconnect import Garmin
    g_user, g_pass = get_credenciales_garmin()
    if not g_user:
        g_user = os.environ.get("GARMIN_USER", "")
    if not g_pass:
        g_pass = os.environ.get("GARMIN_PASS", "")
    if not g_user or not g_pass:
        raise RuntimeError("Credenciales Garmin no configuradas")

    client = Garmin(g_user, g_pass)
    client.login()
    detalle = client.get_activity_details(int(act_id))

    descriptores = detalle.get("metricDescriptors", []) or []
    idx = {}
    for d in descriptores:
        key = d.get("key", "")
        idx[key] = d.get("metricsIndex")

    def get_idx(*keys):
        for k in keys:
            if k in idx:
                return idx[k]
        return None

    i_time = get_idx("directTimestamp", "sumDuration")
    i_hr   = get_idx("directHeartRate")
    i_speed= get_idx("directSpeed")
    i_power= get_idx("directPower")
    i_elev = get_idx("directElevation")
    i_lat  = get_idx("directLatitude")
    i_lon  = get_idx("directLongitude")

    metricas = (detalle.get("activityDetailMetrics") or [])
    # Downsample a máx ~300 puntos para no mandar payloads gigantes al front
    paso = max(1, len(metricas) // 300)

    serie_tiempo = []
    ruta_gps = []
    for i, m in enumerate(metricas):
        if i % paso != 0:
            continue
        vals = m.get("metrics", [])
        def val(j):
            return vals[j] if (j is not None and j < len(vals)) else None
        punto = {
            "t":   val(i_time),
            "hr":  val(i_hr),
            "vel": val(i_speed),
            "pot": val(i_power),
            "alt": val(i_elev),
        }
        serie_tiempo.append(punto)
        lat, lon = val(i_lat), val(i_lon)
        if lat is not None and lon is not None:
            ruta_gps.append([round(lat, 6), round(lon, 6)])

    guardar_detalle_cache(act_id, serie_tiempo, ruta_gps)
    return serie_tiempo, ruta_gps

@training_bp.route("/api/garmin/actividades/<act_id>/detalle")
@login_required
@modulo_required("training")
def api_actividad_detalle_curvas(act_id):
    """Serie temporal (FC/ritmo/potencia/altitud) + ruta GPS de una actividad.
    Se cachea localmente; sólo pega contra Garmin Connect la primera vez."""
    cache = get_detalle_cache(act_id)
    if cache:
        return jsonify({
            "ok": True,
            "serie_tiempo": json.loads(cache["serie_tiempo"] or "[]"),
            "ruta_gps": json.loads(cache["ruta_gps"] or "[]"),
            "cached": True,
        })
    try:
        serie_tiempo, ruta_gps = _descargar_detalle_garmin(act_id)
    except ImportError:
        return jsonify({"ok": False, "error": "garminconnect no instalado"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True, "serie_tiempo": serie_tiempo, "ruta_gps": ruta_gps, "cached": False})


# ══════════════════════════════════════════════════════════════════════════════
# MÓDULO TRAINING
# ══════════════════════════════════════════════════════════════════════════════


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

# ── Rutas ─────────────────────────────────────────────────────────────────────
@training_bp.route("/training")
@login_required
@modulo_required("training")
def training_index():
    return render_template("training.html", username=session.get("username", ""))

@training_bp.route("/api/training/semana/actual")
@login_required
@modulo_required("training")
def api_semana_actual():
    num = _semana_actual()
    sem = _get_semana(num)
    return jsonify({"ok": True, "semana_num": num, "semana": sem})

@training_bp.route("/api/training/semana/<int:num>")
@login_required
@modulo_required("training")
def api_semana(num):
    sem = _get_semana(num)
    if not sem: return jsonify({"ok": False, "error": "Semana no encontrada"})
    return jsonify({"ok": True, "semana": sem})

@training_bp.route("/api/training/semanas")
@login_required
@modulo_required("training")
def api_semanas_lista():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT semana_num, fecha_inicio, fase, es_descarga, objetivo FROM entrenamiento_semanas ORDER BY semana_num"
    ).fetchall()]
    con.close()
    return jsonify({"ok": True, "rows": rows, "actual": _semana_actual()})

@training_bp.route("/api/training/sesion/<sid>", methods=["PUT"])
@login_required
@modulo_required("training")
def api_sesion_update(sid):
    data = request.json or {}
    con = sqlite3.connect(HIST_DB)
    con.execute(
        "UPDATE entrenamiento_plan SET descripcion=?, notas=?, modificado=datetime('now') WHERE id=?",
        (data.get("descripcion",""), data.get("notas",""), sid))
    con.commit(); con.close()
    return jsonify({"ok": True})

@training_bp.route("/api/training/sesion", methods=["POST"])
@login_required
@modulo_required("training")
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
@login_required
@modulo_required("training")
def api_sesion_delete(sid):
    con = sqlite3.connect(HIST_DB)
    con.execute("DELETE FROM entrenamiento_plan WHERE id=?", (sid,))
    con.commit(); con.close()
    return jsonify({"ok": True})

@training_bp.route("/api/training/log", methods=["GET"])
@login_required
@modulo_required("training")
def api_log_list():
    fd = request.args.get("desde")
    fh = request.args.get("hasta")
    rows = _get_log(fd, fh)
    return jsonify({"ok": True, "rows": rows})

@training_bp.route("/api/training/log", methods=["POST"])
@login_required
@modulo_required("training")
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
@login_required
@modulo_required("training")
def api_log_delete(lid):
    con = sqlite3.connect(HIST_DB)
    con.execute("DELETE FROM entrenamiento_log WHERE id=?", (lid,))
    con.commit(); con.close()
    return jsonify({"ok": True})

@training_bp.route("/api/training/claude", methods=["POST"])
@login_required
@modulo_required("training")
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
@login_required
@modulo_required("training")
def api_training_stats():
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
@login_required
@modulo_required("training")
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


@training_bp.route("/api/training/calendario")
@login_required
@modulo_required("training")
def api_training_calendario():
    """
    Trae workouts programados del calendario de Garmin Connect.
    Parámetros opcionales: year, month (si no se pasan, trae los próximos 3 meses desde hoy).
    """
    from datetime import date, timedelta
    try:
        from garminconnect import Garmin
    except ImportError:
        return jsonify({"ok": False, "error": "garminconnect no instalado"})

    g_user, g_pass = get_credenciales_garmin()
    if not g_user or not g_pass:
        return jsonify({"ok": False, "error": "Credenciales Garmin no configuradas"})

    try:
        client = Garmin(g_user, g_pass)
        client.login()
    except Exception as e:
        return jsonify({"ok": False, "error": f"Login Garmin: {e}"})

    # Determinar rango de meses a traer
    year_param  = request.args.get("year")
    month_param = request.args.get("month")

    if year_param and month_param:
        meses = [(int(year_param), int(month_param))]
    else:
        # Desde el mes actual hasta 3 meses adelante
        hoy = date.today()
        meses = []
        for delta in range(4):
            d = date(hoy.year, hoy.month, 1)
            mes = d.month + delta
            anio = d.year + (mes - 1) // 12
            mes = ((mes - 1) % 12) + 1
            meses.append((anio, mes))

    workouts = []
    for anio, mes in meses:
        try:
            data = client.get_scheduled_workouts(anio, mes)
            items = data if isinstance(data, list) else (data.get("calendarItems") or data.get("items") or [])
            for item in items:
                # Normalizar campos
                fecha = (
                    item.get("date") or
                    item.get("calendarDate") or
                    item.get("scheduledDate") or ""
                )
                nombre = (
                    item.get("workoutName") or
                    item.get("title") or
                    item.get("name") or
                    item.get("eventName") or "Entrenamiento"
                )
                tipo = (
                    item.get("sportType") or
                    item.get("sport") or
                    item.get("activityType") or ""
                )
                if isinstance(tipo, dict):
                    tipo = tipo.get("typeKey") or tipo.get("key") or ""
                duracion = item.get("estimatedDurationInSecs") or item.get("duration") or None
                workouts.append({
                    "fecha":    fecha,
                    "nombre":   nombre,
                    "tipo":     str(tipo).lower(),
                    "duracion": duracion,
                    "id":       item.get("workoutId") or item.get("id") or item.get("scheduleId"),
                    "raw":      item,
                })
        except Exception as e:
            logging.warning(f"CALENDARIO | {anio}-{mes:02d} | {e}")
            continue

    # Organizar por semana ISO
    from collections import defaultdict
    semanas = defaultdict(list)
    for w in workouts:
        if not w["fecha"]:
            continue
        try:
            d = date.fromisoformat(w["fecha"][:10])
            lunes = d - timedelta(days=d.weekday())
            semanas[lunes.isoformat()].append(w)
        except Exception:
            continue

    # Convertir a lista ordenada de semanas
    resultado = []
    for lunes_str in sorted(semanas.keys()):
        lunes = date.fromisoformat(lunes_str)
        domingo = lunes + timedelta(days=6)
        dias = {}
        for w in semanas[lunes_str]:
            dia = w["fecha"][:10]
            if dia not in dias:
                dias[dia] = []
            dias[dia].append(w)
        resultado.append({
            "semana":    lunes_str,
            "domingo":   domingo.isoformat(),
            "dias":      dias,
            "total":     len(semanas[lunes_str]),
        })

    return jsonify({"ok": True, "semanas": resultado, "total_workouts": len(workouts)})


@training_bp.route("/api/garmin/actividades/<act_id>/sets")
@login_required
@modulo_required("training")
def api_garmin_sets(act_id):
    """Lee el archivo FIT local y extrae sets/rounds de actividades de fuerza."""
    import zipfile, io

    # Buscar archivo FIT
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    row = con.execute("SELECT archivo_path, fecha FROM garmin_actividades WHERE id=?", (act_id,)).fetchone()
    con.close()
    if not row or not row["archivo_path"]:
        return jsonify({"ok": False, "error": "Archivo FIT no disponible"})

    fit_path = row["archivo_path"]
    if not os.path.exists(fit_path):
        return jsonify({"ok": False, "error": f"Archivo no encontrado: {fit_path}"})

    try:
        import fitparse

        # El archivo puede ser ZIP o FIT directo
        try:
            with zipfile.ZipFile(fit_path) as zf:
                fit_name = next(n for n in zf.namelist() if n.lower().endswith('.fit'))
                fit_data = io.BytesIO(zf.read(fit_name))
            fp = fitparse.FitFile(fit_data)
        except (zipfile.BadZipFile, StopIteration):
            fp = fitparse.FitFile(fit_path)

        EXERCISE_NAMES = {
            0: "Bench Press", 1: "Calf Raise", 2: "Cardio", 3: "Curl",
            4: "Deadlift", 5: "Flye", 6: "Hip Raise", 7: "Kickback",
            8: "Lateral Raise", 9: "Lunge", 10: "Overhead Press", 11: "Plank",
            12: "Pull Up", 13: "Push Up", 14: "Row", 15: "Shoulder Press",
            16: "Shrug", 17: "Sit Up", 18: "Squat", 19: "Total Body",
            20: "Triceps Extension", 21: "Warm Up", 22: "Run", 23: "Row (Remo)",
            24: "Hip Raise", 25: "Triceps Extension", 26: "Shrug", 27: "Pull Up",
            28: "Push Up", 29: "Squat", 30: "Deadlift", 31: "Lunge",
        }

        sets_raw = []
        for msg in fp.get_messages('set'):
            d = {f.name: f.value for f in msg.fields if f.value is not None}
            cat_list = d.get('category', [])
            if not isinstance(cat_list, list):
                cat_list = [cat_list]
            cat_primary = next((c for c in cat_list if c not in (None, 65534)), None)
            nombre = EXERCISE_NAMES.get(cat_primary, f"Ejercicio {cat_primary}") if cat_primary is not None else None
            peso_g = d.get('weight')
            sets_raw.append({
                'tipo':     d.get('set_type', 'active'),
                'dur_seg':  round(float(d.get('duration', 0)), 1),
                'reps':     d.get('repetitions'),
                'nombre':   nombre,
                'peso_kg':  round(peso_g / 1000, 2) if peso_g else None,
                'ts':       str(d.get('timestamp', '')),
            })

        # Agrupar en rounds: cada set 'active' seguido de su 'rest'
        rounds = []
        round_num = 0
        i = 0
        while i < len(sets_raw):
            s = sets_raw[i]
            if s['tipo'] == 'active' and s['nombre']:
                round_num += 1
                rest_seg = None
                if i + 1 < len(sets_raw) and sets_raw[i+1]['tipo'] == 'rest':
                    rest_seg = sets_raw[i+1]['dur_seg']
                    i += 1
                rounds.append({
                    'round':    round_num,
                    'nombre':   s['nombre'],
                    'reps':     s['reps'],
                    'dur_seg':  s['dur_seg'],
                    'rest_seg': rest_seg,
                    'peso_kg':  s['peso_kg'],
                })
            i += 1

        return jsonify({"ok": True, "rounds": rounds, "total": len(rounds)})

    except Exception as e:
        logging.error(f"FIT PARSE ERROR | {act_id} | {e}")
        return jsonify({"ok": False, "error": str(e)})

