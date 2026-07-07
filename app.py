"""
CosmoTools — app.py v2
Plataforma de herramientas DI REPA / ARCA
"""

import os, sqlite3, io, uuid, threading, bcrypt, logging, subprocess, json, tempfile, re, secrets, time, shutil
import xml.etree.ElementTree as ET
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, date
from functools import wraps
from flask import render_template, request, jsonify, send_file, session, redirect, url_for

# app, csrf y limiter viven en core.py — ahí también los necesitan los
# blueprints, y así evitamos que "limiter" no exista todavía cuando se
# importa un blueprint que lo usa en un decorador de ruta.
from core import app, csrf, limiter

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

def notificar_telegram(msg: str):
    """Envía un mensaje al bot de Telegram. No rompe el flujo si falla.
    Corre en un hilo aparte para no bloquear la respuesta al usuario mientras
    espera la llamada de red (hasta 3s si Telegram está lento/caído)."""
    if not TELEGRAM_TOKEN:
        return
    def _enviar():
        try:
            urllib.request.urlopen(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
                f"?chat_id={TELEGRAM_CHAT_ID}&text=" + urllib.parse.quote(msg),
                timeout=3
            )
        except Exception:
            pass
    _threading.Thread(target=_enviar, daemon=True).start()

# ── Blueprints ─────────────────────────────────────────────────────────────────
# Extraídos de app.py en la Fase 2 de profesionalización (separar los ~200
# endpoints en módulos por área). Siguen: vua, senasa, finanzas — de a uno,
# corriendo los tests entre cada extracción.
from blueprints.stock import stock_bp
app.register_blueprint(stock_bp)
from blueprints.training import training_bp
app.register_blueprint(training_bp)
from blueprints.vua import vua_bp
app.register_blueprint(vua_bp)
from blueprints.senasa import senasa_bp
app.register_blueprint(senasa_bp)

# ── Config ─────────────────────────────────────────────────────────────────────
from core import (
    HIST_DB, DB_PATH, OUTPUT_FOLDER, STOCK_REPORTS_DIR,
    login_required, admin_required, modulo_required, finanzas_owner_required,
    tiene_permiso_admin, registrar_sesion, actualizar_sesion, token_revocado,
)

# get_api_key ahora se importa de core.py (más abajo, junto con contexto_repositorio)
APP_USER      = os.environ.get("APP_USER",    "cosmo")
APP_PASS      = os.environ.get("APP_PASS",    "")
APP_USER2     = os.environ.get("APP_USER2",   "")
APP_PASS2     = os.environ.get("APP_PASS2",   "")
os.makedirs(OUTPUT_FOLDER, exist_ok=True)
os.makedirs("/data/minutas", exist_ok=True)
os.makedirs("/tmp/sintia_uploads", exist_ok=True)
os.makedirs(STOCK_REPORTS_DIR, exist_ok=True)

# ── Auth: login_required/admin_required/modulo_required/finanzas_owner_required
# ahora viven en core.py (los necesitan también los blueprints) ────────────────
def check_password(plain, hashed):
    try: return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception: return False  # nunca comparar en texto plano

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(filename="/data/accesos.log", level=logging.INFO,
    format="%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

# ── Context processor ──────────────────────────────────────────────────────────
@app.context_processor
def inject_session_vars():
    return {
        "modulos": session.get("modulos", []),
        "user_role": session.get("role", ""),
    }

# ── Job queue: job_status, job_create, job_get, _job_persist, _JobLog ahora
# viven en core.py (los usan también los blueprints, ej. vua) ──────────────
import threading as _threading

# ── Recordatorio de servicios recurrentes (finanzas) ────────────────────────────
def _mes_anterior_str(mes):
    a, mm = int(mes[:4]), int(mes[5:7]) - 1
    if mm <= 0:
        mm += 12; a -= 1
    return f"{a:04d}-{mm:02d}"

def _buscar_match_servicio(con, patrones, mes):
    if not patrones:
        return None
    condiciones = " OR ".join(["LOWER(descripcion) LIKE ?"] * len(patrones))
    params = [f"%{p.lower()}%" for p in patrones]
    return con.execute(
        f"SELECT fecha, descripcion, monto_ars FROM fin_movimientos "
        f"WHERE substr(fecha,1,7)=? AND ({condiciones}) ORDER BY fecha LIMIT 1",
        [mes] + params).fetchone()

def _estado_servicios_mes(con, mes):
    """Para cada servicio activo, busca si hay un movimiento de ese mes cuya
    descripción coincide con alguno de sus patrones (o si fue marcado pagado
    a mano cuando no hay coincidencia automática). También compara el monto
    contra el del mes anterior, para marcar posibles aumentos de tarifa."""
    con.row_factory = sqlite3.Row
    servicios = con.execute(
        "SELECT * FROM fin_servicios WHERE activo=1 ORDER BY orden").fetchall()
    mes_ant = _mes_anterior_str(mes)
    resultado = []
    for s in servicios:
        patrones = [p.strip() for p in (s["patron"] or "").split(",") if p.strip()]
        match = _buscar_match_servicio(con, patrones, mes)
        match_ant = _buscar_match_servicio(con, patrones, mes_ant)
        manual = con.execute(
            "SELECT pagado, fecha_pago FROM fin_servicios_pagos WHERE servicio_id=? AND mes=?",
            (s["id"], mes)).fetchone()
        pagado_manual = bool(manual and manual["pagado"])
        variacion_pct = None
        if match and match_ant and match_ant["monto_ars"] > 0:
            variacion_pct = round((match["monto_ars"] - match_ant["monto_ars"]) / match_ant["monto_ars"] * 100, 1)
        resultado.append({
            "id": s["id"], "nombre": s["nombre"], "patron": s["patron"],
            "pagado": bool(match) or pagado_manual,
            "automatico": bool(match),
            "movimiento": dict(match) if match else None,
            "pagado_manual": pagado_manual,
            "monto_mes_anterior": match_ant["monto_ars"] if match_ant else None,
            "variacion_pct": variacion_pct,
            "posible_aumento_tarifa": variacion_pct is not None and variacion_pct >= 15,
        })
    return resultado

def _chequear_recordatorio_servicios():
    while True:
        try:
            hoy = datetime.now()
            if hoy.day == 2:
                mes = hoy.strftime("%Y-%m")
                con = sqlite3.connect(HIST_DB)
                row = con.execute("SELECT valor FROM fin_servicios_estado WHERE clave='ultimo_aviso_mes'").fetchone()
                if not (row and row[0] == mes):
                    estado = _estado_servicios_mes(con, mes)
                    pendientes = [s["nombre"] for s in estado if not s["pagado"]]
                    if pendientes:
                        lista = "\n".join(f"• {n}" for n in pendientes)
                        notificar_telegram(f"💸 Sin pago detectado este mes ({mes}):\n\n{lista}")
                    con.execute(
                        "INSERT INTO fin_servicios_estado (clave, valor) VALUES ('ultimo_aviso_mes', ?) "
                        "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor", (mes,))
                    con.commit()
                con.close()
        except Exception:
            logging.exception("Error en chequeo de recordatorio de servicios")
        _threading.Event().wait(3600)  # revisa cada hora

_threading.Thread(target=_chequear_recordatorio_servicios, daemon=True).start()

# ── Backup semanal ───────────────────────────────────────────────────────────
BACKUP_DIR = "/data/backups"
os.makedirs(BACKUP_DIR, exist_ok=True)
BACKUP_FUENTES = {"pad": lambda: DB_PATH, "historial": lambda: HIST_DB}

def _ejecutar_backup(origen="manual"):
    import shutil, json as _json
    resultados = {}
    for clave, get_path in BACKUP_FUENTES.items():
        src = get_path()
        dest = os.path.join(BACKUP_DIR, f"{clave}_backup.db")
        try:
            if not os.path.exists(src):
                raise FileNotFoundError("el archivo origen no existe")
            shutil.copy2(src, dest)
            resultados[clave] = {"ok": True, "size_mb": round(os.path.getsize(dest) / (1024 * 1024), 2)}
        except Exception as e:
            resultados[clave] = {"ok": False, "error": str(e)}
    con = sqlite3.connect(HIST_DB)
    con.execute("INSERT INTO backups (origen, resultados) VALUES (?,?)", (origen, _json.dumps(resultados)))
    con.commit(); con.close()
    todo_ok = all(r["ok"] for r in resultados.values())
    logging.info(f"BACKUP {origen} | ok={todo_ok} | {resultados}")
    if todo_ok:
        notificar_telegram(f"💾 Backup {origen} OK — " + ", ".join(f"{k}: {v['size_mb']} MB" for k, v in resultados.items()))
    else:
        errores = ", ".join(f"{k}: {v.get('error')}" for k, v in resultados.items() if not v["ok"])
        notificar_telegram(f"⚠️ Backup {origen} con errores — {errores}")
    return resultados, todo_ok

def _proximo_backup_domingo():
    hoy = datetime.now()
    dias_hasta_domingo = (6 - hoy.weekday()) % 7
    candidato = (hoy + timedelta(days=dias_hasta_domingo)).replace(hour=2, minute=0, second=0, microsecond=0)
    if candidato <= hoy:
        candidato += timedelta(days=7)
    return candidato.strftime("%Y-%m-%d %H:%M")

def _chequear_backup_automatico():
    while True:
        try:
            hoy = datetime.now()
            if hoy.weekday() == 6 and hoy.hour == 2:  # domingo 02:xx
                semana = hoy.strftime("%Y-W%W")
                con = sqlite3.connect(HIST_DB)
                row = con.execute(
                    "SELECT fecha FROM backups WHERE origen='auto' ORDER BY fecha DESC LIMIT 1").fetchone()
                ya_corrio_esta_semana = row and datetime.strptime(row[0][:19], "%Y-%m-%d %H:%M:%S").strftime("%Y-W%W") == semana
                con.close()
                if not ya_corrio_esta_semana:
                    _ejecutar_backup(origen="auto")
        except Exception:
            logging.exception("Error en backup automático semanal")
        _threading.Event().wait(1800)  # revisa cada 30 min

_threading.Thread(target=_chequear_backup_automatico, daemon=True).start()

# ── Repositorio de documentos por módulo (contexto para la IA) ─────────────────
# get_api_key, contexto_repositorio, _extraer_texto_docx,
# _extraer_cronologia_de_texto, MODULOS_REPOSITORIO, MODULOS_CON_CRONOLOGIA,
# _normalizar_fecha_a_ddmmaaaa y _validar_fecha_ddmmaaaa ahora viven en
# core.py (los usan también los blueprints, ej. vua).
from core import (
    get_api_key, contexto_repositorio, _extraer_texto_docx,
    _extraer_cronologia_de_texto, MODULOS_REPOSITORIO, MODULOS_CON_CRONOLOGIA,
    _normalizar_fecha_a_ddmmaaaa, _validar_fecha_ddmmaaaa,
    job_status, job_create, job_get, _job_persist, _JobLog,
)

def _agregar_entradas_cronologia(con, tabla, entradas, fuente):
    max_orden = con.execute(f"SELECT MAX(orden) FROM {tabla}").fetchone()[0] or 0
    agregadas = 0
    for e in entradas:
        actividad = (e.get("actividad") or "").strip()
        if not actividad:
            continue
        max_orden += 1
        fecha = _normalizar_fecha_a_ddmmaaaa(e.get("fecha", ""))
        con.execute(
            f"INSERT INTO {tabla} (fecha, actividad, participantes, estado, orden, creado, modificado) "
            f"VALUES (?,?,?,?,?,datetime('now'),datetime('now'))",
            (fecha, f"{actividad} (fuente: {fuente})",
             e.get("participantes", ""), "Pendiente", max_orden))
        agregadas += 1
    return agregadas


@app.route("/api/repositorio/<modulo>", methods=["GET"])
@login_required
def repositorio_list(modulo):
    if modulo not in MODULOS_REPOSITORIO or modulo not in session.get("modulos", []):
        return jsonify({"ok": False, "error": "Módulo no habilitado"}), 403
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT id, nombre_archivo, subido_por, creado, length(contenido) as tamano "
        "FROM doc_repositorio WHERE modulo=? ORDER BY creado DESC", (modulo,)).fetchall()]
    con.close()
    return jsonify({"ok": True, "rows": rows})

@app.route("/api/repositorio/<modulo>", methods=["POST"])
@login_required
def repositorio_upload(modulo):
    if modulo not in MODULOS_REPOSITORIO or modulo not in session.get("modulos", []):
        return jsonify({"ok": False, "error": "Módulo no habilitado"}), 403
    if "archivo" not in request.files:
        return jsonify({"ok": False, "error": "No se recibió archivo"})
    f = request.files["archivo"]
    if not f.filename.lower().endswith(".docx"):
        return jsonify({"ok": False, "error": "Por ahora solo se aceptan archivos Word (.docx)"})
    data_bytes = f.read()
    try:
        texto = _extraer_texto_docx(io.BytesIO(data_bytes))
    except Exception as e:
        return jsonify({"ok": False, "error": f"No se pudo leer el documento: {e}"})
    if not texto.strip():
        return jsonify({"ok": False, "error": "El documento no tiene texto extraíble"})
    repo_dir = os.path.join("/data/repositorio", modulo)
    os.makedirs(repo_dir, exist_ok=True)
    ruta_archivo = os.path.join(repo_dir, f"{uuid.uuid4().hex[:12]}_{f.filename}")
    with open(ruta_archivo, "wb") as out:
        out.write(data_bytes)
    con = sqlite3.connect(HIST_DB)
    con.execute("INSERT INTO doc_repositorio (modulo, nombre_archivo, contenido, ruta_archivo, subido_por) VALUES (?,?,?,?,?)",
        (modulo, f.filename, texto, ruta_archivo, session.get("username", "?")))
    con.commit()
    agregadas_cronologia = 0
    error_cronologia = None
    if modulo in MODULOS_CON_CRONOLOGIA:
        try:
            entradas = _extraer_cronologia_de_texto(texto, modulo)
            agregadas_cronologia = _agregar_entradas_cronologia(
                con, MODULOS_CON_CRONOLOGIA[modulo], entradas, f.filename)
            con.commit()
        except Exception as e:
            logging.exception(f"Error extrayendo cronología de '{f.filename}' ({modulo})")
            error_cronologia = str(e)
    con.close()
    logging.info(f"REPOSITORIO UPLOAD | modulo={modulo} | user={session.get('username')} | archivo={f.filename} | cronologia+={agregadas_cronologia}")
    msg = f"📁 Documento '{f.filename}' agregado al repositorio de {modulo} por {session.get('username')}"
    if agregadas_cronologia:
        msg += f"\n🗓️ Se sumaron {agregadas_cronologia} hito(s) a la cronología."
    notificar_telegram(msg)
    return jsonify({"ok": True, "agregadas_cronologia": agregadas_cronologia, "error_cronologia": error_cronologia})

@app.route("/api/repositorio/<modulo>/<int:doc_id>", methods=["DELETE"])
@login_required
def repositorio_delete(modulo, doc_id):
    if modulo not in MODULOS_REPOSITORIO or modulo not in session.get("modulos", []):
        return jsonify({"ok": False, "error": "Módulo no habilitado"}), 403
    con = sqlite3.connect(HIST_DB)
    row = con.execute("SELECT ruta_archivo FROM doc_repositorio WHERE id=? AND modulo=?", (doc_id, modulo)).fetchone()
    con.execute("DELETE FROM doc_repositorio WHERE id=? AND modulo=?", (doc_id, modulo))
    con.commit(); con.close()
    if row and row[0] and os.path.exists(row[0]):
        try: os.remove(row[0])
        except Exception: pass
    return jsonify({"ok": True})

@app.route("/api/repositorio/<modulo>/<int:doc_id>/download")
@login_required
def repositorio_download(modulo, doc_id):
    if modulo not in MODULOS_REPOSITORIO or modulo not in session.get("modulos", []):
        return jsonify({"ok": False, "error": "Módulo no habilitado"}), 403
    con = sqlite3.connect(HIST_DB)
    row = con.execute("SELECT nombre_archivo, ruta_archivo FROM doc_repositorio WHERE id=? AND modulo=?",
                       (doc_id, modulo)).fetchone()
    con.close()
    if not row or not row[1] or not os.path.exists(row[1]):
        return jsonify({"ok": False, "error": "El archivo original ya no está disponible"}), 404
    return send_file(row[1], as_attachment=True, download_name=row[0])

def _limpiar_jobs_viejos():
    """Elimina jobs de más de 2 horas para evitar memory leak (en memoria y en SQLite)."""
    while True:
        time.sleep(3600)  # cada hora
        ahora = time.time()
        viejos = [k for k, v in list(job_status.items())
                  if v.get('_ts', ahora) < ahora - 7200]
        for k in viejos:
            job_status.pop(k, None)
        try:
            con = sqlite3.connect(HIST_DB, timeout=10)
            con.execute("DELETE FROM job_status_db WHERE ts < ?", (ahora - 7200,))
            con.commit(); con.close()
        except Exception:
            logging.exception("No se pudo limpiar job_status_db")
_threading.Thread(target=_limpiar_jobs_viejos, daemon=True).start()

# ── Historial DB ───────────────────────────────────────────────────────────────
# ── Seeds separados ────────────────────────────────────────────────────────────
def _seed_ref_dira(con):
    """Carga inicial de ref_dira a partir de las 8 direcciones regionales vigentes."""
    datos = [
        ('1', 'HIDROVIA', 1),
        ('2', 'NORESTE', 2),
        ('3', 'NOROESTE', 3),
        ('4', 'CENTRAL', 4),
        ('5', 'RIO COLORADO', 5),
        ('6', 'AUSTRAL', 6),
        ('7', 'CUYO', 7),
        ('8', 'No aplica', 8),
    ]
    con.executemany("INSERT OR REPLACE INTO ref_dira (indice, nombre, orden) VALUES (?,?,?)", datos)
    con.commit()

