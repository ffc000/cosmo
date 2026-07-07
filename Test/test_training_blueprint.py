"""
Tests de humo para blueprints/training.py (Garmin + Training) — confirman
que la extracción a blueprint no rompió el registro de rutas ni la
protección de auth, ni el rate limiter compartido (que ahora vive en core.py
justamente para que este blueprint lo pueda usar sin import circular).
"""


def test_rutas_training_registradas_via_blueprint(app_module):
    rutas = {r.rule for r in app_module.app.url_map.iter_rules()}
    esperadas = {
        "/training",
        "/garmin",
        "/api/garmin/sync",
        "/api/garmin/actividades",
        "/api/training/semana/actual",
        "/api/training/claude",
    }
    faltantes = esperadas - rutas
    assert not faltantes, f"Rutas de training/garmin no registradas: {faltantes}"


def test_training_index_requiere_login(client):
    resp = client.get("/training")
    assert resp.status_code in (302, 303)
    assert "/login" in resp.headers.get("Location", "")


def test_garmin_sync_requiere_login(client):
    resp = client.post("/api/garmin/sync")
    assert resp.status_code in (302, 303)


def test_limiter_compartido_desde_core(app_module):
    """El decorador @limiter.limit(...) en training.py usa el mismo objeto
    limiter que core.py/app.py — si esto no fuera así, el import ya habría
    fallado con NameError al cargar la app (como pasó durante el desarrollo)."""
    import core
    assert app_module.limiter is core.limiter
