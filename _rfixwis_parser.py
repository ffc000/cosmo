"""
_rfixwis_parser.py — Parseo de los TXT crudos de stock y depósitos (tabulados,
exportados de SIM/PAD) a listas de filas.
Extraído de _RFIXWIS.py (Fase 3 de profesionalización).
"""

def parsear_stock(contenido: str) -> list:
    """Devuelve lista de [codadu, codlot, fecha_stock_yymmdd, fecha_registro_yymmdd]"""
    rows = []
    for line in contenido.splitlines():
        line = line.strip()
        if not line:
            continue
        cols = [c.strip() for c in line.split('\t')]
        if len(cols) < 4 or cols[0] == 'Aduana':
            continue
        rows.append([cols[0], cols[1], cols[2], cols[3]])
    return rows

def parsear_depositos(contenido: str) -> list:
    """Devuelve lista de [codadu, codlot, razon_social, cuit, tipo, fecha_fin]"""
    rows = []
    for line in contenido.splitlines():
        line = line.strip()
        if not line:
            continue
        cols = [c.strip() for c in line.split('\t')]
        if len(cols) < 6 or cols[0] == 'Aduana de residencia':
            continue
        rows.append([cols[0], cols[1], cols[2], cols[3], cols[4], cols[5]])
    return rows
