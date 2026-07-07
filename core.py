"""
core.py — Infraestructura compartida entre app.py y los blueprints de
CosmoTools (auth, sesiones, rutas de datos).

Por qué existe este archivo separado: los blueprints (blueprints/stock.py,
y los que sigan) necesitan los decoradores de auth (login_required,
modulo_required, etc.) y las rutas de datos (HIST_DB, DB_PATH). Si esas
cosas vivieran definidas en app.py, importar un blueprint DESDE app.py
crearía un import circular (app.py -> blueprints/stock.py -> app.py).
Al vivir acá, tanto app.py como cualquier blueprint importan de core.py
sin que core.py necesite saber que existen.
"""
import os
import sqlite3
from functools import wraps
from datetime import datetime, timedelta

from flask import Flask, session, redirect, url_for, request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

# ── App Flask + CSRF + rate limiter ─────────────────────────────────────────
# Viven acá (no en app.py) para que los blueprints puedan hacer
# `from core import app, limiter` sin generar un import circular con app.py,
# y para garantizar que `limiter` ya exista antes de que se importe
# cualquier blueprint que use @limiter.limit(...) en sus rutas.
app = Flask(__name__)

# Confía en 1 proxy (nginx) para X-Forwarded-For: reescribe request.remote_addr
# de forma segura y evita que el cliente falsee su IP con headers propios.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=0, x_port=0, x_prefix=0)

_secret = os.environ.get("SECRET_KEY")
if not _secret:
    raise RuntimeError("SECRET_KEY no está definida en las variables de entorno. "
                       "Exportá una clave aleatoria antes de iniciar la aplicación.")
app.secret_key = _secret
app.permanent_session_lifetime = timedelta(minutes=10)
app.config['MAX_CONTENT_LENGTH'] = None  # Sin límite en Flask — nginx maneja con client_max_body_size 2G
app.config['SESSION_COOKIE_SECURE'] = True     # solo se envía por HTTPS
app.config['SESSION_COOKIE_HTTPONLY'] = True   # no accesible desde JS
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # mitiga CSRF básico en navegación cruzada
app.config['WTF_CSRF_TIME_LIMIT'] = None  # el timeout de sesión ya lo maneja login_required (10min)

@app.after_request
def _security_headers(resp):
    """Headers de defensa en profundidad — no reemplazan la validación de
    entrada/salida ya hecha, pero limitan el daño si algo se escapa:
    - X-Frame-Options: evita que la app se embeba en un <iframe> ajeno (clickjacking).
    - X-Content-Type-Options: evita que el navegador "adivine" un tipo de
      contenido distinto al declarado (mitiga algunos vectores de XSS).
    - Strict-Transport-Security: fuerza HTTPS en el navegador (la app ya
      requiere cookies solo por HTTPS con SESSION_COOKIE_SECURE).
    - Referrer-Policy: no filtra la URL completa (con tokens/ids) a terceros.
    """
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return resp

csrf = CSRFProtect(app)

# storage_uri: en memoria por defecto (sirve con 1 solo worker). Si se corre con
# más de un worker (gunicorn -w >1), cada worker llevaría su propia cuenta y el
# límite dejaría de cumplirse — en ese caso definir RATELIMIT_STORAGE_URI=redis://...
limiter = Limiter(key_func=get_remote_address, app=app,
    default_limits=["300 per minute"],
    storage_uri=os.environ.get("RATELIMIT_STORAGE_URI", "memory://"))

# ── Rutas de datos (configurables por variable de entorno para poder
# testear sin arriesgar la base real — ver tests/conftest.py) ─────────────────
HIST_DB           = os.environ.get("HIST_DB", "/data/historial.db")
DB_PATH           = os.environ.get("DB_PATH", "/data/pad.db")
OUTPUT_FOLDER     = "/data/informes"
STOCK_REPORTS_DIR = "/data/reports/stock"


# ── Sesiones ─────────────────────────────────────────────────────────────────
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


# ── Decoradores de auth ──────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        token = session.get("token", "")
        if token and token_revocado(token):
            session.clear(); return redirect(url_for("login"))
        last = session.get("last_active")
        if last and datetime.now().timestamp() - last > 600:
            session.clear(); return redirect(url_for("login"))
        session["last_active"] = datetime.now().timestamp()
        if token:
            try: actualizar_sesion(token)
            except: pass
        session.permanent = True
        return f(*args, **kwargs)
    return decorated


def tiene_permiso_admin(seccion=None):
    """seccion=None -> algún permiso admin (bd o sistema). seccion='bd'|'sistema' -> ese en particular.
    role=='admin' es superadmin y siempre pasa (compatibilidad con usuarios existentes)."""
    if session.get("role") == "admin":
        return True
    modulos = session.get("modulos", [])
    if seccion:
        return f"admin_{seccion}" in modulos
    return "admin_bd" in modulos or "admin_sistema" in modulos


def admin_required(seccion=None):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not tiene_permiso_admin(seccion):
                if request.is_json or request.path.startswith("/api/"):
                    return jsonify({"ok": False, "error": "Sin permiso"}), 403
                return redirect(url_for("index"))
            return f(*args, **kwargs)
        return decorated
    return decorator


def modulo_required(nombre):
    """Exige que el usuario tenga `nombre` en su lista de módulos habilitados.
    Sin esto, ocultar el link del sidebar no alcanza: cualquier usuario logueado
    puede pegar la URL directamente."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if nombre not in session.get("modulos", []):
                if request.is_json or request.path.startswith("/api/"):
                    return jsonify({"ok": False, "error": "Módulo no habilitado"}), 403
                return redirect(url_for("index"))
            return f(*args, **kwargs)
        return decorated
    return decorator


_FINANZAS_ALLOWED = {u.strip() for u in os.environ.get("FINANZAS_ALLOWED_USERS", "").split(",") if u.strip()}

def finanzas_owner_required(f):
    """Los datos de fin_ddjj* son personales (una sola declaración jurada por año,
    sin columna de propietario) y NO deben quedar accesibles a cualquier usuario
    al que se le habilite el módulo 'finanzas' desde el panel de admin.
    Requiere, además del módulo habilitado, ser admin o estar en
    FINANZAS_ALLOWED_USERS (env var, usernames separados por coma)."""
    @wraps(f)
    @modulo_required("finanzas")
    def decorated(*args, **kwargs):
        u = session.get("username", "")
        if session.get("role") != "admin" and u not in _FINANZAS_ALLOWED:
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Sin permiso"}), 403
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated
