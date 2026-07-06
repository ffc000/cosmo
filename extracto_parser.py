"""
extracto_parser.py — Extrae movimientos de resúmenes de tarjeta de crédito en PDF.

Hay DOS motores de resumen distintos entre las 3 tarjetas de Fer:
  - "santander": Santander Río AMEX y Santander Río VISA. Mismo layout exacto.
  - "galicia":   Banco Galicia VISA. Layout tipo tabla, distinto del de Santander.

Cada parser devuelve una lista de movimientos:
  {
    "fecha": "2026-05-26",
    "descripcion": "PAYU-UBER 828987",
    "comprobante": "078076",
    "monto_ars": 23528.00,
    "monto_usd": 0.0,
    "cuota_actual": None | int,
    "cuota_total": None | int,
    "tipo": "consumo" | "pago" | "cargo",
  }

"tipo":
  - "consumo": gasto real (lo que el usuario quiere categorizar/presupuestar)
  - "pago":    pago del resumen anterior (no es gasto, reduce saldo)
  - "cargo":   intereses/IVA/percepciones que cobra el banco (es costo real,
               pero no es "gasto discrecional" — se muestra aparte)

No se confía ciegamente en el parser: validar siempre sum(consumos+cargos en ARS)
contra la línea "Total Consumos" / "TARJETA Total Consumos" del propio resumen
(ver `validar_total` más abajo) antes de guardar nada en la base.
"""

import re
from datetime import date

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviem": 11, "noviembre": 11, "diciem": 12, "diciembre": 12,
}

NUM_RE = r"-?[\d\.]+,\d{2}"


def _to_float(token: str) -> float:
    """'6574.417,16' -> 6574417.16 (los puntos son separador de miles, la coma es decimal)."""
    neg = token.strip().startswith("-")
    t = token.replace("-", "").replace(".", "").replace(",", ".")
    val = float(t)
    return -val if neg else val


# ════════════════════════════════════════════════════════════════════════════
# MOTOR SANTANDER RÍO (AMEX y VISA comparten el mismo layout)
# ════════════════════════════════════════════════════════════════════════════

# Línea con fecha completa: "26 Mayo 04 SU PAGO EN PESOS 192.910,00-"
# Línea de continuación (mismo mes/año): "06 855075 * PAYU-UBER 828987 4.890,00"
_SANT_FECHA_FULL = re.compile(r"^(\d{2})\s+([A-Za-zÁÉÍÓÚñÑ]+)\.?\s+(\d{1,2})\s+(.*)$")
_SANT_FECHA_CONT = re.compile(r"^(\d{1,2})\s+(.*)$")
_SANT_CUOTA = re.compile(r"C\.(\d{2})/(\d{2})")
_SANT_TARJETA_TOTAL = re.compile(r"^Tarjeta\s+\S+\s+Total\s+Consumos", re.IGNORECASE)
_SANT_SALDO_ANTERIOR = re.compile(r"^SALDO\s+ANTERIOR", re.IGNORECASE)
_SANT_SU_PAGO = re.compile(r"SU\s+PAGO\s+EN\s+(PESOS|USD)", re.IGNORECASE)
_SANT_CARGO_KEYS = ("INTERESES", "DB IVA", "DB.IVA", "IIBB PERCEP", "IVA RG", "DB.RG", "COMISION")
_SANT_STOP_FOOTER = ("SALDO ACTUAL", "PAGO MINIMO", "EL PRESENTE ES COPIA",
                      "TASA", "PLAN V", "EXPRESS PLAN", "CFTEA", "SUJETO AL LIMITE")


