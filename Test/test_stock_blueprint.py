"""
Tests de humo para blueprints/stock.py — confirman que la extracción a
blueprint no rompió el registro de rutas ni la protección de auth.
"""


def test_rutas_stock_registradas_via_blueprint(app_module):
    rutas = {r.rule for r in app_module.app.url_map.iter_rules()}
    esperadas = {
        "/stock",
        "/api/stock/generar",
        "/api/stock/download/<job_id>",
        "/api/stock/historial",
        "/api/stock/historial/<job_id>",
        "/api/stock/evolucion/<codadu>/<codlot>",
    }
    faltantes = esperadas - rutas
    assert not faltantes, f"Rutas de stock no registradas: {faltantes}"


def test_stock_index_requiere_login(client):
    resp = client.get("/stock")
    assert resp.status_code in (302, 303)
    assert "/login" in resp.headers.get("Location", "")


def test_stock_generar_requiere_login(client):
    resp = client.post("/api/stock/generar")
    assert resp.status_code in (302, 303)


def test_stock_historial_requiere_login(client):
    resp = client.get("/api/stock/historial")
    assert resp.status_code in (302, 303)
