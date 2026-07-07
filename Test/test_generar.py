"""
Tests de generar.py: helpers de formato (pct/fmt), la categorización SQL de
rechazos (_sql_case_categorias — la misma que ahora comparten rechazos_cat y
rechazos_ult_cat, antes duplicada e inconsistente), y las funciones de
verificación numérica que corrigen al texto generado por IA.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import generar  # noqa: E402


# ── Helpers de formato ───────────────────────────────────────────────────────

def test_fmt_separador_de_miles():
    assert generar.fmt(1234567) == "1.234.567"


def test_pct_con_total_cero_no_rompe():
    assert generar.pct(5, 0) == "0,0%"


def test_pct_formato_coma_decimal():
    assert generar.pct(1, 3) == "33,3%"


def test_calcular_totales_separa_cargado_y_lastre():
    totales = [{
        "CARGADO_TRANS": 10, "CARGADO_NO_TRANS": 5, "CARGADO_TARDIO": 2,
        "LASTRE_TRANS": 20, "LASTRE_NO_TRANS": 8, "LASTRE_TARDIO": 1,
    }]
    (cT, cN, cTd, cTot), (lT, lN, lTd, lTot), (gT, gN, gTd, gTot) = generar.calcular_totales(totales)
    assert (cT, cN, cTd, cTot) == (10, 5, 2, 17)
    assert (lT, lN, lTd, lTot) == (20, 8, 1, 29)
    assert (gT, gN, gTd, gTot) == (30, 13, 3, 46)


# ── _sql_case_categorias ─────────────────────────────────────────────────────
# Antes rechazos_ult_cat (informe del "último mes") usaba un CASE hardcodeado
# distinto y más corto que rechazos_cat (período completo) — con categorías
# faltantes y hasta el orden de evaluación invertido. Ahora ambos usan esta
# misma función, así que un mensaje se clasifica igual en las dos tablas del
# mismo informe.

def _clasificar(mensaje):
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE t (mensaje TEXT)")
    con.execute("INSERT INTO t VALUES (?)", (mensaje,))
    row = con.execute(f"SELECT {generar._sql_case_categorias()} AS cat FROM t").fetchone()
    con.close()
    return row[0]


def test_categoria_error_atributo():
    assert _clasificar("ERROR ATRIBUTO/PARAMETRO INVALIDO en el campo X") == "ERROR ATRIBUTO/PARAMETRO INVALIDO"


def test_categoria_nro_mic_inexistente_antes_que_existente():
    """Regresión puntual: la versión vieja (hardcodeada en rechazos_ult_cat)
    evaluaba 'Nro de Mic existente' ANTES que 'NRO DE MIC INEXISTENTE', orden
    invertido respecto a esta función (la única fuente de verdad ahora)."""
    assert _clasificar("NRO DE MIC INEXISTENTE para este envío") == "NRO DE MIC INEXISTENTE"
    assert _clasificar("Nro de Mic existente ya usado") == "NRO DE MIC EXISTENTE"


def test_categoria_contenedor_vacio_dos_variantes():
    assert _clasificar("Error en CONTENEDORESVACIOS") == "CONTENEDOR_VACIO"
    assert _clasificar("Error en CONTENEDORVACIO") == "CONTENEDOR_VACIO"


def test_categoria_carta_porte_duplicado_distinto_de_carta_porte():
    assert _clasificar("CARTA DE PORTE DUPLICADO detectado") == "CARTA_PORTE_DUPLICADO"
    assert _clasificar("Falta CARTA DE PORTE") == "CARTA PORTE"


def test_categoria_otros_si_no_matchea_nada():
    assert _clasificar("un mensaje que no matchea ninguna categoria conocida") == "OTROS"


# ── verificar_narrativa_denominadores ───────────────────────────────────────

def test_verificar_narrativa_corrige_denominador_lastre_mal_etiquetado():
    texto = (
        "En el segmento de lastre, sobre 15.155 operaciones, 4.738 fueron "
        "transmitidas correctamente (31,3% de los cargados), 8.008 no fueron "
        "transmitidas (52,8% de los cargados) y 2.409 registraron transmisión "
        "tardía (15,9% de los cargados)."
    )
    logs = []
    corregido = generar.verificar_narrativa_denominadores(
        texto,
        pcts_cargados=["29,6%", "60,9%", "9,5%"],
        pcts_lastre=["31,3%", "52,8%", "15,9%"],
        log_fn=logs.append,
    )
    assert "31,3% de lastre" in corregido
    assert "52,8% de lastre" in corregido
    assert "15,9% de lastre" in corregido
    assert "de los cargados" not in corregido
    assert logs, "debería haber logueado que corrigió algo"


def test_verificar_narrativa_no_toca_texto_ya_correcto():
    texto = "Cargados: 29,6% de los cargados. Lastre: 31,3% de lastre."
    logs = []
    corregido = generar.verificar_narrativa_denominadores(
        texto, pcts_cargados=["29,6%"], pcts_lastre=["31,3%"], log_fn=logs.append,
    )
    assert corregido == texto
    assert logs == []


def test_verificar_narrativa_con_texto_vacio_no_rompe():
    assert generar.verificar_narrativa_denominadores("", ["1%"], ["2%"], log_fn=lambda m: None) == ""
    assert generar.verificar_narrativa_denominadores(None, ["1%"], ["2%"], log_fn=lambda m: None) is None
