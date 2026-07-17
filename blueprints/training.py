"""
blueprints/training.py — Módulo Garmin (sync de actividades, análisis con IA,
stats, tendencias) + definición del blueprint compartido "training".

Segundo blueprint extraído de app.py (después de stock_bp) en la Fase 2 de
profesionalización. Siguen vua, senasa, finanzas.

Split en dos archivos (Fase 5, 12/07/2026) porque había llegado a ~1900
líneas: este archivo se quedó con Garmin (sync/análisis/stats) + la
definición de training_bp; blueprints/training_plan.py tiene el planificador
semanal, el log de sesiones, el glosario de ejercicios y las rutinas
manuales. Los dos registran rutas sobre el MISMO objeto training_bp (definido
acá) -- training_plan.py se importa al final de este archivo para que sus
decoradores @training_bp.route(...) se ejecuten."""
import os
import re
import json
import uuid
import time
import logging
import threading
import threading as _threading
import urllib.request
import urllib.parse
from datetime import datetime, date, timedelta

from flask import Blueprint, request, jsonify, render_template, session, redirect, url_for, send_file

from core import HIST_DB, login_required, modulo_required, limiter, app, get_db
import garmin_auth

training_bp = Blueprint("training", __name__)

# ══════════════════════════════════════════════════════════════════════════════
# MÓDULO GARMIN
# ══════════════════════════════════════════════════════════════════════════════