def _seed_ref_aduanas(con):
    """Carga inicial de ref_aduanas a partir del CSV de referencia oficial (Norte/Noroeste corregidos)."""
    datos = [
        ('093','RAFAELA','1'), ('062','SANTA FE','1'), ('057','SAN LORENZO','1'),
        ('069','VILLA CONSTITUCION','1'), ('052','ROSARIO','1'), ('016','CONCORDIA','1'),
        ('041','PARANA','1'), ('020','DIAMANTE','1'), ('013','COLON','1'),
        ('015','CONCEPCION DEL URUGUAY','1'), ('026','GUALEGUAYCHU','1'), ('059','SAN NICOLAS','1'),
        ('060','SAN PEDRO','1'), ('094','VENADO TUERTO','1'),
        ('031','JUJUY','3'), ('034','LA QUIACA','3'), ('045','POCITOS','3'),
        ('053','SALTA','3'), ('066','TINOGASTA','3'), ('074','TUCUMAN','3'), ('076','ORAN','3'),
        ('010','BARRANQUERAS','2'), ('012','CLORINDA','2'), ('018','CORRIENTES','2'),
        ('024','FORMOSA','2'), ('025','GOYA','2'), ('029','IGUAZU','2'),
        ('042','PASO DE LOS LIBRES','2'), ('046','POSADAS','2'), ('054','SAN JAVIER','2'),
        ('082','BERNARDO DE IRIGOYEN','2'), ('084','SANTO TOME','2'), ('086','OBERA','2'),
        ('079','LA RIOJA','4'), ('088','GENERAL DEHEZA','4'), ('017','CORDOBA','4'),
        ('089','SANTIAGO DEL ESTERO','4'), ('090','GENERAL PICO','4'),
        ('008','CAMPANA','8'), ('073','EZEIZA','8'), ('033','LA PLATA','8'), ('001','BUENOS AIRES','8'),
        ('003','BAHIA BLANCA','5'), ('004','SAN CARLOS DE BARILOCHE','5'), ('037','MAR DEL PLATA','5'),
        ('040','NECOCHEA','5'), ('058','SAN MARTIN DE LOS ANDES','5'), ('075','NEUQUEN','5'),
        ('080','SAN ANTONIO OESTE','5'), ('085','VILLA REGINA','5'),
        ('014','COMODORO RIVADAVIA','6'), ('019','PUERTO DESEADO','6'), ('023','ESQUEL','6'),
        ('047','PUERTO MADRYN','6'), ('048','RIO GALLEGOS','6'), ('049','RIO GRANDE','6'),
        ('061','SANTA CRUZ','6'), ('067','USHUAIA','6'), ('087','CALETA OLIVIA','6'),
        ('055','SAN JUAN','7'), ('038','MENDOZA','7'), ('083','SAN LUIS','7'), ('078','SAN RAFAEL','7'),
    ]
    con.executemany("INSERT OR REPLACE INTO ref_aduanas (cod, nombre, indice_dira) VALUES (?,?,?)", datos)
    con.commit()


    """Datos iniciales de cronología VUA. Separado de init_historial para mayor claridad."""
    cronologia = [
        ("08/10/2025","Designación del referente de la DGA ante VUCEA para el proyecto VUA","DI REPA","Completado",1),
        ("14/10/2025","Primera reunión: VUCEA expone el nuevo alcance del proyecto VUA Carga","DI REPA, VUCEA","Completado",2),
        ("04/12/2025","Reunión de relevamiento del proceso de carga aérea","DI REPA, VUCEA","Completado",3),
        ("18/12/2025","Reunión de análisis de la RG 5797/2025 y su implicancia en VUA","DI REPA, VUCEA","Completado",4),
        ("11/02/2026","Demo sistema VUA: primera versión del formulario Plan de Vuelo","DI REPA, VUCEA, SENASA, ORSNA, DI ADEZ, Aerolíneas Argentinas, PSA","Completado",5),
        ("25/02/2026","Reunión presencial en Ezeiza: relevamiento operativo de circuitos de carga","DI REPA, DI ADEZ, VUCEA","Completado",6),
        ("17/03/2026","Reunión presencial con TCA: relevamiento del circuito de desconsolidación","DI REPA, DI ADEZ, VUCEA, TCA","Completado",7),
        ("25/03/2026","Reunión sobre Manifiesto Desconsolidado de Importación","DI REPA, DI ADEZ, VUCEA, TCA","Completado",8),
        ("11/05/2026","Reunión ARCA-SENASA: integración de organismos de control en PAD","DI REPA, SENASA","Completado",9),
        ("14/05/2026","Mesa de trabajo: IA, tablero de vuelos, MANE y marco normativo","DI REPA, DI SADU, DI ADEZ, VUCEA","Completado",10),
        ("21/05/2026","Reunión ampliada con aerolíneas: IA, circuito importación, desconsolidado y DAI","DGA, DI REPA, DI SADU, DI ADEZ, VUCEA, Aerolíneas Argentinas, JURCA, IATA","Completado",11),
        ("A definir","Reunión específica análisis del MANE","DI REPA, DI ADEZ, VUCEA","Pendiente",12),
        ("A definir","Análisis normativo rol de VUCEA como intermediario","DI REPA, áreas legales DGA y VUCEA","Pendiente",13),
        ("A definir","Corrección de formularios Guía Madre por parte de VUCEA","VUCEA","Pendiente",14),
    ]
    con.executemany(
        "INSERT INTO vua_cronologia (fecha,actividad,participantes,estado,orden,creado,modificado) "
        "VALUES (?,?,?,?,?,datetime('now'),datetime('now'))",
        cronologia)

def _seed_feriados(con):
    """Carga inicial de feriados migrada desde el dict DIAS_NH hardcodeado en
    stock_depositos.py (2021-2026). Sin descripción — se puede completar
    después desde el panel de administración."""
    datos = [
        ("2021-08-16", ""),
        ("2021-10-08", ""),
        ("2021-10-11", ""),
        ("2021-11-20", ""),
        ("2021-11-22", ""),
        ("2021-12-08", ""),
        ("2021-12-25", ""),
        ("2022-01-01", ""),
        ("2022-02-28", ""),
        ("2022-03-01", ""),
        ("2022-03-24", ""),
        ("2022-04-02", ""),
        ("2022-04-14", ""),
        ("2022-04-15", ""),
        ("2022-05-01", ""),
        ("2022-05-18", ""),
        ("2022-05-25", ""),
        ("2022-06-17", ""),
        ("2022-06-20", ""),
        ("2022-08-15", ""),
        ("2022-09-02", ""),
        ("2022-10-07", ""),
        ("2022-10-10", ""),
        ("2022-11-21", ""),
        ("2022-12-08", ""),
        ("2022-12-09", ""),
        ("2022-12-20", ""),
        ("2023-02-20", ""),
        ("2023-02-21", ""),
        ("2023-03-24", ""),
        ("2023-04-06", ""),
        ("2023-04-07", ""),
        ("2023-05-01", ""),
        ("2023-05-25", ""),
        ("2023-05-26", ""),
        ("2023-06-19", ""),
        ("2023-06-20", ""),
        ("2023-08-21", ""),
        ("2023-10-13", ""),
        ("2023-10-16", ""),
        ("2023-11-20", ""),
        ("2023-12-08", ""),
        ("2023-12-25", ""),
        ("2024-01-01", ""),
        ("2024-02-12", ""),
        ("2024-02-13", ""),
        ("2024-03-28", ""),
        ("2024-03-29", ""),
        ("2024-04-01", ""),
        ("2024-04-02", ""),
        ("2024-06-17", ""),
        ("2024-06-20", ""),
        ("2024-06-21", ""),
        ("2024-07-09", ""),
        ("2024-08-17", ""),
        ("2024-10-11", ""),
        ("2024-11-18", ""),
        ("2024-12-25", ""),
        ("2025-01-01", ""),
        ("2025-03-03", ""),
        ("2025-03-04", ""),
        ("2025-03-24", ""),
        ("2025-04-02", ""),
        ("2025-04-17", ""),
        ("2025-04-18", ""),
        ("2025-05-01", ""),
        ("2025-05-02", ""),
        ("2025-06-16", ""),
        ("2025-06-20", ""),
        ("2025-07-09", ""),
        ("2025-08-15", ""),
        ("2025-10-10", ""),
        ("2025-12-08", ""),
        ("2025-12-25", ""),
        ("2026-01-01", ""),
        ("2026-02-16", ""),
        ("2026-02-17", ""),
        ("2026-03-24", ""),
        ("2026-04-02", ""),
        ("2026-04-03", ""),
        ("2026-05-01", ""),
        ("2026-05-25", ""),
        ("2026-06-15", ""),
        ("2026-06-20", ""),
        ("2026-07-09", ""),
        ("2026-08-17", ""),
        ("2026-10-12", ""),
        ("2026-11-23", ""),
        ("2026-12-08", ""),
        ("2026-12-25", ""),
    ]
    con.executemany("INSERT OR IGNORE INTO feriados (fecha, descripcion) VALUES (?,?)", datos)

def init_historial():
    con = sqlite3.connect(HIST_DB)
    con.execute("""CREATE TABLE IF NOT EXISTS historial (
        id TEXT PRIMARY KEY, fecha TEXT, usuario TEXT, pais TEXT,
        anio TEXT, mes_d TEXT, mes_h TEXT, uso_ia INTEGER,
        archivo_word TEXT, archivo_excel TEXT, revisado INTEGER DEFAULT 0,
        tipo TEXT DEFAULT 'sintia', descripcion TEXT DEFAULT ''
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS vua_cronologia (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT, actividad TEXT, participantes TEXT,
        estado TEXT DEFAULT 'Pendiente', orden INTEGER DEFAULT 0,
        creado TEXT, modificado TEXT
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS vua_minutas (
        id TEXT PRIMARY KEY, fecha TEXT, asunto TEXT, lugar TEXT,
        participantes TEXT, temas TEXT, acuerdos TEXT, proximos TEXT,
        archivo TEXT, creado_por TEXT, creado TEXT
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS vua_ejes (
        id TEXT PRIMARY KEY, nombre TEXT, estado TEXT, orden INTEGER DEFAULT 0
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS ref_aduanas (
        cod TEXT PRIMARY KEY, nombre TEXT NOT NULL, indice_dira TEXT NOT NULL
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS ref_aduanas_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT, usuario TEXT,
        accion TEXT, detalle TEXT
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS ref_dira (
        indice TEXT PRIMARY KEY, nombre TEXT NOT NULL, orden INTEGER DEFAULT 0
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS feriados (
        fecha TEXT PRIMARY KEY, descripcion TEXT DEFAULT ''
    )""")
    cur_fer = con.cursor()
    cur_fer.execute("SELECT COUNT(*) FROM feriados")
    if cur_fer.fetchone()[0] == 0:
        _seed_feriados(con)
    cur_rd = con.cursor()
    cur_rd.execute("SELECT COUNT(*) FROM ref_dira")
    if cur_rd.fetchone()[0] == 0:
        _seed_ref_dira(con)
    cur_ra = con.cursor()
    cur_ra.execute("SELECT COUNT(*) FROM ref_aduanas")
    if cur_ra.fetchone()[0] == 0:
        _seed_ref_aduanas(con)
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM vua_cronologia")
    if cur.fetchone()[0] == 0:
        _seed_cronologia_vua(con)
    cur.execute("SELECT COUNT(*) FROM vua_ejes")
    if cur.fetchone()[0] == 0:
        ejes = [
            ("4.1","Transmisión de información anticipada — XML, sujetos obligados y marco sancionatorio","En análisis — requiere definición normativa",1),
            ("4.2","Tablero de programación de vuelos","En análisis técnico interno",2),
            ("4.3","Manifiesto de Exportación (MANE)","Pendiente — sin normativa vigente para IA de exportación",3),
            ("4.4","Manifiestos desconsolidados de importación","Pendiente — sin normativa vigente",4),
            ("4.5","Estándar de transmisión XML — Guía Madre (XFWB)","Postura definida — observaciones comunicadas a VUCEA",5),
        ]
        con.executemany("INSERT INTO vua_ejes VALUES (?,?,?,?)", ejes)
    # ── Tablas de autenticación y sistema ────────────────────────────────────────
    con.execute("""CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        rol TEXT DEFAULT 'viewer',
        modulos TEXT DEFAULT 'sintia,vua,senasa',
        activo INTEGER DEFAULT 1,
        ultimo_acceso TEXT
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS sesiones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token TEXT UNIQUE NOT NULL,
        username TEXT NOT NULL,
        creado TEXT DEFAULT (datetime('now')),
        ultimo_acceso TEXT DEFAULT (datetime('now')),
        activo INTEGER DEFAULT 1
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS tokens_revocados (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token TEXT UNIQUE NOT NULL,
        revocado TEXT DEFAULT (datetime('now'))
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS prompts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL,
        descripcion TEXT DEFAULT '',
        modulo TEXT DEFAULT 'general',
        contenido TEXT DEFAULT '',
        modificado TEXT DEFAULT (datetime('now'))
    )""")
    # Seed: usuario admin por defecto si tabla vacía
    cur_u = con.cursor()
    cur_u.execute("SELECT COUNT(*) FROM usuarios")
    if cur_u.fetchone()[0] == 0:
        _temp_pass = secrets.token_urlsafe(12)
        default_hash = bcrypt.hashpw(_temp_pass.encode(), bcrypt.gensalt()).decode()
        con.execute("INSERT INTO usuarios (username, password_hash, rol, modulos) VALUES (?,?,?,?)",
            ("admin", default_hash, "admin", "sintia,vua,senasa"))
        logging.warning(f"SEED ADMIN | tabla 'usuarios' vacía — se creó 'admin' con password temporal: {_temp_pass} "
                         f"(cambiarla inmediatamente después de loguear)")

    # ── Tabla compartida de integrantes (VUA, SENASA, SINTIA) ──────────────────
    con.execute("""CREATE TABLE IF NOT EXISTS integrantes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL,
        cargo TEXT DEFAULT '',
        organismo TEXT DEFAULT '',
        email TEXT DEFAULT '',
        activo INTEGER DEFAULT 1,
        orden INTEGER DEFAULT 0,
        creado TEXT DEFAULT (datetime('now'))
    )""")
    # Migrar ROLES_PREDEFINIDOS a la tabla si está vacía
    cur2 = con.cursor()
    cur2.execute("SELECT COUNT(*) FROM integrantes")
    if cur2.fetchone()[0] == 0:
        roles_seed = [
            ("Diego Bugallo",      "Jefe Dpto. Facilitación y Simplificación de Comercio", "DI REPA",  "", 1),
            ("Martín Macías",      "Jefe Div. Modernización de Procesos Aduaneros",        "DI REPA",  "", 2),
            ("Federico Cáceres",   "Sec. Simplificación de Procesos Operativos",           "DI REPA",  "", 3),
            ("Hernán Cascón",      "Supervisor de Informática Aduanera",                   "DI SADU",  "", 4),
            ("Maximiliano Luengo", "Consejero técnico",                                    "DI ADEZ",  "", 5),
            ("Pablo Gómez Valdez", "Consejero técnico",                                    "DI ADEZ",  "", 6),
            ("Fabiola Cochello",   "Directora",                                            "VUCEA",    "", 7),
            ("Vanesa Franco",      "Jefa de Procesos",                                     "VUCEA",    "", 8),
        ]
        con.executemany(
            "INSERT INTO integrantes (nombre, cargo, organismo, email, orden) VALUES (?,?,?,?,?)",
            roles_seed)

    # Tablas VUA adicionales (pueden no existir en instalaciones anteriores)
    con.execute("""CREATE TABLE IF NOT EXISTS vua_config (
        clave TEXT PRIMARY KEY, titulo TEXT, contenido TEXT DEFAULT '',
        modificado TEXT DEFAULT (datetime('now'))
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS vua_equipo (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT, cargo TEXT, organismo TEXT, email TEXT,
        activo INTEGER DEFAULT 1, orden INTEGER DEFAULT 0
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS vua_glosario (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        termino TEXT, definicion TEXT, categoria TEXT DEFAULT 'general',
        orden INTEGER DEFAULT 0
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS vua_riesgos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo TEXT, titulo TEXT, descripcion TEXT, mitigacion TEXT,
        probabilidad TEXT DEFAULT 'Media', impacto TEXT DEFAULT 'Alto',
        activo INTEGER DEFAULT 1, orden INTEGER DEFAULT 0
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS vua_info (
        clave TEXT PRIMARY KEY, contenido TEXT DEFAULT '', modificado TEXT
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS vua_correos_rapidos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        etiqueta TEXT, instruccion TEXT, activo INTEGER DEFAULT 1, orden INTEGER DEFAULT 0
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS vua_consultas_frecuentes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pregunta TEXT, respuesta TEXT, activo INTEGER DEFAULT 1, orden INTEGER DEFAULT 0
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS doc_repositorio (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        modulo TEXT NOT NULL, nombre_archivo TEXT, contenido TEXT, ruta_archivo TEXT,
        subido_por TEXT, creado TEXT DEFAULT (datetime('now'))
    )""")
    try:
        con.execute("ALTER TABLE doc_repositorio ADD COLUMN ruta_archivo TEXT")
    except sqlite3.OperationalError:
        pass  # ya existe (instalación previa a este cambio)
    con.execute("""CREATE TABLE IF NOT EXISTS fin_servicios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL, patron TEXT, activo INTEGER DEFAULT 1, orden INTEGER DEFAULT 0
    )""")
    try:
        con.execute("ALTER TABLE fin_servicios ADD COLUMN patron TEXT")
    except sqlite3.OperationalError:
        pass  # ya existe (instalación previa a este cambio)
    con.execute("""CREATE TABLE IF NOT EXISTS fin_servicios_pagos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        servicio_id INTEGER NOT NULL, mes TEXT NOT NULL,
        pagado INTEGER DEFAULT 0, fecha_pago TEXT,
        UNIQUE(servicio_id, mes)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS fin_servicios_estado (
        clave TEXT PRIMARY KEY, valor TEXT
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS backups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT DEFAULT (datetime('now')), origen TEXT, resultados TEXT
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS fin_tarjetas_montos (
        tarjeta_id TEXT NOT NULL, mes TEXT NOT NULL, monto_a_pagar REAL DEFAULT 0,
        UNIQUE(tarjeta_id, mes)
    )""")
    cur.execute("SELECT COUNT(*) FROM fin_servicios")
    if cur.fetchone()[0] == 0:
        for i, (nombre, patron) in enumerate([
            ("Gas","gas"), ("Luz","edenor,edesur,luz"), ("Agua","aysa,agua"),
            ("Internet","fibertel,telecentro,movistar,internet"), ("Expensas","expensa"),
            ("Crédito","credito,préstamo,prestamo"), ("ABL","abl,rentas")
        ]):
            con.execute("INSERT INTO fin_servicios (nombre, patron, orden) VALUES (?,?,?)", (nombre, patron, i))
    # Seed vua_config con claves mínimas si está vacía
    cur.execute("SELECT COUNT(*) FROM vua_config")
    if cur.fetchone()[0] == 0:
        config_seed = [
            ("resumen_ejecutivo", "Resumen Ejecutivo", ""),
            ("antecedentes",      "Antecedentes",      ""),
            ("objetivo",          "Objetivo del Proyecto", ""),
            ("rol_dga",           "Rol de la DGA",     ""),
            ("alcance_operativo", "Alcance Operativo", ""),
        ]
        con.executemany(
            "INSERT OR IGNORE INTO vua_config (clave, titulo, contenido) VALUES (?,?,?)",
            config_seed)
    # ── Tablas SENASA ────────────────────────────────────────────────────────────
    con.execute("""CREATE TABLE IF NOT EXISTS senasa_cronologia (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT, actividad TEXT, participantes TEXT,
        estado TEXT DEFAULT 'Pendiente', orden INTEGER DEFAULT 0,
        creado TEXT DEFAULT (datetime('now')), modificado TEXT DEFAULT (datetime('now'))
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS senasa_ejes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT, descripcion TEXT, estado TEXT DEFAULT 'Pendiente',
        orden INTEGER DEFAULT 0
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS senasa_minutas (
        id TEXT PRIMARY KEY, fecha TEXT, asunto TEXT, lugar TEXT,
        participantes TEXT, temas TEXT, conclusiones TEXT,
        compromisos TEXT, proximos TEXT, archivo TEXT,
        creado_por TEXT, creado TEXT DEFAULT (datetime('now'))
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS senasa_acuerdos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        descripcion TEXT, responsable TEXT, fecha_compromiso TEXT,
        estado TEXT DEFAULT 'Pendiente', orden INTEGER DEFAULT 0,
        creado TEXT DEFAULT (datetime('now'))
    )""")
    # Seed ejes SENASA si está vacío
    cur3 = con.cursor()
    cur3.execute("SELECT COUNT(*) FROM senasa_ejes")
    if cur3.fetchone()[0] == 0:
        ejes_seed = [
            ("Integración PAD", "Integración del Sistema de Información de Gestión (SIG-SENASA) con el Portal Aduanero Digital (PAD)", "En análisis", 1),
            ("Embalajes de Madera (NIMF-15)", "Implementación del control de embalajes de madera en el circuito aduanero digital", "Pendiente", 2),
            ("Intercambio de información", "Definición de protocolo de intercambio de datos entre SENASA y ARCA", "Pendiente", 3),
            ("Normativa y procedimientos", "Revisión y actualización de normativa conjunta SENASA-ARCA para comercio exterior", "Pendiente", 4),
        ]
        con.executemany("INSERT INTO senasa_ejes (nombre, descripcion, estado, orden) VALUES (?,?,?,?)", ejes_seed)
    con.commit(); con.close()

