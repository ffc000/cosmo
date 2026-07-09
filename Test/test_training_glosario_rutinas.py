"""
Tests de humo + funcionales para el glosario de ejercicios y las rutinas
manuales de blueprints/training.py (funcionalidad agregada para poder cargar
en texto libre ejercicios que Garmin no registra, con un glosario propio
alias->término estándar que se inyecta como contexto al mandar la rutina
a analizar con IA — ver _glosario_ejercicios_texto()).
"""


def test_rutas_glosario_y_rutinas_registradas(app_module):
    rutas = {r.rule for r in app_module.app.url_map.iter_rules()}
    esperadas = {
        "/api/training/glosario",
        "/api/training/glosario/<int:gid>",
        "/api/training/rutinas_manuales",
        "/api/training/rutinas_manuales/<rid>",
        "/api/training/rutinas_manuales/<rid>/analizar",
    }
    faltantes = esperadas - rutas
    assert not faltantes, f"Rutas de glosario/rutinas manuales no registradas: {faltantes}"


def test_glosario_requiere_login(client):
    resp = client.get("/api/training/glosario")
    assert resp.status_code in (302, 303)


def test_rutinas_manuales_requiere_login(client):
    resp = client.get("/api/training/rutinas_manuales")
    assert resp.status_code in (302, 303)


# ── Glosario de ejercicios ───────────────────────────────────────────────────

def test_glosario_viene_precargado_con_los_4_ejemplos(client, test_user):
    """Regresión: el seed en init_training_db() debe cargar estos 4 términos
    la primera vez que se crea la base. Se chequea que estén incluidos (no
    igualdad exacta) porque HIST_DB es compartida entre tests de este archivo
    y otro test puede haber agregado un quinto término antes de este."""
    client.post("/login", data={"user": test_user["username"], "pass": test_user["password"]})
    resp = client.get("/api/training/glosario")
    assert resp.status_code == 200
    aliases = {r["alias"] for r in resp.get_json()["rows"]}
    esperados = {
        "Máquina de remo",
        "Tirones de polea (de pie)",
        "Zancadas alternada hacia adelante",
        "Sentadilla con balón medicinal",
    }
    assert esperados <= aliases, f"Faltan del seed: {esperados - aliases}"


def test_glosario_crear_editar_borrar(client, test_user):
    client.post("/login", data={"user": test_user["username"], "pass": test_user["password"]})

    resp = client.post("/api/training/glosario", json={
        "alias": "Burpees sobre cajón", "termino_estandar": "Box Burpees"})
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True

    resp = client.get("/api/training/glosario")
    fila = next(r for r in resp.get_json()["rows"] if r["alias"] == "Burpees sobre cajón")
    assert fila["termino_estandar"] == "Box Burpees"

    resp = client.put(f"/api/training/glosario/{fila['id']}", json={"notas": "estilo Hyrox"})
    assert resp.status_code == 200
    resp = client.get("/api/training/glosario")
    fila = next(r for r in resp.get_json()["rows"] if r["id"] == fila["id"])
    assert fila["notas"] == "estilo Hyrox"

    resp = client.delete(f"/api/training/glosario/{fila['id']}")
    assert resp.status_code == 200
    resp = client.get("/api/training/glosario")
    assert all(r["alias"] != "Burpees sobre cajón" for r in resp.get_json()["rows"])


def test_glosario_rechaza_alias_o_termino_vacio(client, test_user):
    client.post("/login", data={"user": test_user["username"], "pass": test_user["password"]})
    resp = client.post("/api/training/glosario", json={"alias": "", "termino_estandar": "algo"})
    assert resp.get_json()["ok"] is False
    resp = client.post("/api/training/glosario", json={"alias": "algo", "termino_estandar": ""})
    assert resp.get_json()["ok"] is False


# ── Rutinas manuales ──────────────────────────────────────────────────────────

def test_rutina_manual_crear_listar_borrar(client, test_user):
    client.post("/login", data={"user": test_user["username"], "pass": test_user["password"]})

    texto = ("5 rounds:\n- 200m Máquina de remo\n- 15 Tirones de polea (de pie)\n"
             "- 12 Zancadas alternada hacia adelante\n- 10 Sentadilla con balón medicinal")
    resp = client.post("/api/training/rutinas_manuales", json={
        "fecha": "2026-07-09", "titulo": "Hyrox sim", "texto": texto})
    assert resp.status_code == 200
    rid = resp.get_json()["id"]

    resp = client.get("/api/training/rutinas_manuales")
    fila = next(r for r in resp.get_json()["rows"] if r["id"] == rid)
    assert fila["titulo"] == "Hyrox sim"
    assert "Máquina de remo" in fila["texto"]

    resp = client.delete(f"/api/training/rutinas_manuales/{rid}")
    assert resp.status_code == 200
    resp = client.get("/api/training/rutinas_manuales")
    assert all(r["id"] != rid for r in resp.get_json()["rows"])


def test_rutina_manual_rechaza_texto_vacio(client, test_user):
    client.post("/login", data={"user": test_user["username"], "pass": test_user["password"]})
    resp = client.post("/api/training/rutinas_manuales", json={"fecha": "2026-07-09", "texto": "   "})
    assert resp.get_json()["ok"] is False


def test_analizar_rutina_sin_api_key_no_rompe(client, test_user):
    """Sin API key configurada, /analizar debe devolver un error prolijo
    (ok:False) en vez de un 500 — regresión del smoke test manual hecho
    durante el desarrollo de esta función."""
    client.post("/login", data={"user": test_user["username"], "pass": test_user["password"]})
    resp = client.post("/api/training/rutinas_manuales", json={
        "fecha": "2026-07-09", "texto": "10 Sentadilla con balón medicinal"})
    rid = resp.get_json()["id"]
    resp = client.post(f"/api/training/rutinas_manuales/{rid}/analizar")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is False


def test_analizar_rutina_inexistente_devuelve_error(client, test_user):
    client.post("/login", data={"user": test_user["username"], "pass": test_user["password"]})
    resp = client.post("/api/training/rutinas_manuales/no-existe-este-id/analizar")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is False


# ── El "intérprete" (glosario inyectado como contexto) ───────────────────────

def test_glosario_ejercicios_texto_incluye_los_4_alias(app_module):
    """_glosario_ejercicios_texto() es lo que se inyecta en el prompt de la
    IA — si esto no trae los alias, la IA nunca se entera de la jerga."""
    import blueprints.training as training
    texto = training._glosario_ejercicios_texto()
    for alias in ["Máquina de remo", "Tirones de polea (de pie)",
                  "Zancadas alternada hacia adelante", "Sentadilla con balón medicinal"]:
        assert alias in texto
    assert "Row erg" in texto
    assert "Ski Erg" in texto
    assert "Wall Ball" in texto
