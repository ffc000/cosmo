"""
blueprints/training_plan.py — Planificador semanal, log de sesiones,
glosario de ejercicios y rutinas manuales (texto libre, para ejercicios que
Garmin no registra).

Separado de blueprints/training.py (Fase 5, 12/07/2026) porque ese archivo
había llegado a ~1900 líneas. Registra rutas sobre el MISMO blueprint
training_bp definido en training.py -- no define uno propio. Se importa
desde el final de training.py para que los decoradores @training_bp.route(...)
de acá se ejecuten (ver el comentario al final de training.py).
"""
import os
import json
import uuid
import logging
import threading
import urllib.request
from datetime import datetime, date, timedelta

from flask import request, jsonify, render_template, session

from core import HIST_DB, login_required, modulo_required, get_db, notificar_telegram
from blueprints.training import (training_bp, _api_key, get_credenciales_garmin,
    _conectar_garmin, _sincronizar_wellness_dia, _sincronizar_actividades_dia,
    _obtener_peso_garmin, guardar_analisis, get_actividades, get_analisis, GARMIN_TOKENSTORE)

# ── BD ────────────────────────────────────────────────────────────────────────
def init_training_db():
    with get_db(HIST_DB) as con:
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
        # Glosario de ejercicios: alias (como los nombra Fer/como los llama la
        # máquina/app) -> término estándar (el nombre real del ejercicio,
        # reconocible por la IA). Mismo patrón que vua_glosario — se inyecta
        # como contexto cuando se manda una rutina a analizar, en vez de
        # intentar reemplazar palabras en el texto (más frágil: se rompe con
        # cualquier variación de redacción).
        con.execute("""CREATE TABLE IF NOT EXISTS entrenamiento_glosario (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            alias            TEXT NOT NULL,
            termino_estandar TEXT NOT NULL,
            notas            TEXT DEFAULT '',
            orden            INTEGER DEFAULT 0,
            creado           TEXT DEFAULT (datetime('now'))
        )""")
        # Rutinas cargadas a mano en texto libre (ejercicios que Garmin no
        # registra). Tabla separada de entrenamiento_log a propósito: acá el
        # contenido central es el bloque de texto completo de la rutina, no
        # una descripción corta — mezclarlo con el log le cambiaba el uso
        # a ese campo para las demás sesiones (las que sí vienen de Garmin).
        con.execute("""CREATE TABLE IF NOT EXISTS entrenamiento_rutinas_manuales (
            id          TEXT PRIMARY KEY,
            fecha       TEXT,
            titulo      TEXT DEFAULT '',
            texto       TEXT NOT NULL,
            creado      TEXT DEFAULT (datetime('now'))
        )""")
        if not con.execute("SELECT 1 FROM entrenamiento_glosario LIMIT 1").fetchone():
            con.executemany(
                "INSERT INTO entrenamiento_glosario (alias, termino_estandar, orden) VALUES (?,?,?)",
                [
                    ("Máquina de remo", "Row erg (remo en máquina)", 1),
                    ("Tirones de polea (de pie)", "Ski Erg", 2),
                    ("Zancadas alternada hacia adelante", "Lunges avanzando (estilo Hyrox)", 3),
                    ("Sentadilla con balón medicinal", "Wall Ball", 4),
                ])

def _seed_plan():
    """Importa el plan Post-Hyrox 2026 desde los datos del xlsx."""
    with get_db(HIST_DB) as con:
        n = con.execute("SELECT COUNT(*) FROM entrenamiento_semanas").fetchone()[0]
        if n > 0:
            return

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

init_training_db()
_seed_plan()

# ── Helpers ────────────────────────────────────────────────────────────────────
DIAS_ORDER = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]
TURNOS_ORDER = ["mañana","mediodía","noche"]

def _semana_actual():
    hoy = date.today()
    with get_db(HIST_DB, row_factory=True) as con:
        rows = con.execute("SELECT * FROM entrenamiento_semanas ORDER BY semana_num").fetchall()
    sem_actual = 1
    for r in rows:
        fi = datetime.strptime(r["fecha_inicio"], "%Y-%m-%d").date()
        if hoy >= fi:
            sem_actual = r["semana_num"]
    return sem_actual

def _get_semana(num):
    with get_db(HIST_DB, row_factory=True) as con:
        sem = con.execute("SELECT * FROM entrenamiento_semanas WHERE semana_num=?", (num,)).fetchone()
        sesiones = con.execute(
            "SELECT * FROM entrenamiento_plan WHERE semana_num=? ORDER BY dia_semana, turno",
            (num,)).fetchall()
    if not sem: return None
    s = dict(sem)
    s["sesiones"] = [dict(r) for r in sesiones]
    return s