def _migrar_fechas_cronologia():
    """Normaliza fechas ya guardadas en otros formatos (aaaa-mm-dd, aaaa/mm/dd, etc.)
    al estándar único dd/mm/aaaa."""
    con = sqlite3.connect(HIST_DB)
    for tabla in ("vua_cronologia", "senasa_cronologia"):
        rows = con.execute(f"SELECT id, fecha FROM {tabla}").fetchall()
        for rid, fecha in rows:
            if not _validar_fecha_ddmmaaaa(fecha):
                nueva = _normalizar_fecha_a_ddmmaaaa(fecha)
                if nueva != fecha:
                    con.execute(f"UPDATE {tabla} SET fecha=? WHERE id=?", (nueva, rid))
    con.commit(); con.close()

init_historial()
# _init_job_status_db() ya se ejecuta al importar core.py
_migrar_fechas_cronologia()

# WAL mejora mucho la concurrencia lectura/escritura de SQLite: con dos hilos de
# background (recordatorio de servicios, backup automático) más las requests
# normales escribiendo sobre las mismas bases, el modo "rollback journal" (default)
# genera bloqueos ("database is locked") más seguido de lo necesario.
# Se activa una sola vez: WAL queda grabado en el archivo de la base.
for _db in (HIST_DB, DB_PATH):
    try:
        if os.path.exists(_db):
            _con_wal = sqlite3.connect(_db, timeout=10)
            _con_wal.execute("PRAGMA journal_mode=WAL")
            _con_wal.close()
    except Exception:
        logging.exception(f"No se pudo activar WAL en {_db}")

# ── Migración APP_PASS/APP_USER legacy → BD ────────────────────────────────────
def _migrar_usuarios_legacy():
    """
    Si APP_USER/APP_PASS están definidos en el entorno y el usuario no existe en BD,
    lo crea automáticamente con hash bcrypt y elimina la necesidad del fallback en login.
    Loggea la migración para auditoría.
    """
    for _user, _pass, _rol, _mods in [
        (APP_USER,  APP_PASS,  "admin",    "sintia,vua,senasa,stock,garmin,training"),
        (APP_USER2, APP_PASS2, "readonly", "sintia"),
    ]:
        if not _user or not _pass:
            continue
        con = sqlite3.connect(HIST_DB)
        existing = con.execute("SELECT username FROM usuarios WHERE username=?", (_user,)).fetchone()
        if not existing:
            # Detectar si ya es hash bcrypt o texto plano
            if _pass.startswith("$2b$") or _pass.startswith("$2a$"):
                _hash = _pass
            else:
                _hash = bcrypt.hashpw(_pass.encode(), bcrypt.gensalt()).decode()
                logging.warning(f"LEGACY MIGRATION | usuario '{_user}' migrado desde APP_PASS (texto plano → bcrypt). "
                                f"Se recomienda eliminar APP_PASS del entorno y gestionar usuarios desde la UI.")
            con.execute("INSERT INTO usuarios (username, password_hash, rol, modulos) VALUES (?,?,?,?)",
                        (_user, _hash, _rol, _mods))
            con.commit()
            logging.info(f"LEGACY MIGRATION | usuario '{_user}' creado en BD desde variables de entorno.")
        con.close()

_migrar_usuarios_legacy()

# ── DB helper ──────────────────────────────────────────────────────────────────
from contextlib import contextmanager

@contextmanager
def db_conn(path=None):
    """Context manager para conexiones SQLite. Garantiza commit+close aunque haya excepción."""
    con = sqlite3.connect(path or HIST_DB)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("20 per 15 minutes", error_message="Demasiados intentos.")
def login():
    error = None
    if request.method == "POST":
        u = request.form.get("user","").strip()
        p = request.form.get("pass","")
        ip = request.remote_addr
        ua = request.headers.get("User-Agent","")[:200]
        user = get_user(u)
        autenticado = False; rol = "readonly"
        if user and check_password(p, user["password_hash"]):
            autenticado = True; rol = user["rol"]
        if autenticado:
            import secrets
            token = secrets.token_hex(32)
            session.update({"logged_in":True,"role":rol,"username":u,"token":token,
                "last_active":datetime.now().timestamp(),
                "modulos":user["modulos"].split(",") if user else ["sintia","vua","admin"]})
            session.permanent = True
            registrar_sesion(u, token, ip, ua)
            try:
                con = sqlite3.connect(HIST_DB)
                con.execute("UPDATE usuarios SET ultimo_acceso=datetime('now') WHERE username=?", (u,))
                con.commit(); con.close()
            except: pass
            logging.info("LOGIN OK | user=" + u + " | ip=" + ip)
            notificar_telegram(f"🔓 Login: {u} ({ip})")
            return redirect(url_for("index"))
        else:
            logging.warning("LOGIN FAIL | user=" + u + " | ip=" + ip)
            error = "Usuario o contraseña incorrectos"
    return render_template("login.html", error=error)


@app.route("/health")
def health_check():
    """Chequeo mínimo, sin autenticación, pensado para monitoreo automático
    (uptime checks, balanceador de carga). No expone detalles internos."""
    try:
        con = sqlite3.connect(HIST_DB, timeout=5)
        con.execute("SELECT 1")
        con.close()
        return jsonify({"status": "ok", "ts": datetime.now().isoformat()})
    except Exception:
        return jsonify({"status": "error", "ts": datetime.now().isoformat()}), 503

@app.route("/api/admin/health")
@login_required
@admin_required("sistema")
def health_check_detalle():
    """Chequeo detallado para admins: BD, disco, último backup, último
    informe generado, configuración de Telegram y del rate limiter."""
    detalle = {}

    # Bases de datos
    for nombre, ruta in [("historial_db", HIST_DB), ("pad_db", DB_PATH)]:
        try:
            if os.path.exists(ruta):
                con = sqlite3.connect(ruta, timeout=5)
                con.execute("SELECT 1")
                con.close()
                detalle[nombre] = {"ok": True, "size_mb": round(os.path.getsize(ruta)/1024/1024, 1)}
            else:
                detalle[nombre] = {"ok": False, "error": "archivo no existe"}
        except Exception as e:
            detalle[nombre] = {"ok": False, "error": str(e)}

    # Espacio en disco
    try:
        uso = shutil.disk_usage(os.path.dirname(HIST_DB) or "/")
        detalle["disco"] = {
            "libre_gb": round(uso.free/1024/1024/1024, 1),
            "total_gb": round(uso.total/1024/1024/1024, 1),
            "pct_libre": round(100*uso.free/uso.total, 1),
        }
    except Exception as e:
        detalle["disco"] = {"error": str(e)}

    # Último backup
    try:
        con = sqlite3.connect(HIST_DB, timeout=5)
        row = con.execute("SELECT fecha, origen FROM backups ORDER BY id DESC LIMIT 1").fetchone()
        con.close()
        detalle["ultimo_backup"] = {"fecha": row[0], "origen": row[1]} if row else None
    except Exception as e:
        detalle["ultimo_backup"] = {"error": str(e)}

    # Último informe SINTIA generado
    try:
        con = sqlite3.connect(HIST_DB, timeout=5)
        row = con.execute("SELECT fecha, usuario, descripcion FROM historial WHERE tipo='sintia' ORDER BY fecha DESC LIMIT 1").fetchone()
        con.close()
        detalle["ultimo_informe"] = {"fecha": row[0], "usuario": row[1], "descripcion": row[2]} if row else None
    except Exception as e:
        detalle["ultimo_informe"] = {"error": str(e)}

    # Jobs corriendo ahora mismo (en este worker)
    detalle["jobs_activos_este_worker"] = sum(1 for v in job_status.values() if v.get("status") == "running")

    # Configuración
    detalle["telegram_configurado"] = bool(TELEGRAM_TOKEN)
    detalle["rate_limiter_storage"] = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
    if detalle["rate_limiter_storage"] == "memory://":
        detalle["aviso_rate_limiter"] = ("En memoria: si corren más de 1 worker, el límite de intentos "
                                          "no se comparte entre procesos. Ver RATELIMIT_STORAGE_URI.")

    ok_general = all([
        detalle.get("historial_db", {}).get("ok"),
        detalle.get("pad_db", {}).get("ok", True),  # puede no estar cargada aún, no es un fallo
        detalle.get("disco", {}).get("pct_libre", 100) > 5,
    ])
    return jsonify({"status": "ok" if ok_general else "degraded", "detalle": detalle,
                    "ts": datetime.now().isoformat()})

@app.route("/logout")
def logout():
    logging.info(f"LOGOUT | user={session.get('username','?')} | ip={request.remote_addr}")
    session.clear()
    return redirect(url_for("login"))

# ── Index ──────────────────────────────────────────────────────────────────────
@app.route("/")
@login_required
def index():
    db_exists = os.path.exists(DB_PATH)
    db_size   = round(os.path.getsize(DB_PATH)/(1024**3),2) if db_exists else 0
    hoy = datetime.today()
    mes_ult = str(hoy.month-1).zfill(2) if hoy.month > 1 else "12"
    meses = {"01":"Enero","02":"Febrero","03":"Marzo","04":"Abril","05":"Mayo","06":"Junio",
             "07":"Julio","08":"Agosto","09":"Septiembre","10":"Octubre","11":"Noviembre","12":"Diciembre"}
    pendientes = []
    try:
        con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
        limite = (datetime.now()-timedelta(days=10)).strftime("%Y-%m-%d")
        pendientes = [dict(r) for r in con.execute(
            "SELECT * FROM historial WHERE revisado=0 AND fecha < ? ORDER BY fecha ASC",(limite,)).fetchall()]
        con.close()
    except: pass
    return render_template("dashboard.html",
        db_exists=db_exists, db_size=db_size, now=hoy, mes_ult=mes_ult, meses=meses,
        api_key=bool(get_api_key()), role=session.get("role","admin"),
        username=session.get("username",""), pendientes=pendientes)

# ── DB Status ──────────────────────────────────────────────────────────────────
@app.route("/api/db-status")
@login_required
def db_status():
    if not os.path.exists(DB_PATH): return jsonify({"exists":False})
    try:
        con = sqlite3.connect(DB_PATH); cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        info = {}
        for t in tables:
            try: cur.execute('SELECT COUNT(*) FROM "' + t.replace('"', '""') + '"'); info[t] = cur.fetchone()[0]
            except: pass
        con.close()
        return jsonify({"exists":True,"tables":info,"size_gb":round(os.path.getsize(DB_PATH)/(1024**3),2)})
    except Exception as e:
        return jsonify({"exists":True,"error":str(e)})

# ── Upload BD ──────────────────────────────────────────────────────────────────
@app.route("/api/upload-db", methods=["POST"])
@login_required
@admin_required("bd")
@limiter.limit("5 per hour", error_message="Demasiados intentos de subir la base completa.")
def upload_db():
    if "file" not in request.files: return jsonify({"ok":False,"error":"No se recibió archivo"})
    f = request.files["file"]
    if not f.filename.endswith(".db"): return jsonify({"ok":False,"error":"El archivo debe ser .db"})
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    f.save(DB_PATH)
    size_gb = round(os.path.getsize(DB_PATH)/(1024**3),2)
    logging.info(f"BD UPLOAD | user={session.get('username')} | size={os.path.getsize(DB_PATH)}")
    notificar_telegram(f"📦 BD reemplazada por {session.get('username')} ({size_gb} GB)")
    return jsonify({"ok":True,"size_gb":size_gb})

# ── Import SQL ─────────────────────────────────────────────────────────────────
@app.route("/api/import-sql", methods=["POST"])
@login_required
@admin_required("bd")
@limiter.limit("5 per hour", error_message="Demasiados intentos de reimportar la base completa.")
def import_sql():
    if "file" not in request.files: return jsonify({"ok":False,"error":"No se recibió archivo"})
    f = request.files["file"]
    if not f.filename.endswith(".sql"): return jsonify({"ok":False,"error":"El archivo debe ser .sql"})
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    tmp_sql = "/tmp/import_cosmo.sql"
    f.save(tmp_sql)
    try:
        if os.path.exists(DB_PATH): os.remove(DB_PATH)
        result = subprocess.run(["sqlite3", DB_PATH],
            stdin=open(tmp_sql,"r",encoding="utf-8",errors="replace"),
            capture_output=True, text=True, timeout=1800)
        os.remove(tmp_sql)
        if result.returncode != 0 and result.stderr:
            return jsonify({"ok":False,"error":result.stderr[:500]})
        size = round(os.path.getsize(DB_PATH)/(1024**3),2)
        logging.info(f"SQL IMPORT | user={session.get('username')} | size={size}GB")
        notificar_telegram(f"📦 BD reimportada desde .sql por {session.get('username')} ({size} GB)")
        return jsonify({"ok":True,"size_gb":size})
    except subprocess.TimeoutExpired:
        return jsonify({"ok":False,"error":"Timeout — archivo muy grande."})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/limpiar-tabla", methods=["POST"])
@login_required
@admin_required("bd")
@limiter.limit("10 per hour", error_message="Demasiados intentos de vaciar una tabla.")
def limpiar_tabla():
    """Borra todos los registros de una tabla DAT_YYYY o RECHAZOS, sin
    borrar la tabla en sí (estructura e índices quedan intactos)."""
    data = request.json or {}
    tabla = (data.get("tabla") or "").strip()
    if tabla not in ("REC", "RECHAZOS") and not re.match(r'^DAT_\d{4}$', tabla):
        return jsonify({"ok": False, "error": f"Tabla no permitida: '{tabla}'"})
    if not os.path.exists(DB_PATH):
        return jsonify({"ok": False, "error": "La BD no está cargada"})
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tabla,))
    if not cur.fetchone():
        con.close()
        return jsonify({"ok": False, "error": f"La tabla {tabla} no existe"})
    cur.execute(f"SELECT COUNT(*) FROM {tabla}")
    total_antes = cur.fetchone()[0]
    cur.execute(f"DELETE FROM {tabla}")
    con.commit(); con.close()
    logging.info(f"LIMPIAR TABLA | tabla={tabla} | user={session.get('username')} | borrados={total_antes}")
    notificar_telegram(f"🗑️ Se vació la tabla {tabla} ({total_antes:,} registros borrados) por {session.get('username')}")
    return jsonify({"ok": True, "borrados": total_antes})

@app.route("/api/admin/backup/estado")
@login_required
@admin_required("bd")
def api_backup_estado():
    import json as _json
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    row = con.execute("SELECT fecha, resultados FROM backups ORDER BY fecha DESC LIMIT 1").fetchone()
    con.close()
    ultimo = None
    if row:
        ultimo = {"fecha": row["fecha"], "resultados": _json.loads(row["resultados"])}
    archivos = {}
    for clave in BACKUP_FUENTES:
        path = os.path.join(BACKUP_DIR, f"{clave}_backup.db")
        if os.path.exists(path):
            archivos[clave] = {
                "size_mb": round(os.path.getsize(path) / (1024 * 1024), 2),
                "fecha": datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M"),
            }
        else:
            archivos[clave] = None
    return jsonify({"ok": True, "ultimo": ultimo, "archivos": archivos, "proximo": _proximo_backup_domingo()})

@app.route("/api/admin/backup/ejecutar", methods=["POST"])
@login_required
@admin_required("bd")
@limiter.limit("5 per hour", error_message="Demasiados intentos de backup manual.")
def api_backup_ejecutar():
    resultados, todo_ok = _ejecutar_backup(origen="manual")
    return jsonify({"ok": todo_ok, "resultados": resultados})

