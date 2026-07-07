"""
Tests de humo para blueprints/finanzas.py — con foco extra en que
finanzas_owner_required siga bloqueando a usuarios con el módulo habilitado
pero que no son el dueño ni admin (dato personal/DDJJ, el más sensible).
"""


def test_rutas_finanzas_registradas_via_blueprint(app_module):
    rutas = {r.rule for r in app_module.app.url_map.iter_rules()}
    esperadas = {
        "/finanzas",
        "/api/finanzas/tarjetas",
        "/api/finanzas/movimientos",
        "/api/finanzas/categorias",
        "/api/finanzas/ddjj",
        "/api/finanzas/ddjj/tarjetas/<tarjeta_id>/revelar",
    }
    faltantes = esperadas - rutas
    assert not faltantes, f"Rutas de finanzas no registradas: {faltantes}"


def test_finanzas_index_requiere_login(client):
    resp = client.get("/finanzas")
    assert resp.status_code in (302, 303)
    assert "/login" in resp.headers.get("Location", "")


def test_finanzas_bloquea_usuario_sin_modulo_habilitado(client, test_user):
    """test_user tiene 'finanzas' en sus módulos (ver conftest) pero no es
    admin ni está en FINANZAS_ALLOWED_USERS: finanzas_owner_required debe
    bloquearlo igual, no solo modulo_required."""
    client.post("/login", data={"user": test_user["username"], "pass": test_user["password"]})
    with client.session_transaction() as sess:
        sess["role"] = "viewer"  # ya no admin
    resp = client.get("/finanzas")
    assert resp.status_code in (302, 303, 403)
