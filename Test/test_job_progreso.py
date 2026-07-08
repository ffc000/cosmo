"""Tests de _pct_desde_mensaje / progreso en job_status (core.py)."""


def test_progreso_avanza_con_hitos_conocidos(app_module):
    import core
    job = core.job_create("job-progreso-1", "Iniciando generación...", username="test")
    assert job["progreso"] == 0
    job["log"].append("Conectando a la BD...")
    assert job["progreso"] == 5
    job["log"].append("Corriendo queries...")
    assert job["progreso"] == 15
    job["log"].append("✓ Proceso completado")
    assert job["progreso"] == 100


def test_progreso_nunca_retrocede(app_module):
    import core
    job = core.job_create("job-progreso-2", "Iniciando...", username="test")
    job["log"].append("✓ Gráficos generados")  # 85
    assert job["progreso"] == 85
    job["log"].append("Conectando a la BD...")  # hito de 5, menor al actual
    assert job["progreso"] == 85  # no debe bajar


def test_progreso_no_avanza_con_mensajes_de_error(app_module):
    import core
    job = core.job_create("job-progreso-3", "Iniciando...", username="test")
    job["log"].append("Corriendo queries...")  # 15
    antes = job["progreso"]
    job["log"].append("✗ Error: no se pudo conectar")
    assert job["progreso"] == antes


def test_progreso_se_persiste_y_se_puede_leer_con_job_get(app_module):
    import core
    job = core.job_create("job-progreso-4", "Iniciando...", username="test")
    job["log"].append("Corriendo queries...")
    # Simular que otro worker (sin este job en memoria) lo lee de SQLite
    core.job_status.pop("job-progreso-4")
    leido = core.job_get("job-progreso-4")
    assert leido["progreso"] == 15
