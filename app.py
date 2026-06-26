"""
CosmoTools — app.py v2
Plataforma de herramientas DI REPA / ARCA
"""

import os, sqlite3, io, uuid, threading, bcrypt, logging, subprocess, xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, jsonify, send_file, session, redirect, url_for
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import time, struct
from datetime import timezone
import urllib.request


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "sintia-repa-2026")
app.permanent_session_lifetime = timedelta(hours=4)

# ── Config ─────────────────────────────────────────────────────────────────────
DB_PATH       = os.environ.get("DB_PATH",     "/data/pad.db")
API_KEY       = os.environ.get("ANTHROPIC_API_KEY", "")
def get_api_key(): return os.environ.get("ANTHROPIC_API_KEY", "")
APP_USER      = os.environ.get("APP_USER",    "cosmo")
APP_PASS      = os.environ.get("APP_PASS",    "")
APP_USER2     = os.environ.get("APP_USER2",   "")
APP_PASS2     = os.environ.get("APP_PASS2",   "")
OUTPUT_FOLDER = "/data/informes"
HIST_DB       = "/data/historial.db"
os.makedirs(OUTPUT_FOLDER, exist_ok=True)
os.makedirs("/data/minutas", exist_ok=True)
os.makedirs("/tmp/sintia_uploads", exist_ok=True)
STOCK_REPORTS_DIR = "/data/reports/stock"
os.makedirs(STOCK_REPORTS_DIR, exist_ok=True)

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(filename="/data/accesos.log", level=logging.INFO,
    format="%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

# ── Rate limiter ───────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address, app=app,
    default_limits=[], storage_uri="memory://")

# ── Context processor ──────────────────────────────────────────────────────────
@app.context_processor
def inject_session_vars():
    return {
        "modulos": session.get("modulos", []),
        "user_role": session.get("role", ""),
    }

# ── Job queue ──────────────────────────────────────────────────────────────────
job_status = {}
import threading as _threading
def _limpiar_jobs_viejos():
    """Elimina jobs de más de 2 horas para evitar memory leak."""
    import time
    while True:
        time.sleep(3600)  # cada hora
        ahora = time.time()
        viejos = [k for k, v in list(job_status.items())
                  if v.get('_ts', ahora) < ahora - 7200]
        for k in viejos:
            job_status.pop(k, None)
_threading.Thread(target=_limpiar_jobs_viejos, daemon=True).start()

# ── Historial DB ───────────────────────────────────────────────────────────────
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
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM vua_cronologia")
    if cur.fetchone()[0] == 0:
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
        con.executemany("INSERT INTO vua_cronologia (fecha,actividad,participantes,estado,orden,creado,modificado) VALUES (?,?,?,?,?,datetime('now'),datetime('now'))", cronologia)
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
        import hashlib as _hl
        default_hash = _hl.sha256(b"admin").hexdigest()
        con.execute("INSERT INTO usuarios (username, password_hash, rol, modulos) VALUES (?,?,?,?)",
            ("admin", default_hash, "admin", "sintia,vua,senasa"))

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

init_historial()

# ── Auth ───────────────────────────────────────────────────────────────────────
def check_password(plain, hashed):
    try: return bcrypt.checkpw(plain.encode(), hashed.encode())
    except: return plain == hashed

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        token = session.get("token","")
        if token and token_revocado(token):
            session.clear(); return redirect(url_for("login"))
        last = session.get("last_active")
        if last and datetime.now().timestamp() - last > 14400:
            session.clear(); return redirect(url_for("login"))
        session["last_active"] = datetime.now().timestamp()
        if token:
            try: actualizar_sesion(token)
            except: pass
        session.permanent = True
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("role") != "admin":
            return jsonify({"ok": False, "error": "Sin permiso"})
        return f(*args, **kwargs)
    return decorated

@app.route("/login", methods=["GET", "POST"])
@limiter.limit("20 per 15 minutes", error_message="Demasiados intentos.")
def login():
    error = None
    if request.method == "POST":
        u = request.form.get("user","").strip()
        p = request.form.get("pass","")
        ip = request.headers.get("X-Real-IP", request.remote_addr)
        ua = request.headers.get("User-Agent","")[:200]
        user = get_user(u)
        autenticado = False; rol = "readonly"
        if user and check_password(p, user["password_hash"]):
            autenticado = True; rol = user["rol"]
        elif u == APP_USER and check_password(p, APP_PASS):
            autenticado = True; rol = "admin"
        elif APP_USER2 and u == APP_USER2 and check_password(p, APP_PASS2):
            autenticado = True; rol = "readonly"
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
            return redirect(url_for("index"))
        else:
            logging.warning("LOGIN FAIL | user=" + u + " | ip=" + ip)
            error = "Usuario o contraseña incorrectos"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    logging.info(f"LOGOUT | user={session.get('username','?')} | ip={request.headers.get('X-Real-IP', request.remote_addr)}")
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
        api_key=bool(API_KEY), role=session.get("role","admin"),
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
            try: cur.execute(f"SELECT COUNT(*) FROM {t}"); info[t] = cur.fetchone()[0]
            except: pass
        con.close()
        return jsonify({"exists":True,"tables":info,"size_gb":round(os.path.getsize(DB_PATH)/(1024**3),2)})
    except Exception as e:
        return jsonify({"exists":True,"error":str(e)})

# ── Upload BD ──────────────────────────────────────────────────────────────────
@app.route("/api/upload-db", methods=["POST"])
@login_required
@admin_required
def upload_db():
    if "file" not in request.files: return jsonify({"ok":False,"error":"No se recibió archivo"})
    f = request.files["file"]
    if not f.filename.endswith(".db"): return jsonify({"ok":False,"error":"El archivo debe ser .db"})
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    f.save(DB_PATH)
    logging.info(f"BD UPLOAD | user={session.get('username')} | size={os.path.getsize(DB_PATH)}")
    return jsonify({"ok":True,"size_gb":round(os.path.getsize(DB_PATH)/(1024**3),2)})

# ── Import SQL ─────────────────────────────────────────────────────────────────
@app.route("/api/import-sql", methods=["POST"])
@login_required
@admin_required
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
        return jsonify({"ok":True,"size_gb":size})
    except subprocess.TimeoutExpired:
        return jsonify({"ok":False,"error":"Timeout — archivo muy grande."})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

# ── Update CSV (async) ─────────────────────────────────────────────────────────
@app.route("/api/update-csv", methods=["POST"])
@login_required
def update_csv():
    tabla = request.form.get("tabla","").strip()
    anio  = request.form.get("anio", str(datetime.today().year))
    if tabla == "DAT": tabla = f"DAT_{anio}"
    if "file" not in request.files:
        return jsonify({"ok":False,"error":"No se recibió archivo"})
    if not os.path.exists(DB_PATH):
        return jsonify({"ok":False,"error":"La BD no está cargada"})
    f = request.files["file"]
    tmp_path = f"/tmp/upload_{uuid.uuid4().hex[:8]}.txt"
    f.save(tmp_path)
    size_kb = round(os.path.getsize(tmp_path)/1024,1)
    job_id = str(uuid.uuid4())[:8]
    job_status[job_id] = {"status":"running","log":[f"Archivo recibido: {size_kb} KB"],"files":[]}
    logging.info(f"CSV UPLOAD | tabla={tabla} | size={size_kb}KB")
    t = threading.Thread(target=_run_csv_job, args=(job_id, tmp_path, tabla))
    t.start()
    return jsonify({"ok":True,"job_id":job_id})

def _run_csv_job(job_id, tmp_path, tabla):
    log = job_status[job_id]["log"]
    try:
        _procesar_csv(tmp_path, tabla, log)
        job_status[job_id]["status"] = "done"
    except Exception as e:
        log.append(f"✗ Error: {e}")
        job_status[job_id]["status"] = "error"
    finally:
        try: os.remove(tmp_path)
        except: pass

def _procesar_csv(tmp_path, tabla, log):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    try:
        if tabla.startswith("DAT_"):
            # Streaming línea por línea — evita cargar 650MB en RAM
            log.append("Procesando archivo DAT (streaming)...")
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
                        cur.execute(f"DROP TABLE IF EXISTS {tabla}")
                        cols_def = ", ".join([f'"{h}" TEXT' for h in headers])
                        cur.execute(f"CREATE TABLE {tabla} ({cols_def})")
                        placeholders = ", ".join(["?" for _ in headers])
                        con.commit()
                        log.append(f"Columnas: {len(headers)} — tabla creada, insertando...")
                        continue
                    vals = [v.strip() for v in line.split(";")]
                    while len(vals) < len(headers): vals.append(None)
                    batch.append(vals[:len(headers)])
                    if len(batch) >= batch_size:
                        cur.executemany(f"INSERT INTO {tabla} VALUES ({placeholders})", batch)
                        inserted += len(batch)
                        batch = []
                        if inserted % 50000 == 0:
                            con.commit()
                            log.append(f"  {inserted:,} filas insertadas...")
            if batch:
                cur.executemany(f"INSERT INTO {tabla} VALUES ({placeholders})", batch)
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
            for start in range(1, inserted + batch_iso, batch_iso):
                cur.execute(iso_sql, (start, start + batch_iso - 1))
                con.commit()
                log.append(f"  Fechas ISO: {min(start + batch_iso - 1, inserted):,} / {inserted:,}...")
            log.append("Creando índice...")
            try: cur.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{tabla}_key ON {tabla}(OPERACION_PAD_EXT, MIC, TIPO_REGISTRO)")
            except: pass
            con.commit(); con.close()
            log.append(f"✓ {tabla}: {inserted:,} registros, fechas ISO calculadas, índice creado")

        elif tabla == "RECHAZOS":
            log.append("Procesando archivo RECHAZOS...")
            raw_rech = open(tmp_path, encoding="utf-8", errors="replace").read()
            lines = []
            for l in raw_rech.splitlines():
                s = l.strip()
                if not s or "@" not in s: continue
                if len(s) >= 3 and s[:2].isdigit() and s[2] == "@": s = s[3:]
                parts = [p.strip() for p in s.split("@")][:6]
                if len(parts) >= 2: lines.append(parts)
            if len(lines) < 2:
                log.append("✗ No se encontraron datos válidos"); con.close(); return
            col_map = {"Pais Emisor":"PaisEmisor","Metodo":"Metodo","Nro. MIC/DTA":"NroMic","Fecha":"Fecha","Mensaje":"Mensaje"}
            raw_headers = [col_map.get(h.strip(),h.strip()) for h in lines[0]]
            cols_utiles = ["PaisEmisor","Metodo","NroMic","Fecha","Mensaje"]
            idx_cols = [i for i,h in enumerate(raw_headers) if h in cols_utiles]
            cols_finales = [raw_headers[i] for i in idx_cols]
            rows = [[parts[i].strip() if i < len(parts) else None for i in idx_cols] for parts in lines[1:]]
            log.append(f"Filas a insertar: {len(rows):,}")
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='RECHAZOS'")
            if not cur.fetchone():
                cur.execute("CREATE TABLE RECHAZOS (PaisEmisor TEXT, Metodo TEXT, NroMic TEXT, Fecha TEXT, Mensaje TEXT, Fecha_ISO TEXT, Mes TEXT, Anio TEXT)")
            else:
                for col in ["Fecha_ISO","Mes","Anio"]:
                    try: cur.execute(f"ALTER TABLE RECHAZOS ADD COLUMN {col} TEXT")
                    except: pass
            placeholders = ", ".join(["?" for _ in cols_finales])
            cols_str = ", ".join(cols_finales)
            inserted = 0
            batch_size = 5000
            for i in range(0, len(rows), batch_size):
                batch = rows[i:i+batch_size]
                try: cur.executemany(f"INSERT INTO RECHAZOS ({cols_str}) VALUES ({placeholders})", batch); inserted += len(batch)
                except:
                    for row in batch:
                        try: cur.execute(f"INSERT INTO RECHAZOS ({cols_str}) VALUES ({placeholders})", row); inserted += 1
                        except: pass
                con.commit()
                log.append(f"  {min(i+batch_size,len(rows)):,} / {len(rows):,} filas procesadas...")
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
            usar_ia=usar_ia, api_key=API_KEY, carpeta=OUTPUT_FOLDER,
            log_fn=lambda msg: log.append(msg))
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
    except Exception as e:
        log.append(f"✗ Error: {e}")
        job_status[job_id]["status"] = "error"

@app.route("/api/generar", methods=["POST"])
@login_required
def api_generar():
    if not os.path.exists(DB_PATH):
        return jsonify({"ok":False,"error":"La BD no está cargada"})
    data = request.json or {}
    pais    = data.get("pais","").upper()
    anio    = data.get("anio", str(datetime.today().year))
    mes_d   = str(data.get("mes_d","01")).zfill(2)
    mes_h   = str(data.get("mes_h","12")).zfill(2)
    usar_ia = data.get("usar_ia",True) and bool(API_KEY)
    username = session.get("username","?")
    job_id  = str(uuid.uuid4())[:8]
    job_status[job_id] = {"status":"running","log":["Iniciando generación..."],"files":[]}
    t = threading.Thread(target=run_job, args=(job_id, pais, anio, mes_d, mes_h, usar_ia, username))
    t.start()
    return jsonify({"ok":True,"job_id":job_id})

@app.route("/api/job/<job_id>")
@login_required
def job_poll(job_id):
    info = job_status.get(job_id)
    if not info: return jsonify({"error":"Job no encontrado"})
    return jsonify(info)

@app.route("/api/download/<job_id>/<int:idx>")
@login_required
def download_file(job_id, idx):
    files = job_status.get(job_id,{}).get("files",[])
    if idx >= len(files): return "Archivo no encontrado",404
    path = files[idx]
    if not os.path.exists(path): return "Archivo no encontrado",404
    return send_file(path, as_attachment=True, download_name=os.path.basename(path))

# ── Historial ──────────────────────────────────────────────────────────────────
@app.route("/api/historial")
@login_required
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

# ── VUA ────────────────────────────────────────────────────────────────────────
# ROLES_PREDEFINIDOS — mantenido por compatibilidad, los datos ahora viven en tabla 'integrantes'
def get_roles_predefinidos():
    """Lee los integrantes activos de la BD y devuelve {nombre: cargo (organismo)}."""
    try:
        con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
        rows = con.execute("SELECT nombre, cargo, organismo FROM integrantes WHERE activo=1").fetchall()
        con.close()
        return {r["nombre"]: f"{r['cargo']} ({r['organismo']})" if r["organismo"] else r["cargo"] for r in rows}
    except Exception:
        return {}