GARMIN_DIR = "/data/garmin"
# Fase 11: Garmin bloquea el login por Cloudflare cada tanto (más común
# desde IPs de datacenter, como este droplet) -- la propia librería
# garminconnect recomienda esto: guardar la sesión (tokenstore) después
# del primer login exitoso y reusarla, en vez de loguearse con
# usuario/contraseña en cada llamada. Eso evita pegarle al endpoint de
# login (el que Cloudflare bloquea) casi siempre -- solo se vuelve a usar
# usuario/contraseña si el token cacheado ya no sirve. Carpeta separada de
# GARMIN_DIR (que son los .fit descargados) para no mezclar.
GARMIN_TOKENSTORE = "/data/garmin_tokens"
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
    with get_db(HIST_DB) as con:
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
            sincronizado  TEXT,
            nota_real     TEXT DEFAULT ''
        )""")
        # Columna agregada después de que la tabla ya existía (mismo caso que
        # sesiones/vua_ejes más arriba) — corrección manual de "lo que
        # efectivamente pasó" en una actividad, para cuando Garmin detecta
        # mal los ejercicios (típico en circuitos Hyrox/gimnasio). Se suma al
        # prompt de análisis IA junto a lo que Garmin detectó, no lo reemplaza
        # — ver _build_prompt_sesion.
        try:
            con.execute("ALTER TABLE garmin_actividades ADD COLUMN nota_real TEXT DEFAULT ''")
        except Exception:
            pass  # ya existe
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
        # Fase 10: peso corporal + wellness diario, para el análisis nocturno
        # con IA (cruza peso, entrenamientos del día, wellness de Garmin si
        # el reloj lo reporta, y el estado del plan semanal).
        con.execute("""CREATE TABLE IF NOT EXISTS entrenamiento_peso (
            fecha      TEXT PRIMARY KEY,
            peso_kg    REAL,
            sensacion  INTEGER,
            nota       TEXT DEFAULT '',
            creado     TEXT DEFAULT (datetime('now'))
        )""")
        # raw_json guarda la respuesta cruda completa de cada endpoint de
        # Garmin, además de los campos que se extraen abajo -- no se probó
        # contra una cuenta real en desarrollo (los nombres de campo de la
        # API de Garmin no son públicos/estables), así que si algún nombre
        # no coincide con lo que devuelve tu cuenta, el dato crudo sigue
        # disponible para reprocesar sin tener que sincronizar de nuevo.
        con.execute("""CREATE TABLE IF NOT EXISTS garmin_wellness (
            fecha            TEXT PRIMARY KEY,
            body_battery_max INTEGER,
            body_battery_min INTEGER,
            sleep_score      INTEGER,
            sleep_seg        INTEGER,
            stress_avg       INTEGER,
            hrv_status       TEXT,
            hrv_avg_ms       INTEGER,
            resting_hr       INTEGER,
            total_steps      INTEGER,
            active_kcal      INTEGER,
            moderate_min     INTEGER,
            vigorous_min     INTEGER,
            raw_json         TEXT,
            sincronizado     TEXT DEFAULT (datetime('now'))
        )""")
        # Columnas agregadas después de que la tabla ya existía (mismo caso
        # que nota_real más arriba) -- se suman para poder evaluar días SIN
        # entrenamiento estructurado: cuánto se movió igual la persona en
        # el día (pasos, calorías activas, minutos de intensidad), en vez
        # de mirar solo Body Battery/sueño/estrés/HRV.
        for _col, _tipo in (("total_steps","INTEGER"), ("active_kcal","INTEGER"),
                            ("moderate_min","INTEGER"), ("vigorous_min","INTEGER")):
            try:
                con.execute(f"ALTER TABLE garmin_wellness ADD COLUMN {_col} {_tipo}")
            except Exception:
                pass  # ya existe

init_garmin_db()

# ── Helpers BD ────────────────────────────────────────────────────────────────
def get_actividades(limit=50, tipo=None, desde=None, hasta=None):
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
    with get_db(HIST_DB, row_factory=True) as con:
        rows = [dict(r) for r in con.execute(q, params).fetchall()]
    return rows

def get_actividad(act_id):
    with get_db(HIST_DB, row_factory=True) as con:
        row = con.execute("SELECT * FROM garmin_actividades WHERE id=?", (act_id,)).fetchone()
    return dict(row) if row else None

def guardar_actividad(data: dict):
    with get_db(HIST_DB) as con:
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

def get_detalle_cache(act_id):
    with get_db(HIST_DB, row_factory=True) as con:
        row = con.execute("SELECT * FROM garmin_detalle WHERE actividad_id=?", (act_id,)).fetchone()
    return dict(row) if row else None

def guardar_detalle_cache(act_id, serie_tiempo, ruta_gps):
    with get_db(HIST_DB) as con:
        con.execute("""INSERT OR REPLACE INTO garmin_detalle (actividad_id,serie_tiempo,ruta_gps,obtenido)
            VALUES (?,?,?,?)""",
            (act_id, json.dumps(serie_tiempo or []), json.dumps(ruta_gps or []), datetime.now().isoformat()))

def get_analisis(actividad_id=None, tipo=None):
    with get_db(HIST_DB, row_factory=True) as con:
        if actividad_id:
            rows = con.execute(
                "SELECT * FROM garmin_analisis WHERE actividad_id=? ORDER BY creado DESC",
                (actividad_id,)).fetchall()
        else:
            q = "SELECT * FROM garmin_analisis WHERE tipo=? ORDER BY creado DESC LIMIT 20"
            rows = con.execute(q, (tipo or "sesion",)).fetchall()
    return [dict(r) for r in rows]

def guardar_analisis(data: dict):
    aid = str(uuid.uuid4())[:12]
    tipo = data.get("tipo", "sesion")
    with get_db(HIST_DB) as con:
        if tipo == "sesion" and data.get("actividad_id"):
            # Guardar solo el último análisis por actividad — antes se
            # acumulaba uno nuevo cada vez que se apretaba "Analizar sesión"
            # (ej. después de agregar la nota manual), dejando versiones
            # viejas dando vueltas en la base sin ningún uso.
            con.execute("DELETE FROM garmin_analisis WHERE tipo='sesion' AND actividad_id=?",
                        (data["actividad_id"],))
        con.execute("""INSERT INTO garmin_analisis
            (id,tipo,fecha_desde,fecha_hasta,actividad_id,prompt_usado,respuesta,creado)
            VALUES (?,?,?,?,?,?,?,?)""",
            (aid, tipo, data.get("fecha_desde"), data.get("fecha_hasta"),
             data.get("actividad_id"), data.get("prompt_usado",""), data.get("respuesta",""),
             datetime.now().isoformat()))
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


def _conectar_garmin():
    """Login a Garmin Connect, mismo patrón que usa el sync de actividades
    (sincronizar_garmin) -- extraído acá para no duplicarlo en el sync de
    wellness y en el job nocturno de análisis (training_plan.py). Usa
    tokenstore (ver GARMIN_TOKENSTORE) para reusar la sesión entre
    llamadas en vez de loguearse con usuario/contraseña cada vez -- eso es
    lo que dispara el bloqueo de Cloudflare en el login (Fase 11)."""
    from garminconnect import Garmin
    g_user, g_pass = get_credenciales_garmin()
    if not g_user: g_user = os.environ.get("GARMIN_USER", "")
    if not g_pass: g_pass = os.environ.get("GARMIN_PASS", "")
    if not g_user or not g_pass:
        raise RuntimeError("Credenciales Garmin no configuradas.")
    os.makedirs(GARMIN_TOKENSTORE, exist_ok=True)
    client = Garmin(g_user, g_pass)
    client.login(tokenstore=GARMIN_TOKENSTORE)
    return client


def _sincronizar_actividades_dia(client, fecha):
    """Versión liviana de _sync_worker, sin tracking de progreso para UI --
    paso previo del análisis nocturno (Fase 10): trae y guarda las
    actividades de UN día puntual, para asegurarse de que estén en la base
    antes de armar el análisis (si no se sincronizó a mano ese día, el
    análisis se quedaría sin ver los entrenamientos de hoy). Sí descarga
    el .fit de cada actividad nueva, igual que el sync completo -- si no,
    guardada sin archivo acá, el sync completo la saltearía después (ya
    existe) y el .fit nunca se bajaría. Devuelve la cantidad de nuevas."""
    nuevas = 0
    try:
        actividades = client.get_activities_by_date(fecha, fecha)
    except Exception as e:
        logging.info(f"Sync nocturno: no se pudieron traer actividades de {fecha}: {e}")
        return 0

    for act in actividades or []:
        act_id = str(act.get("activityId", ""))
        if not act_id:
            continue
        with get_db(HIST_DB) as con:
            existe = con.execute("SELECT 1 FROM garmin_actividades WHERE id=?", (act_id,)).fetchone()
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
            logging.info(f"Sync nocturno: no se pudo bajar el .fit de {act_id}: {e}")
        guardar_actividad(datos)
        nuevas += 1
    return nuevas


def _extraer_peso_de_respuesta(resp):
    """Busca un valor de peso (Garmin lo reporta en gramos) en distintas
    formas posibles de respuesta -- no se pudo confirmar contra una cuenta
    real qué shape exacto devuelve get_daily_weigh_ins/get_weigh_ins, así
    que se prueban varias rutas conocidas de la comunidad en vez de asumir
    una sola. Devuelve (fecha_iso_o_None, peso_kg_o_None)."""
    if not resp:
        return None, None
    if isinstance(resp, dict):
        resumenes = resp.get("dailyWeightSummaries")
        if resumenes:
            for r in sorted(resumenes, key=lambda x: x.get("summaryDate", ""), reverse=True):
                for m in (r.get("allWeightMetrics") or []):
                    if m.get("weight"):
                        return r.get("summaryDate"), round(m["weight"] / 1000, 1)
        ta = resp.get("totalAverage")
        if ta and ta.get("weight"):
            return resp.get("endDate"), round(ta["weight"] / 1000, 1)
    if isinstance(resp, list):
        validos = [m for m in resp if isinstance(m, dict) and m.get("weight")]
        if validos:
            ultimo = sorted(validos, key=lambda m: m.get("date") or m.get("calendarDate") or "", reverse=True)[0]
            fecha = ultimo.get("date") or ultimo.get("calendarDate")
            return fecha, round(ultimo["weight"] / 1000, 1)
    return None, None


def _persistir_peso(fecha, peso_kg):
    """Guarda/actualiza SOLO peso_kg de una fecha, sin tocar sensación/nota
    si ya había una carga manual ese día (son cosas independientes, ver
    api_training_peso_guardar)."""
    with get_db(HIST_DB) as con:
        con.execute("""
            INSERT INTO entrenamiento_peso (fecha, peso_kg) VALUES (?,?)
            ON CONFLICT(fecha) DO UPDATE SET peso_kg=excluded.peso_kg
        """, (fecha, peso_kg))


def _obtener_peso_garmin(client, fecha, dias_atras=60):
    """Peso corporal desde Garmin (no manual -- se carga en Garmin Connect,
    acá solo se lee) (Fase 10). Primero intenta el día pedido; si no hay
    carga ese día, busca hacia atrás hasta encontrar el último peso
    registrado (hasta dias_atras). Devuelve (fecha_del_peso, peso_kg,
    raw) -- fecha_del_peso puede ser distinta a `fecha` si tuvo que ir
    para atrás. (None, None, raw) si no encontró nada en todo el rango.

    Efecto secundario a propósito: si encuentra un peso, lo persiste en
    entrenamiento_peso (antes esta función solo devolvía el valor sin
    guardarlo -- "Probar sincronización" mostraba el peso pero nunca
    quedaba en el historial, que solo se llenaba por el job nocturno)."""
    raw = {}
    try:
        raw["dia"] = client.get_daily_weigh_ins(fecha)
        f, kg = _extraer_peso_de_respuesta(raw["dia"])
        if kg:
            _persistir_peso(fecha, kg)
            return fecha, kg, raw
    except Exception as e:
        logging.info(f"Peso Garmin (día puntual) no disponible para {fecha}: {e}")

    try:
        desde = (datetime.strptime(fecha, "%Y-%m-%d").date() - timedelta(days=dias_atras)).isoformat()
        raw["rango"] = client.get_weigh_ins(desde, fecha)
        f, kg = _extraer_peso_de_respuesta(raw["rango"])
        if kg:
            _persistir_peso(f, kg)
            return f, kg, raw
    except Exception as e:
        logging.info(f"Peso Garmin (rango {dias_atras}d) no disponible para {fecha}: {e}")

    return None, None, raw


def _sincronizar_historial_peso(client, dias=90):
    """Trae TODOS los pesajes de Garmin en los últimos `dias` días y los
    guarda -- a diferencia de _obtener_peso_garmin (que solo se queda con
    el más reciente encontrado), esto rellena el historial completo de
    una sola vez. Pensado para un botón de \"sincronizar historial\" en la
    pantalla, así no hay que esperar a que el job nocturno vaya
    completando fecha por fecha. Devuelve la cantidad de registros
    guardados."""
    hasta = date.today().isoformat()
    desde = (date.today() - timedelta(days=dias)).isoformat()
    resp = client.get_weigh_ins(desde, hasta)
    resumenes = (resp or {}).get("dailyWeightSummaries") or []
    guardados = 0
    for r in resumenes:
        fecha_r = r.get("summaryDate")
        for m in (r.get("allWeightMetrics") or []):
            if m.get("weight") and fecha_r:
                _persistir_peso(fecha_r, round(m["weight"] / 1000, 1))
                guardados += 1
                break  # un registro por día alcanza para el historial
    return guardados


# ── Backfill de historial de wellness (Fase 11) ──────────────────────────────
# A diferencia del peso (que tiene get_weigh_ins, un solo pedido para todo
# un rango), Garmin no tiene un endpoint de rango para Body Battery/sueño/
# estrés/HRV -- hay que pedir día por día. Con 90 días eso son ~90
# llamadas seguidas a la API, unos cuantos minutos -- no puede ser una
# request HTTP normal (se colgaría/expiraría), así que corre en background
# con polling de progreso, mismo patrón que el sync de actividades
# (_sync_status).
_wellness_backfill_status = {}

def _sincronizar_historial_wellness_worker(job_id, dias):
    _wellness_backfill_status[job_id] = {"estado": "corriendo", "hecho": 0, "total": dias, "error": None}
    try:
        client = _conectar_garmin()
        hoy = date.today()
        for i in range(dias):
            fecha = (hoy - timedelta(days=i)).isoformat()
            try:
                _sincronizar_wellness_dia(client, fecha)
            except Exception as e:
                logging.info(f"Backfill wellness: {fecha} falló ({e}), sigue con el resto.")
            _wellness_backfill_status[job_id]["hecho"] = i + 1
        _wellness_backfill_status[job_id]["estado"] = "listo"
    except Exception as e:
        _wellness_backfill_status[job_id]["estado"] = "error"
        _wellness_backfill_status[job_id]["error"] = str(e)


@training_bp.route("/api/training/wellness/sincronizar_historial", methods=["POST"])
@login_required
@modulo_required("training")
def api_wellness_sincronizar_historial():
    data = request.json or {}
    dias = min(int(data.get("dias", 90)), 180)  # tope duro -- 180 días ya son ~180 llamadas a Garmin
    job_id = str(uuid.uuid4())[:8]
    threading.Thread(target=_sincronizar_historial_wellness_worker, args=(job_id, dias), daemon=True).start()
    return jsonify({"ok": True, "job_id": job_id})


@training_bp.route("/api/training/wellness/sincronizar_historial/<job_id>")
@login_required
@modulo_required("training")
def api_wellness_sincronizar_historial_status(job_id):
    estado = _wellness_backfill_status.get(job_id)
    if not estado:
        return jsonify({"ok": False, "error": "Job no encontrado."}), 404
    return jsonify({"ok": True, **estado})


def _sincronizar_wellness_dia(client, fecha):
    """Trae Body Battery/sueño/estrés/HRV/FC en reposo + actividad general
    del día (pasos, calorías activas, minutos de intensidad) de Garmin y
    los guarda (Fase 10). Estos últimos importan sobre todo en días SIN
    entrenamiento estructurado -- un día "de descanso" puede tener mucho
    o poco movimiento igual, y eso es información real para el análisis,
    no solo "no entrenó". Defensivo a propósito: cada endpoint en su
    propio try/except -- si el reloj no soporta uno, que no tire abajo
    los demás -- y se guarda el JSON crudo completo en raw_json además de
    los campos extraídos (ver nota en el CREATE TABLE de garmin_wellness:
    no se pudo probar contra una cuenta real en desarrollo)."""
    raw = {}
    bb_max = bb_min = sleep_score = sleep_seg = stress_avg = hrv_avg = resting_hr = None
    hrv_status = None
    total_steps = active_kcal = moderate_min = vigorous_min = None

    try:
        bb = client.get_body_battery(fecha)
        raw["body_battery"] = bb
        if bb and isinstance(bb, list) and bb[0].get("bodyBatteryValuesArray"):
            valores = [v[1] for v in bb[0]["bodyBatteryValuesArray"] if len(v) > 1 and v[1] is not None]
            if valores:
                bb_max, bb_min = max(valores), min(valores)
    except Exception as e:
        logging.info(f"Wellness Body Battery no disponible para {fecha}: {e}")

    try:
        sleep = client.get_sleep_data(fecha)
        raw["sleep"] = sleep
        dto = (sleep or {}).get("dailySleepDTO") or {}
        sleep_seg = dto.get("sleepTimeSeconds")
        scores = (sleep or {}).get("sleepScores") or {}
        sleep_score = (scores.get("overall") or {}).get("value")
    except Exception as e:
        logging.info(f"Wellness sueño no disponible para {fecha}: {e}")

    try:
        stress = client.get_stress_data(fecha)
        raw["stress"] = stress
        stress_avg = (stress or {}).get("avgStressLevel")
    except Exception as e:
        logging.info(f"Wellness estrés no disponible para {fecha}: {e}")

    try:
        hrv = client.get_hrv_data(fecha)
        raw["hrv"] = hrv
        resumen = (hrv or {}).get("hrvSummary") or {}
        hrv_avg = resumen.get("lastNightAvg")
        hrv_status = resumen.get("status")
    except Exception as e:
        logging.info(f"Wellness HRV no disponible para {fecha}: {e}")

    try:
        stats = client.get_stats(fecha)
        raw["stats"] = stats
        stats = stats or {}
        resting_hr = stats.get("restingHeartRate")
        total_steps = stats.get("totalSteps")
        active_kcal = stats.get("activeKilocalories")
        moderate_min = stats.get("moderateIntensityMinutes")
        vigorous_min = stats.get("vigorousIntensityMinutes")
    except Exception as e:
        logging.info(f"Wellness stats no disponible para {fecha}: {e}")

    with get_db(HIST_DB) as con:
        con.execute("""
            INSERT INTO garmin_wellness (fecha, body_battery_max, body_battery_min, sleep_score,
                sleep_seg, stress_avg, hrv_status, hrv_avg_ms, resting_hr,
                total_steps, active_kcal, moderate_min, vigorous_min, raw_json, sincronizado)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
            ON CONFLICT(fecha) DO UPDATE SET
                body_battery_max=excluded.body_battery_max, body_battery_min=excluded.body_battery_min,
                sleep_score=excluded.sleep_score, sleep_seg=excluded.sleep_seg,
                stress_avg=excluded.stress_avg, hrv_status=excluded.hrv_status,
                hrv_avg_ms=excluded.hrv_avg_ms, resting_hr=excluded.resting_hr,
                total_steps=excluded.total_steps, active_kcal=excluded.active_kcal,
                moderate_min=excluded.moderate_min, vigorous_min=excluded.vigorous_min,
                raw_json=excluded.raw_json, sincronizado=excluded.sincronizado
        """, (fecha, bb_max, bb_min, sleep_score, sleep_seg, stress_avg, hrv_status, hrv_avg, resting_hr,
              total_steps, active_kcal, moderate_min, vigorous_min, json.dumps(raw)))

    return {"fecha": fecha, "body_battery_max": bb_max, "body_battery_min": bb_min,
            "sleep_score": sleep_score, "sleep_seg": sleep_seg, "stress_avg": stress_avg,
            "hrv_status": hrv_status, "hrv_avg_ms": hrv_avg, "resting_hr": resting_hr,
            "total_steps": total_steps, "active_kcal": active_kcal,
            "moderate_min": moderate_min, "vigorous_min": vigorous_min}


@training_bp.route("/api/garmin/wellness/probar", methods=["POST"])
@login_required
@modulo_required("training")
def api_garmin_wellness_probar():
    """Sincroniza wellness + peso de una fecha (hoy por default) y devuelve
    tanto los campos extraídos como el JSON crudo completo -- para poder
    ver de entrada qué trae realmente tu cuenta/reloj. No se pudo probar
    en desarrollo sin credenciales reales de Garmin (Fase 10)."""
    data = request.json or {}
    fecha = data.get("fecha") or date.today().isoformat()
    try:
        client = _conectar_garmin()
        resultado = _sincronizar_wellness_dia(client, fecha)
        fecha_peso, peso_kg, raw_peso = _obtener_peso_garmin(client, fecha)
        with get_db(HIST_DB, row_factory=True) as con:
            row = con.execute("SELECT raw_json FROM garmin_wellness WHERE fecha=?", (fecha,)).fetchone()
        return jsonify({"ok": True, "extraido": resultado,
                         "peso": {"fecha": fecha_peso, "peso_kg": peso_kg},
                         "raw": json.loads(row["raw_json"]) if row else {},
                         "raw_peso": raw_peso})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@training_bp.route("/api/training/peso", methods=["POST"])
@login_required
@modulo_required("training")
def api_training_peso_guardar():
    """Carga manual de \"cómo te sentiste\" del día (1-5) + nota. El peso
    corporal NO se carga acá -- se toma de Garmin automáticamente (se
    carga en Garmin Connect, ver _obtener_peso_garmin) en el análisis
    nocturno. peso_kg sigue existiendo como parámetro opcional para poder
    corregir/completar a mano un valor puntual si hiciera falta, pero el
    formulario de la pantalla no lo pide."""
    data = request.json or {}
    fecha = data.get("fecha") or date.today().isoformat()
    peso_kg = data.get("peso_kg")
    sensacion = data.get("sensacion")
    if sensacion is not None:
        try:
            sensacion = int(sensacion)
            if not (1 <= sensacion <= 5):
                return jsonify({"ok": False, "error": "sensación debe ser 1-5."}), 400
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "sensación inválida."}), 400
    nota = (data.get("nota") or "").strip()
    with get_db(HIST_DB) as con:
        con.execute("""
            INSERT INTO entrenamiento_peso (fecha, peso_kg, sensacion, nota) VALUES (?,?,?,?)
            ON CONFLICT(fecha) DO UPDATE SET
                peso_kg=COALESCE(excluded.peso_kg, entrenamiento_peso.peso_kg),
                sensacion=excluded.sensacion, nota=excluded.nota
        """, (fecha, peso_kg, sensacion, nota))
    return jsonify({"ok": True})


@training_bp.route("/api/training/peso")
@login_required
@modulo_required("training")
def api_training_peso_list():
    dias = int(request.args.get("dias", 30))
    desde = (date.today() - timedelta(days=dias)).isoformat()
    with get_db(HIST_DB, row_factory=True) as con:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM entrenamiento_peso WHERE fecha >= ? ORDER BY fecha DESC", (desde,)).fetchall()]
    return jsonify({"ok": True, "rows": rows})


@training_bp.route("/api/training/peso/objetivo", methods=["GET"])
@login_required
@modulo_required("training")
def api_training_peso_objetivo_get():
    with get_db(HIST_DB, row_factory=True) as con:
        row = con.execute("SELECT valor FROM garmin_config WHERE clave='peso_objetivo_kg'").fetchone()
    return jsonify({"ok": True, "peso_objetivo_kg": float(row["valor"]) if row and row["valor"] else None})


@training_bp.route("/api/training/peso/objetivo", methods=["POST"])
@login_required
@modulo_required("training")
def api_training_peso_objetivo_set():
    """Peso objetivo (Fase 11) -- guardado en garmin_config (clave-valor
    genérica que ya existía) para no sumar una tabla nueva por un solo
    número. Se usa para mostrar progreso en Peso y Bienestar y como
    contexto en el análisis diario con IA."""
    data = request.json or {}
    valor = data.get("peso_objetivo_kg")
    with get_db(HIST_DB) as con:
        if valor in (None, ""):
            con.execute("DELETE FROM garmin_config WHERE clave='peso_objetivo_kg'")
        else:
            try:
                valor = float(valor)
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "Peso objetivo inválido."}), 400
            con.execute(
                "INSERT INTO garmin_config (clave,valor,modificado) VALUES ('peso_objetivo_kg',?,datetime('now')) "
                "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor, modificado=excluded.modificado",
                (str(valor),))
    return jsonify({"ok": True})


@training_bp.route("/api/training/peso/sincronizar", methods=["POST"])
@login_required
@modulo_required("training")
def api_training_peso_sincronizar():
    """Trae TODO el historial de pesajes de Garmin (90 días por default) y
    lo guarda de una -- antes solo se completaba fecha por fecha, una vez
    por noche, vía el job de análisis; con una cuenta que pesa cada tanto
    (no todos los días) eso podía tardar semanas en mostrar algo en el
    historial. Este botón lo llena de entrada."""
    data = request.json or {}
    dias = int(data.get("dias", 90))
    try:
        client = _conectar_garmin()
        guardados = _sincronizar_historial_peso(client, dias)
        return jsonify({"ok": True, "guardados": guardados})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@training_bp.route("/api/garmin/analisis/diario")
@login_required
@modulo_required("training")
def api_analisis_diario_list():
    rows = get_analisis(tipo="diario")
    return jsonify({"ok": True, "rows": rows})

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
        os.makedirs(GARMIN_TOKENSTORE, exist_ok=True)
        client = Garmin(g_user, g_pass)
        client.login(tokenstore=GARMIN_TOKENSTORE)
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

            with get_db(HIST_DB) as con:
                existe = con.execute("SELECT 1 FROM garmin_actividades WHERE id=?", (act_id,)).fetchone()
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
    # Import diferido (no al tope del archivo): _glosario_ejercicios_texto
    # vive en training_plan.py, que a su vez importa de acá (_api_key,
    # get_credenciales_garmin) -- import a nivel de módulo en cualquiera de
    # los dos lados crearía un ciclo. Como training_plan.py se importa recién
    # al final de este archivo, para cuando esta función corre (en un
    # request real) los dos módulos ya están completamente cargados.
    from blueprints.training_plan import _glosario_ejercicios_texto
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

    nota_real = (act.get("nota_real") or "").strip()
    nota_txt = ""
    if nota_real:
        nota_txt = f"""
