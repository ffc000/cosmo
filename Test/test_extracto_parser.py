"""
Tests de extracto_parser.py: parseo Santander/Galicia, con foco especial en
la regresión ya corregida donde un monto negativo que NO era "SU PAGO" se
clasificaba como tipo="pago" en vez de tipo="consumo" (negativo).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import extracto_parser as ep  # noqa: E402


# ── Santander ────────────────────────────────────────────────────────────────

def test_santander_pago_de_resumen_se_clasifica_como_pago():
    texto = (
        "SALDO ANTERIOR 100.000,00\n"
        "26 Mayo 04 SU PAGO EN PESOS 192.910,00-\n"
    )
    movs = ep.parse_santander([texto], anio_resumen=2026)
    assert len(movs) == 1
    assert movs[0]["tipo"] == "pago"
    assert movs[0]["monto_ars"] == 192910.00  # el "pago" se guarda en positivo
    assert movs[0]["fecha"] == "2026-05-04"


def test_santander_nota_de_credito_no_es_pago_regresion():
    """Antes de la corrección, esta línea (monto negativo, no es 'SU PAGO')
    se clasificaba como tipo='pago' y desaparecía del gasto real. Ahora debe
    quedar como 'consumo' con el monto en negativo."""
    texto = (
        "SALDO ANTERIOR 100.000,00\n"
        "26 Mayo 04 855075 * PAYU-UBER 828987 4.890,00\n"
        "10 DEVOLUCION MERCADOLIBRE 1.500,00-\n"
    )
    movs = ep.parse_santander([texto], anio_resumen=2026)
    devolucion = next(m for m in movs if "DEVOLUCION" in m["descripcion"])
    assert devolucion["tipo"] == "consumo"
    assert devolucion["monto_ars"] == -1500.00


def test_santander_consumo_normal_positivo():
    texto = (
        "SALDO ANTERIOR 100.000,00\n"
        "26 Mayo 04 855075 * PAYU-UBER 828987 4.890,00\n"
    )
    movs = ep.parse_santander([texto], anio_resumen=2026)
    assert len(movs) == 1
    m = movs[0]
    assert m["tipo"] == "consumo"
    assert m["monto_ars"] == 4890.00
    assert m["comprobante"] == "855075"
    assert "PAYU-UBER" in m["descripcion"]


def test_santander_cuota_se_extrae():
    texto = (
        "SALDO ANTERIOR 100.000,00\n"
        "26 Mayo 04 123456 COMPRA EN CUOTAS C.03/12 5.000,00\n"
    )
    movs = ep.parse_santander([texto], anio_resumen=2026)
    assert movs[0]["cuota_actual"] == 3
    assert movs[0]["cuota_total"] == 12


def test_santander_cargo_por_intereses():
    texto = (
        "SALDO ANTERIOR 100.000,00\n"
        "Tarjeta 1234 Total Consumos 10.000,00\n"
        "26 Mayo 05 INTERESES POR FINANCIACION 350,00\n"
    )
    movs = ep.parse_santander([texto], anio_resumen=2026)
    cargo = next(m for m in movs if "INTERESES" in m["descripcion"])
    assert cargo["tipo"] == "cargo"


# ── Galicia ──────────────────────────────────────────────────────────────────

def test_galicia_pago_de_resumen_se_clasifica_como_pago():
    texto = (
        "FECHA REFERENCIA DESCRIPCION IMPORTE\n"
        "26-05-26 SU PAGO EN PESOS 50.000,00-\n"
    )
    movs = ep.parse_galicia([texto])
    assert movs[0]["tipo"] == "pago"
    assert movs[0]["monto_ars"] == 50000.00


def test_galicia_nota_de_credito_no_es_pago_regresion():
    texto = (
        "FECHA REFERENCIA DESCRIPCION IMPORTE\n"
        "28-05-26 DEVOLUCION TIENDA 078099 -1.200,00\n"
    )
    movs = ep.parse_galicia([texto])
    assert movs[0]["tipo"] == "consumo"
    assert movs[0]["monto_ars"] == -1200.00


def test_galicia_consumo_normal_y_comprobante():
    texto = (
        "FECHA REFERENCIA DESCRIPCION IMPORTE\n"
        "27-05-26 PAYU-UBER 078076 4.890,00\n"
    )
    movs = ep.parse_galicia([texto])
    m = movs[0]
    assert m["tipo"] == "consumo"
    assert m["monto_ars"] == 4890.00
    assert m["comprobante"] == "078076"
    assert m["fecha"] == "2026-05-27"


def test_galicia_fuera_de_la_tabla_se_ignora():
    """Sin el header 'FECHA ... REFERENCIA', ninguna línea debe parsearse."""
    texto = "27-05-26 PAYU-UBER 078076 4.890,00\n"
    movs = ep.parse_galicia([texto])
    assert movs == []


# ── validar_total ────────────────────────────────────────────────────────────

def test_validar_total_excluye_pagos():
    movimientos = [
        {"tipo": "pago", "monto_ars": 50000.0, "monto_usd": 0.0},
        {"tipo": "consumo", "monto_ars": 1000.0, "monto_usd": 0.0},
        {"tipo": "cargo", "monto_ars": 200.0, "monto_usd": 0.0},
    ]
    ok, calculado_ars, _ = ep.validar_total(movimientos, total_esperado_ars=1200.0)
    assert ok
    assert calculado_ars == 1200.0  # 50.000 de pago NO debe sumarse


def test_validar_total_detecta_diferencia():
    movimientos = [{"tipo": "consumo", "monto_ars": 1000.0, "monto_usd": 0.0}]
    ok, calculado_ars, _ = ep.validar_total(movimientos, total_esperado_ars=5000.0)
    assert not ok
    assert calculado_ars == 1000.0