def _get_log(fecha_desde=None, fecha_hasta=None):
    q = "SELECT l.*, g.tipo as garmin_tipo, g.fc_media, g.distancia_m, g.duracion_seg FROM entrenamiento_log l LEFT JOIN garmin_actividades g ON l.garmin_id = g.id"
    params = []
    if fecha_desde and fecha_hasta:
        q += " WHERE l.fecha BETWEEN ? AND ?"; params = [fecha_desde, fecha_hasta]
    q += " ORDER BY l.fecha DESC LIMIT 100"
    with get_db(HIST_DB, row_factory=True) as con:
        rows = [dict(r) for r in con.execute(q, params).fetchall()]
    return rows


# ── Cruce carga semanal vs. plan (Fase 9: alertas proactivas) ────────────────
def _carga_semana_actual():
    """Sesiones planificadas (entrenamiento_plan) vs. completadas
    (entrenamiento_log) de la semana en curso. No hay un campo de "carga"
    numérica en el plan (duración/intensidad estimada) -- se compara
    cantidad de sesiones, que es lo que el esquema realmente tiene."""
    num = _semana_actual()
    with get_db(HIST_DB, row_factory=True) as con:
        semana = con.execute("SELECT * FROM entrenamiento_semanas WHERE semana_num=?", (num,)).fetchone()
        if not semana:
            return None
        fi = datetime.strptime(semana["fecha_inicio"], "%Y-%m-%d").date()
        ff = fi + timedelta(days=6)
        planificadas = con.execute(
            "SELECT COUNT(*) FROM entrenamiento_plan WHERE semana_num=?", (num,)).fetchone()[0]
        completadas = con.execute(
            "SELECT COUNT(*) FROM entrenamiento_log WHERE fecha BETWEEN ? AND ? AND completado=1",
            (fi.isoformat(), ff.isoformat())).fetchone()[0]
    return {
        "semana_num": num, "es_descarga": bool(semana["es_descarga"]), "objetivo": semana["objetivo"],
        "fecha_inicio": fi.isoformat(), "fecha_fin": ff.isoformat(),
        "planificadas": planificadas, "completadas": completadas,
    }


