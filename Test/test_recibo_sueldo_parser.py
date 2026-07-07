"""
Tests de recibo_sueldo_parser.py — usa el PDF real de ejemplo (fixtures/) para
que el test de humo cubra el parseo completo con pdfplumber, no solo texto
sintético; y agrega casos sintéticos puntuales para la clasificación
sueldo/fondo/otros.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import recibo_sueldo_parser as rsp  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "recibo_ejemplo.pdf")


def _paginas_pdf_real():
    import pdfplumber
    with pdfplumber.open(FIXTURE) as pdf:
        return [p.extract_text() or "" for p in pdf.pages]


def test_parse_recibo_real_categoria_sueldo():
    r = rsp.parse_recibo_sueldo(_paginas_pdf_real())
    assert r["categoria"] == "sueldo"
    assert r["mes"] == "2026-06"


def test_parse_recibo_real_totales_coinciden_con_el_pdf():
    r = rsp.parse_recibo_sueldo(_paginas_pdf_real())
    assert r["total_remuneraciones"] == 2679958.49
    assert r["total_descuentos"] == -381375.24
    # Neto impreso en el PDF ("NETO A COBRAR"): 2.298.583,25
    assert r["neto_total"] == 2298583.25


def test_parse_recibo_real_suma_serv_extraordinario():
    r = rsp.parse_recibo_sueldo(_paginas_pdf_real())
    # Suma manual de las 8 líneas "SERV. EXTRAORD..." del PDF de ejemplo
    esperado = 437633.6 + 120679 + 261544.8 + 72407.43 + 64297.63 + 10056.4 + 88072 + 24660
    assert r["serv_extraordinario"] == round(esperado, 2)
    assert r["otros_conceptos"] == round(r["total_remuneraciones"] - r["serv_extraordinario"], 2)


# ── Casos sintéticos de clasificación (sueldo/fondo/otros) ──────────────────

def _texto(lineas_detalle, total_rem, total_desc, periodo="JUNIO 2026"):
    cuerpo = "\n".join(lineas_detalle)
    return [f"{periodo}\n{cuerpo}\nTotal Remuneraciones {total_rem}\nTotal Descuentos {total_desc}"]


def test_categoria_fondo_si_tiene_115_001():
    paginas = _texto(
        ["115-001 CTA. DE JERARQUIZACION INC A. 1 $ 1850335.5",
         "116-001 CTA. DE JERARQUIZACION INC B. 1 $ 815890.19"],
        total_rem=2666225.69, total_desc=-100000,
    )
    r = rsp.parse_recibo_sueldo(paginas)
    assert r["categoria"] == "fondo"


def test_categoria_sueldo_si_tiene_3_001_pero_no_fondo():
    paginas = _texto(
        ["3-001 TITULO UNIVERS. 5AÑOS INC. A) 25 % 56954.5"],
        total_rem=56954.5, total_desc=-5000,
    )
    r = rsp.parse_recibo_sueldo(paginas)
    assert r["categoria"] == "sueldo"


def test_categoria_fondo_gana_si_tiene_ambos_grupos_de_codigos():
    """Un recibo no puede ser dos cosas a la vez — si por algún motivo
    aparecieran códigos de ambos grupos, gana fondo (regla que dio el usuario:
    fondo se decide primero)."""
    paginas = _texto(
        ["3-001 TITULO UNIVERS. 5AÑOS INC. A) 25 % 56954.5",
         "115-001 CTA. DE JERARQUIZACION INC A. 1 $ 1850335.5"],
        total_rem=1907290, total_desc=-100000,
    )
    r = rsp.parse_recibo_sueldo(paginas)
    assert r["categoria"] == "fondo"


def test_categoria_otros_si_no_tiene_ningun_codigo_clave():
    paginas = _texto(
        ["808-001 SIPES 1 $ 153055.04"],
        total_rem=153055.04, total_desc=0,
    )
    r = rsp.parse_recibo_sueldo(paginas)
    assert r["categoria"] == "otros"


def test_sin_total_remuneraciones_lanza_error():
    paginas = ["JUNIO 2026\n3-001 TITULO UNIVERS. 5AÑOS INC. A) 25 % 56954.5\nTotal Descuentos -5000"]
    try:
        rsp.parse_recibo_sueldo(paginas)
        assert False, "debería haber lanzado ValueError"
    except ValueError:
        pass


def test_serv_extraordinario_case_insensitive_y_multiples_lineas():
    paginas = _texto(
        ["3-001 TITULO UNIVERS. 5AÑOS INC. A) 25 % 56954.5",
         "1060-001 serv. extraord. susex 80 $ 04/26 100000",
         "1060-001 SERV. EXTRAORD. SUSEX 20 $ 05/26 50000"],
        total_rem=206954.5, total_desc=0,
    )
    r = rsp.parse_recibo_sueldo(paginas)
    assert r["serv_extraordinario"] == 150000
    assert r["otros_conceptos"] == round(206954.5 - 150000, 2)
