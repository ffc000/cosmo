"""
Tests de autenticación: login correcto/incorrecto, expiración de sesión por
inactividad (10 min) y rate limiting del endpoint de login (20/15min).
"""
import time


def test_login_credenciales_incorrectas(client, test_user):
    resp = client.post("/login", data={"user": test_user["username"], "pass": "clave-equivocada"})
    assert resp.status_code == 200
    assert b"incorrectos" in resp.data
    with client.session_transaction() as sess:
        assert not sess.get("logged_in")


def test_login_usuario_inexistente(client):
    resp = client.post("/login", data={"user": "no_existe_este_usuario", "pass": "cualquiera"})
    assert resp.status_code == 200
    assert b"incorrectos" in resp.data


def test_login_correcto_setea_sesion(client, test_user):
    resp = client.post("/login", data={"user": test_user["username"], "pass": test_user["password"]})
    # login exitoso redirige a "/"
    assert resp.status_code in (302, 303)
    with client.session_transaction() as sess:
        assert sess.get("logged_in") is True
        assert sess.get("username") == test_user["username"]
        assert sess.get("role") == "admin"
        assert "last_active" in sess


def test_ruta_protegida_sin_login_redirige(client):
    resp = client.get("/")
    assert resp.status_code in (302, 303)
    assert "/login" in resp.headers.get("Location", "")


def test_sesion_expira_tras_10_min_de_inactividad(client, test_user):
    client.post("/login", data={"user": test_user["username"], "pass": test_user["password"]})
    with client.session_transaction() as sess:
        # Simula que la última actividad fue hace 11 minutos (601+ segundos)
        sess["last_active"] = time.time() - 700
    resp = client.get("/")
    assert resp.status_code in (302, 303)
    assert "/login" in resp.headers.get("Location", "")


def test_sesion_activa_dentro_de_10_min_no_expira(client, test_user):
    client.post("/login", data={"user": test_user["username"], "pass": test_user["password"]})
    with client.session_transaction() as sess:
        sess["last_active"] = time.time() - 60  # hace 1 minuto, dentro del límite
    resp = client.get("/")
    assert resp.status_code == 200


def test_rate_limit_login_bloquea_tras_muchos_intentos(client):
    """El login tiene @limiter.limit("20 per 15 minutes"). Al intento 21
    debería empezar a devolver 429, sin importar si la contraseña es correcta."""
    ultima_respuesta = None
    for _ in range(25):
        ultima_respuesta = client.post("/login", data={"user": "x", "pass": "y"})
        if ultima_respuesta.status_code == 429:
            break
    assert ultima_respuesta.status_code == 429, (
        "Se esperaba un 429 (Too Many Requests) antes del intento 25; "
        "si esto falla, revisar que RATELIMIT_STORAGE_URI no esté mal configurado "
        "o que el decorador @limiter.limit siga en la ruta /login."
    )
