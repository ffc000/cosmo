"""Tests de humo para blueprints/vua.py."""


def test_rutas_vua_registradas_via_blueprint(app_module):
    rutas = {r.rule for r in app_module.app.url_map.iter_rules()}
    esperadas = {
        "/vua",
        "/api/vua/minuta",
        "/api/vua/informe",
        "/api/vua/ejes",
        "/api/vua/glosario",
        "/api/vua/riesgos",
        "/api/vua/config/resumen_ejecutivo/generar",
    }
    faltantes = esperadas - rutas
    assert not faltantes, f"Rutas de VUA no registradas: {faltantes}"


def test_vua_index_requiere_login(client):
    resp = client.get("/vua")
    assert resp.status_code in (302, 303)
    assert "/login" in resp.headers.get("Location", "")


def test_vua_minuta_requiere_login(client):
    resp = client.post("/api/vua/minuta")
    assert resp.status_code in (302, 303)


def test_integrantes_sigue_en_app_no_en_vua(app_module):
    """/api/integrantes/* es compartido con SENASA — a propósito quedó en
    app.py y no se movió a blueprints/vua.py."""
    rutas = {r.rule for r in app_module.app.url_map.iter_rules()}
    assert "/api/integrantes/organismos" in rutas