@app.route("/api/admin/backup/download/<clave>")
@login_required
@admin_required("bd")
def api_backup_download(clave):
    if clave not in BACKUP_FUENTES:
        return jsonify({"ok": False, "error": "Backup no válido"}), 404
    path = os.path.join(BACKUP_DIR, f"{clave}_backup.db")
    if not os.path.exists(path):
        return jsonify({"ok": False, "error": "No hay backup disponible"}), 404
    return send_file(path, as_attachment=True, download_name=f"{clave}_backup.db")

@app.route("/api/admin/backup/restaurar", methods=["POST"])
@login_required
@admin_required("bd")
@limiter.limit("5 per hour", error_message="Demasiados intentos de restaurar backup.")
def api_backup_restaurar():
    import shutil
    data = request.json or {}
    clave = (data.get("clave") or "").strip()
    if clave not in BACKUP_FUENTES:
        return jsonify({"ok": False, "error": "Backup no válido"})
    backup_path = os.path.join(BACKUP_DIR, f"{clave}_backup.db")
    if not os.path.exists(backup_path):
        return jsonify({"ok": False, "error": "No hay backup disponible para restaurar"})
    destino = BACKUP_FUENTES[clave]()
    try:
        if os.path.exists(destino):
            shutil.copy2(destino, destino + ".pre_restore")  # por si hay que deshacer
        shutil.copy2(backup_path, destino)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    logging.info(f"BACKUP RESTAURAR | clave={clave} | user={session.get('username')}")
    notificar_telegram(f"♻️ Se restauró el backup de '{clave}' sobre la BD en uso — hecho por {session.get('username')}")
    return jsonify({"ok": True})

@app.route("/api/admin/backup/<clave>", methods=["DELETE"])
@login_required
@admin_required("bd")
def api_backup_delete(clave):
    if clave not in BACKUP_FUENTES:
        return jsonify({"ok": False, "error": "Backup no válido"}), 404
    path = os.path.join(BACKUP_DIR, f"{clave}_backup.db")
    if not os.path.exists(path):
        return jsonify({"ok": False, "error": "No hay backup para borrar"}), 404
    os.remove(path)
    logging.info(f"BACKUP DELETE | clave={clave} | user={session.get('username')}")
    notificar_telegram(f"🗑️ Se eliminó el backup de '{clave}' — hecho por {session.get('username')}")
    return jsonify({"ok": True})

# ── Update CSV (async) ─────────────────────────────────────────────────────────
@app.route("/api/update-csv", methods=["POST"])
@login_required
@admin_required("bd")
def update_csv():
    tabla = request.form.get("tabla","").strip()
    anio  = request.form.get("anio", str(datetime.today().year))
    modo  = request.form.get("modo", "reemplazar")
    if modo not in ("reemplazar", "agregar"): modo = "reemplazar"
    if tabla == "DAT": tabla = f"DAT_{anio}"
    # Whitelist: solo tablas conocidas o DAT_YYYY
    if tabla not in ("REC", "RECHAZOS") and not re.match(r'^DAT_\d{4}$', tabla):
        return jsonify({"ok": False, "error": f"Tabla no permitida: '{tabla}'"})
    if "file" not in request.files:
        return jsonify({"ok":False,"error":"No se recibió archivo"})
    if not os.path.exists(DB_PATH):
        return jsonify({"ok":False,"error":"La BD no está cargada"})
    f = request.files["file"]
    tmp_path = f"/tmp/upload_{uuid.uuid4().hex[:8]}.txt"
    f.save(tmp_path)
    size_kb = round(os.path.getsize(tmp_path)/1024,1)
    job_id = str(uuid.uuid4())[:8]
    job_create(job_id, f"Archivo recibido: {size_kb} KB",
               username=session.get("username", "?"))
    logging.info(f"CSV UPLOAD | tabla={tabla} | size={size_kb}KB | modo={modo}")
    t = threading.Thread(target=_run_csv_job, args=(job_id, tmp_path, tabla, modo))
    t.start()
    return jsonify({"ok":True,"job_id":job_id})

def _run_csv_job(job_id, tmp_path, tabla, modo="reemplazar"):
    log = job_status[job_id]["log"]
    username = job_status[job_id].get("username", "?")
    try:
        _procesar_csv(tmp_path, tabla, log, modo)
        job_status[job_id]["status"] = "done"
        _job_persist(job_id)
        ultimo = log[-1] if log else ""
        notificar_telegram(f"📄 Tabla {tabla} actualizada por {username}\n{ultimo}")
    except Exception as e:
        log.append(f"✗ Error: {e}")
        job_status[job_id]["status"] = "error"
        _job_persist(job_id)
        notificar_telegram(f"⚠️ Error actualizando tabla {tabla} ({username}): {e}")
    finally:
        try: os.remove(tmp_path)
        except: pass

def _procesar_csv(tmp_path, tabla, log, modo="reemplazar"):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    try:
        if tabla.startswith("DAT_"):
            # Streaming línea por línea — evita cargar 650MB en RAM
            log.append(f"Procesando archivo DAT (streaming, modo: {modo})...")
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tabla,))
            tabla_existe = cur.fetchone() is not None
            rowid_inicio = 1
            if modo == "agregar" and tabla_existe:
                rowid_inicio = (cur.execute(f"SELECT COALESCE(MAX(rowid),0) FROM {tabla}").fetchone()[0]) + 1
            headers = None
            placeholders = None
            batch = []
            batch_size = 2000
            inserted = 0
            with open(tmp_path, encoding="utf-8", errors="replace") as fh:
                for raw_line in fh:
                    line = raw_line.strip()
                    if not line or ";" not in line or line.startswith("---"):
                        continue
                    if line.upper().startswith("REGISTRO") and ";" not in line:
                        continue
                    if headers is None:
                        headers = [h.strip() for h in line.split(";")]
                        col_normalize = {"TIENE NOVEDAD?": "tiene_novedad", "TIENE NOVEDAD": "tiene_novedad"}
                        headers = [col_normalize.get(h, h) for h in headers]
                        insert_sql = None
                        if modo == "agregar" and tabla_existe:
                            log.append("Modo agregar: se suman filas a la tabla existente, no se pisa nada.")
                            cols_quoted = ", ".join(f'"{h}"' for h in headers)
                            placeholders = ", ".join(["?" for _ in headers])
                            insert_sql = f"INSERT INTO {tabla} ({cols_quoted}) VALUES ({placeholders})"
                        else:
                            cur.execute(f"DROP TABLE IF EXISTS {tabla}")
                            cols_def = ", ".join([f'"{h}" TEXT' for h in headers])
                            cur.execute(f"CREATE TABLE {tabla} ({cols_def})")
                            log.append(f"Columnas: {len(headers)} — tabla creada, insertando...")
                            placeholders = ", ".join(["?" for _ in headers])
                            insert_sql = f"INSERT INTO {tabla} VALUES ({placeholders})"
                        con.commit()
                        continue
                    vals = [v.strip() for v in line.split(";")]
                    while len(vals) < len(headers): vals.append(None)
                    batch.append(vals[:len(headers)])
                    if len(batch) >= batch_size:
                        cur.executemany(insert_sql, batch)
                        inserted += len(batch)
                        batch = []
                        if inserted % 50000 == 0:
                            con.commit()
                            log.append(f"  {inserted:,} filas insertadas...")
            if batch:
                cur.executemany(insert_sql, batch)
                inserted += len(batch)
            con.commit()
            if inserted == 0:
                log.append("✗ No se encontraron datos válidos"); con.close(); return
            log.append(f"  {inserted:,} filas insertadas en total. Calculando fechas ISO...")
            for col in ["FECHA_INGRESO_ISO","FECHA_TRANS_ISO"]:
                try: cur.execute(f"ALTER TABLE {tabla} ADD COLUMN {col} TEXT")
                except: pass
            # Actualizar en batches para no bloquear el proceso con tablas grandes
            iso_sql = f"""UPDATE {tabla} SET
                FECHA_INGRESO_ISO = CASE
                    WHEN FECHA_INGRESO IS NOT NULL AND length(FECHA_INGRESO)>=10 THEN
                        substr(FECHA_INGRESO,7,4)||'-'||substr(FECHA_INGRESO,4,2)||'-'||substr(FECHA_INGRESO,1,2)||
                        CASE WHEN instr(FECHA_INGRESO,' ')>0 THEN ' '||substr(FECHA_INGRESO,instr(FECHA_INGRESO,' ')+1,5) ELSE ' 00:00' END
                    ELSE NULL END,
                FECHA_TRANS_ISO = CASE
                    WHEN FECHA_TRANS IS NOT NULL AND FECHA_TRANS NOT IN ('-','') AND length(FECHA_TRANS)>=10 THEN
                        substr(FECHA_TRANS,7,4)||'-'||substr(FECHA_TRANS,4,2)||'-'||substr(FECHA_TRANS,1,2)||
                        CASE WHEN instr(FECHA_TRANS,' ')>0 THEN ' '||substr(FECHA_TRANS,instr(FECHA_TRANS,' ')+1,5) ELSE ' 00:00' END
                    ELSE NULL END
                WHERE rowid BETWEEN ? AND ?"""
            batch_iso = 50000
            rowid_fin = rowid_inicio + inserted - 1
            for start in range(rowid_inicio, rowid_fin + 1, batch_iso):
                fin_batch = min(start + batch_iso - 1, rowid_fin)
                cur.execute(iso_sql, (start, fin_batch))
                con.commit()
                log.append(f"  Fechas ISO: {fin_batch - rowid_inicio + 1:,} / {inserted:,}...")
            log.append("Creando índice...")
            try: cur.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{tabla}_key ON {tabla}(OPERACION_PAD_EXT, MIC, TIPO_REGISTRO)")
            except: pass
            try: cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{tabla}_fecha ON {tabla}(FECHA_INGRESO_ISO)")
            except: pass
            try: cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{tabla}_estado ON {tabla}(EST_MIC)")
            except: pass
            try: cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{tabla}_aduana ON {tabla}(ADUANA)")
            except: pass
            con.commit(); con.close()
            log.append(f"✓ {tabla}: {inserted:,} registros, fechas ISO calculadas, índices creados")

        elif tabla == "RECHAZOS":
            log.append("Procesando archivo RECHAZOS (streaming)...")
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='RECHAZOS'")
            if not cur.fetchone():
                cur.execute("CREATE TABLE RECHAZOS (PaisEmisor TEXT, Metodo TEXT, NroMic TEXT, Fecha TEXT, Mensaje TEXT, Fecha_ISO TEXT, Mes TEXT, Anio TEXT)")
            else:
                for col in ["Fecha_ISO","Mes","Anio"]:
                    try: cur.execute(f"ALTER TABLE RECHAZOS ADD COLUMN {col} TEXT")
                    except: pass
            con.commit()

            col_map = {"Pais Emisor":"PaisEmisor","Metodo":"Metodo","Nro. MIC/DTA":"NroMic","Fecha":"Fecha","Mensaje":"Mensaje"}
            cols_utiles = ["PaisEmisor","Metodo","NroMic","Fecha","Mensaje"]
            idx_cols = None
            cols_finales = None
            placeholders = None
            batch = []
            batch_size = 5000
            inserted = 0

            with open(tmp_path, encoding="utf-8", errors="replace") as fh:
                for raw_line in fh:
                    s = raw_line.strip()
                    if not s or "@" not in s: continue
                    if len(s) >= 3 and s[:2].isdigit() and s[2] == "@": s = s[3:]
                    parts = [p.strip() for p in s.split("@")][:6]
                    if len(parts) < 2: continue

                    if idx_cols is None:
                        # Primera línea válida = headers
                        raw_headers = [col_map.get(h.strip(), h.strip()) for h in parts]
                        idx_cols = [i for i, h in enumerate(raw_headers) if h in cols_utiles]
                        cols_finales = [raw_headers[i] for i in idx_cols]
                        placeholders = ", ".join(["?" for _ in cols_finales])
                        continue

                    row = [parts[i].strip() if i < len(parts) else None for i in idx_cols]
                    batch.append(row)

                    if len(batch) >= batch_size:
                        try: cur.executemany(f"INSERT INTO RECHAZOS ({', '.join(cols_finales)}) VALUES ({placeholders})", batch); inserted += len(batch)
                        except:
                            for r in batch:
                                try: cur.execute(f"INSERT INTO RECHAZOS ({', '.join(cols_finales)}) VALUES ({placeholders})", r); inserted += 1
                                except: pass
                        con.commit()
                        batch = []
                        log.append(f"  {inserted:,} filas procesadas...")

            # Último batch
            if batch:
                try: cur.executemany(f"INSERT INTO RECHAZOS ({', '.join(cols_finales)}) VALUES ({placeholders})", batch); inserted += len(batch)
                except:
                    for r in batch:
                        try: cur.execute(f"INSERT INTO RECHAZOS ({', '.join(cols_finales)}) VALUES ({placeholders})", r); inserted += 1
                        except: pass
                con.commit()

            log.append(f"Filas insertadas: {inserted:,}")
            log.append("Calculando fechas ISO...")
            cur.execute("""UPDATE RECHAZOS SET
                Fecha_ISO = printf('%04d-%02d-%02d',
                    CAST(SUBSTR(CAST(Fecha AS TEXT),LENGTH(CAST(Fecha AS TEXT))-3,4) AS INT),
                    CAST(SUBSTR(CAST(Fecha AS TEXT),LENGTH(CAST(Fecha AS TEXT))-5,2) AS INT),
                    CAST(SUBSTR(CAST(Fecha AS TEXT),1,LENGTH(CAST(Fecha AS TEXT))-6) AS INT)),
                Anio = CAST(SUBSTR(CAST(Fecha AS TEXT),LENGTH(CAST(Fecha AS TEXT))-3,4) AS INTEGER),
                Mes = CASE CAST(SUBSTR(CAST(Fecha AS TEXT),LENGTH(CAST(Fecha AS TEXT))-5,2) AS INTEGER)
                    WHEN 1 THEN 'ENERO' WHEN 2 THEN 'FEBRERO' WHEN 3 THEN 'MARZO' WHEN 4 THEN 'ABRIL'
                    WHEN 5 THEN 'MAYO' WHEN 6 THEN 'JUNIO' WHEN 7 THEN 'JULIO' WHEN 8 THEN 'AGOSTO'
                    WHEN 9 THEN 'SEPTIEMBRE' WHEN 10 THEN 'OCTUBRE' WHEN 11 THEN 'NOVIEMBRE' WHEN 12 THEN 'DICIEMBRE'
                    END
                WHERE Fecha IS NOT NULL AND LENGTH(CAST(Fecha AS TEXT))>=6""")
            try: cur.execute("CREATE INDEX IF NOT EXISTS idx_rechazos_fecha ON RECHAZOS(Fecha_ISO)")
            except: pass
            con.commit(); con.close()
            log.append(f"✓ RECHAZOS: {inserted:,} registros insertados, fechas calculadas")
        else:
            con.close()
            log.append(f"✗ Tabla no reconocida: {tabla}")
    except Exception as e:
        log.append(f"✗ Error: {e}")
        try: con.close()
        except: pass

# ── Generar informe (async) ────────────────────────────────────────────────────
def run_job(job_id, pais, anio, mes_d, mes_h, usar_ia, username):
    log = job_status[job_id]["log"]
    try:
        from generar import generar_informe
        archivos = generar_informe(
            ruta_db=DB_PATH, pais=pais, anio=anio, mes_d=mes_d, mes_h=mes_h,
            usar_ia=usar_ia, api_key=get_api_key(), carpeta=OUTPUT_FOLDER,
            log_fn=lambda msg: log.append(msg), contexto_extra=contexto_repositorio("sintia"))
        # Guardar en historial
        hist_id = str(uuid.uuid4())[:8]
        word  = next((a for a in archivos if a.endswith(".docx")),"")
        excel = next((a for a in archivos if a.endswith(".xlsx")),"")
        con = sqlite3.connect(HIST_DB)
        con.execute("INSERT INTO historial VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (hist_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), username,
             pais, anio, mes_d, mes_h, int(usar_ia), word, excel, 0, 'sintia',
             f"{pais} {mes_d}-{mes_h}/{anio}"))
        con.commit(); con.close()
        logging.info(f"INFORME OK | user={username} | pais={pais} | {mes_d}-{mes_h}/{anio}")
        job_status[job_id]["status"] = "done"
        job_status[job_id]["files"]  = archivos
        _job_persist(job_id)
    except Exception as e:
        log.append(f"✗ Error: {e}")
        job_status[job_id]["status"] = "error"
        _job_persist(job_id)

@app.route("/api/generar", methods=["POST"])
@login_required
@modulo_required("sintia")
def api_generar():
    if not os.path.exists(DB_PATH):
        return jsonify({"ok":False,"error":"La BD no está cargada"})
    data = request.json or {}
    pais    = data.get("pais","").upper()
    anio    = data.get("anio", str(datetime.today().year))
    mes_d   = str(data.get("mes_d","01")).zfill(2)
    mes_h   = str(data.get("mes_h","12")).zfill(2)
    usar_ia = data.get("usar_ia",True) and bool(get_api_key())
    username = session.get("username","?")
    job_id  = str(uuid.uuid4())[:8]
    job_create(job_id, "Iniciando generación...", username=username)
    t = threading.Thread(target=run_job, args=(job_id, pais, anio, mes_d, mes_h, usar_ia, username))
    t.start()
    return jsonify({"ok":True,"job_id":job_id})

def _job_autorizado(info):
    """El dueño del job o un superadmin pueden verlo/descargarlo."""
    if not info: return False
    if session.get("role") == "admin": return True
    return info.get("username") == session.get("username")

@app.route("/api/job/<job_id>")
@login_required
def job_poll(job_id):
    info = job_get(job_id)
    if not info: return jsonify({"error":"Job no encontrado"})
    if not _job_autorizado(info):
        return jsonify({"error":"Sin permiso"}), 403
    return jsonify(info)

@app.route("/api/download/<job_id>/<int:idx>")
@login_required
def download_file(job_id, idx):
    info = job_get(job_id) or {}
    if not _job_autorizado(info):
        return "Sin permiso",403
    files = info.get("files",[])
    if idx >= len(files): return "Archivo no encontrado",404
    path = files[idx]
    if not os.path.exists(path): return "Archivo no encontrado",404
    return send_file(path, as_attachment=True, download_name=os.path.basename(path))