def parse_santander(paginas_texto: list[str], anio_resumen: int) -> list[dict]:
    """
    paginas_texto: lista de strings, una por página (page.extract_text() de pdfplumber).
    anio_resumen: año del resumen (ej. 2026) — el resumen no imprime el año en
    cada línea de transacción, solo "DD Mes" + el año del header (CIERRE ... 26).
    """
    movimientos = []
    mes_actual, anio_actual = None, anio_resumen
    en_tabla = False
    en_cargos = False
    finalizada = False  # True luego de cerrar la sección "Tarjeta NNNN Total Consumos" + footer

    for texto in paginas_texto:
        for linea in texto.split("\n"):
            linea = linea.strip()
            if not linea:
                continue

            if _SANT_SALDO_ANTERIOR.match(linea):
                finalizada = False  # nueva sección de tarjeta (resumen con varias tarjetas)
                en_tabla = True
                continue
            if linea.startswith("Fecha") and "Comprobante" in linea:
                if not finalizada:
                    en_tabla = True
                continue  # si ya finalizó, este header repetido (planes de cuotas) se ignora
            if not en_tabla:
                continue
            if any(linea.upper().startswith(k) for k in _SANT_STOP_FOOTER):
                en_tabla = False
                if en_cargos:
                    finalizada = True
                en_cargos = False
                continue
            if _SANT_TARJETA_TOTAL.match(linea):
                en_cargos = True  # lo que sigue (hasta el próximo stop) son cargos/impuestos
                continue
            if linea.startswith("___"):
                continue

            m_full = _SANT_FECHA_FULL.match(linea)
            if m_full and m_full.group(2).rstrip(".").lower() in MESES:
                anio_actual = 2000 + int(m_full.group(1))
                mes_actual = MESES[m_full.group(2).rstrip(".").lower()]
                dia = int(m_full.group(3))
                resto = m_full.group(4)
            else:
                m_cont = _SANT_FECHA_CONT.match(linea)
                if not m_cont or mes_actual is None:
                    continue
                dia = int(m_cont.group(1))
                resto = m_cont.group(2)

            fecha = date(anio_actual, mes_actual, dia).isoformat()

            es_pago = bool(_SANT_SU_PAGO.search(resto))
            es_cargo = en_cargos or any(k in resto.upper() for k in _SANT_CARGO_KEYS)
            # Nota de crédito/devolución: monto negativo que NO es "SU PAGO EN PESOS/USD".
            # Antes se clasificaba como "pago" (igual que un pago de resumen), lo que la
            # sacaba del total de gasto real. Ahora queda como "consumo" en negativo,
            # para que reste de la categoría correspondiente en vez de desaparecer.
            es_nota_credito = (not es_pago) and resto.rstrip().endswith("-")
            signo = -1.0 if (es_nota_credito and not es_cargo) else 1.0

            cuota_actual = cuota_total = None
            m_cuota = _SANT_CUOTA.search(resto)
            if m_cuota:
                cuota_actual, cuota_total = int(m_cuota.group(1)), int(m_cuota.group(2))

            montos = re.findall(NUM_RE, resto)
            if not montos:
                continue

            es_usd = "USD" in resto.upper() or "U$S" in resto.upper()
            if es_usd and len(montos) >= 2 and abs(_to_float(montos[-1])) == abs(_to_float(montos[-2])):
                monto_usd, monto_ars = signo * abs(_to_float(montos[-1])), 0.0
            elif es_usd and len(montos) == 1:
                monto_usd, monto_ars = signo * abs(_to_float(montos[0])), 0.0
            else:
                monto_ars, monto_usd = signo * abs(_to_float(montos[-1])), 0.0

            # Descripción: lo que queda antes del primer monto/cuota
            desc = resto
            desc = _SANT_CUOTA.sub("", desc)
            desc = re.sub(NUM_RE, "", desc)
            desc = re.sub(r"\bUSD\b|\bU\$S\b", "", desc, flags=re.IGNORECASE)
            comprobante = ""
            m_comp = re.match(r"^(\d{5,6})\s+([*K]?)\s*(.*)$", desc.strip())
            if m_comp:
                comprobante = m_comp.group(1)
                desc = m_comp.group(3)
            desc = desc.strip(" -*")

            movimientos.append({
                "fecha": fecha,
                "descripcion": desc or resto.strip(),
                "comprobante": comprobante,
                "monto_ars": round(monto_ars, 2),
                "monto_usd": round(monto_usd, 2),
                "cuota_actual": cuota_actual,
                "cuota_total": cuota_total,
                "tipo": "pago" if es_pago else ("cargo" if es_cargo else "consumo"),
            })

    return movimientos


# ════════════════════════════════════════════════════════════════════════════
# MOTOR GALICIA (VISA) — layout de tabla con fecha DD-MM-YY al inicio de línea
# ════════════════════════════════════════════════════════════════════════════

_GAL_LINEA = re.compile(r"^(\d{2})-(\d{2})-(\d{2})\s+(.*)$")
_GAL_CUOTA = re.compile(r"\b(\d{2})/(\d{2})\b")
_GAL_TARJETA_TOTAL = re.compile(r"^TARJETA\s+\S+\s+Total\s+Consumos", re.IGNORECASE)
_GAL_SALDO_ANTERIOR = re.compile(r"^SALDO\s+ANTERIOR", re.IGNORECASE)
_GAL_SU_PAGO = re.compile(r"SU\s+PAGO\s+EN\s+(PESOS|USD)", re.IGNORECASE)
_GAL_CARGO_KEYS = ("INTERESES", "DB IVA", "IIBB PERCEP", "IVA RG", "DB.RG", "COMISION")
_GAL_STOP_FOOTER = ("TOTAL A PAGAR", "PLAN V", "CFTEA", "CUOTAS A VENCER", "SUJETO AL LIMITE")