ROLES_PREDEFINIDOS = {
    "Diego Bugallo": "Jefe Dpto. Facilitación y Simplificación de Comercio (DI REPA)",
    "Martín Macías": "Jefe Div. Modernización de Procesos Aduaneros (DI REPA)",
    "Hernán Cascón": "Supervisor de Informática Aduanera (DI SADU)",
    "Maximiliano Luengo": "Consejero técnico (DI ADEZ)",
    "Pablo Gómez Valdez": "Consejero técnico (DI ADEZ)",
    "Fabiola Cochello": "Directora VUCEA",
    "Vanesa Franco": "Jefa de Procesos VUCEA",
    "Federico Cáceres": "Sec. Simplificación de Procesos Operativos (DI REPA)",
}

CAMPOS_XFWB = [
    {"tab":"Información general","campo":"Número de guía aérea","campo_xml":"masterDocumentNumber","norma":"IATA Cargo-XML XFWB schema","obligatorio":True,"observacion":"Identificador único de la guía aérea master."},
    {"tab":"Información general","campo":"CUIT del consignatario","campo_xml":"OCI/AR/IMP//CUIT...","norma":"Art. 3° RG 4517/2019","obligatorio":True,"observacion":"Formato exacto: OCI/AR/IMP//CUIT12345678901. Sin este campo el XFWB es inválido para Aduana."},
    {"tab":"Información general","campo":"Notificación (notifyParty)","campo_xml":"ConsignmentType/NotifyParty","norma":"IATA Cargo-XML XFWB schema","obligatorio":True,"observacion":"Nombre, número de cuenta y dirección."},
    {"tab":"Información general","campo":"Agente de aduana","campo_xml":"FreightForwarder rol CustomsBroker","norma":"IATA Cargo-XML, RG 3596/2014","obligatorio":True,"observacion":"Distinto del FreightForwarderParty."},
    {"tab":"Información general","campo":"Remitente — Nombre","campo_xml":"ShipperParty/Name","norma":"IATA Cargo-XML XFWB schema","obligatorio":True,"observacion":""},
    {"tab":"Información general","campo":"Destinatario — Nombre","campo_xml":"ConsigneeParty/Name","norma":"IATA Cargo-XML XFWB schema","obligatorio":True,"observacion":""},
    {"tab":"Carga","campo":"Descripción de la mercancía","campo_xml":"GoodsDescription","norma":"IATA Cargo-XML XFWB schema","obligatorio":True,"observacion":""},
    {"tab":"Carga","campo":"Peso bruto total","campo_xml":"TotalGrossWeight","norma":"IATA Cargo-XML XFWB schema","obligatorio":True,"observacion":"En kilogramos."},
    {"tab":"Vuelo","campo":"Número de vuelo","campo_xml":"FlightBooking/FlightIdentifier","norma":"IATA Cargo-XML XFWB schema","obligatorio":True,"observacion":""},
    {"tab":"Vuelo","campo":"Aeropuerto de origen","campo_xml":"DepartureLocation","norma":"IATA Cargo-XML XFWB schema","obligatorio":True,"observacion":"Código IATA de 3 letras."},
    {"tab":"Vuelo","campo":"Aeropuerto de destino","campo_xml":"ArrivalLocation","norma":"IATA Cargo-XML XFWB schema","obligatorio":True,"observacion":"Código IATA de 3 letras."},
]

REGLAS_BPMN = {
    "EXPO": [
        {"id":"EXPO-001","descripcion":"Generación y Registración del MANE deben ser un único nodo en carril ATA MT","patron":["Generación del MANE","Registración del MANE"],"tipo":"error","norma":"RG 5756/2025"},
        {"id":"EXPO-002","descripcion":"Confirmación de Partida debe estar en carril ATA MT","patron":["Confirmación del MANE"],"tipo":"error","norma":"RG 5756/2025 Art. 3"},
        {"id":"EXPO-004","descripcion":"El carril de bodega compartida debe llamarse ATA CBC, no ATA CVC","patron":["ATA CVC"],"tipo":"advertencia","norma":"RG 5756/2025 Anexo §1.2"},
        {"id":"EXPO-005","descripcion":"Debe existir nodo de Ratificación de Autoría con token NF4","patron_ausente":["Ratificación de autoría","Ratificacion de autoria"],"tipo":"error","norma":"RG 5756/2025 §2.2"},
    ],
    "IMPO": [
        {"id":"IMPO-001","descripcion":"Transmisión IA del ATA MT debe incluir XFWB además del XFFM","patron":["XFFM"],"patron_ausente":["XFWB"],"tipo":"error","norma":"RG 3596/2014"},
        {"id":"IMPO-003","descripcion":"El timer de presentación automática debe ser 15 minutos desde confirmación de arribo","patron_ausente":["15 min","15min"],"tipo":"advertencia","norma":"RG 4517/2019 Art. 7"},
        {"id":"IMPO-005","descripcion":"Ratificación de Autoría debe requerir token NF4","patron_ausente":["NF4","nivel 4","token"],"tipo":"error","norma":"RG 4517/2019"},
    ]
}

SYSTEM_NORMATIVA = """Sos un experto en normativa aduanera argentina especializado en carga aérea.
Normativa clave: RG 3596/2014 (IA vía aérea, XFFM+XFWB, plazo 4hs), RG 4517/2019 (MANI SIM, generación automática, token NF4, 15min post-arribo), RG 5756/2025 (MANE exportación, registro post puesta a bordo, 3hs de partida, ATA CBC).
Respondé citando artículos. Si hay ambigüedad normativa, señalala explícitamente."""

SYSTEM_CORREOS = """Sos un asistente de redacción de correos institucionales para DI REPA de ARCA.
Contexto: proyecto VUA, circuito de carga aérea, XML IATA, MANI SIM y MANE.
Estilo: formal con externos/superiores; informal con colegas (primer nombre, "Abrazo" al cerrar).
Federico Cáceres firma como "Fede" en correos informales.
Incluí siempre: ASUNTO: [texto] al inicio."""

@app.route("/vua")
@login_required
def vua_index():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    cronologia = [dict(r) for r in con.execute("SELECT * FROM vua_cronologia ORDER BY orden ASC, id ASC").fetchall()]
    ejes       = [dict(r) for r in con.execute("SELECT * FROM vua_ejes ORDER BY orden ASC").fetchall()]
    minutas    = [dict(r) for r in con.execute("SELECT id,fecha,asunto,lugar,creado_por,creado FROM vua_minutas ORDER BY creado DESC LIMIT 20").fetchall()]
    con.close()
    return render_template("vua.html", roles=ROLES_PREDEFINIDOS, cronologia=cronologia,
        ejes=ejes, minutas=minutas, campos_xfwb=CAMPOS_XFWB,
        role=session.get("role","admin"), username=session.get("username",""))

@app.route("/api/vua/minuta", methods=["POST"])
@login_required
def vua_minuta():
    import json as _json
    from docx import Document
    from docx.shared import Pt, RGBColor, Cm
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    data = request.json or {}
    asunto       = data.get("asunto","")
    fecha        = data.get("fecha", datetime.today().strftime("%d/%m/%Y"))
    lugar        = data.get("lugar","")
    participantes = data.get("participantes",[])
    temas        = data.get("temas",[])
    acuerdos     = data.get("acuerdos",[])
    proximos     = data.get("proximos",[])

    def set_cell_color(cell, hex_color):
        tc=cell._tc; tcPr=tc.get_or_add_tcPr(); shd=OxmlElement("w:shd")
        shd.set(qn("w:val"),"clear"); shd.set(qn("w:color"),"auto"); shd.set(qn("w:fill"),hex_color); tcPr.append(shd)

    doc = Document()
    for section in doc.sections:
        section.top_margin=Cm(2.5); section.bottom_margin=Cm(2.5)
        section.left_margin=Cm(3); section.right_margin=Cm(2.5)
    titulo = doc.add_paragraph(); titulo.alignment=WD_ALIGN_PARAGRAPH.CENTER
    run = titulo.add_run("ACTA DE REUNIÓN"); run.bold=True; run.font.size=Pt(16)
    run.font.color.rgb=RGBColor(0x24,0x2D,0x4F)
    doc.add_paragraph()
    for label, valor in [("Asunto:",asunto),("Fecha:",fecha),("Lugar:",lugar)]:
        p=doc.add_paragraph(); r1=p.add_run(f"{label} "); r1.bold=True; r1.font.size=Pt(11)
        r2=p.add_run(valor); r2.font.size=Pt(11)
    doc.add_paragraph()
    doc.add_paragraph().add_run("Participantes").bold=True
    table=doc.add_table(rows=1,cols=2); table.style="Table Grid"
    hdr=table.rows[0]
    for i,txt in enumerate(["Nombre","Cargo/Organismo"]):
        hdr.cells[i].text=txt
        hdr.cells[i].paragraphs[0].runs[0].bold=True
        hdr.cells[i].paragraphs[0].runs[0].font.color.rgb=RGBColor(0xFF,0xFF,0xFF)
        set_cell_color(hdr.cells[i],"242D4F")
    for p in participantes:
        row=table.add_row()
        row.cells[0].text=p.get("nombre","")
        row.cells[1].text=p.get("cargo",ROLES_PREDEFINIDOS.get(p.get("nombre",""),""))
    for titulo_sec, items in [("Temas tratados",temas),("Acuerdos",acuerdos),("Próximos pasos",proximos)]:
        doc.add_paragraph()
        doc.add_paragraph().add_run(titulo_sec).bold=True
        for item in items:
            p=doc.add_paragraph(style="List Bullet"); p.add_run(item).font.size=Pt(11)
    minuta_id = str(uuid.uuid4())[:8]
    fname = f"Acta_{fecha.replace('/','_')}_{asunto[:30].replace(' ','_')}_{minuta_id}.docx"
    ruta = os.path.join("/data/minutas", fname)

    # Mejora 5: intentar generar con Node (mejor formato); fallback a python-docx
    script = os.path.join(os.path.dirname(__file__), "generar_informe_vua.js")
    datos_minuta = {
        "fecha": fecha, "asunto": asunto, "lugar": lugar,
        "participantes": participantes, "temas": temas,
        "acuerdos": acuerdos, "proximos": proximos,
    }
    usó_node = False
    if os.path.exists(script):
        import tempfile as _tmp
        with _tmp.NamedTemporaryFile(suffix=".json", delete=False, mode="w", encoding="utf-8") as jf:
            _json.dump(datos_minuta, jf, ensure_ascii=False)
            json_path = jf.name
        try:
            res = subprocess.run(["node", script, json_path, ruta, "minuta"],
                                 capture_output=True, text=True, encoding="utf-8",
                                 env={**os.environ, "LANG": "en_US.UTF-8", "NODE_OPTIONS": "--no-deprecation"},
                                 timeout=20)
            if res.returncode == 0 and os.path.exists(ruta):
                usó_node = True
        except Exception:
            pass
        finally:
            try: os.unlink(json_path)
            except: pass

    if not usó_node:
        # Fallback: python-docx básico (comportamiento anterior)
        doc.save(ruta)

    con = sqlite3.connect(HIST_DB)
    con.execute("INSERT INTO vua_minutas VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'))",
        (minuta_id, fecha, asunto, lugar, _json.dumps(participantes), _json.dumps(temas),
         _json.dumps(acuerdos), _json.dumps(proximos), ruta, session.get("username","?")))
    con.commit(); con.close()

    # Construir sugerencia de cronología a partir de los datos de la reunión
    partic_nombres = ", ".join(
        p.get("nombre", p) if isinstance(p, dict) else str(p)
        for p in participantes
    )
    actividad_sugerida = asunto if asunto else "Reunión de trabajo VUA"
    crono_sugerida = {
        "fecha":        fecha,
        "actividad":    actividad_sugerida,
        "participantes": partic_nombres,
        "estado":       "Completado",
    }
    return jsonify({
        "ok":       True,
        "minuta_id": minuta_id,
        "download_url": f"/api/vua/minuta/{minuta_id}/download",
        "fname":    fname,
        "cronologia_sugerida": crono_sugerida,
    })

@app.route("/api/vua/correo", methods=["POST"])
@login_required
def vua_correo():
    data = request.json or {}
    instruccion = data.get("instruccion","")
    if not instruccion: return jsonify({"ok":False,"error":"Instrucción vacía"})
    try:
        import anthropic, httpx
        client = anthropic.Anthropic(api_key=API_KEY, http_client=httpx.Client(follow_redirects=True))
        msg = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=1000,
            system=SYSTEM_CORREOS, messages=[{"role":"user","content":instruccion}])
        return jsonify({"ok":True,"texto":msg.content[0].text})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/vua/normativa", methods=["POST"])
@login_required
def vua_normativa():
    data = request.json or {}
    pregunta = data.get("pregunta","").strip()
    if not pregunta: return jsonify({"ok":False,"error":"Pregunta vacía"})
    try:
        import anthropic, httpx
        client = anthropic.Anthropic(api_key=API_KEY, http_client=httpx.Client(follow_redirects=True))
        msg = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=1200,
            system=SYSTEM_NORMATIVA, messages=[{"role":"user","content":pregunta}])
        return jsonify({"ok":True,"respuesta":msg.content[0].text})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/vua/bpmn", methods=["POST"])
@login_required
def vua_bpmn():
    if "archivo" not in request.files: return jsonify({"ok":False,"error":"No se recibió archivo"})
    archivo = request.files["archivo"]
    circuito = request.form.get("circuito","AUTO")
    try:
        xml_content = archivo.read().decode("utf-8")
        ET.fromstring(xml_content)
    except Exception as e:
        return jsonify({"ok":False,"error":f"XML inválido: {e}"})
    if circuito == "AUTO":
        upper = xml_content.upper()
        if "EXPORTACI" in upper and "MANE" in upper: circuito = "EXPO"
        elif "IMPORTACI" in upper and "MANI" in upper: circuito = "IMPO"
        else: return jsonify({"ok":False,"error":"No se pudo detectar el circuito."})
    reglas = REGLAS_BPMN.get(circuito,[])
    errores=[]; advertencias=[]
    for regla in reglas:
        hallado=False
        if "patron" in regla:
            for p in regla["patron"]:
                if p.lower() in xml_content.lower():
                    (errores if regla["tipo"]=="error" else advertencias).append(
                        {"id":regla["id"],"descripcion":regla["descripcion"],"norma":regla["norma"],"patron_encontrado":p})
                    hallado=True; break
        if "patron_ausente" in regla and not hallado:
            if not any(p.lower() in xml_content.lower() for p in regla["patron_ausente"]):
                (errores if regla["tipo"]=="error" else advertencias).append(
                    {"id":regla["id"],"descripcion":regla["descripcion"],"norma":regla["norma"],"patron_encontrado":"ausente"})
    return jsonify({"ok":True,"circuito":circuito,"errores":errores,"advertencias":advertencias,"total":len(errores)+len(advertencias)})