# ── Historial ──────────────────────────────────────────────────────────────────
@app.route("/api/historial")
@login_required
@admin_required("bd")
def api_historial():
    try:
        con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM historial ORDER BY fecha DESC LIMIT 100").fetchall()]
        con.close()
        return jsonify({"ok":True,"rows":rows})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/historial/completo")
@login_required
@admin_required("bd")
def historial_completo():
    """Mejora 4: paginación y filtros por tipo, fecha y usuario."""
    tipo    = request.args.get("tipo", "todos")      # todos | sintia | minuta | vua
    usuario = request.args.get("usuario", "")
    desde   = request.args.get("desde", "")
    hasta   = request.args.get("hasta", "")
    limit   = min(int(request.args.get("limit", 50)), 200)
    offset  = int(request.args.get("offset", 0))

    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = []

    if tipo in ("todos", "sintia"):
        where = ["1=1"]
        params = []
        if usuario: where.append("usuario=?"); params.append(usuario)
        if desde:   where.append("fecha>=?"); params.append(desde)
        if hasta:   where.append("fecha<=?"); params.append(hasta)
        q = ("SELECT id, fecha, usuario, 'sintia' as tipo, "
             "pais||' '||mes_d||'-'||mes_h||'/'||anio as descripcion, "
             "archivo_word, archivo_excel, revisado FROM historial "
             f"WHERE {' AND '.join(where)} ORDER BY fecha DESC LIMIT ? OFFSET ?")
        rows += [dict(r) for r in con.execute(q, params+[limit, offset]).fetchall()]

    if tipo in ("todos", "minuta"):
        where = ["1=1"]
        params = []
        if usuario: where.append("creado_por=?"); params.append(usuario)
        if desde:   where.append("creado>=?"); params.append(desde)
        if hasta:   where.append("creado<=?"); params.append(hasta)
        q = ("SELECT id, creado as fecha, creado_por as usuario, 'minuta' as tipo, "
             "asunto as descripcion, archivo as archivo_word, '' as archivo_excel, 1 as revisado "
             f"FROM vua_minutas WHERE {' AND '.join(where)} ORDER BY creado DESC LIMIT ? OFFSET ?")
        rows += [dict(r) for r in con.execute(q, params+[limit, offset]).fetchall()]

    if tipo in ("todos", "vua"):
        where = ["h.tipo='vua'"] if "tipo" in con.execute("PRAGMA table_info(historial)").fetchone() or [] else []
        # Informes VUA del historial general
        q = ("SELECT id, fecha, usuario, 'vua' as tipo, descripcion, "
             "archivo_word, '' as archivo_excel, revisado FROM historial "
             "WHERE tipo='vua' ORDER BY fecha DESC LIMIT ? OFFSET ?")
        try:
            rows += [dict(r) for r in con.execute(q, [limit, offset]).fetchall()]
        except: pass

    con.close()
    todos = sorted(rows, key=lambda x: x.get("fecha",""), reverse=True)
    return jsonify({"ok": True, "rows": todos[:limit], "total": len(todos), "offset": offset})

@app.route("/api/historial/<hist_id>/revisar", methods=["POST"])
@login_required
@admin_required("bd")
def revisar_historial(hist_id):
    accion = (request.json or {}).get("accion","conservar")
    try:
        con = sqlite3.connect(HIST_DB)
        if accion == "eliminar":
            row = con.execute("SELECT archivo_word, archivo_excel FROM historial WHERE id=?",(hist_id,)).fetchone()
            if row:
                for f in [row[0],row[1]]:
                    if f and os.path.exists(f):
                        try: os.remove(f)
                        except: pass
            con.execute("DELETE FROM historial WHERE id=?",(hist_id,))
        else:
            con.execute("UPDATE historial SET revisado=1 WHERE id=?",(hist_id,))
        con.commit(); con.close()
        return jsonify({"ok":True})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/historial/<hist_id>/download/<tipo>")
@login_required
@admin_required("bd")
def download_historial(hist_id, tipo):
    try:
        con = sqlite3.connect(HIST_DB)
        row = con.execute("SELECT archivo_word, archivo_excel FROM historial WHERE id=?",(hist_id,)).fetchone()
        con.close()
        if not row: return "No encontrado",404
        path = row[0] if tipo == "word" else row[1]
        if not path or not os.path.exists(path): return "Archivo no encontrado",404
        return send_file(path, as_attachment=True, download_name=os.path.basename(path))
    except: return "Error",500

# MÓDULO VUA -> blueprints/vua.py (registrado como vua_bp)




# ══════════════════════════════════════════════════════════════════════════════
# MÓDULO SENASA -> blueprints/senasa.py (registrado como senasa_bp)

    return jsonify({"ok": False, "error": "Archivo no encontrado. Regenerá el informe."}), 404

# ── Integrantes compartidos (VUA, SENASA, SINTIA) ────────────────────────────
@app.route("/api/integrantes", methods=["GET"])
@login_required
def integrantes_list():
    """Lista integrantes activos. Fusiona vua_equipo si unified=1 (default)."""
    organismo = request.args.get("organismo", "")
    unified   = request.args.get("unified", "1")
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    q = "SELECT * FROM integrantes WHERE activo=1"
    params = []
    if organismo:
        q += " AND organismo=?"; params.append(organismo)
    q += " ORDER BY orden, organismo, nombre"
    rows = [dict(r) for r in con.execute(q, params).fetchall()]
    # Fusionar con vua_equipo si unified=1 y la tabla existe
    if unified == "1":
        try:
            equipo = [dict(r) for r in con.execute(
                "SELECT nombre, cargo, organismo, email FROM vua_equipo WHERE activo=1 ORDER BY organismo, nombre"
            ).fetchall()]
            nombres_existentes = {r["nombre"].lower() for r in rows}
            for e in equipo:
                if e["nombre"].lower() not in nombres_existentes:
                    rows.append({"id": None, "nombre": e["nombre"], "cargo": e["cargo"],
                                 "organismo": e["organismo"], "email": e.get("email",""),
                                 "activo": 1, "orden": 999, "_origen": "vua_equipo"})
        except Exception:
            pass
    con.close()
    return jsonify({"ok": True, "rows": rows})

@app.route("/api/integrantes/migrar-equipo", methods=["POST"])
@login_required
def integrantes_migrar_equipo():
    """Migra todos los registros de vua_equipo a integrantes (ejecutar una sola vez)."""
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    try:
        equipo = [dict(r) for r in con.execute(
            "SELECT nombre, cargo, organismo, email FROM vua_equipo WHERE activo=1").fetchall()]
        migrados = 0
        for e in equipo:
            existe = con.execute("SELECT id FROM integrantes WHERE LOWER(nombre)=LOWER(?)",
                                 (e["nombre"],)).fetchone()
            if not existe:
                cur = con.cursor()
                cur.execute("SELECT MAX(orden) FROM integrantes")
                max_o = cur.fetchone()[0] or 0
                con.execute("INSERT INTO integrantes (nombre,cargo,organismo,email,activo,orden) VALUES (?,?,?,?,1,?)",
                    (e["nombre"], e["cargo"], e["organismo"], e.get("email",""), max_o+1))
                migrados += 1
        con.commit()
    except Exception as ex:
        con.close()
        return jsonify({"ok": False, "error": str(ex)})
    con.close()
    return jsonify({"ok": True, "migrados": migrados})

@app.route("/api/integrantes", methods=["POST"])
@login_required
def integrantes_create():
    data = request.json or {}
    con = sqlite3.connect(HIST_DB); cur = con.cursor()
    cur.execute("SELECT MAX(orden) FROM integrantes")
    max_orden = cur.fetchone()[0] or 0
    cur.execute(
        "INSERT INTO integrantes (nombre, cargo, organismo, email, activo, orden) VALUES (?,?,?,?,1,?)",
        (data.get("nombre",""), data.get("cargo",""),
         data.get("organismo",""), data.get("email",""), max_orden+1))
    new_id = cur.lastrowid; con.commit(); con.close()
    return jsonify({"ok": True, "id": new_id})

@app.route("/api/integrantes/<int:iid>", methods=["PUT"])
@login_required
def integrantes_update(iid):
    data = request.json or {}
    con = sqlite3.connect(HIST_DB)
    con.execute(
        "UPDATE integrantes SET nombre=?, cargo=?, organismo=?, email=?, activo=? WHERE id=?",
        (data.get("nombre",""), data.get("cargo",""),
         data.get("organismo",""), data.get("email",""),
         int(data.get("activo", 1)), iid))
    con.commit(); con.close()
    return jsonify({"ok": True})

@app.route("/api/integrantes/<int:iid>", methods=["DELETE"])
@login_required
def integrantes_delete(iid):
    con = sqlite3.connect(HIST_DB)
    con.execute("UPDATE integrantes SET activo=0 WHERE id=?", (iid,))
    con.commit(); con.close()
    return jsonify({"ok": True})

@app.route("/api/integrantes/organismos", methods=["GET"])
@login_required
def integrantes_organismos():
    """Lista los organismos únicos para el filtro del selector."""
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [r[0] for r in con.execute(
        "SELECT DISTINCT organismo FROM integrantes WHERE activo=1 AND organismo!='' ORDER BY organismo"
    ).fetchall()]
    con.close()
    return jsonify({"ok": True, "organismos": rows})

# (bloque VUA -> blueprints/vua.py, ver más abajo)



# ── Rutas Admin ───────────────────────────────────────────────────────────────
@app.route("/sintia")
@login_required
@modulo_required("sintia")
def sintia_index():
    import generar
    meses = generar.MESES
    hoy = __import__('datetime').date.today()
    mes_ult = str(hoy.month - 1).zfill(2) if hoy.month > 1 else '12' 
    db_exists = os.path.exists(DB_PATH)
    db_size = round(os.path.getsize(DB_PATH)/1e9, 2) if db_exists else 0
    api_key = get_api_key()
    return render_template("sintia.html",
        meses=meses, mes_ult=mes_ult, now=datetime.now(),
        db_exists=db_exists, db_size=db_size,
        api_key=api_key, username=session.get("username",""))

@app.route("/admin")
@login_required
@admin_required()
def admin_index():
    db_exists = os.path.exists(DB_PATH)
    db_size = round(os.path.getsize(DB_PATH)/1e9, 2) if db_exists else 0
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    pendientes = con.execute(
        "SELECT * FROM historial WHERE revisado=0 AND "
        "julianday('now') - julianday(fecha) > 10").fetchall()
    con.close()
    return render_template("admin.html",
        db_exists=db_exists, db_size=db_size, now=datetime.now(),
        pendientes=pendientes, username=session.get("username",""),
        role=session.get("role","readonly"),
        permiso_bd=tiene_permiso_admin("bd"), permiso_sistema=tiene_permiso_admin("sistema"))

@app.route("/api/admin/usuarios", methods=["GET"])
@login_required
@admin_required("sistema")
def admin_usuarios_list():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT id, username, rol, modulos, activo, ultimo_acceso FROM usuarios ORDER BY id").fetchall()]
    con.close(); return jsonify({"ok": True, "rows": rows})

@app.route("/api/admin/usuarios", methods=["POST"])
@login_required
@admin_required("sistema")
@limiter.limit("20 per hour", error_message="Demasiados intentos de crear usuarios.")
def admin_usuarios_create():
    data = request.json or {}
    username = data.get("username","").strip()
    password = data.get("password","")
    rol = data.get("rol","readonly")
    modulos = data.get("modulos","sintia,vua")
    if not username or not password:
        return jsonify({"ok": False, "error": "Usuario y password requeridos"})
    if not re.match(r'^[a-zA-Z0-9_.-]{3,32}$', username):
        return jsonify({"ok": False, "error": "Usuario inválido: solo letras, números, '.', '_', '-' (3-32 caracteres)"})
    if len(password) < 8:
        return jsonify({"ok": False, "error": "Password minimo 8 caracteres"})
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    try:
        con = sqlite3.connect(HIST_DB)
        con.execute("INSERT INTO usuarios (username, password_hash, rol, modulos, activo) VALUES (?,?,?,?,1)",
            (username, hashed, rol, modulos))
        con.commit(); con.close()
        logging.info(f"USUARIO CREATE | by={session.get('username')} | nuevo={username} | rol={rol} | modulos={modulos}")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": "Usuario ya existe" if "UNIQUE" in str(e) else str(e)})

@app.route("/api/admin/usuarios/<int:uid>", methods=["PUT"])
@login_required
@admin_required("sistema")
def admin_usuarios_update(uid):
    data = request.json or {}
    con = sqlite3.connect(HIST_DB)
    fields = []; params = []
    for f in ["rol","modulos","activo"]:
        if f in data: fields.append(f + "=?"); params.append(data[f])
    cambia_pass = "password" in data and data["password"]
    if cambia_pass:
        hashed = bcrypt.hashpw(data["password"].encode(), bcrypt.gensalt()).decode()
        fields.append("password_hash=?"); params.append(hashed)
    if fields:
        params.append(uid)
        con.execute("UPDATE usuarios SET " + ", ".join(fields) + " WHERE id=?", params)
        con.commit()
    con.close()
    logging.info(f"USUARIO UPDATE | by={session.get('username')} | uid={uid} | campos={list(data.keys())}" +
                 (" | password cambiado" if cambia_pass else ""))
    return jsonify({"ok": True})

@app.route("/api/admin/usuarios/<int:uid>", methods=["DELETE"])
@login_required
@admin_required("sistema")
def admin_usuarios_delete(uid):
    con = sqlite3.connect(HIST_DB)
    con.execute("DELETE FROM usuarios WHERE id=?", (uid,))
    con.commit(); con.close()
    logging.info(f"USUARIO DELETE | by={session.get('username')} | uid={uid}")
    return jsonify({"ok": True})

@app.route("/api/admin/sesiones", methods=["GET"])
@login_required
@admin_required("sistema")
def admin_sesiones_list():
    current_token = session.get("token","")
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT username, SUBSTR(token,1,8)||'...' as token, ip, ultimo_acceso, token as full_token "
        "FROM sesiones WHERE activo=1 ORDER BY ultimo_acceso DESC").fetchall()
    con.close()
    result = []
    for r in rows:
        d = dict(r); d["es_propia"] = d.pop("full_token","") == current_token; result.append(d)
    return jsonify({"ok": True, "rows": result})

@app.route("/api/admin/sesiones/<token_prefix>/revocar", methods=["POST"])
@login_required
@admin_required("sistema")
def admin_sesiones_revocar(token_prefix):
    con = sqlite3.connect(HIST_DB)
    con.execute("UPDATE sesiones SET activo=0 WHERE token LIKE ?", (token_prefix + "%",))
    con.execute("INSERT OR IGNORE INTO tokens_revocados (token) SELECT token FROM sesiones WHERE token LIKE ?",
        (token_prefix + "%",))
    con.commit(); con.close(); return jsonify({"ok": True})

@app.route("/api/admin/sesiones/revocar-todas", methods=["POST"])
@login_required
@admin_required("sistema")
def admin_sesiones_revocar_todas():
    current_token = session.get("token","")
    con = sqlite3.connect(HIST_DB)
    rows = con.execute("SELECT token FROM sesiones WHERE activo=1 AND token!=?", (current_token,)).fetchall()
    for r in rows:
        con.execute("INSERT OR IGNORE INTO tokens_revocados (token) VALUES (?)", (r[0],))
    con.execute("UPDATE sesiones SET activo=0 WHERE token!=?", (current_token,))
    con.commit(); con.close(); return jsonify({"ok": True, "revocadas": len(rows)})

@app.route("/api/admin/prompts", methods=["GET"])
@login_required
@admin_required("sistema")
def admin_prompts_list():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT id, nombre, descripcion, modulo, modificado FROM prompts ORDER BY modulo, nombre").fetchall()]
    con.close(); return jsonify({"ok": True, "rows": rows})

@app.route("/api/admin/prompts/<int:pid>", methods=["GET"])
@login_required
@admin_required("sistema")
def admin_prompts_get(pid):
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM prompts WHERE id=?", (pid,)).fetchone()
    con.close()
    if not row: return jsonify({"ok": False, "error": "No encontrado"})
    return jsonify({"ok": True, "prompt": dict(row)})

@app.route("/api/admin/prompts/<int:pid>", methods=["PUT"])
@login_required
@admin_required("sistema")
def admin_prompts_update(pid):
    data = request.json or {}
    contenido = data.get("contenido","").strip()
    if not contenido: return jsonify({"ok": False, "error": "Contenido vacio"})
    con = sqlite3.connect(HIST_DB)
    con.execute("UPDATE prompts SET contenido=?, modificado=datetime('now') WHERE id=?", (contenido, pid))
    con.commit(); con.close(); return jsonify({"ok": True})

# ── Helpers BD usuarios/sesiones ─────────────────────────────────────────────
def get_user(username):
    try:
        con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM usuarios WHERE username=? AND activo=1", (username,)).fetchone()
        con.close(); return dict(row) if row else None
    except: return None

# registrar_sesion / actualizar_sesion / token_revocado ahora viven en core.py

# ══════════════════════════════════════════════════════════════════════════════
# SUBMÓDULOS CONSULTA DAT / RECHAZOS — agregar a app.py antes del if __name__
# Requiere: openpyxl   →   pip install openpyxl --break-system-packages
# ══════════════════════════════════════════════════════════════════════════════

def _resolver_tabla_dat(p):
    """Resuelve la tabla DAT_<anio> a partir del parámetro opcional 'anio' del
    request; si no viene o es inválido, usa el año actual. Valida rango para
    evitar que un valor arbitrario llegue al f-string de la query SQL."""
    import datetime as _dt
    anio = p.get("anio")
    try:
        anio = int(anio) if anio else _dt.date.today().year
    except (TypeError, ValueError):
        anio = _dt.date.today().year
    if anio < 2000 or anio > 2100:
        anio = _dt.date.today().year
    return f"DAT_{anio}", anio

