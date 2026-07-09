"""
actas.py — Generación del Word de "Acta de Reunión", compartida entre VUA y
SENASA (antes cada blueprint tenía su propia implementación, con estilos
distintos aunque el contenido de fondo era el mismo: reunión con
participantes/temas/acuerdos).

No se unificó al 100% porque las secciones no son idénticas — VUA usa
Temas/Acuerdos/Próximos pasos (3), SENASA usa Temas/Conclusiones/
Compromisos/Próximos pasos (4). Eso queda como parámetro (`secciones`) en
vez de hardcodeado, así cada módulo sigue mandando sus propias secciones sin
forzar un esquema común que no existe.

Nota de diseño: el formato de tabla de participantes (con encabezado azul
oscuro) es el que ya tenía VUA — al unificar, SENASA pasa a usar el mismo
formato en vez de su lista con viñetas anterior. Es un cambio de estilo
visible en los próximos Acta de SENASA que se generen (no en las ya
guardadas), a propósito: el objetivo de unificar es quedarse con la mejor
versión de las dos, no con el promedio.

Sin dependencia de Flask — solo necesita python-docx.
"""
from docx import Document
from docx.shared import Pt, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement


def _set_cell_color(cell, hex_color):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tcPr.append(shd)


def generar_acta_word(titulo_doc, fecha, asunto, lugar, participantes, secciones,
                       roles_predefinidos=None):
    """
    titulo_doc: encabezado del documento, ej. "ACTA DE REUNIÓN" (VUA) o
        "ACTA DE REUNIÓN — SENASA / ARCA" (SENASA).
    fecha, asunto, lugar: strings simples.
    participantes: lista de dicts {"nombre":..., "cargo":..., "organismo":...}
        (cargo/organismo opcionales) o strings sueltos (solo nombre).
    secciones: lista de tuplas (nombre_seccion, lista_de_items). Una sección
        con items=[] se omite del documento (no aparece un título vacío).
    roles_predefinidos: dict opcional {nombre: cargo} para completar el cargo
        de un participante si no vino explícito en el dict.

    Devuelve un objeto Document (python-docx) ya armado — el caller decide
    dónde guardarlo (doc.save(ruta)).
    """
    roles_predefinidos = roles_predefinidos or {}
    doc = Document()
    for section in doc.sections:
        section.top_margin = Cm(2.5)
        section.bottom_margin = Cm(2.5)
        section.left_margin = Cm(3)
        section.right_margin = Cm(2.5)

    titulo = doc.add_paragraph()
    titulo.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = titulo.add_run(titulo_doc)
    run.bold = True
    run.font.size = Pt(16)
    run.font.color.rgb = RGBColor(0x24, 0x2D, 0x4F)
    doc.add_paragraph()

    for label, valor in [("Asunto:", asunto), ("Fecha:", fecha), ("Lugar:", lugar)]:
        p = doc.add_paragraph()
        r1 = p.add_run(f"{label} ")
        r1.bold = True
        r1.font.size = Pt(11)
        r2 = p.add_run(valor or "")
        r2.font.size = Pt(11)
    doc.add_paragraph()

    if participantes:
        doc.add_paragraph().add_run("Participantes").bold = True
        table = doc.add_table(rows=1, cols=3)
        table.style = "Table Grid"
        hdr = table.rows[0]
        for i, txt in enumerate(["Nombre", "Cargo", "Organismo"]):
            hdr.cells[i].text = txt
            hdr.cells[i].paragraphs[0].runs[0].bold = True
            hdr.cells[i].paragraphs[0].runs[0].font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
            _set_cell_color(hdr.cells[i], "242D4F")
        for p in participantes:
            if isinstance(p, dict):
                nombre = p.get("nombre", "")
                cargo = p.get("cargo") or roles_predefinidos.get(nombre, "")
                organismo = p.get("organismo", "")
            else:
                nombre = str(p)
                cargo = roles_predefinidos.get(nombre, "")
                organismo = ""
            row = table.add_row()
            row.cells[0].text = nombre
            row.cells[1].text = cargo
            row.cells[2].text = organismo

    for nombre_seccion, items in secciones:
        if not items:
            continue
        doc.add_paragraph()
        doc.add_paragraph().add_run(nombre_seccion).bold = True
        for item in items:
            p = doc.add_paragraph(style="List Bullet")
            p.add_run(item).font.size = Pt(11)

    return doc
