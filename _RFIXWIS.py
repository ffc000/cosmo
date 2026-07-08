"""
stock_depositos.py (_RFIXWIS.py) — CosmoTools
Procesa los TXT de stock y depósitos y genera el HTML del reporte.
Replica la lógica de armadorDatos.html / CargaAutomatica().

Fase 3 de profesionalización: este archivo antes tenía ~1020 líneas mezclando
carga de tablas de referencia, helpers de fecha, parseo de TXT, el cálculo
del semáforo y el armado del HTML/JS del reporte en un solo módulo. Se partió en:
  _rfixwis_datos.py     — tablas de referencia (Aduana/DIRA/feriados), cargadas 1 vez
  _rfixwis_fechas.py    — conversión de fechas y cálculo de días hábiles
  _rfixwis_parser.py    — parseo de los TXT de stock/depósitos
  _rfixwis_procesar.py  — cruce stock+depósitos, cálculo del semáforo
  _rfixwis_html.py      — armado del HTML/JS descargable
  _RFIXWIS.py (acá)     — re-exporta todo lo anterior

stock.py carga este archivo con importlib.util.spec_from_file_location (no
un import normal) y usa mod.procesar / mod.calcular_serie_grafico /
mod.es_no_habil / mod.generar_html — todos siguen disponibles acá igual que
antes del split, así que stock.py no necesita ningún cambio.
"""
from _rfixwis_datos import *  # noqa: F401,F403
from _rfixwis_fechas import *  # noqa: F401,F403
from _rfixwis_parser import *  # noqa: F401,F403
from _rfixwis_procesar import *  # noqa: F401,F403
from _rfixwis_html import *  # noqa: F401,F403
