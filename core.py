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
from datetime import datetime

from flask import session, redirect, url_for, request, jsonify

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