def parse_galicia(paginas_texto: list[str]) -> list[dict]:
    movimientos = []
    en_tabla = False
    en_cargos = False

    for texto in paginas_texto:
        for linea in texto.split("\n"):
            linea = linea.strip()
            if not linea:
                continue

            if linea.startswith("FECHA") and "REFERENCIA" in linea:
                en_tabla = True
                continue
            if not en_tabla:
                continue
            if _GAL_SALDO_ANTERIOR.match(linea):
                continue
            if any(linea.upper().startswith(k) for k in _GAL_STOP_FOOTER):
                en_tabla = False
                en_cargos = False
                continue
            if _GAL_TARJETA_TOTAL.match(linea):
                en_cargos = True
                continue

            m = _GAL_LINEA.match(linea)
            if not m:
                continue
            dd, mm, yy = m.group(1), m.group(2), m.group(3)
            fecha = date(2000 + int(yy), int(mm), int(dd)).isoformat()
            resto = m.group(4)

            montos = re.findall(NUM_RE, resto)
            es_pago = bool(_GAL_SU_PAGO.search(resto))
            # Nota de crédito/devolución: monto negativo que NO es "SU PAGO EN PESOS/USD".
            # Antes se clasificaba como "pago" (igual que un pago de resumen), lo que la
            # sacaba del total de gasto real. Ahora queda como "consumo" en negativo,
            # para que reste de la categoría correspondiente en vez de desaparecer.
            es_nota_credito = (not es_pago) and any(t.strip().startswith("-") for t in montos)
            es_cargo = en_cargos or any(k in resto.upper() for k in _GAL_CARGO_KEYS)
            signo = -1.0 if (es_nota_credito and not es_cargo) else 1.0

            cuota_actual = cuota_total = None
            m_cuota = _GAL_CUOTA.search(resto)
            if m_cuota:
                cuota_actual, cuota_total = int(m_cuota.group(1)), int(m_cuota.group(2))

            if not montos:
                continue

            es_usd = "USD" in resto.upper()
            if es_usd and len(montos) >= 2 and abs(_to_float(montos[-1])) == abs(_to_float(montos[-2])):
                monto_usd, monto_ars = signo * abs(_to_float(montos[-1])), 0.0
            elif es_usd and len(montos) == 1:
                monto_usd, monto_ars = signo * abs(_to_float(montos[0])), 0.0
            else:
                monto_ars, monto_usd = signo * abs(_to_float(montos[-1])), 0.0

            desc = resto
            if m_cuota:
                desc = desc.replace(m_cuota.group(0), "")
            desc = re.sub(NUM_RE, "", desc)
            desc = re.sub(r"\bUSD\b", "", desc, flags=re.IGNORECASE)
            m_comp = re.search(r"\b(\d{5,9})\b", desc)
            comprobante = m_comp.group(1) if m_comp else ""
            desc = re.sub(r"\b\d{5,9}\b", "", desc)  # comprobante suelto (ya capturado arriba)
            desc = re.sub(r"^[*K]\s*", "", desc.strip())
            desc = desc.strip(" -*")

            movimientos.append({
                "fecha": fecha,
                "descripcion": desc or resto.strip(),
                "comprobante": comprobante,
                "monto_ars": round(monto_ars, 2),
                "monto_usd": round(monto_usd, 2),
                "cuota_actual": cuota_actual,
                "cuota_total": cuota_total,
                "tipo": "pago" if es_pago else ("cargo" if es_cargo else "consumo"),
            })

    return movimientos


def validar_total(movimientos: list[dict], total_esperado_ars: float, total_esperado_usd: float = 0.0, tol=1.0):
    """Suma consumo+cargo (NO pago) y compara contra el total impreso en el resumen.
    Devuelve (ok: bool, calculado_ars, calculado_usd)."""
    ars = sum(m["monto_ars"] for m in movimientos if m["tipo"] in ("consumo", "cargo"))
    usd = sum(m["monto_usd"] for m in movimientos if m["tipo"] in ("consumo", "cargo"))
    ok = abs(ars - total_esperado_ars) <= tol and abs(usd - total_esperado_usd) <= tol
    return ok, round(ars, 2), round(usd, 2)


def extraer_total_declarado(paginas_texto: list[str], motor: str):
    """Busca la línea 'Tarjeta NNNN Total Consumos ...' (Santander) o
    'TARJETA NNNN Total Consumos ...' (Galicia) y devuelve (total_ars, total_usd)
    tal como los imprime el propio resumen — el número contra el que hay que
    validar la suma de movimientos tipo='consumo'. (None, None) si no se encontró."""
    total_re = _SANT_TARJETA_TOTAL if motor == "santander" else _GAL_TARJETA_TOTAL
    for texto in paginas_texto:
        for linea in texto.split("\n"):
            linea = linea.strip()
            if total_re.match(linea):
                montos = re.findall(NUM_RE, linea)
                if len(montos) >= 2:
                    return abs(_to_float(montos[-2])), abs(_to_float(montos[-1]))
                if len(montos) == 1:
                    return abs(_to_float(montos[0])), 0.0
    return None, None