@app.route("/api/sintia/dashboard")
@login_required
@modulo_required("sintia")
def sintia_dashboard():
    if not os.path.exists(DB_PATH):
        return jsonify({"ok": False, "error": "BD no cargada."})
    import datetime as _dt
    try:
        con = sqlite3.connect(DB_PATH, timeout=10)
        cur = con.cursor()
        anio = _dt.date.today().year
        tabla = f"DAT_{anio}"
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tabla,))
        if not cur.fetchone():
            con.close()
            return jsonify({"ok": False, "error": f"Tabla {tabla} no encontrada."})

        hoy = _dt.date.today()
        hoy_iso = hoy.isoformat()
        d30 = (hoy - _dt.timedelta(days=30)).isoformat()
        d60 = (hoy - _dt.timedelta(days=60)).isoformat()
        d7  = (hoy - _dt.timedelta(days=7)).isoformat()

        cur.execute(f"SELECT COUNT(*) FROM {tabla} WHERE FECHA_INGRESO_ISO BETWEEN ? AND ?", (d30, hoy_iso))
        total_mes = cur.fetchone()[0]
        cur.execute(f"SELECT COUNT(*) FROM {tabla} WHERE FECHA_INGRESO_ISO BETWEEN ? AND ?", (d60, d30))
        total_ant = cur.fetchone()[0]

        paises = {"BO": "Bolivia", "PY": "Paraguay", "BR": "Brasil", "CL": "Chile", "UY": "Uruguay"}
        por_pais = {}
        for cod, nombre in paises.items():
            cur.execute(f"SELECT COUNT(*) FROM {tabla} WHERE FECHA_INGRESO_ISO BETWEEN ? AND ? AND MIC LIKE ?", (d30, hoy_iso, f"%{cod}%"))
            cnt = cur.fetchone()[0]
            if cnt: por_pais[nombre] = cnt

        cur.execute(f"SELECT EST_MIC, COUNT(*) as cnt FROM {tabla} WHERE FECHA_INGRESO_ISO BETWEEN ? AND ? GROUP BY EST_MIC ORDER BY cnt DESC", (d30, hoy_iso))
        por_estado = {r[0]: r[1] for r in cur.fetchall() if r[0]}

        rechazos_mes = 0
        rechazos_7d  = 0
        alerta_rechazos = False
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='RECHAZOS'")
        if cur.fetchone():
            cur.execute("SELECT COUNT(*) FROM RECHAZOS WHERE Fecha_ISO BETWEEN ? AND ?", (d30, hoy_iso))
            rechazos_mes = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM RECHAZOS WHERE Fecha_ISO BETWEEN ? AND ?", (d7, hoy_iso))
            rechazos_7d = cur.fetchone()[0]
            prom_30 = rechazos_mes / 30
            prom_7  = rechazos_7d / 7
            alerta_rechazos = prom_7 > prom_30

        cur.execute(f"SELECT ADUANA, COUNT(*) as cnt FROM {tabla} WHERE FECHA_INGRESO_ISO BETWEEN ? AND ? GROUP BY ADUANA ORDER BY cnt DESC LIMIT 6", (d30, hoy_iso))
        top_aduanas = {r[0]: r[1] for r in cur.fetchall() if r[0]}

        cur.execute(f"SELECT MAX(FECHA_INGRESO_ISO) FROM {tabla}")
        ultima_fecha_bd = cur.fetchone()[0] or ''
        dias_sin_actualizar = 0
        if ultima_fecha_bd:
            try:
                uf = _dt.date.fromisoformat(ultima_fecha_bd[:10])
                dias_sin_actualizar = (hoy - uf).days
            except: pass

        evolucion = []
        for i in range(5, -1, -1):
            d = (hoy.replace(day=1) - _dt.timedelta(days=1)) if i > 0 else hoy
            for _ in range(i - 1):
                d = (d.replace(day=1) - _dt.timedelta(days=1))
            mes = d.strftime("%Y-%m")
            label = d.strftime("%b %Y")
            try:
                cur.execute("SELECT COUNT(*) FROM RECHAZOS WHERE Fecha_ISO LIKE ?", (f"{mes}%",))
                cnt = cur.fetchone()[0]
            except: cnt = 0
            evolucion.append({"mes": label, "total": cnt})

        con.close()
        return jsonify({
            "ok": True,
            "label_actual": "Últimos 30 días",
            "label_ant": "30 días anteriores",
            "total_mes": total_mes,
            "total_ant": total_ant,
            "variacion": round((total_mes - total_ant) / total_ant * 100, 1) if total_ant else 0,
            "por_pais": por_pais,
            "por_estado": por_estado,
            "rechazos_mes": rechazos_mes,
            "rechazos_7d": rechazos_7d,
            "alerta_rechazos": alerta_rechazos,
            "top_aduanas": top_aduanas,
            "dias_sin_actualizar": dias_sin_actualizar,
            "evolucion": evolucion,
        })
    except Exception as e:
        logging.error(f"SINTIA DASHBOARD ERROR | {e}")
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/sintia/dat", methods=["POST"])
@login_required
@modulo_required("sintia")
def sintia_dat_query():
    if not os.path.exists(DB_PATH):
        return jsonify({"ok": False, "error": "BD no cargada."})
    p = request.json or {}

    conditions, params = [], []

    if p.get("fec_d"):
        conditions.append("FECHA_INGRESO_ISO >= ?"); params.append(p["fec_d"])
    if p.get("fec_h"):
        conditions.append("FECHA_INGRESO_ISO <= ?"); params.append(p["fec_h"])
    if p.get("ftx_d"):
        conditions.append("FECHA_TRANS_ISO >= ?");  params.append(p["ftx_d"])
    if p.get("ftx_h"):
        conditions.append("FECHA_TRANS_ISO <= ?");  params.append(p["ftx_h"])
    if p.get("aduana"):
        conditions.append("ADUANA = ?");            params.append(p["aduana"])
    if p.get("tipo"):
        conditions.append("TIPO_REGISTRO = ?");     params.append(p["tipo"])
    if p.get("cargado"):
        conditions.append("CARGADO = ?");           params.append(p["cargado"])
    if p.get("pais"):
        conditions.append("MIC LIKE ?");            params.append(f"%{p['pais']}%")
    if p.get("mic"):
        conditions.append("MIC LIKE ?");            params.append(f"%{p['mic']}%")
    if p.get("lot"):
        conditions.append("LOT LIKE ?");            params.append(f"%{p['lot']}%")
    if p.get("empresa"):
        conditions.append("EMPRESA LIKE ?");        params.append(f"%{p['empresa']}%")
    if p.get("vehiculo"):
        conditions.append("(TRACTOR LIKE ? OR SEMI LIKE ?)");
        params += [f"%{p['vehiculo']}%", f"%{p['vehiculo']}%"]
    if p.get("est"):
        conditions.append("EST_MIC = ?");           params.append(p["est"])
    if p.get("novedad"):
        conditions.append("tiene_novedad = ?");     params.append(p["novedad"])

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    # Tabla según año elegido (por defecto, el actual)
    tabla, anio = _resolver_tabla_dat(p)

    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        # Verificar que la tabla existe
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tabla,))
        if not cur.fetchone():
            con.close()
            return jsonify({"ok": False, "error": f"Tabla {tabla} no encontrada en la BD."})

        sql = f"SELECT * FROM {tabla} {where} LIMIT ? OFFSET ?"
        page_size = int(p.get("page_size", 500))
        offset    = int(p.get("offset", 0))
        cur.execute(sql, params + [page_size + 1, offset])
        rows_raw = cur.fetchall()

        cols = list(rows_raw[0].keys()) if rows_raw else []
        truncated = len(rows_raw) > page_size
        rows = [list(r) for r in rows_raw[:page_size]]

        resumen = {"por_pais": {}, "por_estado": {}, "total": 0}
        try:
            cur.execute(f"SELECT COUNT(*) FROM {tabla} {where}", params)
            resumen["total"] = cur.fetchone()[0]
            paises = {"Bolivia": "BO", "Paraguay": "PY", "Brasil": "BR", "Chile": "CL", "Uruguay": "UY"}
            for nombre, cod in paises.items():
                cur.execute(f"SELECT COUNT(*) FROM {tabla} {where} {'AND' if conditions else 'WHERE'} MIC LIKE ?", params + [f"%{cod}%"])
                cnt = cur.fetchone()[0]
                if cnt: resumen["por_pais"][nombre] = cnt
            cur.execute(f"SELECT EST_MIC, COUNT(*) as cnt FROM {tabla} {where} GROUP BY EST_MIC ORDER BY cnt DESC", params)
            resumen["por_estado"] = {r[0]: r[1] for r in cur.fetchall()}
        except Exception as e:
            logging.warning(f"DAT RESUMEN ERROR | {e}")

        con.close()
        return jsonify({"ok": True, "cols": cols, "rows": rows, "truncated": truncated,
                        "resumen": resumen, "offset": offset, "page_size": page_size})

    except Exception as e:
        logging.error(f"DAT QUERY ERROR | {e}")
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/sintia/rec", methods=["POST"])
@login_required
@modulo_required("sintia")
def sintia_rec_query():
    if not os.path.exists(DB_PATH):
        return jsonify({"ok": False, "error": "BD no cargada."})
    p = request.json or {}

    conditions, params = [], []

    if p.get("fec_d"):
        conditions.append("Fecha_ISO >= ?");   params.append(p["fec_d"])
    if p.get("fec_h"):
        conditions.append("Fecha_ISO <= ?");   params.append(p["fec_h"])
    if p.get("pais"):
        conditions.append("PaisEmisor = ?");   params.append(p["pais"])
    if p.get("anio"):
        conditions.append("Anio = ?");         params.append(str(p["anio"]))
    if p.get("nromic"):
        conditions.append("NroMic LIKE ?");    params.append(f"%{p['nromic']}%")
    if p.get("mensaje"):
        conditions.append("Mensaje LIKE ?");   params.append(f"%{p['mensaje']}%")
    if p.get("metodo"):
        conditions.append("Metodo = ?");       params.append(p["metodo"])

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        page_size = int(p.get("page_size", 500))
        offset    = int(p.get("offset", 0))
        sql = f"SELECT PaisEmisor, Anio, NroMic, Fecha_ISO, Mes, Metodo, Mensaje FROM RECHAZOS {where} ORDER BY Fecha_ISO DESC LIMIT ? OFFSET ?"
        cur.execute(sql, params + [page_size + 1, offset])
        rows_raw = cur.fetchall()

        cols = list(rows_raw[0].keys()) if rows_raw else []
        truncated = len(rows_raw) > page_size
        rows = [list(r) for r in rows_raw[:page_size]]

        resumen = {"por_pais": {}, "por_metodo": {}, "total": 0}
        try:
            cur.execute(f"SELECT COUNT(*) FROM RECHAZOS {where}", params)
            resumen["total"] = cur.fetchone()[0]
            cur.execute(f"SELECT PaisEmisor, COUNT(*) as cnt FROM RECHAZOS {where} GROUP BY PaisEmisor ORDER BY cnt DESC", params)
            resumen["por_pais"] = {r[0]: r[1] for r in cur.fetchall()}
            cur.execute(f"SELECT Metodo, COUNT(*) as cnt FROM RECHAZOS {where} GROUP BY Metodo ORDER BY cnt DESC LIMIT 6", params)
            resumen["por_metodo"] = {r[0]: r[1] for r in cur.fetchall()}
        except Exception as e:
            logging.warning(f"REC RESUMEN ERROR | {e}")

        con.close()
        return jsonify({"ok": True, "cols": cols, "rows": rows, "truncated": truncated,
                        "resumen": resumen, "offset": offset, "page_size": page_size})

    except Exception as e:
        logging.error(f"REC QUERY ERROR | {e}")
        return jsonify({"ok": False, "error": str(e)})