ACLARACIÓN MANUAL DEL USUARIO — lo que efectivamente pasó (Garmin a veces
detecta mal los ejercicios de fuerza/circuitos; esto es la corrección/
aclaración de la persona, tenela en cuenta junto con los datos de arriba,
no la ignores ni la reemplaces por lo que Garmin detectó):
{nota_real}
{_glosario_ejercicios_texto()}
"""

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
{nota_txt}
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

@training_bp.route("/api/garmin/actividades/<act_id>/nota", methods=["POST"])
@login_required
@modulo_required("training")
def api_actividad_nota_guardar(act_id):
    """Guarda 'lo que efectivamente sucedió' en una actividad — corrección
    manual para cuando Garmin detecta mal los ejercicios (típico en
    circuitos Hyrox/gimnasio). Se usa junto al glosario de ejercicios
    (ver _glosario_ejercicios_texto) cuando se manda a analizar con IA."""
    data = request.json or {}
    nota = (data.get("nota") or "").strip()
    with get_db(HIST_DB) as con:
        cur = con.execute("UPDATE garmin_actividades SET nota_real=? WHERE id=?", (nota, act_id))
        if cur.rowcount == 0:
            return jsonify({"ok": False, "error": "Actividad no encontrada"})
    return jsonify({"ok": True})

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

    # "Analizar sesión" es una acción explícita del usuario (botón), no algo
    # que se dispara solo al abrir la pantalla — así que siempre debe volver
    # a llamar a la IA con los datos actuales. Antes, si ya existía un
    # análisis guardado para esa actividad, se devolvía ese sin recalcular
    # nada — por eso agregar la nota manual (ver nota_real) después de haber
    # analizado una vez no tenía ningún efecto: el botón "Analizar" seguía
    # mostrando el análisis viejo, que nunca había visto la nota.

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
    with get_db(HIST_DB, row_factory=True) as con:
        total = con.execute("SELECT COUNT(*) FROM garmin_actividades").fetchone()[0]
        por_tipo = [dict(r) for r in con.execute(
            "SELECT tipo, COUNT(*) as n FROM garmin_actividades GROUP BY tipo ORDER BY n DESC"
        ).fetchall()]
        ultima = con.execute(
            "SELECT sincronizado FROM garmin_actividades ORDER BY sincronizado DESC LIMIT 1"
        ).fetchone()
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

@training_bp.route("/api/training/tendencias_diarias")
@login_required
@modulo_required("training")
def api_tendencias_diarias():
    """Serie temporal DIARIA (peso + wellness) -- a diferencia de
    /api/garmin/tendencias (una fila por ACTIVIDAD, ciego en días de
    descanso), esto trae una fila por DÍA CALENDARIO independientemente de
    si hubo entrenamiento, cruzando entrenamiento_peso y garmin_wellness
    (Fase 11: ninguna de las dos tablas estaba conectada a "Tendencias")."""
    dias = int(request.args.get("dias", 90))
    desde = (date.today() - timedelta(days=dias)).isoformat()
    with get_db(HIST_DB, row_factory=True) as con:
        pesos = {r["fecha"]: dict(r) for r in con.execute(
            "SELECT * FROM entrenamiento_peso WHERE fecha >= ?", (desde,)).fetchall()}
        wellness = {r["fecha"]: dict(r) for r in con.execute(
            "SELECT * FROM garmin_wellness WHERE fecha >= ?", (desde,)).fetchall()}
    fechas = sorted(set(pesos) | set(wellness))
    serie = []
    for f in fechas:
        p = pesos.get(f, {})
        w = wellness.get(f, {})
        serie.append({
            "fecha": f,
            "peso_kg": p.get("peso_kg"),
            "sensacion": p.get("sensacion"),
            "body_battery_min": w.get("body_battery_min"),
            "body_battery_max": w.get("body_battery_max"),
            "sleep_score": w.get("sleep_score"),
            "sleep_horas": round((w.get("sleep_seg") or 0) / 3600, 1) if w.get("sleep_seg") else None,
            "stress_avg": w.get("stress_avg"),
            "hrv_avg_ms": w.get("hrv_avg_ms"),
            "resting_hr": w.get("resting_hr"),
            "total_steps": w.get("total_steps"),
        })
    return jsonify({"ok": True, "rows": serie})


@training_bp.route("/api/garmin/carga_semanal")
@login_required
@modulo_required("training")
def api_carga_semanal():
    """ATL (fatiga aguda 7d) y CTL (forma crónica 42d) basados en carga diaria.
    Suma también HRV/Body Battery promedio por semana (Fase 11) -- para
    poder ver en el mismo gráfico si, cuando la carga sube mucho, la
    recuperación empieza a bajar (la pregunta que ATL/CTL/TSB solos no
    contestan: no dicen nada de cómo está respondiendo el cuerpo)."""
    with get_db(HIST_DB, row_factory=True) as con:
        rows = con.execute("""
            SELECT DATE(fecha) as dia,
                   SUM(COALESCE(CAST(json_extract(metadata,'$.load_primario') AS REAL), 0)) as carga,
                   COUNT(*) as sesiones
            FROM garmin_actividades
            WHERE fecha >= DATE('now', '-90 days')
            GROUP BY dia ORDER BY dia
        """).fetchall()
        wellness_rows = con.execute(
            "SELECT fecha, hrv_avg_ms, body_battery_max, body_battery_min FROM garmin_wellness "
            "WHERE fecha >= DATE('now', '-90 days')").fetchall()

    from datetime import date, timedelta
    datos = {r["dia"]: {"carga": r["carga"], "sesiones": r["sesiones"]} for r in rows}
    wellness_por_dia = {r["fecha"]: dict(r) for r in wellness_rows}

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
        hrv_sem = [wellness_por_dia.get((lunes + timedelta(days=d)).isoformat(), {}).get("hrv_avg_ms")
                   for d in range(7)]
        hrv_sem = [v for v in hrv_sem if v is not None]
        semanas.append({
            "semana": lunes.isoformat(),
            "carga": round(carga_sem, 1),
            "sesiones": sesiones_sem,
            "hrv_prom": round(sum(hrv_sem) / len(hrv_sem), 1) if hrv_sem else None,
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

    with get_db(HIST_DB, row_factory=True) as con:
        rows = con.execute(
            "SELECT id,tipo,fecha,nombre,duracion_seg,distancia_m,fc_media,tss,metadata "
            "FROM garmin_actividades WHERE fecha >= ? AND fecha < ? ORDER BY fecha",
            (desde, hasta)
        ).fetchall()

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
    os.makedirs(GARMIN_TOKENSTORE, exist_ok=True)
    client.login(tokenstore=GARMIN_TOKENSTORE)
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



# Registrar las rutas del plan de entrenamiento (semana, log, glosario,
# rutinas manuales) -- viven en training_plan.py, un archivo aparte por
# tamaño (blueprints/training.py había llegado a ~1900 líneas). Import al
# final a propósito: training_plan.py necesita _api_key/get_credenciales_
# garmin de este módulo, así que training_bp (definido arriba) y esas dos
# funciones ya tienen que existir antes de que training_plan.py se cargue.
import blueprints.training_plan  # noqa: E402,F401  (registra rutas sobre training_bp, no se usa directo)
