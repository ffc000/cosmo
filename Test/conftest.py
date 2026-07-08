"""
conftest.py — fixtures compartidas para los tests de CosmoTools.

IMPORTANTE: nunca corras estos tests apuntando HIST_DB/DB_PATH a las bases
reales de producción (/data/historial.db, /data/pad.db). Este conftest
redirige ambas a archivos temporales ANTES de importar app.py, precisamente
para que correr `pytest` no pueda tocar datos reales por accidente.
"""
import os
import sys
import secrets
import tempfile
import importlib

import pytest

# Debe pasar ANTES de importar app.py: HIST_DB/DB_PATH se leen como
# variables de entorno al momento del import (nivel de módulo).
_TMP_DIR = tempfile.mkdtemp(prefix="cosmo_tests_")
os.environ["HIST_DB"] = os.path.join(_TMP_DIR, "historial_test.db")
os.environ["DB_PATH"] = os.path.join(_TMP_DIR, "pad_test.db")
os.environ.setdefault("SECRET_KEY", secrets.token_hex(16))
# Sin token de Telegram en tests: notificar_telegram() debe ser un no-op.
os.environ.pop("TELEGRAM_BOT_TOKEN", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="session")
def app_module():
    """Importa app.py una sola vez por sesión de tests, ya apuntando a las
    bases temporales (ver arriba). Si algún test necesita un usuario, se
    crea con la fixture `test_user` más abajo."""
    import app as app_module  # noqa: WPS433 (import diferido a propósito)
    return app_module


@pytest.fixture()
def client(app_module):
    app_module.app.config["TESTING"] = True
    app_module.app.config["WTF_CSRF_ENABLED"] = False  # los tests no arman el token CSRF
    try:
        import core
        core.limiter.reset()  # cada test arranca con el rate limiter limpio
    except Exception:
        pass
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture()
def test_user(app_module):
    """Crea (o reemplaza) un usuario de prueba con password conocida."""
    import bcrypt
    import sqlite3
    username, password = "test_user", "ClaveDePrueba123!"
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    con = sqlite3.connect(app_module.HIST_DB)
    con.execute("DELETE FROM usuarios WHERE username=?", (username,))
    con.execute(
        "INSERT INTO usuarios (username, password_hash, rol, modulos, activo) "
        "VALUES (?,?,?,?,1)",
        (username, pw_hash, "admin", "sintia,vua,senasa,finanzas,training,stock"))
    con.commit()
    con.close()
    return {"username": username, "password": password}