def _exportar_xlsx(cols, rows):
    """Genera un Excel en memoria con los datos recibidos."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    wb = Workbook()
    ws = wb.active
    # Encabezado
    ws.append(cols)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1E2A3B")
        cell.alignment = Alignment(horizontal="center")
    # Datos
    for row in rows:
        ws.append(row)
    # Ajustar ancho
    for col in ws.columns:
        max_len = max((len(str(c.value or "")) for c in col), default=8)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 40)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


@app.route("/api/sintia/dat/export", methods=["POST"])
@login_required
@modulo_required("sintia")
def sintia_dat_export():
    """Re-ejecuta la query sin LIMIT para exportar todos los resultados."""
    if not os.path.exists(DB_PATH):
        return jsonify({"ok": False, "error": "BD no cargada."})
    p = request.json or {}

    conditions, params = [], []
    if p.get("fec_d"):   conditions.append("FECHA_INGRESO_ISO >= ?"); params.append(p["fec_d"])
    if p.get("fec_h"):   conditions.append("FECHA_INGRESO_ISO <= ?"); params.append(p["fec_h"])
    if p.get("ftx_d"):   conditions.append("FECHA_TRANS_ISO >= ?");   params.append(p["ftx_d"])
    if p.get("ftx_h"):   conditions.append("FECHA_TRANS_ISO <= ?");   params.append(p["ftx_h"])
    if p.get("aduana"):  conditions.append("ADUANA = ?");             params.append(p["aduana"])
    if p.get("tipo"):    conditions.append("TIPO_REGISTRO = ?");      params.append(p["tipo"])
    if p.get("cargado"): conditions.append("CARGADO = ?");            params.append(p["cargado"])
    if p.get("pais"):    conditions.append("MIC LIKE ?");             params.append(f"%{p['pais']}%")
    if p.get("mic"):     conditions.append("MIC LIKE ?");             params.append(f"%{p['mic']}%")
    if p.get("lot"):     conditions.append("LOT LIKE ?");             params.append(f"%{p['lot']}%")
    if p.get("empresa"): conditions.append("EMPRESA LIKE ?");         params.append(f"%{p['empresa']}%")
    if p.get("vehiculo"):
        conditions.append("(TRACTOR LIKE ? OR SEMI LIKE ?)");
        params += [f"%{p['vehiculo']}%", f"%{p['vehiculo']}%"]
    if p.get("est"):     conditions.append("EST_MIC = ?");            params.append(p["est"])
    if p.get("novedad"): conditions.append("tiene_novedad = ?");      params.append(p["novedad"])

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    tabla, anio = _resolver_tabla_dat(p)

    try:
        con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute(f"SELECT * FROM {tabla} {where}", params)
        rows_raw = cur.fetchall(); con.close()
        cols = list(rows_raw[0].keys()) if rows_raw else []
        rows = [list(r) for r in rows_raw]
        buf = _exportar_xlsx(cols, rows)
        return send_file(buf, as_attachment=True,
                         download_name=f"DAT_{anio}_consulta.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as e:
        logging.error(f"DAT EXPORT ERROR | {e}")
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/sintia/rec/export", methods=["POST"])
@login_required
@modulo_required("sintia")
def sintia_rec_export():
    """Re-ejecuta la query sin LIMIT para exportar todos los resultados."""
    if not os.path.exists(DB_PATH):
        return jsonify({"ok": False, "error": "BD no cargada."})
    p = request.json or {}

    conditions, params = [], []
    if p.get("fec_d"):   conditions.append("Fecha_ISO >= ?");  params.append(p["fec_d"])
    if p.get("fec_h"):   conditions.append("Fecha_ISO <= ?");  params.append(p["fec_h"])
    if p.get("pais"):    conditions.append("PaisEmisor = ?");  params.append(p["pais"])
    if p.get("anio"):    conditions.append("Anio = ?");        params.append(str(p["anio"]))
    if p.get("nromic"):  conditions.append("NroMic LIKE ?");   params.append(f"%{p['nromic']}%")
    if p.get("mensaje"): conditions.append("Mensaje LIKE ?");  params.append(f"%{p['mensaje']}%")
    if p.get("metodo"):  conditions.append("Metodo = ?");      params.append(p["metodo"])

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    try:
        con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute(f"SELECT PaisEmisor, Anio, NroMic, Fecha_ISO, Mes, Metodo, Mensaje FROM RECHAZOS {where} ORDER BY Fecha_ISO DESC", params)
        rows_raw = cur.fetchall(); con.close()
        cols = list(rows_raw[0].keys()) if rows_raw else []
        rows = [list(r) for r in rows_raw]
        buf = _exportar_xlsx(cols, rows)
        return send_file(buf, as_attachment=True,
                         download_name="RECHAZOS_consulta.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as e:
        logging.error(f"REC EXPORT ERROR | {e}")
        return jsonify({"ok": False, "error": str(e)})



# MÓDULOS GARMIN + TRAINING -> blueprints/training.py (registrado como training_bp)



# MÓDULO STOCK -> blueprints/stock.py (registrado como stock_bp)


# ══════════════════════════════════════════════════════════════════════════════
# MÓDULO REF. ADUANAS / DIRA
# ══════════════════════════════════════════════════════════════════════════════

def _get_dira_nombres():
    """Lee el dict {indice: nombre} de direcciones regionales desde ref_dira (BD)."""
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = con.execute("SELECT indice, nombre FROM ref_dira ORDER BY orden, indice").fetchall()
    con.close()
    return {r["indice"]: r["nombre"] for r in rows}

@app.route("/api/admin/ref-dira")
@login_required
def ref_dira_list():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT indice, nombre, orden FROM ref_dira ORDER BY orden, indice"
    ).fetchall()]
    con.close()
    return jsonify({"ok": True, "rows": rows})

@app.route("/api/admin/ref-dira/save", methods=["POST"])
@login_required
@admin_required("bd")
def ref_dira_save():
    """Reemplaza completamente la tabla ref_dira con el array editado en vivo.
    Body esperado: {"rows": [{"indice": "1", "nombre": "HIDROVIA", "orden": 1}, ...]}"""
    data = request.get_json(force=True) or {}
    rows = data.get("rows")
    if not isinstance(rows, list) or not rows:
        return jsonify({"ok": False, "error": "No se recibieron filas para guardar"}), 400

    # Validar que ningún índice a eliminar siga referenciado en ref_aduanas
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    indices_en_uso = {r["indice_dira"] for r in con.execute("SELECT DISTINCT indice_dira FROM ref_aduanas").fetchall()}

    nuevos = []
    errores = []
    indices_vistos = set()
    for i, row in enumerate(rows, 1):
        try:
            indice = str(row.get("indice", "")).strip()
            nombre = str(row.get("nombre", "")).strip().upper()
            try:
                orden = int(row.get("orden", i))
            except (TypeError, ValueError):
                orden = i
            if not indice:
                errores.append(f"Fila {i}: índice vacío")
                continue
            if not nombre:
                errores.append(f"Fila {i} (índice {indice}): nombre vacío")
                continue
            if indice in indices_vistos:
                errores.append(f"Fila {i}: índice '{indice}' duplicado — se mantiene la última ocurrencia")
            indices_vistos.add(indice)
            nuevos.append((indice, nombre, orden))
        except Exception as e:
            errores.append(f"Fila {i}: {e}")

    if not nuevos:
        con.close()
        return jsonify({"ok": False, "error": "Ninguna fila válida para guardar", "detalle_errores": errores}), 400

    dedup = {}
    for indice, nombre, orden in nuevos:
        dedup[indice] = (nombre, orden)
    nuevos_dedup = [(indice, n, o) for indice, (n, o) in dedup.items()]
    indices_finales = set(dedup.keys())

    # Bloquear si se eliminaría un índice DIRA que todavía usa alguna aduana
    indices_eliminados = indices_en_uso - indices_finales
    if indices_eliminados:
        con.close()
        return jsonify({
            "ok": False,
            "error": f"No se puede eliminar el/los índice(s) {', '.join(sorted(indices_eliminados))} porque hay aduanas que todavía los usan. Reasigná esas aduanas primero en Ref. Aduanas."
        }), 400

    con.execute("DELETE FROM ref_dira")
    con.executemany("INSERT INTO ref_dira (indice, nombre, orden) VALUES (?,?,?)", nuevos_dedup)
    con.execute("INSERT INTO ref_aduanas_log (fecha, usuario, accion, detalle) VALUES (datetime('now'),?,?,?)",
                (session.get("username","?"), "save_ref_dira",
                 f"{len(nuevos_dedup)} direcciones regionales guardadas" + (f", {len(errores)} filas con error" if errores else "")))
    con.commit(); con.close()
    logging.info(f"REF_DIRA SAVE | user={session.get('username')} | {len(nuevos_dedup)} filas | {len(errores)} errores")
    return jsonify({"ok": True, "guardadas": len(nuevos_dedup), "errores": errores[:30]})

@app.route("/api/admin/ref-aduanas")
@login_required
def ref_aduanas_list():
    dira_nombres = _get_dira_nombres()
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT cod, nombre, indice_dira FROM ref_aduanas ORDER BY indice_dira, nombre"
    ).fetchall()]
    con.close()
    for r in rows:
        r["dira_nombre"] = dira_nombres.get(r["indice_dira"], "N/E")
    return jsonify({"ok": True, "rows": rows, "dira_nombres": dira_nombres})

@app.route("/api/admin/ref-aduanas/download")
@login_required
def ref_aduanas_download():
    """Descarga la tabla actual en el mismo formato CSV de referencia (; como separador)."""
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = con.execute("SELECT cod, nombre, indice_dira FROM ref_aduanas ORDER BY indice_dira, nombre").fetchall()
    con.close()
    lines = ["indice_dira;nombre;cod"]
    for r in rows:
        lines.append(f"{r['indice_dira']};{r['nombre']};{int(r['cod'])}")
    csv_content = "\r\n".join(lines) + "\r\n"
    from flask import Response
    return Response(csv_content, mimetype="text/csv",
                     headers={"Content-Disposition": "attachment; filename=ref_aduanas.csv"})

@app.route("/api/admin/ref-aduanas/save", methods=["POST"])
@login_required
@admin_required("bd")
def ref_aduanas_save():
    """Reemplaza completamente la tabla ref_aduanas con el array editado en vivo desde el panel.
    Body esperado: {"rows": [{"cod": "001", "nombre": "BUENOS AIRES", "indice_dira": "8"}, ...]}"""
    dira_nombres = _get_dira_nombres()
    data = request.get_json(force=True) or {}
    rows = data.get("rows")
    if not isinstance(rows, list) or not rows:
        return jsonify({"ok": False, "error": "No se recibieron filas para guardar"}), 400

    nuevos = []
    errores = []
    cods_vistos = set()
    for i, row in enumerate(rows, 1):
        try:
            cod = str(row.get("cod", "")).strip().zfill(3)
            nombre = str(row.get("nombre", "")).strip().upper()
            indice_dira = str(row.get("indice_dira", "")).strip()
            if not cod or not cod.isdigit():
                errores.append(f"Fila {i}: código inválido '{row.get('cod')}'")
                continue
            if not nombre:
                errores.append(f"Fila {i} (cod {cod}): nombre vacío")
                continue
            if indice_dira not in dira_nombres:
                errores.append(f"Fila {i} (cod {cod}): índice DIRA '{indice_dira}' inválido")
                continue
            if cod in cods_vistos:
                errores.append(f"Fila {i}: código '{cod}' duplicado en la tabla — se mantiene la última ocurrencia")
            cods_vistos.add(cod)
            nuevos.append((cod, nombre, indice_dira))
        except Exception as e:
            errores.append(f"Fila {i}: {e}")

    if not nuevos:
        return jsonify({"ok": False, "error": "Ninguna fila válida para guardar", "detalle_errores": errores}), 400

    # Deduplicar por cod conservando la última ocurrencia (consistente con el aviso de arriba)
    dedup = {}
    for cod, nombre, indice_dira in nuevos:
        dedup[cod] = (nombre, indice_dira)
    nuevos_dedup = [(cod, n, d) for cod, (n, d) in dedup.items()]

    con = sqlite3.connect(HIST_DB)
    con.execute("DELETE FROM ref_aduanas")
    con.executemany("INSERT INTO ref_aduanas (cod, nombre, indice_dira) VALUES (?,?,?)", nuevos_dedup)
    con.execute("INSERT INTO ref_aduanas_log (fecha, usuario, accion, detalle) VALUES (datetime('now'),?,?,?)",
                (session.get("username","?"), "save_tabla_completa",
                 f"{len(nuevos_dedup)} aduanas guardadas" + (f", {len(errores)} filas con error" if errores else "")))
    con.commit(); con.close()
    logging.info(f"REF_ADUANAS SAVE | user={session.get('username')} | {len(nuevos_dedup)} filas | {len(errores)} errores")
    return jsonify({"ok": True, "guardadas": len(nuevos_dedup), "errores": errores[:30]})


@app.route("/api/admin/feriados", methods=["GET"])
@login_required
@admin_required("bd")
def feriados_list():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT fecha, descripcion FROM feriados ORDER BY fecha"
    ).fetchall()]
    con.close()
    return jsonify({"ok": True, "rows": rows})

@app.route("/api/admin/feriados", methods=["POST"])
@login_required
@admin_required("bd")
def feriados_create():
    """Body esperado: {"fecha": "2027-01-01", "descripcion": "Año Nuevo"}"""
    data = request.get_json(force=True) or {}
    fecha = str(data.get("fecha", "")).strip()
    descripcion = str(data.get("descripcion", "")).strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", fecha):
        return jsonify({"ok": False, "error": "Fecha inválida, formato esperado AAAA-MM-DD"}), 400
    try:
        datetime.strptime(fecha, "%Y-%m-%d")
    except ValueError:
        return jsonify({"ok": False, "error": "Fecha inválida"}), 400
    con = sqlite3.connect(HIST_DB)
    con.execute("INSERT OR REPLACE INTO feriados (fecha, descripcion) VALUES (?,?)", (fecha, descripcion))
    con.commit(); con.close()
    logging.info(f"FERIADOS CREATE | user={session.get('username')} | {fecha} | {descripcion}")
    return jsonify({"ok": True})

@app.route("/api/admin/feriados/<fecha>", methods=["DELETE"])
@login_required
@admin_required("bd")
def feriados_delete(fecha):
    con = sqlite3.connect(HIST_DB)
    cur = con.execute("DELETE FROM feriados WHERE fecha=?", (fecha,))
    con.commit()
    borrado = cur.rowcount > 0
    con.close()
    if not borrado:
        return jsonify({"ok": False, "error": "No existe ese feriado"}), 404
    logging.info(f"FERIADOS DELETE | user={session.get('username')} | {fecha}")
    return jsonify({"ok": True})


# ══════════════════════════════════════════════════════════════════════════════
# MÓDULO FINANZAS
# ══════════════════════════════════════════════════════════════════════════════
import io
import pdfplumber
import finanzas as fin
from extracto_parser import parse_santander, parse_galicia, extraer_total_declarado

fin.init_finanzas_db(HIST_DB)


def _extraer_paginas_pdf(file_storage):
    data = file_storage.read()
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return [p.extract_text() or "" for p in pdf.pages]


@app.route("/finanzas")
@login_required
@finanzas_owner_required
def finanzas_index():
    return render_template("finanzas.html", username=session.get("username", ""))


@app.route("/api/finanzas/tarjetas", methods=["GET"])
@login_required
@finanzas_owner_required
def api_fin_tarjetas():
    return jsonify({"ok": True, "rows": fin.get_tarjetas(HIST_DB)})


@app.route("/api/finanzas/tarjetas", methods=["POST"])
@login_required
@finanzas_owner_required
def api_fin_tarjeta_crear():
    data = request.json or {}
    nombre = data.get("nombre", "").strip()
    motor = data.get("motor", "")
    dia_cierre = data.get("dia_cierre")
    if not nombre or motor not in ("santander", "galicia"):
        return jsonify({"ok": False, "error": "Nombre y motor (santander|galicia) son requeridos"})
    tid = fin.crear_tarjeta(HIST_DB, nombre, motor, dia_cierre)
    return jsonify({"ok": True, "id": tid})


@app.route("/api/finanzas/tarjetas/<tid>/monto", methods=["POST"])
@login_required
@finanzas_owner_required
def api_fin_tarjeta_monto(tid):
    """Fija cuánto hay que pagar de esta tarjeta en un mes dado (del resumen: total o pago mínimo)."""
    data = request.json or {}
    mes = data.get("mes") or date.today().isoformat()[:7]
    monto = data.get("monto_a_pagar")
    if monto is None:
        return jsonify({"ok": False, "error": "Falta el monto"})
    con = sqlite3.connect(HIST_DB)
    con.execute(
        "INSERT INTO fin_tarjetas_montos (tarjeta_id, mes, monto_a_pagar) VALUES (?,?,?) "
        "ON CONFLICT(tarjeta_id, mes) DO UPDATE SET monto_a_pagar=excluded.monto_a_pagar",
        (tid, mes, float(monto)))
    con.commit(); con.close()
    return jsonify({"ok": True})


@app.route("/api/finanzas/tarjetas/estado_pago")
@login_required
@finanzas_owner_required
def api_fin_tarjetas_estado_pago():
    """Para cada tarjeta con un monto a pagar fijado este mes: cuánto se
    pagó ya (movimientos tipo='pago' de esa tarjeta en el mes) y cuánto falta."""
    mes = request.args.get("mes") or date.today().isoformat()[:7]
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT t.id as tarjeta_id, t.nombre, m.monto_a_pagar, "
        "COALESCE((SELECT SUM(monto_ars) FROM fin_movimientos "
        " WHERE tarjeta_id=t.id AND tipo='pago' AND substr(fecha,1,7)=?), 0) as pagado "
        "FROM fin_tarjetas t JOIN fin_tarjetas_montos m ON m.tarjeta_id=t.id AND m.mes=? "
        "WHERE m.monto_a_pagar > 0", (mes, mes)).fetchall()
    con.close()
    resultado = []
    for r in rows:
        falta = round(r["monto_a_pagar"] - r["pagado"], 2)
        resultado.append({
            "tarjeta_id": r["tarjeta_id"], "nombre": r["nombre"],
            "monto_a_pagar": round(r["monto_a_pagar"], 2), "pagado": round(r["pagado"], 2),
            "falta": max(falta, 0), "saldado": falta <= 0,
        })
    return jsonify({"ok": True, "mes": mes, "rows": resultado})


@app.route("/api/finanzas/upload", methods=["POST"])
@login_required
@finanzas_owner_required
@limiter.limit("30 per hour", error_message="Demasiados uploads de resúmenes.")
def api_fin_upload():
    """Parsea el PDF y devuelve una previsualización editable. Todavía no guarda nada."""
    if "archivo" not in request.files:
        return jsonify({"ok": False, "error": "No se recibió archivo"})
    tarjeta_id = request.form.get("tarjeta_id")
    if not tarjeta_id:
        return jsonify({"ok": False, "error": "Falta seleccionar la tarjeta"})

    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    tarjeta = con.execute("SELECT * FROM fin_tarjetas WHERE id=?", (tarjeta_id,)).fetchone()
    con.close()
    if not tarjeta:
        return jsonify({"ok": False, "error": "Tarjeta no encontrada"})

    archivo = request.files["archivo"]
    if not archivo.filename.lower().endswith(".pdf"):
        return jsonify({"ok": False, "error": "El archivo debe ser un PDF"})

    try:
        paginas = _extraer_paginas_pdf(archivo)
    except Exception as e:
        return jsonify({"ok": False, "error": f"No se pudo leer el PDF: {e}"})

    motor = tarjeta["motor"]
    try:
        if motor == "santander":
            movimientos = parse_santander(paginas, date.today().year)
        elif motor == "galicia":
            movimientos = parse_galicia(paginas)
        else:
            return jsonify({"ok": False, "error": f"La tarjeta '{tarjeta['nombre']}' no tiene un motor de parseo válido"})
    except Exception as e:
        logging.error(f"FINANZAS PARSE ERROR | {e}")
        return jsonify({"ok": False, "error": f"Error parseando el resumen: {e}"})

    if not movimientos:
        return jsonify({"ok": False, "error": "No se encontraron movimientos en el PDF. ¿Es el resumen correcto para esta tarjeta?"})

    total_ars, total_usd = extraer_total_declarado(paginas, motor)
    calc_ars = round(sum(m["monto_ars"] for m in movimientos if m["tipo"] == "consumo"), 2)
    calc_usd = round(sum(m["monto_usd"] for m in movimientos if m["tipo"] == "consumo"), 2)
    validado = total_ars is not None and abs(calc_ars - total_ars) <= 1.0

    fechas_consumo = [m["fecha"] for m in movimientos if m["tipo"] == "consumo"]
    periodo_desde = min(fechas_consumo) if fechas_consumo else None
    periodo_hasta = max(fechas_consumo) if fechas_consumo else None

    # Pre-categorizar para la previsualización (todavía no se guarda en BD)
    posibles_dup_total = 0
    for m in movimientos:
        if m["tipo"] == "consumo":
            m["categoria_id"] = fin.categorizar(HIST_DB, m["descripcion"])
        elif m["tipo"] == "cargo":
            m["categoria_id"] = "cargos_tarjeta"
        else:
            m["categoria_id"] = None
        m["posibles_duplicados"] = fin.buscar_posible_duplicado(HIST_DB, tarjeta_id, m["fecha"], m["monto_ars"])
        if m["posibles_duplicados"]:
            posibles_dup_total += 1

    return jsonify({
        "ok": True,
        "tarjeta_id": tarjeta_id,
        "archivo_nombre": archivo.filename,
        "posibles_duplicados_total": posibles_dup_total,
        "movimientos": movimientos,
        "total_declarado_ars": total_ars,
        "total_declarado_usd": total_usd,
        "total_calculado_ars": calc_ars,
        "total_calculado_usd": calc_usd,
        "validado": validado,
        "periodo_desde": periodo_desde,
        "periodo_hasta": periodo_hasta,
    })


@app.route("/api/finanzas/confirmar", methods=["POST"])
@login_required
@finanzas_owner_required
def api_fin_confirmar():
    """Guarda en BD los movimientos que el usuario ya revisó/corrigió en la previsualización."""
    data = request.json or {}
    tarjeta_id = data.get("tarjeta_id")
    movimientos = data.get("movimientos", [])
    if not tarjeta_id or not movimientos:
        return jsonify({"ok": False, "error": "Faltan datos"})

    try:
        resumen_id = fin.guardar_resumen(
            HIST_DB, tarjeta_id, data.get("archivo_nombre", ""),
            data.get("periodo_desde"), data.get("periodo_hasta"),
            data.get("total_declarado_ars") or 0, data.get("total_calculado_ars") or 0,
            bool(data.get("validado")), session.get("username", "?"))
        guardados = fin.guardar_movimientos(HIST_DB, tarjeta_id, resumen_id, movimientos)
    except (KeyError, TypeError, ValueError) as e:
        logging.error(f"FINANZAS CONFIRMAR ERROR | {e}")
        return jsonify({"ok": False, "error": f"Los datos recibidos no tienen el formato esperado: {e}"})

    logging.info(f"FINANZAS UPLOAD | user={session.get('username')} | tarjeta={tarjeta_id} | movs={len(guardados)}")
    notificar_telegram(f"💳 Resumen '{data.get('archivo_nombre','')}' cargado por {session.get('username')} — {len(guardados)} movimientos")
    return jsonify({"ok": True, "resumen_id": resumen_id, "guardados": len(guardados)})


@app.route("/api/finanzas/movimiento_manual", methods=["POST"])
@login_required
@finanzas_owner_required
def api_fin_movimiento_manual():
    """Carga manual de un gasto (ej. transferencia bancaria) o de un pago
    hecho sobre una tarjeta, sin pasar por un PDF."""
    data = request.json or {}
    fecha = data.get("fecha")
    descripcion = (data.get("descripcion") or "").strip()
    monto_ars = data.get("monto_ars")
    categoria_id = data.get("categoria_id")
    tipo = data.get("tipo", "consumo")
    if tipo not in ("consumo", "pago"):
        tipo = "consumo"
    tarjeta_id = data.get("tarjeta_id") if tipo == "pago" else None
    forzar = bool(data.get("forzar"))
    if not fecha or not descripcion or monto_ars is None:
        return jsonify({"ok": False, "error": "Faltan fecha, descripción o monto"})
    if tipo == "pago" and not tarjeta_id:
        return jsonify({"ok": False, "error": "Elegí a qué tarjeta corresponde el pago"})

    if not forzar:
        dups = fin.buscar_posible_duplicado(HIST_DB, tarjeta_id or "manual", fecha, float(monto_ars))
        if dups:
            return jsonify({"ok": False, "posible_duplicado": True, "coincidencias": dups,
                             "error": "Ya existe un movimiento con monto y fecha similares. "
                                      "Reenviá con forzar=true si igual querés cargarlo."})

    mov = {
        "fecha": fecha, "descripcion": descripcion, "comprobante": "",
        "monto_ars": float(monto_ars), "monto_usd": 0.0,
        "cuota_actual": None, "cuota_total": None, "tipo": tipo,
    }
    guardados = fin.guardar_movimientos(HIST_DB, tarjeta_id or "manual", None, [mov], origen="manual")
    if tipo == "consumo":
        cat_final = categoria_id or fin.categorizar(HIST_DB, descripcion)
        if cat_final:
            fin.recategorizar_movimiento(HIST_DB, guardados[0]["id"], cat_final, aprender=False)
            guardados[0]["categoria_id"] = cat_final
    return jsonify({"ok": True, "movimiento": guardados[0]})


@app.route("/api/finanzas/movimientos")
@login_required
@finanzas_owner_required
def api_fin_movimientos():
    mes = request.args.get("mes")
    tarjeta_id = request.args.get("tarjeta_id")
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    q = ("SELECT m.*, c.nombre as categoria_nombre, c.color as categoria_color, "
         "t.nombre as tarjeta_nombre FROM fin_movimientos m "
         "LEFT JOIN fin_categorias c ON m.categoria_id=c.id "
         "LEFT JOIN fin_tarjetas t ON m.tarjeta_id=t.id WHERE 1=1")
    params = []
    if mes:
        q += " AND substr(m.fecha,1,7)=?"; params.append(mes)
    if tarjeta_id:
        q += " AND m.tarjeta_id=?"; params.append(tarjeta_id)
    q += " ORDER BY m.fecha DESC"
    rows = [dict(r) for r in con.execute(q, params).fetchall()]
    con.close()
    return jsonify({"ok": True, "rows": rows})


@app.route("/api/finanzas/movimientos/export")
@login_required
@finanzas_owner_required
def api_fin_movimientos_export():
    """Exporta a Excel los mismos movimientos que /api/finanzas/movimientos,
    con los mismos filtros opcionales de mes/tarjeta."""
    mes = request.args.get("mes")
    tarjeta_id = request.args.get("tarjeta_id")
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    q = ("SELECT m.fecha, m.descripcion, t.nombre as tarjeta, c.nombre as categoria, "
         "m.monto_ars, m.monto_usd, m.cuota_actual, m.cuota_total, m.tipo, m.origen "
         "FROM fin_movimientos m "
         "LEFT JOIN fin_categorias c ON m.categoria_id=c.id "
         "LEFT JOIN fin_tarjetas t ON m.tarjeta_id=t.id WHERE 1=1")
    params = []
    if mes:
        q += " AND substr(m.fecha,1,7)=?"; params.append(mes)
    if tarjeta_id:
        q += " AND m.tarjeta_id=?"; params.append(tarjeta_id)
    q += " ORDER BY m.fecha DESC"
    cur = con.execute(q, params)
    cols = [d[0] for d in cur.description]
    rows = [list(r) for r in cur.fetchall()]
    con.close()
    buf = _exportar_xlsx(cols, rows)
    nombre = f"finanzas_{mes or 'todo'}.xlsx"
    return send_file(buf, as_attachment=True, download_name=nombre,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ── Servicios recurrentes ──────────────────────────────────────────────────────
@app.route("/api/finanzas/servicios")
@login_required
@finanzas_owner_required
def api_fin_servicios_list():
    mes = request.args.get("mes") or datetime.now().strftime("%Y-%m")
    con = sqlite3.connect(HIST_DB)
    rows = _estado_servicios_mes(con, mes)
    con.close()
    return jsonify({"ok": True, "mes": mes, "rows": rows})

@app.route("/api/finanzas/servicios", methods=["POST"])
@login_required
@finanzas_owner_required
def api_fin_servicios_create():
    data = request.json or {}
    nombre = (data.get("nombre") or "").strip()
    patron = (data.get("patron") or "").strip()
    if not nombre:
        return jsonify({"ok": False, "error": "Falta el nombre"})
    con = sqlite3.connect(HIST_DB)
    orden = con.execute("SELECT COALESCE(MAX(orden),0)+1 FROM fin_servicios").fetchone()[0]
    con.execute("INSERT INTO fin_servicios (nombre, patron, orden) VALUES (?,?,?)", (nombre, patron, orden))
    con.commit(); con.close()
    return jsonify({"ok": True})

@app.route("/api/finanzas/servicios/<int:sid>", methods=["DELETE"])
@login_required
@finanzas_owner_required
def api_fin_servicios_delete(sid):
    con = sqlite3.connect(HIST_DB)
    con.execute("UPDATE fin_servicios SET activo=0 WHERE id=?", (sid,))
    con.commit(); con.close()
    return jsonify({"ok": True})

@app.route("/api/finanzas/servicios/<int:sid>/pagar", methods=["POST"])
@login_required
@finanzas_owner_required
def api_fin_servicios_pagar(sid):
    """Marca (o desmarca) el pago manualmente, para el caso en que la
    descripción del movimiento no coincida con ningún patrón automático."""
    data = request.json or {}
    mes = data.get("mes") or datetime.now().strftime("%Y-%m")
    pagado = 1 if data.get("pagado", True) else 0
    con = sqlite3.connect(HIST_DB)
    con.execute("INSERT INTO fin_servicios_pagos (servicio_id, mes, pagado, fecha_pago) VALUES (?,?,?,?) "
                "ON CONFLICT(servicio_id, mes) DO UPDATE SET pagado=excluded.pagado, fecha_pago=excluded.fecha_pago",
                (sid, mes, pagado, datetime.now().strftime("%Y-%m-%d") if pagado else None))
    con.commit(); con.close()
    return jsonify({"ok": True})


@app.route("/api/finanzas/movimientos/<mov_id>/categoria", methods=["POST"])
@login_required
@finanzas_owner_required
def api_fin_recategorizar(mov_id):
    data = request.json or {}
    categoria_id = data.get("categoria_id")
    if not categoria_id:
        return jsonify({"ok": False, "error": "Falta categoria_id"})
    ok = fin.recategorizar_movimiento(HIST_DB, mov_id, categoria_id, aprender=data.get("aprender", True))
    return jsonify({"ok": ok})


@app.route("/api/finanzas/movimientos/<mov_id>", methods=["DELETE"])
@login_required
@finanzas_owner_required
def api_fin_eliminar_movimiento(mov_id):
    ok = fin.eliminar_movimiento(HIST_DB, mov_id)
    if not ok:
        return jsonify({"ok": False, "error": "No se encontró el movimiento"})
    logging.info(f"FINANZAS DELETE | user={session.get('username')} | mov={mov_id}")
    return jsonify({"ok": True})


@app.route("/api/finanzas/categorias", methods=["GET"])
@login_required
@finanzas_owner_required
def api_fin_categorias():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM fin_categorias ORDER BY orden").fetchall()]
    con.close()
    return jsonify({"ok": True, "rows": rows})


@app.route("/api/finanzas/categorias", methods=["POST"])
@login_required
@finanzas_owner_required
def api_fin_crear_categoria():
    data = request.json or {}
    nombre = (data.get("nombre") or "").strip()
    color = data.get("color") or "#9CA3AF"
    presupuesto_mensual = data.get("presupuesto_mensual") or 0
    if not nombre:
        return jsonify({"ok": False, "error": "Falta el nombre de la categoría"})
    try:
        cat_id = fin.crear_categoria(HIST_DB, nombre, color, float(presupuesto_mensual))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True, "id": cat_id})


@app.route("/api/finanzas/categorias/<cat_id>/presupuesto", methods=["POST"])
@login_required
@finanzas_owner_required
def api_fin_set_presupuesto_cat(cat_id):
    data = request.json or {}
    monto = data.get("monto")
    mes = data.get("mes") or date.today().isoformat()[:7]
    if monto is None:
        return jsonify({"ok": False, "error": "Falta monto"})
    monto = float(monto)

    total_mes = fin.get_presupuesto_total(HIST_DB, mes)
    if total_mes is not None:
        suma_otras = fin.suma_presupuesto_categorias(HIST_DB, excluir_categoria_id=cat_id)
        if suma_otras + monto > total_mes + 0.01:
            disponible = round(total_mes - suma_otras, 2)
            return jsonify({
                "ok": False,
                "error": f"Superarías el presupuesto total del mes (${total_mes:,.2f}). "
                         f"A esta categoría le queda como máximo ${disponible:,.2f}."
            })

    fin.set_presupuesto_categoria(HIST_DB, cat_id, monto)
    return jsonify({"ok": True})


@app.route("/api/finanzas/presupuesto", methods=["POST"])
@login_required
@finanzas_owner_required
def api_fin_set_presupuesto_total():
    data = request.json or {}
    mes = data.get("mes")
    monto = data.get("monto")
    if not mes or monto is None:
        return jsonify({"ok": False, "error": "Faltan mes y monto"})
    monto = float(monto)

    suma_cats = fin.suma_presupuesto_categorias(HIST_DB)
    if suma_cats > monto + 0.01:
        return jsonify({
            "ok": False,
            "error": f"La suma de presupuestos por categoría (${suma_cats:,.2f}) ya supera ese total. "
                     f"Bajá alguna categoría primero o poné un total mayor."
        })

    fin.set_presupuesto_total(HIST_DB, mes, monto)
    return jsonify({"ok": True})


@app.route("/api/finanzas/resumen")
@login_required
@finanzas_owner_required
def api_fin_resumen():
    mes = request.args.get("mes") or date.today().isoformat()[:7]
    return jsonify({
        "ok": True,
        "resumen": fin.resumen_mes(HIST_DB, mes),
        "categorias": fin.gasto_por_categoria(HIST_DB, mes),
    })


@app.route("/api/finanzas/comparativo")
@login_required
@finanzas_owner_required
def api_fin_comparativo():
    mes = request.args.get("mes") or date.today().isoformat()[:7]
    return jsonify({"ok": True, **fin.comparativo_por_categoria(HIST_DB, mes)})


@app.route("/api/finanzas/proyeccion")
@login_required
@finanzas_owner_required
def api_fin_proyeccion():
    mes = request.args.get("mes") or date.today().isoformat()[:7]
    return jsonify({"ok": True, "proyeccion": fin.proyeccion_cierre_mes(HIST_DB, mes)})


@app.route("/api/finanzas/atipicos")
@login_required
@finanzas_owner_required
def api_fin_atipicos():
    mes = request.args.get("mes") or date.today().isoformat()[:7]
    return jsonify({"ok": True, "rows": fin.gastos_atipicos(HIST_DB, mes)})


@app.route("/api/finanzas/evolucion_anual")
@login_required
@finanzas_owner_required
def api_fin_evolucion_anual():
    mes = request.args.get("mes") or date.today().isoformat()[:7]
    return jsonify({"ok": True, "rows": fin.evolucion_anual(HIST_DB, mes)})


@app.route("/api/finanzas/resumenes_subidos")
@login_required
@finanzas_owner_required
def api_fin_resumenes_subidos():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT r.*, t.nombre as tarjeta_nombre FROM fin_resumenes r "
        "LEFT JOIN fin_tarjetas t ON r.tarjeta_id=t.id ORDER BY r.creado DESC LIMIT 50"
    ).fetchall()]
    con.close()
    return jsonify({"ok": True, "rows": rows})


# ── Declaraciones Juradas ────────────────────────────────────────────────────

@app.route("/api/finanzas/ddjj", methods=["GET"])
@login_required
@finanzas_owner_required
def api_ddjj_list():
    return jsonify({"ok": True, "rows": fin.listar_ddjj(HIST_DB)})


@app.route("/api/finanzas/ddjj", methods=["POST"])
@login_required
@finanzas_owner_required
def api_ddjj_crear():
    data = request.json or {}
    try:
        anio = int(data.get("anio"))
        fecha_cierre = str(data.get("fecha_cierre", "")).strip()
        valor_dolar = float(data.get("valor_dolar"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "anio, fecha_cierre y valor_dolar son requeridos y numéricos"}), 400
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", fecha_cierre):
        return jsonify({"ok": False, "error": "fecha_cierre debe ser AAAA-MM-DD"}), 400
    try:
        did = fin.crear_ddjj(HIST_DB, anio, fecha_cierre, valor_dolar)
    except sqlite3.IntegrityError:
        return jsonify({"ok": False, "error": f"Ya existe una DDJJ para el año {anio}"}), 400
    logging.info(f"DDJJ CREATE | user={session.get('username')} | anio={anio} | dolar={valor_dolar}")
    return jsonify({"ok": True, "id": did})


@app.route("/api/finanzas/ddjj/<ddjj_id>", methods=["PUT"])
@login_required
@finanzas_owner_required
def api_ddjj_actualizar(ddjj_id):
    data = request.json or {}
    campos = {k: v for k, v in data.items() if k in ("fecha_cierre", "valor_dolar", "estado", "fecha_presentacion")}
    fin.actualizar_ddjj(HIST_DB, ddjj_id, **campos)
    logging.info(f"DDJJ UPDATE | user={session.get('username')} | id={ddjj_id} | campos={list(campos.keys())}")
    return jsonify({"ok": True})


@app.route("/api/finanzas/ddjj/<ddjj_id>", methods=["DELETE"])
@login_required
@finanzas_owner_required
def api_ddjj_borrar(ddjj_id):
    fin.borrar_ddjj(HIST_DB, ddjj_id)
    logging.info(f"DDJJ DELETE | user={session.get('username')} | id={ddjj_id}")
    return jsonify({"ok": True})


@app.route("/api/finanzas/ddjj/<ddjj_id>/dinero", methods=["GET"])
@login_required
@finanzas_owner_required
def api_ddjj_dinero_list(ddjj_id):
    return jsonify({"ok": True, "rows": fin.listar_dinero_ddjj(HIST_DB, ddjj_id)})


@app.route("/api/finanzas/ddjj/<ddjj_id>/dinero", methods=["POST"])
@login_required
@finanzas_owner_required
def api_ddjj_dinero_crear(ddjj_id):
    data = request.json or {}
    try:
        rid = fin.crear_dinero_ddjj(
            HIST_DB, ddjj_id,
            fecha=str(data.get("fecha", "")).strip(),
            banco=str(data.get("banco", "")).strip(),
            cuenta=str(data.get("cuenta", "")).strip(),
            cbu=str(data.get("cbu", "")).strip(),
            moneda=str(data.get("moneda", "")).strip().upper(),
            importe=float(data.get("importe")),
        )
    except (TypeError, ValueError) as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "id": rid})


@app.route("/api/finanzas/ddjj/dinero/<reg_id>", methods=["DELETE"])
@login_required
@finanzas_owner_required
def api_ddjj_dinero_borrar(reg_id):
    fin.borrar_dinero_ddjj(HIST_DB, reg_id)
    return jsonify({"ok": True})


@app.route("/api/finanzas/ddjj/<ddjj_id>/propiedades", methods=["GET"])
@login_required
@finanzas_owner_required
def api_ddjj_propiedades_list(ddjj_id):
    return jsonify({"ok": True, "rows": fin.listar_propiedades_ddjj(HIST_DB, ddjj_id)})


@app.route("/api/finanzas/ddjj/<ddjj_id>/propiedades", methods=["POST"])
@login_required
@finanzas_owner_required
def api_ddjj_propiedades_crear(ddjj_id):
    data = request.json or {}
    try:
        rid = fin.crear_propiedad_ddjj(
            HIST_DB, ddjj_id,
            direccion=str(data.get("direccion", "")).strip(),
            fecha_adquisicion=str(data.get("fecha_adquisicion", "")).strip(),
            superficie=float(data.get("superficie") or 0),
            base_imponible=float(data.get("base_imponible") or 0),
            valor_compra_actualizado=float(data.get("valor_compra_actualizado") or 0),
        )
    except (TypeError, ValueError) as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "id": rid})


@app.route("/api/finanzas/ddjj/propiedades/<reg_id>", methods=["DELETE"])
@login_required
@finanzas_owner_required
def api_ddjj_propiedades_borrar(reg_id):
    fin.borrar_propiedad_ddjj(HIST_DB, reg_id)
    return jsonify({"ok": True})


@app.route("/api/finanzas/ddjj/<ddjj_id>/tarjetas", methods=["GET"])
@login_required
@finanzas_owner_required
def api_ddjj_tarjetas_list(ddjj_id):
    rows = fin.listar_tarjetas_ddjj(HIST_DB, app.secret_key, ddjj_id, revelar=False)
    return jsonify({"ok": True, "rows": rows})


@app.route("/api/finanzas/ddjj/tarjetas/<tarjeta_id>/revelar", methods=["POST"])
@login_required
@finanzas_owner_required
@limiter.limit("20 per 15 minutes", error_message="Demasiados intentos de ver números completos.")
def api_ddjj_tarjetas_revelar(tarjeta_id):
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    row = con.execute("SELECT ddjj_id FROM fin_ddjj_tarjetas WHERE id=?", (tarjeta_id,)).fetchone()
    con.close()
    if not row:
        return jsonify({"ok": False, "error": "No existe esa tarjeta"}), 404
    logging.info(f"DDJJ TARJETA REVEAL | user={session.get('username')} | tarjeta_id={tarjeta_id}")
    rows = fin.listar_tarjetas_ddjj(HIST_DB, app.secret_key, row["ddjj_id"], revelar=True)
    encontrada = next((r for r in rows if r["id"] == tarjeta_id), None)
    if not encontrada:
        return jsonify({"ok": False, "error": "No se pudo descifrar"}), 500
    return jsonify({"ok": True, "numero": encontrada["numero"]})


@app.route("/api/finanzas/ddjj/<ddjj_id>/tarjetas", methods=["POST"])
@login_required
@finanzas_owner_required
def api_ddjj_tarjetas_crear(ddjj_id):
    data = request.json or {}
    try:
        tid = fin.crear_tarjeta_ddjj(
            HIST_DB, app.secret_key, ddjj_id,
            emisor=str(data.get("emisor", "")).strip(),
            numero=str(data.get("numero", "")).strip(),
        )
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    logging.info(f"DDJJ TARJETA CREATE | user={session.get('username')} | ddjj_id={ddjj_id} | emisor={data.get('emisor')}")
    return jsonify({"ok": True, "id": tid})


@app.route("/api/finanzas/ddjj/tarjetas/<tarjeta_id>", methods=["DELETE"])
@login_required
@finanzas_owner_required
def api_ddjj_tarjetas_borrar(tarjeta_id):
    fin.borrar_tarjeta_ddjj(HIST_DB, tarjeta_id)
    logging.info(f"DDJJ TARJETA DELETE | user={session.get('username')} | id={tarjeta_id}")
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)

