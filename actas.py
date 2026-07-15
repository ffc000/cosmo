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

Reusa header/pie de página y el estilo de tabla de generar_documento.py (Fase
8 de profesionalización -- unificar el diseño de los 3 generadores de Word
que había: SINTIA/consolidado, informe de Aduanas del país, y este). No lleva
índice (TOC): las actas son de 1-2 páginas, un índice ahí sería ruido, no
ayuda -- a diferencia de los informes largos donde sí aporta.

Sin dependencia de Flask -- generar_documento tampoco la tiene (solo
docx/openpyxl/matplotlib), así que sigue sin arrastrar Flask acá.
"""
from docx import Document
from docx.shared import Pt, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from generar_documento import agregar_tabla_word, _agregar_encabezado, _agregar_pie_pagina


def generar_acta_word(titulo_doc, fecha, asunto, lugar, participantes, secciones,
                       roles_predefinidos=None, nombre_archivo=""):
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
    nombre_archivo: opcional, para mostrar en el pie de página -- el caller
        es quien decide el nombre final del archivo (esta función no lo
        guarda), así que si no se pasa, el pie queda sin nombre de archivo.

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
    _agregar_encabezado(doc, "Dirección de Reingeniería de Procesos Aduaneros")
    _agregar_pie_pagina(doc, nombre_archivo)

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
        filas = []
        for p in participantes:
            if isinstance(p, dict):
                nombre = p.get("nombre", "")
                cargo = p.get("cargo") or roles_predefinidos.get(nombre, "")
                organismo = p.get("organismo", "")
            else:
                nombre = str(p)
                cargo = roles_predefinidos.get(nombre, "")
                organismo = ""
            filas.append([nombre, cargo, organismo])
        agregar_tabla_word(doc, ["Nombre", "Cargo", "Organismo"], filas, col_widths=[5, 5, 5.5])

    for nombre_seccion, items in secciones:
        if not items:
            continue
        doc.add_paragraph()
        doc.add_paragraph().add_run(nombre_seccion).bold = True
        for item in items:
            p = doc.add_paragraph(style="List Bullet")
            p.add_run(item).font.size = Pt(11)

    return doc
