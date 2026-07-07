"""
recibo_sueldo_parser.py — Extrae los datos de un recibo de sueldo ARCA en PDF.

Formato de línea de detalle: CODIGO DESCRIPCION CANTIDAD UNIDAD [PERIODO] IMPORTE
  ej: "1060-001 SERV. EXTRAORD. SUSEX 80 $ 04/26 437633.6"
  ej: "3-001 TITULO UNIVERS. 5AÑOS INC. A) 25 % 56954.5"

A diferencia de los resúmenes de tarjeta (extracto_parser.py), acá los
importes usan punto decimal, sin separador de miles ("2679958.49").

Categorización del recibo completo (uno de los tres, nunca combinado):
  - "fondo":  si aparece el código 115-001 y/o 116-001
  - "sueldo": si no es fondo, pero aparece 3-001, 99-006 o 102-019
  - "otros":  si no tiene ninguno de esos códigos

En cualquier categoría, además se separa:
  - serv_extraordinario: suma de las líneas cuya descripción contiene
    "SERV. EXTRAORD" (case-insensitive)
  - otros_conceptos: Total Remuneraciones - serv_extraordinario
  - total_remuneraciones / total_descuentos: tal cual figuran impresos
  - neto_total: total_remuneraciones + total_descuentos (descuentos ya viene
    negativo en el PDF)
"""
import re

LINEA_RE = re.compile(
    r'^(\d+-\d+)\s+(.+?)\s+(-?[\d.]+)\s+(%|\$|H|A|D)\s+(?:(\d{2}/\d{2})\s+)?(-?[\d.]+)\s*$'
)
TOTAL_REM_RE = re.compile(r'^Total Remuneraciones\s+(-?[\d.]+)$', re.IGNORECASE)
TOTAL_DESC_RE = re.compile(r'^Total Descuentos\s+(-?[\d.]+)$', re.IGNORECASE)

CODIGOS_FONDO = {"115-001", "116-001"}
CODIGOS_SUELDO = {"3-001", "99-006", "102-019"}

MESES = {
    "ENERO": "01", "FEBRERO": "02", "MARZO": "03", "ABRIL": "04", "MAYO": "05", "JUNIO": "06",
    "JULIO": "07", "AGOSTO": "08", "SEPTIEMBRE": "09", "SETIEMBRE": "09", "OCTUBRE": "10",
    "NOVIEMBRE": "11", "DICIEMBRE": "12",
}
PERIODO_RE = re.compile(
    r'\b(' + "|".join(MESES.keys()) + r')\s+(\d{4})\b', re.IGNORECASE
)


def _to_float(token: str) -> float:
    return float(token)


def parse_recibo_sueldo(paginas_texto: list[str]) -> dict:
    """paginas_texto: lista de strings, una por página (page.extract_text() de pdfplumber).
    Devuelve un dict con mes, categoria, serv_extraordinario, otros_conceptos,
    total_remuneraciones, total_descuentos, neto_total. Lanza ValueError si no
    encuentra Total Remuneraciones / Total Descuentos (recibo con formato
    inesperado — mejor avisar que guardar datos a medias)."""
    texto_completo = "\n".join(paginas_texto)

    codigos_presentes = set()
    serv_extraordinario = 0.0
    total_remuneraciones = None
    total_descuentos = None

    for linea in texto_completo.split("\n"):
        linea = linea.strip()
        if not linea:
            continue

        m_rem = TOTAL_REM_RE.match(linea)
        if m_rem:
            total_remuneraciones = _to_float(m_rem.group(1))
            continue
        m_desc = TOTAL_DESC_RE.match(linea)
        if m_desc:
            total_descuentos = _to_float(m_desc.group(1))
            continue

        m = LINEA_RE.match(linea)
        if not m:
            continue
        codigo, descripcion, _cant, _unidad, _periodo, importe = m.groups()
        codigos_presentes.add(codigo)
        if "SERV. EXTRAORD" in descripcion.upper():
            serv_extraordinario += _to_float(importe)

    if total_remuneraciones is None or total_descuentos is None:
        raise ValueError(
            "No se encontraron las líneas 'Total Remuneraciones' / 'Total Descuentos' "
            "en el PDF — no parece un recibo de sueldo ARCA con el formato esperado."
        )

    if codigos_presentes & CODIGOS_FONDO:
        categoria = "fondo"
    elif codigos_presentes & CODIGOS_SUELDO:
        categoria = "sueldo"
    else:
        categoria = "otros"

    otros_conceptos = round(total_remuneraciones - serv_extraordinario, 2)
    neto_total = round(total_remuneraciones + total_descuentos, 2)

    m_periodo = PERIODO_RE.search(texto_completo)
    mes = None
    if m_periodo:
        nombre_mes, anio = m_periodo.group(1).upper(), m_periodo.group(2)
        mes = f"{anio}-{MESES[nombre_mes]}"

    return {
        "mes": mes,
        "categoria": categoria,
        "serv_extraordinario": round(serv_extraordinario, 2),
        "otros_conceptos": otros_conceptos,
        "total_remuneraciones": round(total_remuneraciones, 2),
        "total_descuentos": round(total_descuentos, 2),
        "neto_total": neto_total,
    }
