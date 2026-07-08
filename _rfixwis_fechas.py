"""
_rfixwis_fechas.py — Conversión de fechas YYMMDD y cálculo de días hábiles
(sábado/domingo/feriado) para el reporte de stock.
Extraído de _RFIXWIS.py (Fase 3 de profesionalización).
"""
from datetime import date, timedelta
from _rfixwis_datos import FERIADOS

def yymmdd_to_date(s: str) -> date:
    """'260618' → date(2026,6,18)"""
    s = str(s).strip()
    return date(2000 + int(s[0:2]), int(s[2:4]), int(s[4:6]))

def date_to_yymmdd(d: date) -> str:
    return d.strftime('%y%m%d')

def yymmdd_to_ddmmyyyy(s: str) -> str:
    if not s:
        return ''
    return f"{s[4:6]}/{s[2:4]}/20{s[0:2]}"

def calc_fecha(base_yymmdd: str, delta_days: int) -> str:
    d = yymmdd_to_date(base_yymmdd)
    return date_to_yymmdd(d + timedelta(days=delta_days))

def es_no_habil(yymmdd_int: int) -> str:
    """Devuelve tipo ('S','D','F','SF','DF') o '' si es hábil.
    Sábado/domingo se calculan del día de la semana; feriado sale de FERIADOS
    (cargado desde la tabla `feriados` en la BD)."""
    d = yymmdd_to_date(str(yymmdd_int))
    wd = d.weekday()  # 0=lunes ... 5=sábado, 6=domingo
    es_fer = yymmdd_int in FERIADOS
    if wd == 5:
        return 'SF' if es_fer else 'S'
    if wd == 6:
        return 'DF' if es_fer else 'D'
    return 'F' if es_fer else ''
