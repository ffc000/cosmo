"""Tests de humo para blueprints/senasa.py."""


def test_rutas_senasa_registradas_via_blueprint(app_module):
    rutas = {r.rule for r in app_module.app.url_map.iter_rules()}
    esperadas = {
        "/senasa",
        "/api/senasa/cronologia",
        "/api/senasa/minutas",
        "/api/senasa/acuerdos",
        "/api/senasa/informe",
        "/api/senasa/informe/download/<job_id>",
    }
    faltantes = esperadas - rutas
    assert not faltantes, f"Rutas de SENASA no registradas: {faltantes}"


def test_senasa_index_requiere_login(client):
    resp = client.get("/senasa")
    assert resp.status_code in (302, 303)
    assert "/login" in resp.headers.get("Location", "")


def test_senasa_informe_requiere_login(client):
    resp = client.get("/api/senasa/informe")
    assert resp.status_code in (302, 303)