def _chequear_carga_semanal():
    """Compara sesiones planificadas vs. completadas de la semana en curso
    y avisa por Telegram en 2 casos (un aviso por semana como máximo por
    caso, dedup vía garmin_config -- mismo patrón que el aviso de
    sincronización atrasada):

    1. Vas muy atrasado: a partir del jueves, completaste menos de la
       mitad de lo planificado para la semana.
    2. Sobrecarga en semana de descarga: la semana está marcada como
       es_descarga (buscando bajar volumen a propósito) pero ya
       completaste tantas o más sesiones que las planificadas -- justo lo
       que una semana de descarga busca evitar."""
    DIAS = ["", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    while True:
        try:
            carga = _carga_semana_actual()
            if carga and carga["planificadas"] > 0:
                dia_semana = date.today().isoweekday()  # 1=lunes ... 7=domingo
                pct = carga["completadas"] / carga["planificadas"]
                with get_db(HIST_DB, row_factory=True) as con:
                    def _ya_avisado(clave, valor):
                        row = con.execute("SELECT valor FROM garmin_config WHERE clave=?", (clave,)).fetchone()
                        return row and row["valor"] == str(valor)

                    def _marcar(clave, valor):
                        con.execute(
                            "INSERT INTO garmin_config (clave,valor,modificado) VALUES (?,?,datetime('now')) "
                            "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor, modificado=excluded.modificado",
                            (clave, str(valor)))

                    if dia_semana >= 4 and pct < 0.5 and not _ya_avisado("aviso_carga_baja_semana", carga["semana_num"]):
                        notificar_telegram(
                            f"🏋️ Semana {carga['semana_num']}: completaste {carga['completadas']}/"
                            f"{carga['planificadas']} sesiones planificadas (ya es {DIAS[dia_semana]}).")
                        _marcar("aviso_carga_baja_semana", carga["semana_num"])

                    if (carga["es_descarga"] and carga["completadas"] >= carga["planificadas"]
                            and not _ya_avisado("aviso_sobrecarga_descarga_semana", carga["semana_num"])):
                        notificar_telegram(
                            f"⚠️ Semana {carga['semana_num']} es de DESCARGA y ya completaste "
                            f"{carga['completadas']}/{carga['planificadas']} sesiones planificadas — "
                            f"cuidado con no bajar el volumen como corresponde en esta fase.")
                        _marcar("aviso_sobrecarga_descarga_semana", carga["semana_num"])
        except Exception:
            logging.exception("Error en chequeo de carga semanal de entrenamiento")
        threading.Event().wait(21600)  # cada 6 horas

threading.Thread(target=_chequear_carga_semanal, daemon=True).start()


# ── Análisis matutino con IA (Fase 10/11) ─────────────────────────────────────
def _sesiones_planificadas_dia(semana_num, dia_nombre):
    with get_db(HIST_DB, row_factory=True) as con:
        rows = con.execute(
            "SELECT * FROM entrenamiento_plan WHERE semana_num=? AND dia_semana=? ORDER BY turno",
            (semana_num, dia_nombre)).fetchall()
    return [dict(r) for r in rows]


def _workout_programado_garmin(client, fecha):
    """Trae lo que GARMIN CONNECT tiene programado en SU calendario para un
    día puntual (get_scheduled_workouts) -- a diferencia de
    entrenamiento_plan (nuestro plan interno de 14 semanas), esto es lo
    que se carga directamente en Garmin Connect, y en la práctica es la
    fuente real de "qué toca hoy" (bug encontrado: entrenamiento_plan
    estaba vacío/desactualizado para la semana en curso, y sin datos
    reales la IA terminó inventando una sesión de running que no
    existía). Misma lógica de parseo que /api/training/calendario.
    Devuelve una lista de dicts (puede haber más de una sesión el mismo
    día) -- [] si no hay nada programado ahí o falló la consulta."""
    anio, mes = int(fecha[:4]), int(fecha[5:7])
    try:
        data = client.get_scheduled_workouts(anio, mes)
    except Exception as e:
        logging.info(f"Workout programado Garmin no disponible para {fecha}: {e}")
        return []
    items = data if isinstance(data, list) else ((data or {}).get("calendarItems") or (data or {}).get("items") or [])
    resultado = []
    for item in items:
        f = item.get("date") or item.get("calendarDate") or item.get("scheduledDate") or ""
        if f[:10] != fecha:
            continue
        nombre = item.get("workoutName") or item.get("title") or item.get("name") or item.get("eventName") or "Entrenamiento"
        tipo = item.get("sportType") or item.get("sport") or item.get("activityType") or ""
        if isinstance(tipo, dict):
            tipo = tipo.get("typeKey") or tipo.get("key") or ""
        duracion = item.get("estimatedDurationInSecs") or item.get("duration")
        resultado.append({"nombre": nombre, "tipo": str(tipo).lower(), "duracion_seg": duracion})
    return resultado


def _armar_prompt_matutino(fecha_ayer, fecha_hoy, dia_ayer, dia_hoy, peso_row,
                            actividades_ayer, actividades_semana, wellness_ayer, wellness_hoy,
                            workout_hoy_garmin, sesiones_hoy_plan, carga, progresion_reciente=None):
    """Fase 11: cambió el enfoque de 'analizar hoy a la noche' a 'analizar
    ayer + la semana + recomendar para hoy, listo a la mañana' -- correr a
    la noche no tenía sentido para el sueño (la noche todavía no pasó) ni
    para dar una recomendación accionable (el día ya se estaba yendo).
    Wellness se pide de DOS fechas: ayer (para Body Battery/estrés/pasos
    del día completo de ayer, que a la mañana siguiente ya está cerrado) y
    hoy (para el sueño, que Garmin asocia a la fecha en la que te
    despertaste -- la noche de ayer a hoy)."""
    partes = [f"Son las primeras horas de la mañana del {dia_hoy} {fecha_hoy}. "
              f"Analizá el día de ayer ({dia_ayer} {fecha_ayer}), la última semana, y dame "
              f"recomendaciones para hoy, de un atleta que entrena para Hyrox, con estos datos:\n"]

    if peso_row:
        if peso_row["fecha"] == fecha_hoy:
            origen = "cargado hoy"
        else:
            origen = f"último registrado, del {peso_row['fecha']}"
        partes.append(f"PESO CORPORAL: {peso_row['peso_kg']} kg ({origen}).")
        if peso_row.get("sensacion"):
            partes.append(f"Cómo se sintió ayer en general (1=mal, 5=muy bien): {peso_row['sensacion']}/5.")
        # Las 4 dimensiones son opcionales e independientes entre sí (Fer
        # puede cargar solo alguna) -- se arma una sola línea con las que
        # haya, en vez de una línea fija por cada una, para no llenar el
        # prompt de "sin datos" cuando no se cargó nada. El objetivo es que
        # el análisis pueda distinguir matices que "sensación" sola no
        # puede (ej. cansado pero de buen ánimo, o con energía pero
        # estresado) -- no es un diagnóstico de nada, es una señal más
        # para cruzar con el entrenamiento real del día.
        dimensiones = []
        if peso_row.get("energia"): dimensiones.append(f"energía {peso_row['energia']}/5")
        if peso_row.get("animo"): dimensiones.append(f"ánimo {peso_row['animo']}/5")
        if peso_row.get("estres"): dimensiones.append(f"estrés {peso_row['estres']}/5")
        if peso_row.get("motivacion"): dimensiones.append(f"motivación para entrenar {peso_row['motivacion']}/5")
        if dimensiones:
            partes.append("Detalle de cómo se sintió ayer: " + ", ".join(dimensiones) + ".")
        if peso_row.get("nota"):
            partes.append(f"Nota de ayer: {peso_row['nota']}")
    else:
        partes.append("PESO CORPORAL: sin registros todavía.")

    if actividades_ayer:
        partes.append(f"\nENTRENAMIENTOS DE AYER ({dia_ayer} {fecha_ayer}) -- {len(actividades_ayer)}:")
        for a in actividades_ayer:
            dur_min = round((a.get("duracion_seg") or 0) / 60)
            partes.append(
                f"- {a.get('nombre','')} ({a.get('tipo','')}), {dur_min} min, "
                f"FC media {a.get('fc_media') or '—'}, {a.get('calorias') or '—'} kcal."
                + (f" Nota real: {a['nota_real']}" if a.get("nota_real") else ""))
    else:
        partes.append(f"\nENTRENAMIENTOS DE AYER ({dia_ayer} {fecha_ayer}): ninguno registrado (descanso o sin sincronizar).")

    if wellness_ayer:
        partes.append(
            f"\nACTIVIDAD Y RECUPERACIÓN DE AYER (Garmin, día completo): "
            f"Body Battery {wellness_ayer.get('body_battery_min','—')}-{wellness_ayer.get('body_battery_max','—')}, "
            f"estrés medio {wellness_ayer.get('stress_avg','—')}, FC en reposo {wellness_ayer.get('resting_hr','—')}, "
            f"{wellness_ayer.get('total_steps','—')} pasos, {wellness_ayer.get('active_kcal','—')} kcal activas, "
            f"{wellness_ayer.get('moderate_min','—')} min moderada + {wellness_ayer.get('vigorous_min','—')} min vigorosa.")
    if wellness_hoy and wellness_hoy.get("sleep_seg"):
        partes.append(
            f"SUEÑO (noche de ayer a hoy): {round((wellness_hoy.get('sleep_seg') or 0)/3600,1)}h, "
            f"score {wellness_hoy.get('sleep_score','—')}, HRV {wellness_hoy.get('hrv_avg_ms','—')}ms "
            f"({wellness_hoy.get('hrv_status','—')}).")
    if not wellness_ayer and not (wellness_hoy and wellness_hoy.get("sleep_seg")):
        partes.append("\nWELLNESS: no disponible (el reloj no lo reporta, o no se pudo sincronizar).")

    if actividades_semana:
        dias_con_sesion = len({(a.get("fecha") or "")[:10] for a in actividades_semana})
        partes.append(f"\nÚLTIMA SEMANA (últimos 7 días): {len(actividades_semana)} sesión(es) en {dias_con_sesion} día(s) distintos.")
        for a in actividades_semana:
            dur_min = round((a.get("duracion_seg") or 0) / 60)
            partes.append(f"- {(a.get('fecha') or '')[:10]}: {a.get('nombre','')} ({a.get('tipo','')}), {dur_min} min")
    else:
        partes.append("\nÚLTIMA SEMANA: sin entrenamientos registrados en los últimos 7 días.")

    if carga:
        partes.append(
            f"\nPLAN SEMANAL (periodización interna): semana {carga['semana_num']}"
            f"{' (DESCARGA)' if carga['es_descarga'] else ''}, objetivo: {carga.get('objetivo','')}. "
            f"Completadas {carga['completadas']}/{carga['planificadas']} sesiones planificadas esta semana.")

    # "Planificado para hoy" tiene DOS fuentes posibles: el calendario real
    # de Garmin Connect (workout_hoy_garmin -- lo que efectivamente carga
    # el usuario/coach ahí, la fuente más confiable en la práctica) y
    # nuestro plan interno de 14 semanas (sesiones_hoy_plan). Se muestran
    # ambas si existen; si ninguna tiene nada, se dice explícitamente para
    # que la IA no tenga que inventar una sesión (bug real encontrado en
    # Fase 11: sin datos, la IA describió una sesión de running con
    # estructura y duración específicas que no existía en ningún lado).
    hay_algo_hoy = bool(workout_hoy_garmin) or bool(sesiones_hoy_plan)
    if workout_hoy_garmin:
        partes.append(f"\nPROGRAMADO HOY EN GARMIN CONNECT ({dia_hoy} {fecha_hoy}) -- esta es la fuente real:")
        for w in workout_hoy_garmin:
            dur_min = round((w.get("duracion_seg") or 0) / 60) if w.get("duracion_seg") else None
            partes.append(f"- {w.get('nombre','')} ({w.get('tipo','')})" + (f", {dur_min} min estimados" if dur_min else ""))
    if sesiones_hoy_plan:
        partes.append(f"\nADEMÁS, EN EL PLAN INTERNO DE 14 SEMANAS PARA HOY:")
        for s in sesiones_hoy_plan:
            partes.append(f"- {s.get('turno','')}: {s.get('descripcion','')}" +
                          (f" ({s['notas']})" if s.get("notas") else ""))
    if not hay_algo_hoy:
        partes.append(
            f"\nPROGRAMADO PARA HOY ({dia_hoy} {fecha_hoy}): NO hay ninguna sesión cargada, ni en Garmin "
            f"Connect ni en el plan interno. No hay ninguna sesión real que analizar hoy.")

    if progresion_reciente:
        resumen = progresion_reciente.get("respuesta", "")[:600]
        partes.append(
            f"\nÚLTIMO ANÁLISIS DE PROGRESIÓN (del {progresion_reciente.get('creado','')[:10]}, hecho aparte, "
            f"mirando una racha de sesiones del mismo tipo): {resumen}"
            f"{'...' if len(progresion_reciente.get('respuesta',''))>600 else ''}\n"
            f"Si es relevante, podés referenciarlo en tu análisis (ej. si confirma o contradice una tendencia), "
            f"pero no es obligatorio si no aporta a lo de hoy.")

    partes.append(
        "\nDame TRES cosas, cada una en su propio párrafo corto:\n"
        "1) Análisis del entrenamiento y la recuperación de AYER.\n"
        "2) Análisis de cómo viene la ÚLTIMA SEMANA (volumen, consistencia, señales de sobrecarga o buen progreso).\n"
        "3) Sobre HOY: tu trabajo es EVALUAR la sesión que ya está programada (arriba), no proponer una "
        "sesión distinta. Confirmá si conviene hacerla tal cual está, o avisá si ayer/la semana muestran una "
        "señal de riesgo concreta (mala recuperación, sobrecarga, síntomas) que justifique ajustar intensidad, "
        "volumen, o directamente descansar en su lugar. Si NO hay ninguna sesión programada hoy (ver arriba), "
        "decilo explícitamente -- NO describas ni evalúes una sesión específica que no te di, como mucho opiná "
        "en términos generales si conviene entrenar algo liviano o descansar, sin inventar tipo/duración/estructura.\n"
        "No inventes datos que no te di -- ni sesiones, ni duraciones, ni estructuras de entrenamiento. "
        "Si falta información para opinar sobre algo, decilo en vez de asumir.")
    return "\n".join(partes)


def _llamar_ia_haiku(prompt, api_key):
    """Mismo patrón (urllib directo, sin SDK) que ya usa training.py para
    analizarSesion/analizarProgresion -- se replica acá en vez de importar
    esas funciones, que están atadas a devolver un jsonify de Flask."""
    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001", "max_tokens": 1024,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=payload,
        headers={"Content-Type": "application/json", "x-api-key": api_key, "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read())
    return result["content"][0]["text"]


def _analisis_matutino_diario():
    """Análisis diario con IA (Fase 11) -- corre a la madrugada (5hs, se
    chequea cada hora, dedup por fecha vía garmin_config) para estar listo
    cuando te despertás: analiza el entrenamiento y la recuperación de
    AYER, cómo viene la ÚLTIMA SEMANA, y da recomendaciones para lo
    planificado HOY. Antes corría a las 22hs analizando "hoy" -- eso no
    tenía sentido para el sueño (la noche todavía no había pasado) ni para
    dar una recomendación accionable (el día ya se estaba yendo).

    Sincroniza los entrenamientos de AYER antes de armar el análisis
    (_sincronizar_actividades_dia) -- si una sesión de anoche no se
    sincronizó todavía, esto la trae. Una sola conexión a Garmin para todo
    el job (sync actividades + wellness + peso)."""
    while True:
        try:
            ahora = datetime.now()
            hoy = ahora.date().isoformat()
            ayer_date = ahora.date() - timedelta(days=1)
            ayer = ayer_date.isoformat()
            dia_hoy = DIAS_ORDER[ahora.date().isoweekday() - 1]
            dia_ayer = DIAS_ORDER[ayer_date.isoweekday() - 1]

            if ahora.hour == 5:
                with get_db(HIST_DB, row_factory=True) as con:
                    row = con.execute(
                        "SELECT valor FROM garmin_config WHERE clave='ultimo_analisis_diario_fecha'").fetchone()
                    ya_corrio_hoy = row and row["valor"] == hoy

                if not ya_corrio_hoy:
                    api_key = _api_key()
                    if not api_key:
                        logging.warning("Análisis diario: sin API key configurada, se salta hoy.")
                    else:
                        client = None
                        try:
                            client = _conectar_garmin()
                        except Exception as e:
                            logging.info(f"Análisis diario: no se pudo conectar a Garmin ({e}); "
                                         f"sigue con lo que ya haya en la base.")

                        if client:
                            try:
                                nuevas = _sincronizar_actividades_dia(client, ayer)
                                if nuevas:
                                    logging.info(f"Análisis diario: sincronizadas {nuevas} actividad(es) de ayer.")
                            except Exception:
                                logging.exception("Análisis diario: fall\u00f3 el sync de actividades de ayer")

                        actividades_ayer = [a for a in get_actividades(limit=20) if (a.get("fecha") or "").startswith(ayer)]
                        desde_semana = (ayer_date - timedelta(days=6)).isoformat()
                        actividades_semana = [a for a in get_actividades(limit=50) if (a.get("fecha") or "")[:10] >= desde_semana]

                        # Wellness de DOS fechas: ayer (Body Battery/estrés/pasos
                        # del día completo, ya cerrado) y hoy (para el sueño --
                        # Garmin lo asocia a la fecha en la que te despertaste).
                        wellness_ayer = wellness_hoy = None
                        if client:
                            try:
                                wellness_ayer = _sincronizar_wellness_dia(client, ayer)
                            except Exception as e:
                                logging.info(f"Análisis diario: wellness de ayer no disponible ({e}).")
                            try:
                                wellness_hoy = _sincronizar_wellness_dia(client, hoy)
                            except Exception as e:
                                logging.info(f"Análisis diario: wellness de hoy (sueño) no disponible ({e}).")

                        # Peso: de Garmin (hoy si hay, si no el último
                        # registrado ahí); si Garmin no devuelve nada, se cae
                        # a lo último que haya en nuestra base.
                        peso_row = None
                        if client:
                            try:
                                fecha_peso, peso_kg, _raw = _obtener_peso_garmin(client, hoy)
                                if peso_kg:
                                    peso_row = {"fecha": fecha_peso, "peso_kg": peso_kg, "sensacion": None,
                                                "nota": "", "energia": None, "animo": None,
                                                "estres": None, "motivacion": None}
                            except Exception as e:
                                logging.info(f"Análisis diario: peso de Garmin no disponible ({e}).")
                        if not peso_row:
                            with get_db(HIST_DB, row_factory=True) as con:
                                fila = con.execute(
                                    "SELECT * FROM entrenamiento_peso WHERE fecha <= ? AND peso_kg IS NOT NULL "
                                    "ORDER BY fecha DESC LIMIT 1", (hoy,)).fetchone()
                                peso_row = dict(fila) if fila else None
                        # sensación/nota de AYER (carga manual) se suman al
                        # peso -- son cosas independientes, ver
                        # api_training_peso_guardar. energia/animo/estres/
                        # motivacion son las 4 dimensiones que desglosan
                        # "sensación" (Fase 12, a pedido -- cruzar bienestar
                        # subjetivo con el entrenamiento en el análisis
                        # nocturno).
                        with get_db(HIST_DB, row_factory=True) as con:
                            fila_ayer = con.execute(
                                "SELECT sensacion, nota, energia, animo, estres, motivacion "
                                "FROM entrenamiento_peso WHERE fecha=?", (ayer,)).fetchone()
                            if fila_ayer and peso_row:
                                for _campo in ("sensacion", "nota", "energia", "animo", "estres", "motivacion"):
                                    peso_row[_campo] = fila_ayer[_campo]

                        carga = _carga_semana_actual()
                        sesiones_hoy_plan = _sesiones_planificadas_dia(carga["semana_num"], dia_hoy) if carga else []
                        workout_hoy_garmin = []
                        if client:
                            try:
                                workout_hoy_garmin = _workout_programado_garmin(client, hoy)
                            except Exception as e:
                                logging.info(f"Análisis diario: no se pudo traer el calendario de Garmin ({e}).")

                        # Análisis de Progresión más reciente, si es de los
                        # últimos 14 días -- así el análisis diario puede
                        # construir sobre lo que ya se vio ahí (antes eran
                        # dos sistemas de IA que nunca se cruzaban).
                        progresion_reciente = None
                        try:
                            ultimos = get_analisis(tipo="progresion")
                            if ultimos:
                                creado = ultimos[0].get("creado", "")
                                if creado and (ahora.date() - datetime.strptime(creado[:10], "%Y-%m-%d").date()).days <= 14:
                                    progresion_reciente = ultimos[0]
                        except Exception:
                            logging.exception("Análisis diario: no se pudo leer el último análisis de progresión")

                        prompt = _armar_prompt_matutino(ayer, hoy, dia_ayer, dia_hoy, peso_row,
                                                         actividades_ayer, actividades_semana,
                                                         wellness_ayer, wellness_hoy,
                                                         workout_hoy_garmin, sesiones_hoy_plan, carga,
                                                         progresion_reciente)
                        try:
                            respuesta = _llamar_ia_haiku(prompt, api_key)
                            guardar_analisis({"tipo": "diario", "fecha_desde": ayer, "fecha_hasta": hoy,
                                               "prompt_usado": prompt, "respuesta": respuesta})
                            notificar_telegram(f"☀️ Análisis del día ({dia_ayer} {ayer} → hoy {dia_hoy}):\n\n{respuesta[:3500]}")
                        except Exception:
                            logging.exception("Análisis diario: fall\u00f3 la llamada a la IA")

                        with get_db(HIST_DB) as con:
                            con.execute(
                                "INSERT INTO garmin_config (clave,valor,modificado) VALUES "
                                "('ultimo_analisis_diario_fecha',?,datetime('now')) "
                                "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor, modificado=excluded.modificado",
                                (hoy,))
        except Exception:
            logging.exception("Error en el análisis diario matutino")
        threading.Event().wait(3600)  # revisa cada hora, corre solo a las 5hs

threading.Thread(target=_analisis_matutino_diario, daemon=True).start()

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
    with get_db(HIST_DB, row_factory=True) as con:
        rows = [dict(r) for r in con.execute(
            "SELECT semana_num, fecha_inicio, fase, es_descarga, objetivo FROM entrenamiento_semanas ORDER BY semana_num"
        ).fetchall()]
    return jsonify({"ok": True, "rows": rows, "actual": _semana_actual()})

@training_bp.route("/api/training/sesion/<sid>", methods=["PUT"])
@login_required
@modulo_required("training")
def api_sesion_update(sid):
    data = request.json or {}
    with get_db(HIST_DB) as con:
        con.execute(
            "UPDATE entrenamiento_plan SET descripcion=?, notas=?, modificado=datetime('now') WHERE id=?",
            (data.get("descripcion",""), data.get("notas",""), sid))
    return jsonify({"ok": True})

@training_bp.route("/api/training/sesion", methods=["POST"])
@login_required
@modulo_required("training")
def api_sesion_nueva():
    data = request.json or {}
    sid = str(uuid.uuid4())[:12]
    with get_db(HIST_DB, row_factory=True) as con:
        sem = con.execute("SELECT * FROM entrenamiento_semanas WHERE semana_num=?",
                          (data.get("semana_num"),)).fetchone()
        if not sem:
            return jsonify({"ok": False, "error": "Semana inválida"})
        con.execute(
            "INSERT INTO entrenamiento_plan (id,semana_num,fecha_inicio,fase,es_descarga,dia_semana,turno,descripcion,notas) VALUES (?,?,?,?,?,?,?,?,?)",
            (sid, data["semana_num"], sem["fecha_inicio"], sem["fase"], sem["es_descarga"],
             data.get("dia_semana",""), data.get("turno","mañana"), data.get("descripcion",""), data.get("notas","")))
    return jsonify({"ok": True, "id": sid})

@training_bp.route("/api/training/sesion/<sid>", methods=["DELETE"])
@login_required
@modulo_required("training")
def api_sesion_delete(sid):
    with get_db(HIST_DB) as con:
        con.execute("DELETE FROM entrenamiento_plan WHERE id=?", (sid,))
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
    with get_db(HIST_DB) as con:
        con.execute(
            "INSERT INTO entrenamiento_log (id,fecha,tipo,descripcion,duracion_min,notas,completado,garmin_id,plan_id) VALUES (?,?,?,?,?,?,?,?,?)",
            (lid, data.get("fecha"), data.get("tipo"), data.get("descripcion"),
             data.get("duracion_min"), data.get("notas",""), data.get("completado",1),
             data.get("garmin_id"), data.get("plan_id")))
    return jsonify({"ok": True, "id": lid})

@training_bp.route("/api/training/log/<lid>", methods=["DELETE"])
@login_required
@modulo_required("training")
def api_log_delete(lid):
    with get_db(HIST_DB) as con:
        con.execute("DELETE FROM entrenamiento_log WHERE id=?", (lid,))
    return jsonify({"ok": True})

# ── Glosario de ejercicios ───────────────────────────────────────────────────
@training_bp.route("/api/training/glosario", methods=["GET"])
@login_required
@modulo_required("training")
def api_glosario_list():
    with get_db(HIST_DB, row_factory=True) as con:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM entrenamiento_glosario ORDER BY orden, alias").fetchall()]
    return jsonify({"ok": True, "rows": rows})

@training_bp.route("/api/training/glosario", methods=["POST"])
@login_required
@modulo_required("training")
def api_glosario_create():
    data = request.json or {}
    alias = (data.get("alias") or "").strip()
    termino = (data.get("termino_estandar") or "").strip()
    if not alias or not termino:
        return jsonify({"ok": False, "error": "Falta el alias o el término estándar"})
    with get_db(HIST_DB, row_factory=True) as con:
        max_orden = con.execute("SELECT MAX(orden) FROM entrenamiento_glosario").fetchone()[0] or 0
        con.execute("INSERT INTO entrenamiento_glosario (alias, termino_estandar, notas, orden) VALUES (?,?,?,?)",
            (alias, termino, data.get("notas",""), max_orden + 1))
    return jsonify({"ok": True})

@training_bp.route("/api/training/glosario/<int:gid>", methods=["PUT"])
@login_required
@modulo_required("training")
def api_glosario_update(gid):
    data = request.json or {}
    with get_db(HIST_DB) as con:
        fields = []; params = []
        for f in ["alias", "termino_estandar", "notas"]:
            if f in data: fields.append(f + "=?"); params.append(data[f])
        if fields:
            params.append(gid)
            con.execute("UPDATE entrenamiento_glosario SET " + ", ".join(fields) + " WHERE id=?", params)
    return jsonify({"ok": True})

@training_bp.route("/api/training/glosario/<int:gid>", methods=["DELETE"])
@login_required
@modulo_required("training")
def api_glosario_delete(gid):
    with get_db(HIST_DB) as con:
        con.execute("DELETE FROM entrenamiento_glosario WHERE id=?", (gid,))
    return jsonify({"ok": True})

def _glosario_ejercicios_texto():
    """Arma el bloque de contexto 'alias -> término estándar' para inyectar
    en el prompt de la IA cuando analiza una rutina — ver nota en
    init_training_db() sobre por qué esto y no un buscar/reemplazar."""
    with get_db(HIST_DB, row_factory=True) as con:
        rows = con.execute(
            "SELECT alias, termino_estandar, notas FROM entrenamiento_glosario ORDER BY orden, alias").fetchall()
    if not rows:
        return ""
    lineas = [f"- \"{r['alias']}\" = {r['termino_estandar']}" + (f" ({r['notas']})" if r["notas"] else "")
              for r in rows]
    return ("\n\nGLOSARIO DE EJERCICIOS (así los nombra Fer/el gimnasio — usá el término "
            "estándar entre paréntesis para interpretar la rutina):\n" + "\n".join(lineas))

# ── Rutinas manuales (texto libre, ejercicios que Garmin no registra) ───────
@training_bp.route("/api/training/rutinas_manuales", methods=["GET"])
@login_required
@modulo_required("training")
def api_rutinas_manuales_list():
    with get_db(HIST_DB, row_factory=True) as con:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM entrenamiento_rutinas_manuales ORDER BY fecha DESC, creado DESC LIMIT 100").fetchall()]
    return jsonify({"ok": True, "rows": rows})

@training_bp.route("/api/training/rutinas_manuales", methods=["POST"])
@login_required
@modulo_required("training")
def api_rutinas_manuales_create():
    data = request.json or {}
    texto = (data.get("texto") or "").strip()
    if not texto:
        return jsonify({"ok": False, "error": "La rutina no puede estar vacía"})
    rid = str(uuid.uuid4())[:12]
    with get_db(HIST_DB) as con:
        con.execute(
            "INSERT INTO entrenamiento_rutinas_manuales (id, fecha, titulo, texto) VALUES (?,?,?,?)",
            (rid, data.get("fecha") or datetime.now().strftime("%Y-%m-%d"),
             (data.get("titulo") or "").strip(), texto))
    return jsonify({"ok": True, "id": rid})

@training_bp.route("/api/training/rutinas_manuales/<rid>", methods=["DELETE"])
@login_required
@modulo_required("training")
def api_rutinas_manuales_delete(rid):
    with get_db(HIST_DB) as con:
        con.execute("DELETE FROM entrenamiento_rutinas_manuales WHERE id=?", (rid,))
    return jsonify({"ok": True})

@training_bp.route("/api/training/rutinas_manuales/<rid>/analizar", methods=["POST"])
@login_required
@modulo_required("training")
def api_rutinas_manuales_analizar(rid):
    """Manda una rutina manual ya guardada a la IA, con el glosario de
    ejercicios como contexto para que interprete bien la jerga."""
    key = _api_key()
    if not key:
        return jsonify({"ok": False, "error": "API key no configurada"})
    with get_db(HIST_DB, row_factory=True) as con:
        rutina = con.execute("SELECT * FROM entrenamiento_rutinas_manuales WHERE id=?", (rid,)).fetchone()
    if not rutina:
        return jsonify({"ok": False, "error": "Rutina no encontrada"})

    prompt = f"""Sos un asistente de entrenamiento deportivo para un triatleta/Hyrox competidor.
Te paso una rutina que se cargó a mano (ejercicios que el reloj/app no registra automáticamente).
{_glosario_ejercicios_texto()}

RUTINA ({rutina["fecha"]}{f' — {rutina["titulo"]}' if rutina["titulo"] else ''}):
{rutina["texto"]}

Interpretá la rutina usando el glosario de arriba y dame un análisis breve: qué trabajó
(grupos musculares / capacidades), volumen aproximado, y alguna observación útil para el
seguimiento del plan. Respondé en español, de forma concisa y práctica."""

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
    with get_db(HIST_DB, row_factory=True) as con:
        total_plan = con.execute("SELECT COUNT(*) FROM entrenamiento_plan").fetchone()[0]
        total_log  = con.execute("SELECT COUNT(*) FROM entrenamiento_log").fetchone()[0]
        completadas = con.execute("SELECT COUNT(*) FROM entrenamiento_log WHERE completado=1").fetchone()[0]
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
    with get_db(HIST_DB, row_factory=True) as con:
        rows = con.execute(
            "SELECT id, nombre, tipo, duracion_seg, distancia_m, fc_media, calorias "
            "FROM garmin_actividades WHERE DATE(fecha) = ? ORDER BY fecha",
            (fecha,)
        ).fetchall()
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
        os.makedirs(GARMIN_TOKENSTORE, exist_ok=True)
        client = Garmin(g_user, g_pass)
        client.login(tokenstore=GARMIN_TOKENSTORE)
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
    with get_db(HIST_DB, row_factory=True) as con:
        row = con.execute("SELECT archivo_path, fecha FROM garmin_actividades WHERE id=?", (act_id,)).fetchone()
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

