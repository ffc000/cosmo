"""
Tests de finanzas.py: categorización automática, aprendizaje de reglas
(con foco en el bug ya corregido de reglas duplicadas) y detección de
posibles duplicados entre cargas.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import finanzas  # noqa: E402


@pytest.fixture()
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    finanzas.init_finanzas_db(path)
    yield path
    os.remove(path)


def _crear_categoria(db_path, nombre):
    return finanzas.crear_categoria(db_path, nombre)


def test_categorizar_sin_reglas_devuelve_none(db_path):
    import sqlite3
    con = sqlite3.connect(db_path)
    con.execute("DELETE FROM fin_reglas_categorizacion")  # sin las reglas default
    con.commit(); con.close()
    assert finanzas.categorizar(db_path, "COMPRA EN UN COMERCIO CUALQUIERA") is None


def test_categorizar_usa_regla_existente(db_path):
    cat_id = _crear_categoria(db_path, "Supermercado Test")
    finanzas.aprender_regla(db_path, "carrefour", cat_id)
    resultado = finanzas.categorizar(db_path, "COMPRA CARREFOUR SUC 123")
    assert resultado == cat_id


def test_aprender_regla_reemplaza_la_anterior_no_la_duplica(db_path):
    """Regresión del bug real: antes, corregir dos veces el mismo comercio
    dejaba dos reglas contradictorias en la tabla, y la vieja podía seguir
    ganando el empate en `categorizar()`. Ahora debe haber una sola regla
    por patrón, y debe ser siempre la más reciente."""
    cat_super = _crear_categoria(db_path, "Supermercado Test")
    cat_combustible = _crear_categoria(db_path, "Combustible")

    finanzas.aprender_regla(db_path, "YPF", cat_super)       # corrección #1 (mal categorizada a propósito)
    finanzas.aprender_regla(db_path, "YPF", cat_combustible)  # corrección #2, la correcta

    import sqlite3
    con = sqlite3.connect(db_path)
    cantidad = con.execute(
        "SELECT COUNT(*) FROM fin_reglas_categorizacion WHERE patron=?", ("YPF",)
    ).fetchone()[0]
    con.close()
    assert cantidad == 1, "aprender_regla no debe dejar reglas duplicadas para el mismo patrón"

    resultado = finanzas.categorizar(db_path, "YPF FULL SUC CENTRO")
    assert resultado == cat_combustible, "debe ganar la corrección más reciente, no la primera"


def test_categorizar_regla_mas_especifica_gana_empate(db_path):
    """Con la misma prioridad, gana el patrón más largo (más específico)."""
    cat_generico = _crear_categoria(db_path, "Genérico")
    cat_especifico = _crear_categoria(db_path, "Específico")
    finanzas.aprender_regla(db_path, "SUC", cat_generico)
    finanzas.aprender_regla(db_path, "SUC CENTRO 123", cat_especifico)
    resultado = finanzas.categorizar(db_path, "COMPRA SUC CENTRO 123 VARIOS")
    assert resultado == cat_especifico


def test_buscar_posible_duplicado_detecta_mismo_monto_en_rango(db_path):
    tarjeta_id = finanzas.crear_tarjeta(db_path, "Visa Test", "visa")
    finanzas.guardar_movimientos(db_path, tarjeta_id, "resumen-1", [{
        "fecha": "2026-05-10", "descripcion": "COMPRA TEST", "comprobante": "1",
        "monto_ars": 1500.0, "monto_usd": 0.0, "cuota_actual": None, "cuota_total": None,
        "tipo": "consumo",
    }])
    duplicados = finanzas.buscar_posible_duplicado(db_path, tarjeta_id, "2026-05-12", 1500.0, dias=5)
    assert len(duplicados) == 1
    assert duplicados[0]["descripcion"] == "COMPRA TEST"


def test_buscar_posible_duplicado_no_detecta_fuera_de_rango(db_path):
    tarjeta_id = finanzas.crear_tarjeta(db_path, "Visa Test", "visa")
    finanzas.guardar_movimientos(db_path, tarjeta_id, "resumen-1", [{
        "fecha": "2026-05-10", "descripcion": "COMPRA TEST", "comprobante": "1",
        "monto_ars": 1500.0, "monto_usd": 0.0, "cuota_actual": None, "cuota_total": None,
        "tipo": "consumo",
    }])
    duplicados = finanzas.buscar_posible_duplicado(db_path, tarjeta_id, "2026-06-01", 1500.0, dias=5)
    assert duplicados == []


def test_buscar_posible_duplicado_no_detecta_monto_distinto(db_path):
    tarjeta_id = finanzas.crear_tarjeta(db_path, "Visa Test", "visa")
    finanzas.guardar_movimientos(db_path, tarjeta_id, "resumen-1", [{
        "fecha": "2026-05-10", "descripcion": "COMPRA TEST", "comprobante": "1",
        "monto_ars": 1500.0, "monto_usd": 0.0, "cuota_actual": None, "cuota_total": None,
        "tipo": "consumo",
    }])
    duplicados = finanzas.buscar_posible_duplicado(db_path, tarjeta_id, "2026-05-11", 2000.0, dias=5)
    assert duplicados == []


def test_ingresos_vacio_devuelve_ceros(db_path):
    assert finanzas.get_ingresos(db_path, "2026-07") == {"sueldo": 0, "fondo": 0, "otros": 0, "total": 0}


def test_guardar_recibo_sueldo_y_get_ingresos(db_path):
    finanzas.guardar_recibo_sueldo(db_path, "2026-06", "sueldo",
        serv_extraordinario=1079350.86, otros_conceptos=1600607.63,
        total_remuneraciones=2679958.49, total_descuentos=-381375.24,
        neto_total=2298583.25, archivo_nombre="recibo.pdf")
    ing = finanzas.get_ingresos(db_path, "2026-06")
    assert ing["sueldo"] == 2298583.25
    assert ing["fondo"] == 0
    assert ing["total"] == 2298583.25


def test_guardar_recibo_sueldo_actualiza_no_duplica(db_path):
    finanzas.guardar_recibo_sueldo(db_path, "2026-06", "sueldo", 100, 200, 300, -50, 250, "v1.pdf")
    finanzas.guardar_recibo_sueldo(db_path, "2026-06", "sueldo", 110, 210, 320, -60, 260, "v2.pdf")
    recibos = finanzas.listar_recibos_sueldo(db_path, "2026-06")
    assert len(recibos) == 1
    assert recibos[0]["neto_total"] == 260
    assert recibos[0]["archivo_nombre"] == "v2.pdf"


def test_recibos_de_distinta_categoria_mismo_mes_no_se_pisan(db_path):
    finanzas.guardar_recibo_sueldo(db_path, "2026-06", "sueldo", 0, 0, 2000000, -300000, 1700000)
    finanzas.guardar_recibo_sueldo(db_path, "2026-06", "fondo", 0, 0, 1000000, -100000, 900000)
    ing = finanzas.get_ingresos(db_path, "2026-06")
    assert ing["sueldo"] == 1700000
    assert ing["fondo"] == 900000
    assert ing["total"] == 2600000
    assert len(finanzas.listar_recibos_sueldo(db_path, "2026-06")) == 2


def test_ingresos_no_afecta_otros_meses(db_path):
    finanzas.guardar_recibo_sueldo(db_path, "2026-07", "sueldo", 0, 0, 500000, 0, 500000)
    ing_agosto = finanzas.get_ingresos(db_path, "2026-08")
    assert ing_agosto["total"] == 0