@app.route("/api/vua/xfwb")
@login_required
def vua_xfwb():
    import openpyxl
    from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    wb=openpyxl.Workbook(); ws=wb.active; ws.title="Checklist XFWB"
    HDR=PatternFill("solid",fgColor="242D4F"); HDR_F=Font(bold=True,color="FFFFFF",size=10)
    bs=Side(style="thin",color="CCCCCC"); BORDER=Border(left=bs,right=bs,top=bs,bottom=bs)
    headers=["Tab","Campo","Campo XML","Norma","Obligatorio","Observación"]
    ws.append(headers)
    for ci,h in enumerate(headers,1):
        cell=ws.cell(1,ci); cell.fill=HDR; cell.font=HDR_F
        cell.alignment=Alignment(horizontal="center"); cell.border=BORDER
    ALT=PatternFill("solid",fgColor="EEF2F7")
    for ri,campo in enumerate(CAMPOS_XFWB,2):
        row=[campo["tab"],campo["campo"],campo["campo_xml"],campo["norma"],
             "Sí" if campo["obligatorio"] else "No",campo["observacion"]]
        for ci,val in enumerate(row,1):
            cell=ws.cell(ri,ci,val); cell.border=BORDER
            cell.fill=ALT if ri%2==0 else PatternFill(); cell.font=Font(size=10)
    for ci in range(1,len(headers)+1):
        ws.column_dimensions[get_column_letter(ci)].width=25
    buf=io.BytesIO(); wb.save(buf); buf.seek(0)
    return send_file(buf,as_attachment=True,download_name="Checklist_XFWB.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.route("/api/vua/informe")
@login_required
def vua_informe():
    """Mejora 2: generación async del informe VUA — mismo patrón que SINTIA."""
    import json as _json
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    try:
        def _q(sql, fallback=[]):
            try: return [dict(r) for r in con.execute(sql).fetchall()]
            except: return fallback
        config     = _q("SELECT * FROM vua_config")
        ejes       = _q("SELECT * FROM vua_ejes ORDER BY orden ASC")
        equipo     = _q("SELECT * FROM vua_equipo WHERE activo=1 ORDER BY organismo, nombre ASC")
        cronologia = _q("SELECT * FROM vua_cronologia ORDER BY orden ASC, id ASC")
        glosario   = _q("SELECT * FROM vua_glosario ORDER BY termino ASC")
        riesgos    = _q("SELECT * FROM vua_riesgos WHERE activo=1 ORDER BY orden ASC")
        minutas    = _q("SELECT * FROM vua_minutas ORDER BY id DESC LIMIT 20")
    except Exception as e:
        con.close()
        return jsonify({"ok": False, "error": f"Error leyendo BD: {e}"}), 500
    finally:
        con.close()

    datos = {"config": config, "ejes": ejes, "equipo": equipo,
             "cronologia": cronologia, "glosario": glosario,
             "riesgos": riesgos, "minutas": minutas}

    job_id = str(uuid.uuid4())[:8]
    job_status[job_id] = {"status": "running", "log": ["Generando informe VUA..."], "files": [], "_ts": __import__("time").time()}

    def _run_vua_informe(job_id, datos):
        import json as _j, tempfile as _t
        log = job_status[job_id]["log"]
        try:
            with _t.NamedTemporaryFile(suffix=".json", delete=False, mode="w", encoding="utf-8") as jf:
                _j.dump(datos, jf, ensure_ascii=False)
                json_path = jf.name
            out_path = json_path.replace(".json", ".docx")
            script   = os.path.join(os.path.dirname(__file__), "generar_informe_vua.js")
            result   = subprocess.run(["node", script, json_path, out_path],
                           capture_output=True, text=True, encoding="utf-8",
                           env={**os.environ, "LANG": "en_US.UTF-8", "NODE_OPTIONS": "--no-deprecation"},
                           timeout=60)
            if result.returncode != 0 or not os.path.exists(out_path):
                stderr_txt = result.stderr[:400] if result.stderr else "(sin stderr)"
                stdout_txt = result.stdout[:200] if result.stdout else "(sin stdout)"
                log.append(f"✗ Error Node (rc={result.returncode}): {stderr_txt} | stdout: {stdout_txt}")
                job_status[job_id]["status"] = "error"
                return
            fname = f"Informe_VUA_{datetime.today().strftime('%Y%m%d_%H%M')}_{job_id}.docx"
            os.makedirs(OUTPUT_FOLDER, exist_ok=True)
            dest = os.path.join(OUTPUT_FOLDER, fname)
            import shutil as _sh
            _sh.copy2(out_path, dest)
            try: os.unlink(out_path)
            except: pass
            job_status[job_id]["files"] = [dest]
            log.append(f"✓ Informe generado: {fname}")
            job_status[job_id]["status"] = "done"
            try: os.unlink(json_path)
            except: pass
        except subprocess.TimeoutExpired:
            log.append("✗ Timeout generando informe")
            job_status[job_id]["status"] = "error"
        except Exception as e:
            log.append(f"✗ {e}")
            job_status[job_id]["status"] = "error"

    threading.Thread(target=_run_vua_informe, args=(job_id, datos)).start()
    return jsonify({"ok": True, "job_id": job_id})

@app.route("/api/vua/informe/download/<job_id>")
@login_required
def vua_informe_download(job_id):
    """Descarga el informe VUA. Busca primero en memoria, luego en disco por job_id."""
    # 1. Verificar en memoria (caso normal: proceso no reiniciado)
    job = job_status.get(job_id)
    if job:
        if job["status"] != "done":
            return jsonify({"ok": False, "status": job["status"], "log": job.get("log",[])}), 202
        files = job.get("files", [])
        if files and os.path.exists(files[0]):
            fpath = files[0]
            fname = os.path.basename(fpath)
            if not fname.endswith(".docx"): fname = fname.rsplit(".", 1)[0] + ".docx"
            return send_file(fpath, as_attachment=True, download_name=fname,
                mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    # 2. Fallback: buscar en disco por job_id en el nombre del archivo
    #    (cubre reinicio del servidor entre generación y descarga)
    import glob
    patron = os.path.join(OUTPUT_FOLDER, f"*{job_id}*.docx")
    archivos = sorted(glob.glob(patron), key=os.path.getmtime, reverse=True)
    if archivos and os.path.exists(archivos[0]):
        return send_file(archivos[0], as_attachment=True,
            download_name=os.path.basename(archivos[0]),
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    # 3. Si no hay job en memoria y no hay archivo, dar error claro
    if not job:
        return jsonify({"ok": False,
            "error": "Sesión expirada o servidor reiniciado. Regenerá el informe."}), 404
    return jsonify({"ok": False, "error": "Archivo no encontrado en el servidor"}), 404


@app.route("/api/vua/cronologia", methods=["GET"])
@login_required
def vua_cronologia_get():
    con=sqlite3.connect(HIST_DB); con.row_factory=sqlite3.Row
    rows=[dict(r) for r in con.execute("SELECT * FROM vua_cronologia ORDER BY orden ASC, id ASC").fetchall()]
    con.close(); return jsonify({"ok":True,"rows":rows})

@app.route("/api/vua/cronologia", methods=["POST"])
@login_required
def vua_cronologia_add():
    data=request.json or {}
    con=sqlite3.connect(HIST_DB); cur=con.cursor()
    cur.execute("SELECT MAX(orden) FROM vua_cronologia")
    max_orden=cur.fetchone()[0] or 0
    cur.execute("INSERT INTO vua_cronologia (fecha,actividad,participantes,estado,orden,creado,modificado) VALUES (?,?,?,?,?,datetime('now'),datetime('now'))",
        (data.get("fecha","A definir"),data.get("actividad",""),data.get("participantes",""),data.get("estado","Pendiente"),max_orden+1))
    new_id=cur.lastrowid; con.commit(); con.close()
    return jsonify({"ok":True,"id":new_id})

@app.route("/api/vua/cronologia/<int:item_id>", methods=["PUT"])
@login_required
def vua_cronologia_update(item_id):
    data=request.json or {}
    con=sqlite3.connect(HIST_DB)
    con.execute("UPDATE vua_cronologia SET fecha=?,actividad=?,participantes=?,estado=?,modificado=datetime('now') WHERE id=?",
        (data.get("fecha"),data.get("actividad"),data.get("participantes"),data.get("estado"),item_id))
    con.commit(); con.close(); return jsonify({"ok":True})

@app.route("/api/vua/cronologia/<int:item_id>", methods=["DELETE"])
@login_required
@admin_required
def vua_cronologia_delete(item_id):
    con=sqlite3.connect(HIST_DB)
    con.execute("DELETE FROM vua_cronologia WHERE id=?",(item_id,))
    con.commit(); con.close(); return jsonify({"ok":True})

@app.route("/api/vua/minutas", methods=["GET"])
@login_required
def vua_minutas_list():
    con=sqlite3.connect(HIST_DB); con.row_factory=sqlite3.Row
    rows=[dict(r) for r in con.execute("SELECT id,fecha,asunto,lugar,creado_por,creado FROM vua_minutas ORDER BY creado DESC").fetchall()]
    con.close(); return jsonify({"ok":True,"rows":rows})

@app.route("/api/vua/minutas/<minuta_id>/download")
@login_required
def vua_minuta_download(minuta_id):
    con=sqlite3.connect(HIST_DB); con.row_factory=sqlite3.Row
    row=con.execute("SELECT * FROM vua_minutas WHERE id=?",(minuta_id,)).fetchone()
    con.close()
    if not row: return "No encontrada",404
    row=dict(row)
    if row.get("archivo") and os.path.exists(row["archivo"]):
        return send_file(row["archivo"],as_attachment=True,download_name=os.path.basename(row["archivo"]))
    return "Archivo no encontrado",404

@app.route("/api/vua/minutas/<minuta_id>", methods=["DELETE"])
@login_required
def vua_minuta_delete(minuta_id):
    con=sqlite3.connect(HIST_DB)
    row=con.execute("SELECT archivo FROM vua_minutas WHERE id=?",(minuta_id,)).fetchone()
    if row and row[0] and os.path.exists(row[0]):
        try: os.remove(row[0])
        except: pass
    con.execute("DELETE FROM vua_minutas WHERE id=?",(minuta_id,))
    con.commit(); con.close(); return jsonify({"ok":True})


# ══════════════════════════════════════════════════════
# RUTAS VUA BD DINAMICA
# ══════════════════════════════════════════════════════


# ── VUA Config ────────────────────────────────────────────────────────────────
@app.route("/api/vua/config", methods=["GET"])
@login_required
def vua_config_list():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM vua_config ORDER BY clave").fetchall()]
    con.close(); return jsonify({"ok": True, "rows": rows})

@app.route("/api/vua/config/<clave>", methods=["PUT"])
@login_required
def vua_config_update(clave):
    data = request.json or {}
    contenido = data.get("contenido", "").strip()
    if not contenido: return jsonify({"ok": False, "error": "Contenido vacio"})
    con = sqlite3.connect(HIST_DB)
    con.execute("UPDATE vua_config SET contenido=?, modificado=datetime('now') WHERE clave=?", (contenido, clave))
    con.commit(); con.close(); return jsonify({"ok": True})

@app.route("/api/vua/config/<clave>/mejorar", methods=["POST"])
@login_required
def vua_config_mejorar(clave):
    api_key = get_api_key()
    if not api_key: return jsonify({"ok": False, "error": "API key no configurada"})
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM vua_config WHERE clave=?", (clave,)).fetchone()
    con.close()
    if not row: return jsonify({"ok": False, "error": "No encontrado"})
    try:
        import anthropic, httpx
        client = anthropic.Anthropic(api_key=api_key, http_client=httpx.Client(follow_redirects=True))
        titulo = row["titulo"]
        contenido_actual = row["contenido"]
        # Mejora 3: system prompt con contexto institucional
        system_informe = (
            "Sos redactor de informes de gestión de proyectos para DI REPA de ARCA (Aduana Argentina). "
            "El proyecto es VUA (Ventanilla Única Aeroportuaria) para digitalización del circuito de carga aérea. "
            "Organismos involucrados: ARCA/DGA, VUCEA, SENASA, ORSNA, IATA, aerolíneas. "
            "Estilo: formal, preciso, en español rioplatense institucional. "
            "No agregues datos que no estén en el texto original. Solo mejorá la redacción y claridad."
        )
        prompt = (f"Mejorar la redacción de la sección '{titulo}' del informe de estado de situación del proyecto VUA. "
                  f"Conservá todos los datos y hechos del original. Devolvé solo el texto mejorado, sin encabezados ni explicaciones:\n\n{contenido_actual}")
        msg = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=1500,
            system=system_informe,
            messages=[{"role": "user", "content": prompt}])
        return jsonify({"ok": True, "texto": msg.content[0].text.strip()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

# ── VUA Ejes ──────────────────────────────────────────────────────────────────
@app.route("/api/vua/ejes", methods=["GET"])
@login_required
def vua_ejes_list():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM vua_ejes ORDER BY orden").fetchall()]
    con.close(); return jsonify({"ok": True, "rows": rows})

@app.route("/api/vua/ejes/<eje_id>", methods=["PUT"])
@login_required
def vua_ejes_update_bd(eje_id):
    data = request.json or {}
    con = sqlite3.connect(HIST_DB)
    fields = []; params = []
    for f in ["nombre", "estado", "descripcion", "propuesta_vucea", "postura_aduana", "recomendacion"]:
        if f in data: fields.append(f + "=?"); params.append(data[f])
    if fields:
        params.append(str(eje_id))
        con.execute("UPDATE vua_ejes SET " + ", ".join(fields) + " WHERE id=?", params)
        con.commit()
    con.close(); return jsonify({"ok": True})

@app.route("/api/vua/ejes", methods=["POST"])
@login_required
def vua_ejes_create():
    data = request.json or {}
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    max_orden = con.execute("SELECT MAX(orden) FROM vua_ejes").fetchone()[0] or 0
    max_id = con.execute("SELECT id FROM vua_ejes ORDER BY orden DESC LIMIT 1").fetchone()
    try:
        last_num = float((max_id[0] if max_id else "0").replace(",","."))
        new_id = str(round(last_num + 0.1, 1))
    except: new_id = str(max_orden + 1)
    con.execute("INSERT INTO vua_ejes (id, nombre, estado, orden, descripcion, propuesta_vucea, postura_aduana, recomendacion) VALUES (?,?,?,?,?,?,?,?)",
        (new_id, data.get("nombre",""), data.get("estado","Pendiente"), max_orden + 1,
         data.get("descripcion",""), data.get("propuesta_vucea",""), data.get("postura_aduana",""), data.get("recomendacion","")))
    con.commit(); con.close()
    return jsonify({"ok": True, "id": new_id})

@app.route("/api/vua/ejes/<eje_id>", methods=["DELETE"])
@login_required
def vua_ejes_delete(eje_id):
    con = sqlite3.connect(HIST_DB)
    con.execute("DELETE FROM vua_ejes WHERE id=?", (str(eje_id),))
    con.commit(); con.close()
    return jsonify({"ok": True})

@app.route("/api/vua/ejes/<eje_id>/mejorar", methods=["POST"])
@login_required
def vua_eje_mejorar(eje_id):
    api_key = get_api_key()
    if not api_key: return jsonify({"ok": False, "error": "API key no configurada"})
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    eje = con.execute("SELECT * FROM vua_ejes WHERE id=?", (str(eje_id),)).fetchone()
    con.close()
    if not eje: return jsonify({"ok": False, "error": "No encontrado"})
    try:
        import anthropic, httpx, json as _json
        client = anthropic.Anthropic(api_key=api_key, http_client=httpx.Client(follow_redirects=True))
        nombre = eje["nombre"]; estado = eje["estado"]
        prompt = "Mejora nombre y estado del eje VUA. Solo JSON: {\"nombre\":\"...\",\"estado\":\"...\"}\nNOMBRE: " + nombre + "\nESTADO: " + estado
        msg = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=400,
            messages=[{"role": "user", "content": prompt}])
        resultado = _json.loads(msg.content[0].text.strip().replace("```json","").replace("```",""))
        return jsonify({"ok": True, "nombre": resultado.get("nombre",""), "estado": resultado.get("estado","")})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

# ── VUA Equipo ────────────────────────────────────────────────────────────────
@app.route("/api/vua/equipo", methods=["GET"])
@login_required
def vua_equipo_list():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM vua_equipo WHERE activo=1 ORDER BY orden, organismo, nombre").fetchall()]
    con.close(); return jsonify({"ok": True, "rows": rows})

@app.route("/api/vua/equipo", methods=["POST"])
@login_required
def vua_equipo_create():
    data = request.json or {}
    con = sqlite3.connect(HIST_DB)
    con.execute("INSERT INTO vua_equipo (nombre, cargo, organismo, email, activo) VALUES (?,?,?,?,1)",
        (data.get("nombre",""), data.get("cargo",""), data.get("organismo",""), data.get("email","")))
    con.commit(); con.close(); return jsonify({"ok": True})

@app.route("/api/vua/equipo/<int:uid>", methods=["PUT"])
@login_required
def vua_equipo_update(uid):
    data = request.json or {}
    con = sqlite3.connect(HIST_DB)
    fields = []; params = []
    for f in ["nombre","cargo","organismo","email","activo"]:
        if f in data: fields.append(f + "=?"); params.append(data[f])
    if fields:
        params.append(uid)
        con.execute("UPDATE vua_equipo SET " + ", ".join(fields) + " WHERE id=?", params)
        con.commit()
    con.close(); return jsonify({"ok": True})

@app.route("/api/vua/equipo/<int:uid>", methods=["DELETE"])
@login_required
def vua_equipo_delete(uid):
    con = sqlite3.connect(HIST_DB)
    con.execute("UPDATE vua_equipo SET activo=0 WHERE id=?", (uid,))
    con.commit(); con.close(); return jsonify({"ok": True})

# ── VUA Glosario ──────────────────────────────────────────────────────────────
@app.route("/api/vua/glosario", methods=["GET"])
@login_required
def vua_glosario_list():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM vua_glosario ORDER BY orden, termino").fetchall()]
    con.close(); return jsonify({"ok": True, "rows": rows})

@app.route("/api/vua/glosario", methods=["POST"])
@login_required
def vua_glosario_create():
    data = request.json or {}
    con = sqlite3.connect(HIST_DB)
    con.execute("INSERT INTO vua_glosario (termino, definicion, categoria) VALUES (?,?,?)",
        (data.get("termino",""), data.get("definicion",""), data.get("categoria","general")))
    con.commit(); con.close(); return jsonify({"ok": True})

@app.route("/api/vua/glosario/<int:gid>", methods=["PUT"])
@login_required
def vua_glosario_update(gid):
    data = request.json or {}
    con = sqlite3.connect(HIST_DB)
    fields = []; params = []
    for f in ["termino","definicion","categoria"]:
        if f in data: fields.append(f + "=?"); params.append(data[f])
    if fields:
        params.append(gid)
        con.execute("UPDATE vua_glosario SET " + ", ".join(fields) + " WHERE id=?", params)
        con.commit()
    con.close(); return jsonify({"ok": True})

@app.route("/api/vua/glosario/<int:gid>", methods=["DELETE"])
@login_required
def vua_glosario_delete(gid):
    con = sqlite3.connect(HIST_DB)
    con.execute("DELETE FROM vua_glosario WHERE id=?", (gid,))
    con.commit(); con.close(); return jsonify({"ok": True})

# ── VUA Riesgos ───────────────────────────────────────────────────────────────
@app.route("/api/vua/riesgos", methods=["GET"])
@login_required
def vua_riesgos_list():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM vua_riesgos WHERE activo=1 ORDER BY orden").fetchall()]
    con.close(); return jsonify({"ok": True, "rows": rows})

@app.route("/api/vua/riesgos/<int:rid>", methods=["PUT"])
@login_required
def vua_riesgos_update(rid):
    data = request.json or {}
    con = sqlite3.connect(HIST_DB)
    fields = []; params = []
    for f in ["titulo","descripcion","mitigacion","probabilidad","impacto","activo"]:
        if f in data: fields.append(f + "=?"); params.append(data[f])
    if fields:
        params.append(rid)
        con.execute("UPDATE vua_riesgos SET " + ", ".join(fields) + " WHERE id=?", params)
        con.commit()
    con.close(); return jsonify({"ok": True})

@app.route("/api/vua/riesgos", methods=["POST"])
@login_required
def vua_riesgos_create():
    data = request.json or {}
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    max_orden = con.execute("SELECT MAX(orden) FROM vua_riesgos").fetchone()[0] or 0
    max_id = con.execute("SELECT MAX(id) FROM vua_riesgos").fetchone()[0] or 0
    codigo = f"R{max_id + 1:02d}"
    con.execute("INSERT INTO vua_riesgos (codigo, titulo, descripcion, mitigacion, probabilidad, impacto, activo, orden) VALUES (?,?,?,?,?,?,1,?)",
        (data.get("codigo", codigo), data.get("titulo",""), data.get("descripcion",""),
         data.get("mitigacion",""), data.get("probabilidad","Media"), data.get("impacto","Alto"), max_orden + 1))
    con.commit(); con.close()
    return jsonify({"ok": True})

@app.route("/api/vua/riesgos/<int:rid>", methods=["DELETE"])
@login_required
def vua_riesgos_delete(rid):
    con = sqlite3.connect(HIST_DB)
    con.execute("UPDATE vua_riesgos SET activo=0 WHERE id=?", (rid,))
    con.commit(); con.close()
    return jsonify({"ok": True})

# ── VUA Correos rapidos ───────────────────────────────────────────────────────
@app.route("/api/vua/correos_rapidos", methods=["GET"])
@login_required
def vua_correos_rapidos_list():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM vua_correos_rapidos WHERE activo=1 ORDER BY orden").fetchall()]
    con.close(); return jsonify({"ok": True, "rows": rows})

@app.route("/api/vua/correos_rapidos", methods=["POST"])
@login_required
def vua_correos_rapidos_create():
    data = request.json or {}
    con = sqlite3.connect(HIST_DB)
    con.execute("INSERT INTO vua_correos_rapidos (etiqueta, instruccion, activo) VALUES (?,?,1)",
        (data.get("etiqueta",""), data.get("instruccion","")))
    con.commit(); con.close(); return jsonify({"ok": True})

@app.route("/api/vua/correos_rapidos/<int:cid>", methods=["PUT"])
@login_required
def vua_correos_rapidos_update(cid):
    data = request.json or {}
    con = sqlite3.connect(HIST_DB)
    fields = []; params = []
    for f in ["etiqueta","instruccion","activo"]:
        if f in data: fields.append(f + "=?"); params.append(data[f])
    if fields:
        params.append(cid)
        con.execute("UPDATE vua_correos_rapidos SET " + ", ".join(fields) + " WHERE id=?", params)
        con.commit()
    con.close(); return jsonify({"ok": True})

@app.route("/api/vua/correos_rapidos/<int:cid>", methods=["DELETE"])
@login_required
def vua_correos_rapidos_delete(cid):
    con = sqlite3.connect(HIST_DB)
    con.execute("UPDATE vua_correos_rapidos SET activo=0 WHERE id=?", (cid,))
    con.commit(); con.close(); return jsonify({"ok": True})

# ── VUA Info ──────────────────────────────────────────────────────────────────
@app.route("/api/vua/info/<clave>", methods=["GET"])
@login_required
def vua_info_get(clave):
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM vua_info WHERE clave=?", (clave,)).fetchone()
    con.close()
    if not row: return jsonify({"ok": False, "error": "No encontrado"})
    return jsonify({"ok": True, "item": dict(row)})

@app.route("/api/vua/info/<clave>", methods=["PUT"])
@login_required
def vua_info_update(clave):
    data = request.json or {}
    contenido = data.get("contenido", "").strip()
    if not contenido: return jsonify({"ok": False, "error": "Contenido vacio"})
    con = sqlite3.connect(HIST_DB)
    con.execute("UPDATE vua_info SET contenido=?, modificado=datetime('now') WHERE clave=?", (contenido, clave))
    con.commit(); con.close(); return jsonify({"ok": True})

# ── VUA Consultas frecuentes ──────────────────────────────────────────────────
@app.route("/api/vua/consultas_frecuentes", methods=["GET"])
@login_required
def vua_consultas_frecuentes_list():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM vua_consultas_frecuentes WHERE activo=1 ORDER BY orden").fetchall()]
    con.close(); return jsonify({"ok": True, "rows": rows})

# ── VUA Minuta IA ─────────────────────────────────────────────────────────────
@app.route("/api/vua/minuta_ia", methods=["POST"])
@login_required
def vua_minuta_ia():
    """Mejora 6: minuta_ia con contexto de minutas anteriores para detectar pendientes."""
    api_key = get_api_key()
    if not api_key: return jsonify({"ok": False, "error": "API key no configurada"})
    data = request.json or {}
    asunto        = data.get("asunto", "")
    participantes = data.get("participantes", [])
    temas         = data.get("temas", [])
    try:
        import anthropic, httpx, json as _json
        client = anthropic.Anthropic(api_key=api_key, http_client=httpx.Client(follow_redirects=True))

        # Cargar últimas 5 minutas para contexto acumulado
        con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
        minutas_ant = [dict(r) for r in con.execute(
            "SELECT fecha, asunto, acuerdos, proximos FROM vua_minutas ORDER BY creado DESC LIMIT 5").fetchall()]
        con.close()

        ctx_minutas = ""
        if minutas_ant:
            ctx_minutas = "\n\nCONTEXTO — Últimas minutas del proyecto (para detectar pendientes y continuidad):\n"
            for m in reversed(minutas_ant):  # cronológico
                try:
                    acuerdos_prev = _json.loads(m.get("acuerdos","[]") or "[]")
                    proximos_prev = _json.loads(m.get("proximos","[]") or "[]")
                    ctx_minutas += (f"\n• {m['fecha']} — {m['asunto']}\n"
                                    f"  Acuerdos: {'; '.join(acuerdos_prev[:3])}\n"
                                    f"  Próximos pasos: {'; '.join(proximos_prev[:3])}\n")
                except: pass

        p_txt = "; ".join([p.get("nombre","") + " (" + p.get("cargo","") + ")" for p in participantes])
        t_txt = "\n".join(["- " + t for t in temas])

        prompt = (
            "Sos analista de DI REPA. Generá un borrador de acta para el proyecto VUA.\n"
            f"ASUNTO: {asunto}\n"
            f"PARTICIPANTES: {p_txt}\n"
            f"TEMAS TRATADOS HOY:\n{t_txt}"
            f"{ctx_minutas}\n\n"
            "Con ese contexto:\n"
            "1. Redactá los puntos tratados en esta reunión\n"
            "2. Identificá acuerdos concretos (con responsable si es posible)\n"
            "3. Definí próximos pasos, mencionando si alguno viene de reuniones anteriores y aún está pendiente\n\n"
            "Devolvé SOLO JSON válido (sin markdown):\n"
            "{\"temas_tratados\":[\"...\"]}\n"
            "{\"acuerdos\":[\"...\"]}\n"
            "{\"proximos_pasos\":[\"...\"]}\n"
            "{\"pendientes_anteriores\":[\"...\"]}\n"
            "Estilo: formal, español rioplatense institucional."
        )
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=1800,
            system="Sos un asistente experto en gestión de proyectos aduaneros para ARCA Argentina. Respondés solo con JSON válido, sin texto adicional.",
            messages=[{"role": "user", "content": prompt}])
        texto = msg.content[0].text.strip().replace("```json","").replace("```","").strip()
        resultado = _json.loads(texto)
        return jsonify({"ok": True, **resultado})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})



