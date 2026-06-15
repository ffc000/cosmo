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

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(filename="/data/accesos.log", level=logging.INFO,
    format="%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

# ── Rate limiter ───────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address, app=app,
    default_limits=[], storage_uri="memory://")

# ── Job queue ──────────────────────────────────────────────────────────────────
job_status = {}

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
    raw = open(tmp_path, encoding="utf-8", errors="replace").read()
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    try:
        if tabla.startswith("DAT_"):
            log.append("Procesando archivo DAT...")
            lines = [l for l in raw.splitlines()
                     if l.strip() and ";" in l
                     and not l.strip().startswith("---")
                     and not (l.strip().upper().startswith("REGISTRO") and ";" not in l)]
            if len(lines) < 2:
                log.append("✗ No se encontraron datos válidos"); con.close(); return
            headers = [h.strip() for h in lines[0].split(";")]
            log.append(f"Columnas: {len(headers)} | Filas a insertar: {len(lines)-1:,}")
            rows = []
            for line in lines[1:]:
                vals = [v.strip() for v in line.split(";")]
                while len(vals) < len(headers): vals.append(None)
                rows.append(vals[:len(headers)])
            cur.execute(f"DROP TABLE IF EXISTS {tabla}")
            cols_def = ", ".join([f'"{h}" TEXT' for h in headers])
            cur.execute(f"CREATE TABLE {tabla} ({cols_def})")
            placeholders = ", ".join(["?" for _ in headers])
            batch_size = 2000
            for i in range(0, len(rows), batch_size):
                cur.executemany(f"INSERT INTO {tabla} VALUES ({placeholders})", rows[i:i+batch_size])
                if (i+batch_size) % 20000 == 0 or i+batch_size >= len(rows):
                    con.commit()
                    log.append(f"  {min(i+batch_size,len(rows)):,} / {len(rows):,} filas insertadas...")
            log.append("Calculando fechas ISO...")
            for col in ["FECHA_INGRESO_ISO","FECHA_TRANS_ISO"]:
                try: cur.execute(f"ALTER TABLE {tabla} ADD COLUMN {col} TEXT")
                except: pass
            cur.execute(f"""UPDATE {tabla} SET
                FECHA_INGRESO_ISO = CASE
                    WHEN FECHA_INGRESO IS NOT NULL AND length(FECHA_INGRESO)>=10 THEN
                        substr(FECHA_INGRESO,7,4)||'-'||substr(FECHA_INGRESO,4,2)||'-'||substr(FECHA_INGRESO,1,2)||
                        CASE WHEN instr(FECHA_INGRESO,' ')>0 THEN ' '||substr(FECHA_INGRESO,instr(FECHA_INGRESO,' ')+1,5) ELSE ' 00:00' END
                    ELSE NULL END,
                FECHA_TRANS_ISO = CASE
                    WHEN FECHA_TRANS IS NOT NULL AND FECHA_TRANS NOT IN ('-','') AND length(FECHA_TRANS)>=10 THEN
                        substr(FECHA_TRANS,7,4)||'-'||substr(FECHA_TRANS,4,2)||'-'||substr(FECHA_TRANS,1,2)||
                        CASE WHEN instr(FECHA_TRANS,' ')>0 THEN ' '||substr(FECHA_TRANS,instr(FECHA_TRANS,' ')+1,5) ELSE ' 00:00' END
                    ELSE NULL END""")
            con.commit()
            log.append("Creando índice...")
            try: cur.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{tabla}_key ON {tabla}(OPERACION_PAD_EXT, MIC, TIPO_REGISTRO)")
            except: pass
            con.commit(); con.close()
            log.append(f"✓ {tabla}: {len(rows):,} registros, fechas ISO calculadas, índice creado")

        elif tabla == "RECHAZOS":
            log.append("Procesando archivo RECHAZOS...")
            lines = []
            for l in raw.splitlines():
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
                                 capture_output=True, text=True, timeout=20)
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
    return send_file(ruta, as_attachment=True, download_name=fname,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

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
    job_status[job_id] = {"status": "running", "log": ["Generando informe VUA..."], "files": []}

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
                           capture_output=True, text=True, timeout=60)
            if result.returncode != 0 or not os.path.exists(out_path):
                log.append(f"✗ Error Node: {result.stderr[:300]}")
                job_status[job_id]["status"] = "error"
                return
            fname    = f"Informe_VUA_{datetime.today().strftime('%Y%m%d_%H%M')}.docx"
            dest     = os.path.join(OUTPUT_FOLDER, fname)
            os.rename(out_path, dest)
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
    """Descarga el informe VUA una vez completado el job."""
    job = job_status.get(job_id)
    if not job: return jsonify({"ok": False, "error": "Job no encontrado"}), 404
    if job["status"] != "done": return jsonify({"ok": False, "status": job["status"]}), 202
    files = job.get("files", [])
    if not files or not os.path.exists(files[0]):
        return jsonify({"ok": False, "error": "Archivo no encontrado"}), 404
    return send_file(files[0], as_attachment=True,
        download_name=os.path.basename(files[0]),
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


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
            f"Proyecto VUA — Ventanilla Única Aeroportuaria (ARCA Argentina, carga aérea internacional).\n"
            f"Ejes del proyecto: {ejes_txt}\n\n"
            f"RIESGO A MITIGAR:\n"
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