# ── Importar minuta desde Word con IA ────────────────────────────────────────
@app.route("/api/vua/minuta/importar", methods=["POST"])
@login_required
def vua_minuta_importar():
    """Recibe un .docx, extrae el texto y usa la IA para estructurarlo en campos de minuta."""
    api_key = get_api_key()
    if not api_key: return jsonify({"ok": False, "error": "API key no configurada"})
    if "archivo" not in request.files:
        return jsonify({"ok": False, "error": "No se recibió archivo"})

    archivo = request.files["archivo"]
    if not archivo.filename.endswith(".docx"):
        return jsonify({"ok": False, "error": "Solo se aceptan archivos .docx"})

    # Extraer texto del Word con python-docx
    try:
        from docx import Document as DocxDoc
        import io as _io
        doc_bytes = archivo.read()
        docx_doc  = DocxDoc(_io.BytesIO(doc_bytes))
        # Extraer párrafos y tablas
        partes = []
        for p in docx_doc.paragraphs:
            txt = p.text.strip()
            if txt: partes.append(txt)
        for tabla in docx_doc.tables:
            for fila in tabla.rows:
                celda_txt = " | ".join(c.text.strip() for c in fila.cells if c.text.strip())
                if celda_txt: partes.append(celda_txt)
        texto_completo = "\n".join(partes)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Error leyendo el Word: {e}"})

    if not texto_completo.strip():
        return jsonify({"ok": False, "error": "El documento está vacío o no se pudo extraer texto"})

    # Llamar a la IA para estructurar
    try:
        import anthropic, httpx, json as _json
        client = anthropic.Anthropic(api_key=api_key, http_client=httpx.Client(follow_redirects=True))

        prompt = (
            "El siguiente texto es una minuta/acta de reunión del proyecto VUA (Ventanilla Única Aeroportuaria — ARCA Argentina).\n"
            "Extraé y estructurá la información en el JSON solicitado.\n\n"
            f"TEXTO DE LA MINUTA:\n{texto_completo[:6000]}\n\n"
            "Devolvé SOLO este JSON válido (sin markdown ni texto adicional):\n"
            "{\n"
            '  "asunto": "título o asunto principal de la reunión",\n'
            '  "fecha": "fecha en formato YYYY-MM-DD si la encontrás, sino vacío",\n'
            '  "lugar": "lugar o modalidad (ej: Videoconferencia, Sala 3 Paseo Colón)",\n'
            '  "participantes": [\n'
            '    {"nombre": "Nombre completo o sigla del organismo", "cargo": "cargo si está disponible"}\n'
            '  ],\n'
            '  "temas_tratados": ["tema 1", "tema 2"],\n'
            '  "acuerdos": ["acuerdo 1", "acuerdo 2"],\n'
            '  "proximos_pasos": ["paso 1", "paso 2"]\n'
            "}\n\n"
            "Reglas:\n"
            "- temas_tratados: los temas principales que se discutieron, uno por ítem, en forma concisa\n"
            "- acuerdos: compromisos concretos que se tomaron en la reunión\n"
            "- proximos_pasos: tareas o acciones pendientes mencionadas\n"
            "- Si la fecha está escrita en texto (ej: '11 de junio de 2026'), convertila a YYYY-MM-DD\n"
            "- Español rioplatense, con tildes y caracteres especiales correctos (á, é, í, ó, ú, ñ), sin markdown"
        )

        msg = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=2000,
            system="Sos un asistente experto en gestión de proyectos aduaneros para ARCA Argentina. Extraés información estructurada de minutas institucionales. Respondés solo con JSON válido.",
            messages=[{"role": "user", "content": prompt}]
        )
        texto_resp = msg.content[0].text.strip().replace("```json","").replace("```","").strip()
        resultado  = _json.loads(texto_resp)
        return jsonify({"ok": True, **resultado})

    except _json.JSONDecodeError as e:
        return jsonify({"ok": False, "error": f"La IA no devolvió JSON válido: {e}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})



# ══════════════════════════════════════════════════════════════════════════════
# MÓDULO SENASA
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/senasa")
@login_required
def senasa_index():
    return render_template("senasa.html", username=session.get("username",""),
        role=session.get("role","admin"))

# ── Cronología SENASA ─────────────────────────────────────────────────────────
@app.route("/api/senasa/cronologia", methods=["GET"])
@login_required
def senasa_crono_list():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM senasa_cronologia ORDER BY orden ASC, id ASC").fetchall()]
    con.close()
    return jsonify({"ok": True, "rows": rows})

@app.route("/api/senasa/cronologia", methods=["POST"])
@login_required
def senasa_crono_add():
    data = request.json or {}
    con = sqlite3.connect(HIST_DB); cur = con.cursor()
    cur.execute("SELECT MAX(orden) FROM senasa_cronologia")
    max_o = cur.fetchone()[0] or 0
    cur.execute("INSERT INTO senasa_cronologia (fecha,actividad,participantes,estado,orden) VALUES (?,?,?,?,?)",
        (data.get("fecha",""), data.get("actividad",""),
         data.get("participantes",""), data.get("estado","Pendiente"), max_o+1))
    new_id = cur.lastrowid; con.commit(); con.close()
    return jsonify({"ok": True, "id": new_id})

@app.route("/api/senasa/cronologia/<int:iid>", methods=["PUT"])
@login_required
def senasa_crono_update(iid):
    data = request.json or {}
    con = sqlite3.connect(HIST_DB)
    con.execute("UPDATE senasa_cronologia SET fecha=?,actividad=?,participantes=?,estado=?,modificado=datetime('now') WHERE id=?",
        (data.get("fecha",""), data.get("actividad",""),
         data.get("participantes",""), data.get("estado","Pendiente"), iid))
    con.commit(); con.close()
    return jsonify({"ok": True})

@app.route("/api/senasa/cronologia/<int:iid>", methods=["DELETE"])
@login_required
def senasa_crono_delete(iid):
    con = sqlite3.connect(HIST_DB)
    con.execute("DELETE FROM senasa_cronologia WHERE id=?", (iid,))
    con.commit(); con.close()
    return jsonify({"ok": True})

# ── Ejes SENASA ───────────────────────────────────────────────────────────────
@app.route("/api/senasa/ejes", methods=["GET"])
@login_required
def senasa_ejes_list():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM senasa_ejes ORDER BY orden ASC").fetchall()]
    con.close()
    return jsonify({"ok": True, "rows": rows})

@app.route("/api/senasa/ejes/<int:iid>", methods=["PUT"])
@login_required
def senasa_ejes_update(iid):
    data = request.json or {}
    con = sqlite3.connect(HIST_DB)
    con.execute("UPDATE senasa_ejes SET nombre=?,descripcion=?,estado=? WHERE id=?",
        (data.get("nombre",""), data.get("descripcion",""), data.get("estado",""), iid))
    con.commit(); con.close()
    return jsonify({"ok": True})

@app.route("/api/senasa/ejes", methods=["POST"])
@login_required
def senasa_ejes_create():
    data = request.json or {}
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    max_orden = con.execute("SELECT MAX(orden) FROM senasa_ejes").fetchone()[0] or 0
    con.execute("INSERT INTO senasa_ejes (nombre, descripcion, estado, orden) VALUES (?,?,?,?)",
        (data.get("nombre",""), data.get("descripcion",""), data.get("estado","Pendiente"), max_orden + 1))
    con.commit(); con.close()
    return jsonify({"ok": True})

@app.route("/api/senasa/ejes/<int:iid>", methods=["DELETE"])
@login_required
def senasa_ejes_delete(iid):
    con = sqlite3.connect(HIST_DB)
    con.execute("DELETE FROM senasa_ejes WHERE id=?", (iid,))
    con.commit(); con.close()
    return jsonify({"ok": True})

# ── Minutas SENASA ────────────────────────────────────────────────────────────
@app.route("/api/senasa/minutas", methods=["GET"])
@login_required
def senasa_minutas_list():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT id,fecha,asunto,lugar,creado_por,creado FROM senasa_minutas ORDER BY creado DESC").fetchall()]
    con.close()
    return jsonify({"ok": True, "rows": rows})

@app.route("/api/senasa/minuta", methods=["POST"])
@login_required
def senasa_minuta_create():
    import json as _json
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

    # Generar Word con python-docx
    from docx import Document as DocxDoc
    from docx.shared import Pt, RGBColor
    doc = DocxDoc()
    doc.add_heading("Acta de Reunión — SENASA / ARCA", 0)
    doc.add_heading(f"{asunto}", 1)
    p = doc.add_paragraph()
    p.add_run(f"Fecha: {fecha}   |   Lugar: {lugar}").bold = False

    if participantes:
        doc.add_heading("Participantes", 2)
        for pt in participantes:
            n = pt.get("nombre","") if isinstance(pt,dict) else str(pt)
            c = pt.get("cargo","")  if isinstance(pt,dict) else ""
            o = pt.get("organismo","") if isinstance(pt,dict) else ""
            doc.add_paragraph(f"{n} — {c} ({o})" if c else n, style="List Bullet")

    for titulo, items in [("Temas tratados",temas),("Conclusiones",conclusiones),
                           ("Compromisos",compromisos),("Próximos pasos",proximos)]:
        if items:
            doc.add_heading(titulo, 2)
            for item in items:
                doc.add_paragraph(item, style="List Bullet")

    os.makedirs("/data/minutas_senasa", exist_ok=True)
    fname = f"Acta_SENASA_{fecha.replace('/','_')}_{minuta_id}.docx"
    ruta  = os.path.join("/data/minutas_senasa", fname)
    doc.save(ruta)

    con = sqlite3.connect(HIST_DB)
    con.execute("INSERT INTO senasa_minutas VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
        (minuta_id, fecha, asunto, lugar,
         _json.dumps(participantes), _json.dumps(temas), _json.dumps(conclusiones),
         _json.dumps(compromisos), _json.dumps(proximos), ruta, session.get("username","?")))
    con.commit(); con.close()

    partic_str = ", ".join(
        p.get("nombre",p) if isinstance(p,dict) else str(p) for p in participantes)
    return jsonify({
        "ok": True, "minuta_id": minuta_id,
        "download_url": f"/api/senasa/minutas/{minuta_id}/download",
        "fname": fname,
        "cronologia_sugerida": {
            "fecha": fecha, "actividad": asunto,
            "participantes": partic_str, "estado": "Completado"
        }
    })

@app.route("/api/senasa/minutas/<minuta_id>/download")
@login_required
def senasa_minuta_download(minuta_id):
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    row = con.execute("SELECT archivo FROM senasa_minutas WHERE id=?", (minuta_id,)).fetchone()
    con.close()
    if not row or not os.path.exists(row["archivo"]):
        return jsonify({"ok": False, "error": "Archivo no encontrado"}), 404
    return send_file(row["archivo"], as_attachment=True,
        download_name=os.path.basename(row["archivo"]),
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

# ── IA SENASA ─────────────────────────────────────────────────────────────────
@app.route("/api/senasa/minuta_ia", methods=["POST"])
@login_required
def senasa_minuta_ia():
    api_key = get_api_key()
    if not api_key: return jsonify({"ok": False, "error": "API key no configurada"})
    data = request.json or {}
    notas = data.get("notas","").strip()
    if not notas: return jsonify({"ok": False, "error": "Sin notas"})
    try:
        import anthropic, httpx, json as _json
        client = anthropic.Anthropic(api_key=api_key, http_client=httpx.Client(follow_redirects=True))
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=1500,
            system="Sos asistente de DI REPA (ARCA Argentina). Estructurás minutas de reuniones con SENASA. Respondés solo con JSON válido.",
            messages=[{"role":"user","content":(
                f"Estructurá estas notas de reunión SENASA-ARCA en JSON:\n{notas}\n\n"
                'Devolvé: {"asunto":"...","temas":["..."],"conclusiones":["..."],"compromisos":["ORG — compromiso..."],"proximos":["..."]}'
            )}])
        texto = msg.content[0].text.strip().replace("```json","").replace("```","").strip()
        return jsonify({"ok": True, "resultado": _json.loads(texto)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

# ── Acuerdos SENASA ───────────────────────────────────────────────────────────
@app.route("/api/senasa/acuerdos", methods=["GET"])
@login_required
def senasa_acuerdos_list():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM senasa_acuerdos ORDER BY estado ASC, orden ASC").fetchall()]
    con.close()
    return jsonify({"ok": True, "rows": rows})

@app.route("/api/senasa/acuerdos", methods=["POST"])
@login_required
def senasa_acuerdos_add():
    data = request.json or {}
    con = sqlite3.connect(HIST_DB); cur = con.cursor()
    cur.execute("SELECT MAX(orden) FROM senasa_acuerdos")
    max_o = cur.fetchone()[0] or 0
    cur.execute("INSERT INTO senasa_acuerdos (descripcion,responsable,fecha_compromiso,estado,orden) VALUES (?,?,?,?,?)",
        (data.get("descripcion",""), data.get("responsable",""),
         data.get("fecha_compromiso",""), data.get("estado","Pendiente"), max_o+1))
    new_id = cur.lastrowid; con.commit(); con.close()
    return jsonify({"ok": True, "id": new_id})

@app.route("/api/senasa/acuerdos/<int:iid>", methods=["PUT"])
@login_required
def senasa_acuerdos_update(iid):
    data = request.json or {}
    con = sqlite3.connect(HIST_DB)
    con.execute("UPDATE senasa_acuerdos SET descripcion=?,responsable=?,fecha_compromiso=?,estado=? WHERE id=?",
        (data.get("descripcion",""), data.get("responsable",""),
         data.get("fecha_compromiso",""), data.get("estado","Pendiente"), iid))
    con.commit(); con.close()
    return jsonify({"ok": True})

@app.route("/api/senasa/acuerdos/<int:iid>", methods=["DELETE"])
@login_required
def senasa_acuerdos_delete(iid):
    con = sqlite3.connect(HIST_DB)
    con.execute("DELETE FROM senasa_acuerdos WHERE id=?", (iid,))
    con.commit(); con.close()
    return jsonify({"ok": True})

# ── Informe SENASA (async) ────────────────────────────────────────────────────
@app.route("/api/senasa/informe")
@login_required
def senasa_informe():
    """Genera el informe SENASA en background — misma arquitectura que VUA."""
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    datos = {
        "modulo": "SENASA",
        "cronologia": [dict(r) for r in con.execute("SELECT * FROM senasa_cronologia ORDER BY orden").fetchall()],
        "ejes":       [dict(r) for r in con.execute("SELECT * FROM senasa_ejes ORDER BY orden").fetchall()],
        "minutas":    [dict(r) for r in con.execute("SELECT * FROM senasa_minutas ORDER BY creado DESC LIMIT 10").fetchall()],
        "acuerdos":   [dict(r) for r in con.execute("SELECT * FROM senasa_acuerdos ORDER BY estado, orden").fetchall()],
    }
    con.close()
    job_id = str(uuid.uuid4())[:8]
    job_status[job_id] = {"status": "running", "log": ["Generando informe SENASA..."], "files": [], "_ts": __import__("time").time()}

    def _run(jid, datos):
        import json as _j, tempfile as _t
        log = job_status[jid]["log"]
        try:
            # Generar Word con python-docx (sin Node para SENASA)
            from docx import Document as DocxDoc
            doc = DocxDoc()
            doc.add_heading("Informe de Avance — Integración SENASA / ARCA", 0)
            doc.add_heading("Ejes de trabajo", 1)
            for e in datos["ejes"]:
                doc.add_heading(e["nombre"], 2)
                if e.get("descripcion"): doc.add_paragraph(e["descripcion"])
                doc.add_paragraph(f"Estado: {e['estado']}")
            doc.add_heading("Cronología de reuniones", 1)
            for c in datos["cronologia"]:
                doc.add_paragraph(f"{c['fecha']} — {c['actividad']} ({c['estado']})", style="List Bullet")
            doc.add_heading("Compromisos pendientes", 1)
            for a in datos["acuerdos"]:
                if a["estado"] != "Completado":
                    doc.add_paragraph(f"{a['descripcion']} | {a.get('responsable','')} | {a.get('fecha_compromiso','')}", style="List Bullet")
            os.makedirs(OUTPUT_FOLDER, exist_ok=True)
            fname = f"Informe_SENASA_{datetime.today().strftime('%Y%m%d_%H%M')}_{jid}.docx"
            dest  = os.path.join(OUTPUT_FOLDER, fname)
            doc.save(dest)
            job_status[jid]["files"] = [dest]
            log.append(f"✓ Informe generado: {fname}")
            job_status[jid]["status"] = "done"
        except Exception as e:
            log.append(f"✗ {e}")
            job_status[jid]["status"] = "error"

    threading.Thread(target=_run, args=(job_id, datos)).start()
    return jsonify({"ok": True, "job_id": job_id})

@app.route("/api/senasa/informe/download/<job_id>")
@login_required
def senasa_informe_download(job_id):
    import glob
    job = job_status.get(job_id)
    if job and job.get("status") == "done" and job.get("files") and os.path.exists(job["files"][0]):
        return send_file(job["files"][0], as_attachment=True,
            download_name=os.path.basename(job["files"][0]),
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    archivos = sorted(glob.glob(os.path.join(OUTPUT_FOLDER, f"*{job_id}*.docx")), key=os.path.getmtime, reverse=True)
    if archivos:
        return send_file(archivos[0], as_attachment=True, download_name=os.path.basename(archivos[0]),
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
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

# ── VUA Resumen ejecutivo generado por IA (Mejora 7) ─────────────────────────
@app.route("/api/vua/config/resumen_ejecutivo/generar", methods=["POST"])
@login_required
def vua_resumen_generar():
    """Mejora 7: genera el resumen ejecutivo automáticamente desde el estado actual del proyecto."""
    api_key = get_api_key()
    if not api_key: return jsonify({"ok": False, "error": "API key no configurada"})
    try:
        import anthropic, httpx, json as _json
        con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
        ejes       = [dict(r) for r in con.execute("SELECT id, nombre, estado FROM vua_ejes ORDER BY orden").fetchall()]
        riesgos    = [dict(r) for r in con.execute("SELECT titulo, probabilidad, impacto FROM vua_riesgos WHERE activo=1").fetchall()]
        cronologia = [dict(r) for r in con.execute(
            "SELECT fecha, actividad, estado FROM vua_cronologia ORDER BY orden DESC LIMIT 10").fetchall()]
        minutas    = [dict(r) for r in con.execute(
            "SELECT fecha, asunto, proximos FROM vua_minutas ORDER BY creado DESC LIMIT 3").fetchall()]
        con.close()

        ult_actividad = next((c for c in cronologia if c["estado"].lower() == "completado"), {})
        prox_actividad = next((c for c in reversed(cronologia) if c["estado"].lower() == "pendiente"), {})

        ejes_txt = "\n".join([f"• {e['id']} {e['nombre']}: {e['estado']}" for e in ejes])
        riesgos_txt = "\n".join([f"• {r['titulo']} (Prob: {r['probabilidad']}, Imp: {r['impacto']})" for r in riesgos[:5]])
        
        pendientes_minuta = []
        for m in minutas:
            try:
                proximos = _json.loads(m.get("proximos","[]") or "[]")
                pendientes_minuta.extend(proximos[:2])
            except: pass

        prompt = (
            "Redactá el Resumen Ejecutivo del informe de estado de situación del proyecto VUA "
            "(Ventanilla Única Aeroportuaria — ARCA/Aduana Argentina) para un informe formal de gestión.\n\n"
            f"EJES DEL PROYECTO:\n{ejes_txt}\n\n"
            f"RIESGOS ACTIVOS:\n{riesgos_txt}\n\n"
            f"ÚLTIMA ACTIVIDAD COMPLETADA: {ult_actividad.get('fecha','')} — {ult_actividad.get('actividad','')}\n"
            f"PRÓXIMA ACTIVIDAD PROGRAMADA: {prox_actividad.get('fecha','')} — {prox_actividad.get('actividad','')}\n\n"
            f"COMPROMISOS PENDIENTES DE MINUTAS: {'; '.join(pendientes_minuta[:5])}\n\n"
            "El resumen debe: describir el estado general del proyecto, mencionar los ejes más avanzados y los pendientes, "
            "señalar los principales riesgos y los próximos hitos. "
            "Extensión: 3-4 párrafos. Estilo: formal, español rioplatense institucional. "
            "No uses bullet points — solo prosa. No incluyas títulos."
        )
        client = anthropic.Anthropic(api_key=api_key, http_client=httpx.Client(follow_redirects=True))
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=1200,
            system="Sos redactor de informes institucionales de ARCA Argentina. Redactás en prosa formal, español rioplatense, sin markdown.",
            messages=[{"role": "user", "content": prompt}])
        return jsonify({"ok": True, "texto": msg.content[0].text.strip()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ── VUA Validador BPMN con IA (Mejora 8) ─────────────────────────────────────
@app.route("/api/vua/bpmn/ia", methods=["POST"])
@login_required
def vua_bpmn_ia():
    """Mejora 8: validación BPMN profunda con IA — complementa el validador de regex."""
    api_key = get_api_key()
    if not api_key: return jsonify({"ok": False, "error": "API key no configurada"})
    if "archivo" not in request.files: return jsonify({"ok": False, "error": "No se recibió archivo"})
    archivo  = request.files["archivo"]
    circuito = request.form.get("circuito", "AUTO")
    try:
        xml_content = archivo.read().decode("utf-8")
        import xml.etree.ElementTree as ET
        ET.fromstring(xml_content)
    except Exception as e:
        return jsonify({"ok": False, "error": f"XML inválido: {e}"})

    if circuito == "AUTO":
        upper = xml_content.upper()
        if   "EXPORTACI" in upper and "MANE" in upper: circuito = "EXPO"
        elif "IMPORTACI" in upper and "MANI" in upper: circuito = "IMPO"
        else: return jsonify({"ok": False, "error": "No se pudo detectar el circuito automáticamente."})

    # Primero correr el validador rápido de regex
    reglas = REGLAS_BPMN.get(circuito, [])
    errores_regex = []; advertencias_regex = []
    for regla in reglas:
        hallado = False
        if "patron" in regla:
            for pat in regla["patron"]:
                if pat.lower() in xml_content.lower():
                    (errores_regex if regla["tipo"]=="error" else advertencias_regex).append(
                        {"id":regla["id"],"descripcion":regla["descripcion"],"norma":regla["norma"],"fuente":"regex"})
                    hallado = True; break
        if "patron_ausente" in regla and not hallado:
            if not any(p.lower() in xml_content.lower() for p in regla["patron_ausente"]):
                (errores_regex if regla["tipo"]=="error" else advertencias_regex).append(
                    {"id":regla["id"],"descripcion":regla["descripcion"],"norma":regla["norma"],"fuente":"regex"})

    # Luego análisis profundo con IA
    try:
        import anthropic, httpx, json as _json
        client = anthropic.Anthropic(api_key=api_key, http_client=httpx.Client(follow_redirects=True))

        # Truncar XML a 8000 chars para no superar contexto
        xml_truncado = xml_content[:8000] + ("\n[...truncado...]" if len(xml_content) > 8000 else "")

        prompt = (
            f"Analizá este diagrama BPMN del circuito {circuito} de carga aérea (proyecto VUA, ARCA Argentina).\n\n"
            f"XML DEL BPMN:\n{xml_truncado}\n\n"
            "Verificá específicamente:\n"
            "1. Asignación correcta de tareas a swimlanes (ATA MT, ATA CBC, ATA CVC, ATA AGT, ADUANA, SENASA)\n"
            "2. Nombres de nodos según nomenclatura aduanera argentina (MANE, MANI SIM, XFFM, XFWB, PATAI, OFTAI)\n"
            "3. Completitud del flujo: inicio → transmisión anticipada → arribo → validación → despacho\n"
            "4. Presencia de elementos obligatorios según la normativa (RG 3596/2014, RG 4517/2019, RG 5756/2025)\n"
            "5. Inconsistencias lógicas en el flujo (decisiones sin todas sus ramas, tareas sin conexión)\n\n"
            "Devolvé SOLO JSON válido:\n"
            "{\"errores_ia\":[{\"id\":\"IA-001\",\"descripcion\":\"...\",\"norma\":\"...\",\"sugerencia\":\"...\"}],\"advertencias_ia\":[...],\"observaciones\":\"...resumen general...\"}"
        )
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=2000,
            system=SYSTEM_NORMATIVA + "\nRespondés solo con JSON válido, sin texto adicional.",
            messages=[{"role": "user", "content": prompt}])
        texto = msg.content[0].text.strip().replace("```json","").replace("```","").strip()
        resultado_ia = _json.loads(texto)
    except Exception as e:
        resultado_ia = {"errores_ia": [], "advertencias_ia": [], "observaciones": f"Análisis IA no disponible: {e}"}

    return jsonify({
        "ok": True,
        "circuito": circuito,
        "errores":       errores_regex + resultado_ia.get("errores_ia", []),
        "advertencias":  advertencias_regex + resultado_ia.get("advertencias_ia", []),
        "observaciones": resultado_ia.get("observaciones",""),
        "total": len(errores_regex) + len(advertencias_regex) + len(resultado_ia.get("errores_ia",[])) + len(resultado_ia.get("advertencias_ia",[])),
    })


# ── VUA Acuerdos pendientes (Mejora 9) ────────────────────────────────────────
@app.route("/api/vua/acuerdos/pendientes", methods=["GET"])
@login_required
def vua_acuerdos_pendientes():
    """Mejora 9: detecta compromisos sin seguimiento cruzando minutas con IA."""
    api_key = get_api_key()
    if not api_key: return jsonify({"ok": False, "error": "API key no configurada"})
    try:
        import anthropic, httpx, json as _json
        con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
        minutas = [dict(r) for r in con.execute(
            "SELECT fecha, asunto, acuerdos, proximos FROM vua_minutas ORDER BY creado ASC").fetchall()]
        con.close()

        if len(minutas) < 2:
            return jsonify({"ok": True, "pendientes": [], "mensaje": "Se necesitan al menos 2 minutas para detectar pendientes."})

        # Construir resumen de todas las minutas
        minutas_txt = ""
        for m in minutas:
            try:
                acuerdos = _json.loads(m.get("acuerdos","[]") or "[]")
                proximos = _json.loads(m.get("proximos","[]") or "[]")
                minutas_txt += f"\n--- {m['fecha']} — {m['asunto']} ---\n"
                if acuerdos: minutas_txt += "Acuerdos: " + " | ".join(acuerdos) + "\n"
                if proximos: minutas_txt += "Próximos pasos: " + " | ".join(proximos) + "\n"
            except: pass

        prompt = (
            "Analizá estas minutas del proyecto VUA (en orden cronológico) y detectá compromisos o próximos pasos "
            "que aparecen en reuniones anteriores pero NO tienen evidencia de resolución en reuniones posteriores.\n\n"
            f"MINUTAS:\n{minutas_txt}\n\n"
            "Devolvé SOLO JSON válido:\n"
            "{\"pendientes\":[{\"descripcion\":\"...\",\"origen\":\"fecha — reunión\",\"estado\":\"Sin evidencia de cierre\",\"prioridad\":\"Alta|Media|Baja\"}],"
            "\"resueltos_recientes\":[{\"descripcion\":\"...\",\"cerrado_en\":\"fecha\"}]}"
        )
        client = anthropic.Anthropic(api_key=api_key, http_client=httpx.Client(follow_redirects=True))
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=2000,
            system="Sos analista de seguimiento de proyectos. Identificás compromisos y verificás su cumplimiento. Respondés solo con JSON válido.",
            messages=[{"role": "user", "content": prompt}])
        texto = msg.content[0].text.strip().replace("```json","").replace("```","").strip()
        resultado = _json.loads(texto)
        return jsonify({"ok": True, **resultado})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ── VUA Sugerencia de mitigación de riesgos (Mejora 10) ──────────────────────
@app.route("/api/vua/riesgos/<int:rid>/mitigacion_ia", methods=["POST"])
@login_required
def vua_riesgo_mitigacion_ia(rid):
    """Mejora 10: sugiere estrategias de mitigación específicas para un riesgo dado el contexto del proyecto."""
    api_key = get_api_key()
    if not api_key: return jsonify({"ok": False, "error": "API key no configurada"})
    try:
        import anthropic, httpx
        con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
        riesgo = con.execute("SELECT * FROM vua_riesgos WHERE id=?", (rid,)).fetchone()
        ejes   = [dict(r) for r in con.execute("SELECT nombre, estado FROM vua_ejes ORDER BY orden").fetchall()]
        con.close()
        if not riesgo: return jsonify({"ok": False, "error": "Riesgo no encontrado"})
        riesgo = dict(riesgo)

        ejes_txt   = " | ".join([f"{e['nombre']} ({e['estado']})" for e in ejes])
        prompt = (
            "Proyecto VUA — Ventanilla Única Aeroportuaria (ARCA Argentina, carga aérea internacional).\n"
            f"Ejes del proyecto: {ejes_txt}\n\n"
            "RIESGO A MITIGAR:\n"
            f"Título: {riesgo.get('titulo','')}\n"
            f"Descripción: {riesgo.get('descripcion','')}\n"
            f"Probabilidad: {riesgo.get('probabilidad','')} | Impacto: {riesgo.get('impacto','')}\n"
            f"Mitigación actual: {riesgo.get('mitigacion','')}\n\n"
            "Sugerí 3 estrategias de mitigación concretas y específicas para el contexto aduanero argentino. "
            "Para cada una indicá: acción concreta, responsable sugerido (organismo), y plazo estimado. "
            "Devolvé solo el texto de las 3 estrategias en prosa, numeradas, sin JSON."
        )
        client = anthropic.Anthropic(api_key=api_key, http_client=httpx.Client(follow_redirects=True))
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=800,
            system=SYSTEM_NORMATIVA,
            messages=[{"role": "user", "content": prompt}])
        return jsonify({"ok": True, "sugerencias": msg.content[0].text.strip()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ── Rutas Admin ───────────────────────────────────────────────────────────────
@app.route("/sintia")
@login_required
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
        role=session.get("role","readonly"))

@app.route("/api/admin/usuarios", methods=["GET"])
@login_required
def admin_usuarios_list():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT id, username, rol, modulos, activo, ultimo_acceso FROM usuarios ORDER BY id").fetchall()]
    con.close(); return jsonify({"ok": True, "rows": rows})

@app.route("/api/admin/usuarios", methods=["POST"])
@login_required
def admin_usuarios_create():
    data = request.json or {}
    username = data.get("username","").strip()
    password = data.get("password","")
    rol = data.get("rol","readonly")
    modulos = data.get("modulos","sintia,vua")
    if not username or not password:
        return jsonify({"ok": False, "error": "Usuario y password requeridos"})
    if len(password) < 8:
        return jsonify({"ok": False, "error": "Password minimo 8 caracteres"})
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    try:
        con = sqlite3.connect(HIST_DB)
        con.execute("INSERT INTO usuarios (username, password_hash, rol, modulos, activo) VALUES (?,?,?,?,1)",
            (username, hashed, rol, modulos))
        con.commit(); con.close(); return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": "Usuario ya existe" if "UNIQUE" in str(e) else str(e)})

@app.route("/api/admin/usuarios/<int:uid>", methods=["PUT"])
@login_required
def admin_usuarios_update(uid):
    data = request.json or {}
    con = sqlite3.connect(HIST_DB)
    fields = []; params = []
    for f in ["rol","modulos","activo"]:
        if f in data: fields.append(f + "=?"); params.append(data[f])
    if "password" in data and data["password"]:
        hashed = bcrypt.hashpw(data["password"].encode(), bcrypt.gensalt()).decode()
        fields.append("password_hash=?"); params.append(hashed)
    if fields:
        params.append(uid)
        con.execute("UPDATE usuarios SET " + ", ".join(fields) + " WHERE id=?", params)
        con.commit()
    con.close(); return jsonify({"ok": True})

@app.route("/api/admin/usuarios/<int:uid>", methods=["DELETE"])
@login_required
def admin_usuarios_delete(uid):
    con = sqlite3.connect(HIST_DB)
    con.execute("DELETE FROM usuarios WHERE id=?", (uid,))
    con.commit(); con.close(); return jsonify({"ok": True})

@app.route("/api/admin/sesiones", methods=["GET"])
@login_required
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
def admin_sesiones_revocar(token_prefix):
    con = sqlite3.connect(HIST_DB)
    con.execute("UPDATE sesiones SET activo=0 WHERE token LIKE ?", (token_prefix + "%",))
    con.execute("INSERT OR IGNORE INTO tokens_revocados (token) SELECT token FROM sesiones WHERE token LIKE ?",
        (token_prefix + "%",))
    con.commit(); con.close(); return jsonify({"ok": True})

@app.route("/api/admin/sesiones/revocar-todas", methods=["POST"])
@login_required
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
def admin_prompts_list():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT id, nombre, descripcion, modulo, modificado FROM prompts ORDER BY modulo, nombre").fetchall()]
    con.close(); return jsonify({"ok": True, "rows": rows})

@app.route("/api/admin/prompts/<int:pid>", methods=["GET"])
@login_required
def admin_prompts_get(pid):
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM prompts WHERE id=?", (pid,)).fetchone()
    con.close()
    if not row: return jsonify({"ok": False, "error": "No encontrado"})
    return jsonify({"ok": True, "prompt": dict(row)})

@app.route("/api/admin/prompts/<int:pid>", methods=["PUT"])
@login_required
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

def registrar_sesion(username, token, ip, ua):
    try:
        con = sqlite3.connect(HIST_DB)
        con.execute("INSERT OR REPLACE INTO sesiones (username, token, ip, user_agent, activo, ultimo_acceso) "
            "VALUES (?,?,?,?,1,datetime('now'))", (username, token, ip, ua[:200]))
        con.commit(); con.close()
    except: pass

def actualizar_sesion(token):
    try:
        con = sqlite3.connect(HIST_DB)
        con.execute("UPDATE sesiones SET ultimo_acceso=datetime('now') WHERE token=?", (token,))
        con.commit(); con.close()
    except: pass

def token_revocado(token):
    try:
        con = sqlite3.connect(HIST_DB)
        row = con.execute("SELECT 1 FROM tokens_revocados WHERE token=?", (token,)).fetchone()
        con.close(); return row is not None
    except: return False

# ══════════════════════════════════════════════════════════════════════════════
# SUBMÓDULOS CONSULTA DAT / RECHAZOS — agregar a app.py antes del if __name__
# Requiere: openpyxl   →   pip install openpyxl --break-system-packages
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/sintia/dashboard")
@login_required
def sintia_dashboard():
    if not os.path.exists(DB_PATH):
        return jsonify({"ok": False, "error": "BD no cargada."})
    import datetime as _dt
    try:
        con = sqlite3.connect(DB_PATH)
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

    # Detectar tabla del año actual
    import datetime as _dt
    anio = _dt.date.today().year
    tabla = f"DAT_{anio}"

    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        # Verificar que la tabla existe
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tabla,))
        if not cur.fetchone():
            con.close()
            return jsonify({"ok": False, "error": f"Tabla {tabla} no encontrada en la BD."})

        sql = f"SELECT * FROM {tabla} {where} LIMIT 501"
        cur.execute(sql, params)
        rows_raw = cur.fetchall()

        cols = list(rows_raw[0].keys()) if rows_raw else []
        truncated = len(rows_raw) > 500
        rows = [list(r) for r in rows_raw[:500]]

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
        return jsonify({"ok": True, "cols": cols, "rows": rows, "truncated": truncated, "resumen": resumen})

    except Exception as e:
        logging.error(f"DAT QUERY ERROR | {e}")
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/sintia/rec", methods=["POST"])
@login_required
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
        sql = f"SELECT PaisEmisor, Anio, NroMic, Fecha_ISO, Mes, Metodo, Mensaje FROM RECHAZOS {where} ORDER BY Fecha_ISO DESC LIMIT 501"
        cur.execute(sql, params)
        rows_raw = cur.fetchall()

        cols = list(rows_raw[0].keys()) if rows_raw else []
        truncated = len(rows_raw) > 500
        rows = [list(r) for r in rows_raw[:500]]

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
        return jsonify({"ok": True, "cols": cols, "rows": rows, "truncated": truncated, "resumen": resumen})

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
    import datetime as _dt
    tabla = f"DAT_{_dt.date.today().year}"

    try:
        con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute(f"SELECT * FROM {tabla} {where}", params)
        rows_raw = cur.fetchall(); con.close()
        cols = list(rows_raw[0].keys()) if rows_raw else []
        rows = [list(r) for r in rows_raw]
        buf = _exportar_xlsx(cols, rows)
        return send_file(buf, as_attachment=True,
                         download_name="DAT_consulta.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as e:
        logging.error(f"DAT EXPORT ERROR | {e}")
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/sintia/rec/export", methods=["POST"])
@login_required
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

@app.route("/garmin")
def garmin_index():
    from flask import redirect
    return redirect("/training")

@app.route("/api/garmin/actividades")
def api_actividades():
    tipo  = request.args.get("tipo", "todas")
    limit = int(request.args.get("limit", 50))
    desde = request.args.get("desde")
    hasta = request.args.get("hasta")
    rows  = get_actividades(limit=limit, tipo=tipo, desde=desde, hasta=hasta)
    return jsonify({"ok": True, "rows": rows})

@app.route("/api/garmin/actividades/<act_id>")
def api_actividad_detalle(act_id):
    act = get_actividad(act_id)
    if not act: return jsonify({"ok": False, "error": "No encontrada"})
    # Adjuntar análisis previos
    act["analisis"] = get_analisis(actividad_id=act_id)
    return jsonify({"ok": True, "actividad": act})

@app.route("/api/garmin/sync", methods=["POST"])
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

@app.route("/api/garmin/sync/status/<job_id>")
def api_sync_status(job_id):
    return jsonify(_sync_status.get(job_id, {"estado": "no_encontrado"}))

@app.route("/api/garmin/analizar", methods=["POST"])
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

@app.route("/api/garmin/analisis/progresion")
def api_analisis_progresion():
    rows = get_analisis(tipo="progresion")
    return jsonify({"ok": True, "rows": rows})

@app.route("/api/garmin/stats")
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

@app.route("/api/garmin/config", methods=["GET"])
def api_config_get():
    u, _ = get_credenciales_garmin()
    return jsonify({
        "ok": True,
        "configurado": credenciales_configuradas(),
        "usuario": u,
    })

@app.route("/api/garmin/config", methods=["POST"])
def api_config_set():
    data = request.json or {}
    usuario = data.get("usuario", "").strip()
    passwd  = data.get("passwd", "").strip()
    if not usuario or not passwd:
        return jsonify({"ok": False, "error": "Usuario y contraseña son requeridos"})
    set_credenciales_garmin(usuario, passwd)
    return jsonify({"ok": True})

@app.route("/api/garmin/carga_semanal")
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

@app.route("/api/garmin/comparar")
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

@app.route("/api/garmin/export_csv")
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
@app.route("/training")
def training_index():
    return render_template("training.html", username=session.get("username", ""))

@app.route("/api/training/semana/actual")
def api_semana_actual():
    num = _semana_actual()
    sem = _get_semana(num)
    return jsonify({"ok": True, "semana_num": num, "semana": sem})

@app.route("/api/training/semana/<int:num>")
def api_semana(num):
    sem = _get_semana(num)
    if not sem: return jsonify({"ok": False, "error": "Semana no encontrada"})
    return jsonify({"ok": True, "semana": sem})

@app.route("/api/training/semanas")
def api_semanas_lista():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT semana_num, fecha_inicio, fase, es_descarga, objetivo FROM entrenamiento_semanas ORDER BY semana_num"
    ).fetchall()]
    con.close()
    return jsonify({"ok": True, "rows": rows, "actual": _semana_actual()})

@app.route("/api/training/sesion/<sid>", methods=["PUT"])
def api_sesion_update(sid):
    data = request.json or {}
    con = sqlite3.connect(HIST_DB)
    con.execute(
        "UPDATE entrenamiento_plan SET descripcion=?, notas=?, modificado=datetime('now') WHERE id=?",
        (data.get("descripcion",""), data.get("notas",""), sid))
    con.commit(); con.close()
    return jsonify({"ok": True})

@app.route("/api/training/sesion", methods=["POST"])
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

@app.route("/api/training/sesion/<sid>", methods=["DELETE"])
def api_sesion_delete(sid):
    con = sqlite3.connect(HIST_DB)
    con.execute("DELETE FROM entrenamiento_plan WHERE id=?", (sid,))
    con.commit(); con.close()
    return jsonify({"ok": True})

@app.route("/api/training/log", methods=["GET"])
def api_log_list():
    fd = request.args.get("desde")
    fh = request.args.get("hasta")
    rows = _get_log(fd, fh)
    return jsonify({"ok": True, "rows": rows})

@app.route("/api/training/log", methods=["POST"])
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

@app.route("/api/training/log/<lid>", methods=["DELETE"])
def api_log_delete(lid):
    con = sqlite3.connect(HIST_DB)
    con.execute("DELETE FROM entrenamiento_log WHERE id=?", (lid,))
    con.commit(); con.close()
    return jsonify({"ok": True})

@app.route("/api/training/claude", methods=["POST"])
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

@app.route("/api/training/stats")
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

@app.route("/api/training/garmin_dia")
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


@app.route("/api/training/calendario")
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


@app.route("/api/garmin/actividades/<act_id>/sets")
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


# ══════════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════
# MÓDULO STOCK DEPÓSITOS
# ══════════════════════════════════════════════════════════════════════════════
_stock_jobs = {}

# ── BD Stock ──────────────────────────────────────────────────────────────────
def init_stock_db():
    """Crea las tablas de historial de stock si no existen."""
    con = sqlite3.connect(HIST_DB)
    con.execute("""CREATE TABLE IF NOT EXISTS stock_reportes (
        id          TEXT PRIMARY KEY,
        fecha_corte TEXT NOT NULL,
        fecha_gen   TEXT DEFAULT (datetime('now')),
        dias_tol    INTEGER DEFAULT 0,
        usuario     TEXT DEFAULT '',
        total       INTEGER DEFAULT 0,
        verde       INTEGER DEFAULT 0,
        azul        INTEGER DEFAULT 0,
        amarillo    INTEGER DEFAULT 0,
        rojo        INTEGER DEFAULT 0,
        negro       INTEGER DEFAULT 0
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS stock_registros (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        reporte_id  TEXT NOT NULL REFERENCES stock_reportes(id),
        codadu      TEXT,
        codlot      TEXT,
        razon_social TEXT,
        cuit        TEXT,
        tipo        TEXT,
        nombre_adu  TEXT,
        nombre_dira TEXT,
        semaforo    TEXT,
        comentario  TEXT,
        freg        TEXT,
        fstock      TEXT
    )""")
    con.execute("""CREATE INDEX IF NOT EXISTS idx_stock_reg_lot
        ON stock_registros(codadu, codlot)""")
    con.execute("""CREATE INDEX IF NOT EXISTS idx_stock_reg_reporte
        ON stock_registros(reporte_id)""")
    con.execute("""CREATE TABLE IF NOT EXISTS stock_tendencia (
        id                   TEXT NOT NULL,
        reporte_id           TEXT NOT NULL REFERENCES stock_reportes(id),
        freg_transmitio      INTEGER DEFAULT 0,
        freg_no_transmitio   INTEGER DEFAULT 0,
        freg_no_habil        INTEGER DEFAULT 0,
        fstock_transmitio    INTEGER DEFAULT 0,
        fstock_no_transmitio INTEGER DEFAULT 0,
        fstock_no_habil      INTEGER DEFAULT 0,
        pct_reg              REAL DEFAULT NULL,
        pct_stock            REAL DEFAULT NULL,
        PRIMARY KEY (id, reporte_id)
    )""")
    con.execute("""CREATE INDEX IF NOT EXISTS idx_stock_tend_id
        ON stock_tendencia(id)""")
    # Columna file_path agregada en v2 — ALTER TABLE idempotente
    try:
        con.execute("ALTER TABLE stock_reportes ADD COLUMN file_path TEXT DEFAULT ''")
    except Exception:
        pass  # Ya existe
    # Columnas pct agregadas en v3
    for _col in ["pct_reg REAL", "pct_stock REAL"]:
        try:
            con.execute(f"ALTER TABLE stock_tendencia ADD COLUMN {_col} DEFAULT NULL")
        except Exception:
            pass
    con.commit()
    con.close()

init_stock_db()

def _cargar_mod_rfixwis():
    """Carga el módulo _RFIXWIS.py desde el directorio de la app."""
    import importlib.util, os as _os
    script_path = _os.path.join(_os.path.dirname(__file__), "_RFIXWIS.py")
    spec = importlib.util.spec_from_file_location("rfixwis", script_path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def _guardar_reporte_bd(reporte_id, fecha_corte, dias_tol, usuario, registros, file_path=""):
    """Persiste una corrida completa en stock_reportes + stock_registros."""
    conteo = {"VERDE": 0, "AZUL": 0, "AMARILLO": 0, "ROJO": 0, "NEGRO": 0}
    for r in registros:
        s = r[5] if len(r) > 5 else "NEGRO"
        if s in conteo:
            conteo[s] += 1
    total = sum(conteo.values())

    con = sqlite3.connect(HIST_DB)
    # Upsert del reporte (puede re-generarse el mismo día)
    con.execute("""INSERT OR REPLACE INTO stock_reportes
        (id, fecha_corte, dias_tol, usuario, total, verde, azul, amarillo, rojo, negro, file_path)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (reporte_id, fecha_corte, dias_tol, usuario, total,
         conteo["VERDE"], conteo["AZUL"], conteo["AMARILLO"],
         conteo["ROJO"], conteo["NEGRO"], file_path))

    # Borrar registros anteriores del mismo reporte_id antes de reinsertar
    con.execute("DELETE FROM stock_registros WHERE reporte_id=?", (reporte_id,))

    batch = []
    for r in registros:
        batch.append((
            reporte_id,
            r[0],   # codadu
            r[1],   # codlot
            r[6],   # razon_social
            r[7],   # cuit
            r[8],   # tipo
            r[9],   # nombre_adu
            r[10],  # nombre_dira
            r[5],   # semaforo
            r[11],  # comentario
            r[3],   # freg  (ya en DD/MM/YYYY)
            r[4],   # fstock
        ))
    con.executemany("""INSERT INTO stock_registros
        (reporte_id, codadu, codlot, razon_social, cuit, tipo,
         nombre_adu, nombre_dira, semaforo, comentario, freg, fstock)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", batch)

    con.commit()
    con.close()
    return conteo, total

# ── Rutas ──────────────────────────────────────────────────────────────────────

@app.route("/stock")
@login_required
def stock_index():
    return render_template("stock.html", username=session.get("username", ""))


@app.route("/api/stock/generar", methods=["POST"])
@login_required
def api_stock_generar():
    import hashlib
    from datetime import datetime

    stock_file = request.files.get("stock")
    depo_file  = request.files.get("depositos")
    fecha_max  = request.form.get("fecha_max", "")
    dias_tol   = int(request.form.get("dias_tol", 5))

    if not stock_file or not depo_file:
        return jsonify({"ok": False, "error": "Faltan archivos"})

    try:
        stock_txt = stock_file.read().decode("utf-8", errors="replace")
        depo_txt  = depo_file.read().decode("utf-8", errors="replace")

        try:
            fecha_dt = datetime.strptime(fecha_max, "%Y-%m-%d") if fecha_max else datetime.today()
        except ValueError:
            fecha_dt = datetime.today()
        fecha_yymmdd = fecha_dt.strftime("%y%m%d")
        fecha_iso    = fecha_dt.strftime("%Y-%m-%d")

        mod       = _cargar_mod_rfixwis()
        registros = mod.procesar(stock_txt, depo_txt, fecha_yymmdd, dias_tol)
        serie     = mod.calcular_serie_grafico(stock_txt, depo_txt, fecha_yymmdd, dias_tol)

        # ID basado en fecha+tolerancia para que el mismo día/params sobreescriba
        reporte_id = hashlib.md5(f"{fecha_iso}|{dias_tol}".encode()).hexdigest()[:12]

        # ── Calcular tendencia por LOT ──────────────────────────────────────────
        # Reconstruir arr15 (misma lógica que _RFIXWIS.py)
        def _calc_fecha_bk(yymmdd, delta):
            from datetime import date, timedelta
            y, m, d = 2000+int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
            nd = date(y, m, d) + timedelta(days=delta)
            return f"{nd.year-2000:02d}{nd.month:02d}{nd.day:02d}"

        arr15_bk = []
        f_bk = fecha_yymmdd
        for _ in range(14):
            f_bk = _calc_fecha_bk(f_bk, -1)
            arr15_bk.insert(0, f_bk)
        arr15_bk.append(fecha_yymmdd)

        # Días no hábiles del módulo cargado
        dias_nh = getattr(mod, 'DIAS_NH', {})

        def _contar_u15(arr_bits):
            trans = no_trans = no_hab = 0
            for i, bit in enumerate(arr_bits):
                es_nh = bool(dias_nh.get(int(arr15_bk[i]), ''))
                if bit == 1:
                    trans += 1
                elif es_nh:
                    no_hab += 1
                else:
                    no_trans += 1
            habiles = trans + no_trans
            pct = round(trans / habiles * 100, 1) if habiles > 0 else None
            return trans, no_trans, no_hab, pct

        tend_batch = []
        tend_data  = {}   # {lot_id: {freg_t, freg_nt, freg_nh, fstock_t, fstock_nt, fstock_nh}}
        for r in registros:
            lot_id  = f"{r[0]}-{r[1]}"
            u15     = r[16] if len(r) > 16 else [0]*15
            u15s    = r[17] if len(r) > 17 else u15
            ft, fnt, fnh, pct_r   = _contar_u15(u15)
            st, snt, snh, pct_s   = _contar_u15(u15s)
            tend_data[lot_id] = {
                'freg_t': ft, 'freg_nt': fnt, 'freg_nh': fnh, 'pct_reg': pct_r,
                'fstock_t': st, 'fstock_nt': snt, 'fstock_nh': snh, 'pct_stock': pct_s,
            }
            tend_batch.append((lot_id, reporte_id, ft, fnt, fnh, st, snt, snh, pct_r, pct_s))

        con_t = sqlite3.connect(HIST_DB)
        con_t.execute("DELETE FROM stock_tendencia WHERE reporte_id=?", (reporte_id,))
        con_t.executemany("""INSERT OR REPLACE INTO stock_tendencia
            (id, reporte_id, freg_transmitio, freg_no_transmitio, freg_no_habil,
             fstock_transmitio, fstock_no_transmitio, fstock_no_habil, pct_reg, pct_stock)
            VALUES (?,?,?,?,?,?,?,?,?,?)""", tend_batch)
        con_t.commit()

        # ── Consultar reporte anterior por LOT para calcular tendencia ──────────
        lot_ids = list(tend_data.keys())
        tend_prev = {}
        if lot_ids:
            placeholders = ','.join('?' * len(lot_ids))
            rows_prev = con_t.execute(f"""
                SELECT t.id, t.pct_reg, t.pct_stock
                FROM stock_tendencia t
                JOIN stock_reportes p ON t.reporte_id = p.id
                WHERE t.id IN ({placeholders})
                  AND p.fecha_corte < ?
                  AND t.reporte_id != ?
                ORDER BY p.fecha_corte DESC
            """, lot_ids + [fecha_iso, reporte_id]).fetchall()
            seen = set()
            for row in rows_prev:
                lid = row[0]
                if lid not in seen:
                    tend_prev[lid] = {'pct_reg': row[1], 'pct_stock': row[2]}
                    seen.add(lid)
        con_t.close()

        # Calcular símbolo tendencia
        def _tend_symbol(curr, prev):
            if curr is None or prev is None: return '—'
            if curr > prev:  return '↑'
            if curr < prev:  return '↓'
            return '→'

        def _fmt_pct(pct):
            return f"{pct:.1f}%" if pct is not None else "—"

        tendencia = {}
        for lot_id, td in tend_data.items():
            prev = tend_prev.get(lot_id)
            tendencia[lot_id] = {
                'treg':          _tend_symbol(td['pct_reg'],   prev['pct_reg']   if prev else None),
                'tstock':        _tend_symbol(td['pct_stock'], prev['pct_stock'] if prev else None),
                'pct_reg':       _fmt_pct(td['pct_reg']),
                'pct_stock':     _fmt_pct(td['pct_stock']),
                'pct_reg_prev':  _fmt_pct(prev['pct_reg'])   if prev else None,
                'pct_stock_prev':_fmt_pct(prev['pct_stock']) if prev else None,
                'freg_t':        td['freg_t'],  'freg_nt': td['freg_nt'],  'freg_nh': td['freg_nh'],
                'fstock_t':      td['fstock_t'],'fstock_nt':td['fstock_nt'],'fstock_nh':td['fstock_nh'],
            }

        html_out  = mod.generar_html(registros, fecha_yymmdd, serie, tendencia)

        # Guardar HTML en disco
        fname_html = f"ReporteStock_{fecha_iso}_{reporte_id}.html"
        file_path  = os.path.join(STOCK_REPORTS_DIR, fname_html)
        with open(file_path, "w", encoding="utf-8") as fh:
            fh.write(html_out)

        conteo, total = _guardar_reporte_bd(
            reporte_id, fecha_iso, dias_tol,
            session.get("username", "?"), registros, file_path
        )

        # Cachear HTML en memoria (para descarga inmediata)
        _stock_jobs[reporte_id] = {
            "html":  html_out,
            "fecha": fecha_dt.strftime("%y%m%d"),
            "_ts":   __import__("time").time(),
        }

        logging.info(f"STOCK GENERAR | user={session.get('username')} | fecha={fecha_iso} | total={total}")

        return jsonify({
            "ok":        True,
            "job_id":    reporte_id,
            "total":     total,
            "conteo":    conteo,
            "reporte_id": reporte_id,
        })

    except Exception as e:
        logging.error(f"STOCK ERROR | {e}")
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/stock/download/<job_id>")
@login_required
def api_stock_download(job_id):
    from flask import Response, send_file

    # 1. Buscar en caché de memoria
    job = _stock_jobs.get(job_id)
    if job and job.get("html"):
        html  = job["html"]
        fecha = job.get("fecha", job_id)
        return Response(html, mimetype="text/html",
                        headers={"Content-Disposition": f"attachment; filename=ReporteStock_{fecha}.html"})

    # 2. Buscar archivo en disco
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rep = con.execute("SELECT * FROM stock_reportes WHERE id=?", (job_id,)).fetchone()
    con.close()
    if not rep:
        return jsonify({"ok": False, "error": "Reporte no encontrado"}), 404

    fp = rep["file_path"] if rep["file_path"] else ""
    if fp and os.path.exists(fp):
        return send_file(fp, mimetype="text/html", as_attachment=True,
                         download_name=os.path.basename(fp))

    return jsonify({"ok": False, "error": "Archivo no disponible. Regenerá el reporte."}), 410


@app.route("/api/stock/historial")
@login_required
def api_stock_historial():
    """Lista los últimos N reportes generados, con flag de archivo disponible."""
    limit = min(int(request.args.get("limit", 30)), 100)
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM stock_reportes ORDER BY fecha_corte DESC, fecha_gen DESC LIMIT ?",
        (limit,)).fetchall()]
    con.close()
    for r in rows:
        r["file_ok"] = bool(r.get("file_path") and os.path.exists(r["file_path"]))
    return jsonify({"ok": True, "rows": rows})


@app.route("/api/stock/historial/<job_id>", methods=["DELETE"])
@login_required
def api_stock_historial_delete(job_id):
    """Elimina un reporte del historial: registro en BD y archivo en disco."""
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rep = con.execute("SELECT * FROM stock_reportes WHERE id=?", (job_id,)).fetchone()
    if not rep:
        con.close()
        return jsonify({"ok": False, "error": "Reporte no encontrado"}), 404

    fp = rep["file_path"] if rep["file_path"] else ""

    # Borrar registros detalle y cabecera
    con.execute("DELETE FROM stock_registros WHERE reporte_id=?", (job_id,))
    con.execute("DELETE FROM stock_reportes WHERE id=?", (job_id,))
    con.commit()
    con.close()

    # Borrar archivo físico si existe
    file_deleted = False
    if fp and os.path.exists(fp):
        try:
            os.remove(fp)
            file_deleted = True
        except OSError as e:
            logging.warning(f"STOCK DELETE | no se pudo borrar {fp}: {e}")

    # Limpiar caché en memoria
    _stock_jobs.pop(job_id, None)

    logging.info(f"STOCK DELETE | user={session.get('username')} | id={job_id} | file={file_deleted}")
    return jsonify({"ok": True, "file_deleted": file_deleted})


@app.route("/api/stock/evolucion/<codadu>/<codlot>")
@login_required
def api_stock_evolucion(codadu, codlot):
    """
    Devuelve la serie histórica de estados de un LOT específico
    cruzando stock_registros con stock_reportes (ordenado por fecha_corte ASC).
    """
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("""
        SELECT
            p.fecha_corte,
            r.semaforo,
            r.comentario,
            r.freg,
            r.fstock,
            r.razon_social,
            r.tipo,
            r.nombre_adu
        FROM stock_registros r
        JOIN stock_reportes p ON r.reporte_id = p.id
        WHERE r.codadu = ? AND r.codlot = ?
        ORDER BY p.fecha_corte ASC
    """, (codadu, codlot)).fetchall()]
    con.close()

    if not rows:
        return jsonify({"ok": False, "error": "Sin historial para este depósito"})

    meta = {
        "codadu":      codadu,
        "codlot":      codlot,
        "razon_social": rows[-1]["razon_social"],
        "tipo":         rows[-1]["tipo"],
        "nombre_adu":   rows[-1]["nombre_adu"],
    }
    return jsonify({"ok": True, "serie": rows, "meta": meta})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)