"""
stock_depositos.py — CosmoTools
Procesa los TXT de stock y depósitos y genera el HTML del reporte.
Replica la lógica de armadorDatos.html / CargaAutomatica().
"""

from datetime import date, timedelta
import io, os, zipfile

# ── Tablas estáticas (de arrDatos.js) ─────────────────────────────────────────

ARR_DRA = {
    '1': 'HIDROVIA',
    '2': 'NORESTE',
    '3': 'NOROESTE',
    '4': 'CENTRAL',
    '5': 'RIO COLORADO',
    '6': 'AUSTRAL',
    '7': 'CUYO',
    '8': 'No aplica',
}

# [cod, nombre, indice_dira]
ARR_ADU = [
    ['093','RAFAELA','1'],
    ['062','SANTA FE','1'],
    ['057','SAN LORENZO','1'],
    ['069','VILLA CONSTITUCION','1'],
    ['052','ROSARIO','1'],
    ['016','CONCORDIA','1'],
    ['041','PARANA','1'],
    ['020','DIAMANTE','1'],
    ['013','COLON','1'],
    ['015','CONCEPCION DEL URUGUAY','1'],
    ['026','GUALEGUAYCHU','1'],
    ['059','SAN NICOLAS','1'],
    ['060','SAN PEDRO','1'],
    ['094','VENADO TUERTO','1'],
    ['031','JUJUY','2'],
    ['034','LA QUIACA','2'],
    ['045','POCITOS','2'],
    ['053','SALTA','2'],
    ['066','TINOGASTA','2'],
    ['074','TUCUMAN','2'],
    ['076','ORAN','2'],
    ['010','BARRANQUERAS','3'],
    ['012','CLORINDA','3'],
    ['018','CORRIENTES','3'],
    ['024','FORMOSA','3'],
    ['025','GOYA','3'],
    ['029','IGUAZU','3'],
    ['042','PASO DE LOS LIBRES','3'],
    ['046','POSADAS','3'],
    ['054','SAN JAVIER','3'],
    ['082','BERNARDO DE IRIGOYEN','3'],
    ['084','SANTO TOME','3'],
    ['086','OBERA','3'],
    ['079','LA RIOJA','4'],
    ['088','GENERAL DEHEZA','4'],
    ['017','CORDOBA','4'],
    ['089','SANTIAGO DEL ESTERO','4'],
    ['090','GENERAL PICO','4'],
    ['008','CAMPANA','8'],
    ['073','EZEIZA','8'],
    ['033','LA PLATA','8'],
    ['001','BUENOS AIRES','8'],
    ['003','BAHIA BLANCA','5'],
    ['004','BARILOCHE','5'],
    ['037','MAR DEL PLATA','5'],
    ['040','NECOCHEA','5'],
    ['058','SAN MARTIN DE LOS ANDES','5'],
    ['075','NEUQUEN','5'],
    ['080','SAN ANTONIO OESTE','5'],
    ['085','VILLA REGINA','5'],
    ['014','COMODORO RIVADAVIA','6'],
    ['019','PUERTO DESEADO','6'],
    ['023','ESQUEL','6'],
    ['047','PUERTO MADRYN','6'],
    ['048','RIO GALLEGOS','6'],
    ['049','RIO GRANDE','6'],
    ['061','SANTA CRUZ','6'],
    ['067','USHUAIA','6'],
    ['087','CALETA OLIVIA','6'],
    ['055','SAN JUAN','7'],
    ['038','MENDOZA','7'],
    ['083','SAN LUIS','7'],
    ['078','SAN RAFAEL','7'],
]

DIAS_NH = {
    210801: "D",
    210807: "S",
    210808: "D",
    210814: "S",
    210815: "D",
    210816: "F",
    210821: "S",
    210822: "D",
    210828: "S",
    210829: "D",
    210904: "S",
    210905: "D",
    210911: "S",
    210912: "D",
    210918: "S",
    210919: "D",
    210925: "S",
    210926: "D",
    211002: "S",
    211003: "D",
    211008: "F",
    211009: "S",
    211010: "D",
    211011: "F",
    211016: "S",
    211017: "D",
    211023: "S",
    211024: "D",
    211030: "S",
    211031: "D",
    211106: "S",
    211107: "D",
    211113: "S",
    211114: "D",
    211120: "SF",
    211121: "D",
    211122: "F",
    211127: "S",
    211128: "D",
    211204: "S",
    211205: "D",
    211208: "F",
    211211: "S",
    211212: "D",
    211218: "S",
    211219: "D",
    211225: "SF",
    211226: "D",
    220101: "SF",
    220102: "D",
    220108: "S",
    220109: "D",
    220115: "S",
    220116: "D",
    220122: "S",
    220123: "D",
    220129: "S",
    220130: "D",
    220205: "S",
    220206: "D",
    220212: "S",
    220213: "D",
    220219: "S",
    220220: "D",
    220226: "S",
    220227: "D",
    220228: "F",
    220301: "F",
    220305: "S",
    220306: "D",
    220312: "S",
    220313: "D",
    220319: "S",
    220320: "D",
    220324: "F",
    220326: "S",
    220327: "D",
    220402: "SF",
    220403: "D",
    220409: "S",
    220410: "D",
    220414: "F",
    220415: "F",
    220416: "S",
    220417: "D",
    220423: "S",
    220424: "D",
    220430: "S",
    220501: "DF",
    220507: "S",
    220508: "D",
    220514: "S",
    220515: "D",
    220518: "F",
    220521: "S",
    220522: "D",
    220525: "F",
    220528: "S",
    220529: "D",
    220604: "S",
    220605: "D",
    220611: "S",
    220612: "D",
    220617: "F",
    220618: "S",
    220619: "D",
    220620: "F",
    220625: "S",
    220626: "D",
    220702: "S",
    220703: "D",
    220709: "S",
    220710: "D",
    220716: "S",
    220717: "D",
    220723: "S",
    220724: "D",
    220730: "S",
    220731: "D",
    220806: "S",
    220807: "D",
    220813: "S",
    220814: "D",
    220815: "F",
    220820: "S",
    220821: "D",
    220827: "S",
    220828: "D",
    220902: "F",
    220903: "S",
    220904: "D",
    220910: "S",
    220911: "D",
    220917: "S",
    220918: "D",
    220924: "S",
    220925: "D",
    221001: "S",
    221002: "D",
    221007: "F",
    221008: "S",
    221009: "D",
    221010: "F",
    221015: "S",
    221016: "D",
    221022: "S",
    221023: "D",
    221029: "S",
    221030: "D",
    221105: "S",
    221106: "D",
    221112: "S",
    221113: "D",
    221119: "S",
    221120: "D",
    221121: "F",
    221126: "S",
    221127: "D",
    221203: "S",
    221204: "D",
    221208: "F",
    221209: "F",
    221210: "S",
    221211: "D",
    221217: "S",
    221218: "D",
    221220: "F",
    221224: "S",
    221225: "D",
    221231: "S",
    230101: "D",
    230107: "S",
    230108: "D",
    230114: "S",
    230115: "D",
    230121: "S",
    230122: "D",
    230128: "S",
    230129: "D",
    230204: "S",
    230205: "D",
    230204: "S",
    230205: "D",
    230211: "S",
    230212: "D",
    230218: "S",
    230219: "D",
    230220: "F",
    230221: "F",
    230225: "S",
    230226: "D",
    230304: "S",
    230305: "D",
    230311: "S",
    230312: "D",
    230318: "S",
    230319: "D",
    230324: "F",
    230325: "S",
    230326: "D",
    230401: "S",
    230402: "D",
    230406: "F",
    230407: "F",
    230408: "S",
    230409: "D",
    230415: "S",
    230416: "D",
    230422: "S",
    230423: "D",
    230429: "S",
    230430: "D",
    230501: "F",
    230506: "S",
    230507: "D",
    230513: "S",
    230514: "D",
    230520: "S",
    230521: "D",
    230525: "F",
    230526: "F",
    230527: "S",
    230528: "D",
    230603: "S",
    230604: "D",
    230610: "S",
    230611: "D",
    230617: "S",
    230618: "D",
    230619: "F",
    230620: "F",
    230624: "S",
    230625: "D",
    230701: "S",
    230702: "D",
    230708: "S",
    230709: "D",
    230715: "S",
    230716: "D",
    230722: "S",
    230723: "D",
    230729: "S",
    230730: "D",
    230805: "S",
    230806: "D",
    230812: "S",
    230813: "D",
    230819: "S",
    230820: "D",
    230821: "F",
    230826: "S",
    230827: "D",
    230902: "S",
    230903: "D",
    230909: "S",
    230910: "D",
    230916: "S",
    230917: "D",
    230923: "S",
    230924: "D",
    230930: "S",
    231001: "D",
    231007: "S",
    231008: "D",
    231013: "F",
    231014: "S",
    231015: "D",
    231016: "F",
    231021: "S",
    231022: "D",
    231028: "S",
    231029: "D",
    231104: "S",
    231105: "D",
    231111: "S",
    231112: "D",
    231118: "S",
    231119: "D",
    231120: "F",
    231125: "S",
    231126: "D",
    231202: "S",
    231203: "D",
    231208: "F",
    231209: "S",
    231210: "D",
    231216: "S",
    231217: "D",
    231223: "S",
    231224: "D",
    231225: "F",
    231230: "S",
    231231: "D",
    240101: "F",
    240106: "S",
    240107: "D",
    240113: "S",
    240114: "D",
    240120: "S",
    240121: "D",
    240127: "S",
    240128: "D",
    240203: "S",
    240204: "D",
    240210: "S",
    240211: "D",
    240212: "F",
    240213: "F",
    240217: "S",
    240218: "D",
    240224: "S",
    240225: "D",
    240302: "S",
    240303: "D",
    240309: "S",
    240310: "D",
    240316: "S",
    240317: "D",
    240323: "S",
    240324: "D",
    240328: "F",
    240329: "F",
    240330: "S",
    240331: "D",
    240401: "F",
    240402: "F",
    240406: "S",
    240407: "D",
    240413: "S",
    240414: "D",
    240420: "S",
    240421: "D",
    240427: "S",
    240428: "D",
    240504: "S",
    240505: "D",
    240511: "S",
    240512: "D",
    240518: "S",
    240519: "D",
    240525: "S",
    240526: "D",
    240601: "S",
    240602: "D",
    240608: "S",
    240609: "D",
    240615: "S",
    240616: "D",
    240617: "F",
    240620: "F",
    240621: "F",
    240622: "S",
    240623: "D",
    240629: "S",
    240630: "D",
    240706: "S",
    240707: "D",
    240709: "F",
    240713: "S",
    240714: "D",
    240720: "S",
    240721: "D",
    240727: "S",
    240728: "D",
    240803: "S",
    240804: "D",
    240810: "S",
    240811: "D",
    240817: "F",
    240818: "D",
    240824: "S",
    240825: "D",
    240831: "S",
    240901: "D",
    240907: "S",
    240908: "D",
    240914: "S",
    240915: "D",
    240921: "S",
    240922: "D",
    240928: "S",
    240929: "D",
    241005: "S",
    241006: "D",
    241011: "F",
    241012: "S",
    241013: "D",
    241019: "S",
    241020: "D",
    241026: "S",
    241027: "D",
    241102: "S",
    241103: "D",
    241109: "S",
    241110: "D",
    241116: "S",
    241117: "D",
    241118: "F",
    241123: "S",
    241124: "D",
    241130: "S",
    241201: "D",
    241207: "S",
    241208: "D",
    241214: "S",
    241215: "D",
    241221: "S",
    241222: "D",
    241225: "F",
    241228: "S",
    241229: "D",
    250101: "F",
    250104: "S",
    250105: "D",
    250111: "S",
    250112: "D",
    250118: "S",
    250119: "D",
    250125: "S",
    250126: "D",
    250201: "S",
    250202: "D",
    250208: "S",
    250209: "D",
    250215: "S",
    250216: "D",
    250222: "S",
    250223: "D",
    250301: "S",
    250302: "D",
    250303: "F",
    250304: "F",
    250308: "S",
    250309: "D",
    250315: "S",
    250316: "D",
    250322: "S",
    250323: "D",
    250324: "F",
    250329: "S",
    250330: "D",
    250402: "F",
    250405: "S",
    250406: "D",
    250412: "S",
    250413: "D",
    250417: "F",
    250418: "F",
    250419: "S",
    250420: "D",
    250426: "S",
    250427: "D",
    250501: "F",
    250502: "F",
    250503: "S",
    250504: "D",
    250510: "S",
    250511: "D",
    250517: "S",
    250518: "D",
    250524: "S",
    250525: "D",
    250531: "S",
    250601: "D",
    250607: "S",
    250608: "D",
    250614: "S",
    250615: "D",
    250616: "F",
    250620: "F",
    250621: "S",
    250622: "D",
    250628: "S",
    250629: "D",
    250705: "S",
    250706: "D",
    250709: "F",
    250712: "S",
    250713: "D",
    250719: "S",
    250720: "D",
    250726: "S",
    250727: "D",
    250802: "S",
    250803: "D",
    250809: "S",
    250810: "D",
    250815: "F",
    250810: "D",
    250816: "S",
    250817: "D",
    250823: "S",
    250824: "D",
    250830: "S",
    250831: "D",
    250906: "S",
    250907: "D",
    250913: "S",
    250914: "D",
    250920: "S",
    250921: "D",
    250927: "S",
    250928: "D",
    251004: "S",
    251005: "D",
    251010: "F",
    251011: "S",
    251012: "D",
    251018: "S",
    251019: "D",
    251025: "S",
    251026: "D",
    251101: "S",
    251102: "D",
    251108: "S",
    251109: "D",
    251115: "S",
    251116: "D",
    251122: "S",
    251123: "D",
    251129: "S",
    251130: "D",
    251206: "S",
    251207: "D",
    251208: "L",
    251213: "S",
    251214: "D",
    251220: "S",
    251221: "D",
    251225: "J",
    251227: "S",
    251228: "D",
    260101: "J",
    260103: "S",
    260104: "D",
    260110: "S",
    260111: "D",
    260117: "S",
    260118: "D",
    260124: "S",
    260125: "D",
    260131: "S",
    260201: "D",
    260207: "S",
    260208: "D",
    260214: "S",
    260215: "D",
    260216: "L",
    260217: "M",
    260221: "S",
    260222: "D",
    260228: "S",
    260301: "D",
    260307: "S",
    260308: "D",
    260314: "S",
    260315: "D",
    260321: "S",
    260322: "D",
    260324: "F",
    260328: "S",
    260329: "D",
    260402: "F",
    260403: "F",
    260404: "S",
    260405: "D",
    260411: "S",
    260412: "D",
    260418: "S",
    260419: "D",
    260425: "S",
    260426: "D",
    260501: "F",
    260502: "S",
    260503: "D",
    260509: "S",
    260510: "D",
    260516: "S",
    260517: "D",
    260523: "S",
    260524: "D",
    260525: "F",
    260530: "S",
    260531: "D",
    260606: "S",
    260607: "D",
    260613: "S",
    260614: "D",
    260615: "F",
    260620: "F",
    260621: "D",
    260627: "S",
    260628: "D",
    260704: "S",
    260705: "D",
    260709: "F",
    260711: "S",
    260712: "D",
    260718: "S",
    260719: "D",
    260725: "S",
    260726: "D",
    260801: "S",
    260802: "D",
    260808: "S",
    260809: "D",
    260815: "S",
    260816: "D",
    260817: "F",
    260822: "S",
    260823: "D",
    260829: "S",
    260830: "D",
    260905: "S",
    260906: "D",
    260912: "S",
    260913: "D",
    260919: "S",
    260920: "D",
    260926: "S",
    260927: "D",
    261003: "S",
    261004: "D",
    261010: "S",
    261011: "D",
    261012: "F",
    261017: "S",
    261018: "D",
    261024: "S",
    261025: "D",
    261031: "S",
    261101: "D",
    261107: "S",
    261108: "D",
    261114: "S",
    261115: "D",
    261123: "F",
    261121: "S",
    261122: "D",
    261128: "S",
    261129: "D",
    261205: "S",
    261206: "D",
    261208: "F",
    261212: "S",
    261213: "D",
    261219: "S",
    261220: "D",
    261225: "F",
    261226: "S",
    261227: "D",
}

ARR_BARRIDO = {'0011100Q','0011104E','00111028','0011109B','0011205D'}

# ── Helpers de fecha ──────────────────────────────────────────────────────────

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
    """Devuelve tipo ('S','D','F','SF','DF',...) o '' si es hábil."""
    return DIAS_NH.get(yymmdd_int, '')

# ── Parseo de archivos TXT ────────────────────────────────────────────────────

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

# ── Tablas de referencia ──────────────────────────────────────────────────────

def _adu_index():
    return {a[0]: a for a in ARR_ADU}

ADU_IDX = _adu_index()

def nombre_adu_y_dira(cod3: str):
    a = ADU_IDX.get(cod3)
    if not a:
        return ('N/E', 'N/E')
    return (a[1], ARR_DRA.get(a[2], 'N/E'))

def adu_vs_dira(cod3: str, dira_idx: str) -> bool:
    if dira_idx == '0':
        return True
    a = ADU_IDX.get(cod3)
    return bool(a and a[2] == dira_idx)

# ── Procesamiento principal ───────────────────────────────────────────────────

def procesar(stock_txt: str, depositos_txt: str, fecha_max_yymmdd: str,
             dias_tolerancia: int = 0) -> list:
    """
    Replica CargaAutomatica() → AgruparPorVariosCampos().
    Devuelve lista de registros con todos los campos del semáforo.

    Cada registro:
      [0]  codadu
      [1]  codlot
      [2]  cantidad (transmisiones en el período)
      [3]  max_fecha_registro  YYMMDD o ''
      [4]  max_fecha_stock     YYMMDD o ''
      [5]  semaforo  VERDE|AZUL|AMARILLO|ROJO|NEGRO
      [6]  razon_social
      [7]  cuit
      [8]  tipo
      [9]  nombre_aduana
      [10] nombre_dira
      [11] comentario
      [12] cumple_max_fregistro (bool)
      [13] cumple_15dias (bool)
      [14] cumple_max_fstock (bool)
      [15] indice
      [16] ultimos_15_dias  [0|1]*15
    """
    arr_stock = parsear_stock(stock_txt)
    arr_depo  = parsear_depositos(depositos_txt)

    # Índice rápido de depósitos
    depo_idx = {}
    for d in arr_depo:
        depo_idx[(d[0], d[1])] = d  # (codadu, codlot) → fila

    fecha = fecha_max_yymmdd  # YYMMDD str

    # Calcular las 15 fechas hacia atrás (día a día)
    arr15 = []
    f = fecha
    for _ in range(14):
        f = calc_fecha(f, -1)
        arr15.insert(0, f)
    arr15.append(fecha)  # arr15[14] = fecha_max

    # fecha más antigua del período de 15 días
    fecha_menos_15 = arr15[0]

    # ── Agrupación por (codadu, codlot) ──
    interno: dict = {}   # (codadu,codlot) → registro
    u15_dias: dict = {}       # (codadu,codlot) → [0]*15  (por fecha registro)
    u15_stock_dias: dict = {}  # (codadu,codlot) → [0]*15  (por fecha stock)

    for row in arr_stock:
        codadu, codlot, fstock, fregistro = row
        if fregistro > fecha:
            continue  # posterior a la fecha máxima, ignorar
        if not adu_vs_dira(codadu, '0'):  # siempre todas (cuando no hay filtro)
            continue

        key = (codadu, codlot)
        if key not in interno:
            dep = depo_idx.get(key, [codadu, codlot, 'N/E', 'N/E', 'N/E', ''])
            nau, ndira = nombre_adu_y_dira(codadu)
            interno[key] = [
                codadu, codlot,
                1,          # [2] cantidad
                fregistro,  # [3] max fecha registro
                fstock,     # [4] max fecha stock
                '',         # [5] semáforo
                dep[2].replace('  ', ' '),  # [6] razón social
                dep[3],     # [7] cuit
                dep[4],     # [8] tipo
                nau,        # [9] nombre aduana
                ndira,      # [10] nombre dira
                '',         # [11] comentario
                False,      # [12]
                False,      # [13]
                False,      # [14]
                len(interno),  # [15] índice
                [0]*15,     # [16] últimos 15 días
            ]
            u15_dias[key]       = [0] * 15
            u15_stock_dias[key] = [0] * 15
        else:
            interno[key][2] += 1
            if fregistro > interno[key][3]:
                interno[key][3] = fregistro
            if fstock > interno[key][4]:
                interno[key][4] = fstock

    # Marcar presencia en cada uno de los últimos 15 días (por fecha de registro)
    for row in arr_stock:
        codadu, codlot, fstock, fregistro = row
        key = (codadu, codlot)
        if key in u15_dias:
            if fregistro >= fecha_menos_15:
                for i, fd in enumerate(arr15):
                    if fregistro == fd:
                        u15_dias[key][i] = 1
            # Marcar por fecha de stock (puede ser anterior a fecha_menos_15)
            if fstock >= fecha_menos_15:
                for i, fd in enumerate(arr15):
                    if fstock == fd:
                        u15_stock_dias[key][i] = 1

    # Copiar u15_dias y u15_stock al registro
    for key, rec in interno.items():
        rec[16] = u15_dias.get(key, [0]*15)

    # ── Calcular semáforo ──
    fecha_menos_uno    = calc_fecha(fecha, -1)
    fecha_menos_dos    = calc_fecha(fecha, -2)
    fecha_menos_quince = calc_fecha(fecha, -15)

    def cumple_todos_los_dias(u15):
        """Devuelve (cumple:bool, color:str, msg:str)"""
        faltas_habiles = 0
        hay_inh = False
        for i, val in enumerate(u15):
            if val == 0:
                tipo = es_no_habil(int(arr15[i]))
                if tipo:
                    hay_inh = True
                else:
                    faltas_habiles += 1
        if faltas_habiles > dias_tolerancia:
            return (False, '', '')
        if hay_inh:
            return (True, 'AZUL', 'Cumple. Excepto Sáb. Dom. y Feriados')
        return (True, 'VERDE', 'Cumplimiento total')

    for rec in interno.values():
        fregistro = rec[3]
        fstock    = rec[4]
        u15       = rec[16]

        rec[12] = fregistro >= fecha_menos_uno
        cum      = cumple_todos_los_dias(u15)
        rec[13]  = cum[0]
        rec[14]  = fstock >= fecha_menos_dos

        if fregistro >= fecha_menos_uno:
            if rec[13]:
                if rec[14]:
                    rec[5]  = cum[1]   # VERDE o AZUL
                    rec[11] = cum[2]
                else:
                    rec[5]  = 'AMARILLO'
                    rec[11] = f'Última fecha de stock informada: {yymmdd_to_ddmmyyyy(fstock)}'
            else:
                rec[5]  = 'AMARILLO'
                rec[11] = 'Intermitencias en la transmisión'
        elif fregistro >= fecha_menos_quince:
            rec[5]  = 'AMARILLO'
            rec[11] = f'Última fecha de transmisión: {yymmdd_to_ddmmyyyy(fregistro)}'
        else:
            rec[5]  = 'ROJO'
            rec[11] = 'Sin transmisión en, al menos, los últimos 15 días'

    # ── Agregar NEGROS (en maestro pero nunca transmitieron) ──
    fecha_8dig = '20' + fecha   # YYYYMMDD para comparar con col[5] del maestro (YYYYMMDD)
    for dep in arr_depo:
        codadu, codlot = dep[0], dep[1]
        key = (codadu, codlot)
        fin_vigencia = dep[5]  # YYYYMMDD
        # Solo incluir si fin_vigencia año <= 30 (es decir 20xx con xx<=30 → vigentes próximamente)
        try:
            anio_fin = int(fin_vigencia[:2]) if len(fin_vigencia) == 8 else int('20' + fin_vigencia[:2])
        except:
            continue
        if anio_fin > 30:
            continue
        if key not in interno:
            nau, ndira = nombre_adu_y_dira(codadu)
            idx = len(interno)
            rec = [
                codadu, codlot,
                0, '', '',
                'NEGRO',
                dep[2].replace('  ', ' '),
                dep[3], dep[4],
                nau, ndira,
                'Nunca transmitió',
                False, False, False,
                idx,
                [0]*15,
            ]
            interno[key] = rec

    # ── Plan Barrido ──
    for key, rec in interno.items():
        tag = rec[0] + rec[1]
        if tag in ARR_BARRIDO:
            rec[11] += ' — <strong>PLAN BARRIDO</strong>'

    # Formatear fechas a DD/MM/YYYY para display
    result = list(interno.values())
    for rec in result:
        rec[3] = yymmdd_to_ddmmyyyy(rec[3])
        rec[4] = yymmdd_to_ddmmyyyy(rec[4])

    # Copiar u15_stock_dias como índice 17
    for key, rec in interno.items():
        rec.append(u15_stock_dias.get(key, [0]*15))

    # Filtrar registros sin CUIT (no están en el maestro de depósitos) — replica JS: filter r[7] !== 'N/E'
    result = [r for r in result if r[7] != 'N/E']

    # Reindexar
    for i, rec in enumerate(result):
        rec[15] = i

    return result

# ── Datos para gráficos (serie de 15 puntos) ─────────────────────────────────

def calcular_serie_grafico(stock_txt: str, depositos_txt: str,
                           fecha_max: str, dias_tolerancia: int = 0) -> list:
    """
    Replica localData1(): 15 cortes (un corte por día hacia atrás).
    Devuelve lista de [periodo_ddmmyyyy, azules, negros, rojos, amarillos, verdes].
    """
    arr_stock = parsear_stock(stock_txt)
    arr_depo  = parsear_depositos(depositos_txt)

    serie = []
    f = fecha_max
    fechas_corte = []
    for _ in range(14):
        f = calc_fecha(f, -1)
        fechas_corte.insert(0, f)
    fechas_corte.append(fecha_max)

    for fc in fechas_corte:
        # llamada rápida solo para contar semáforos
        # reusar parsear ya hecho
        from io import StringIO
        st = '\n'.join(['\t'.join(r) for r in [['Aduana','Lugar Operativo','Fecha del Stock','Fecha de Registro']] +
                        [[r[0],r[1],r[2],r[3]] for r in arr_stock]])
        dep_txt = '\n'.join(['\t'.join(r) for r in
                             [['Aduana de residencia','Codigo del lugar Operativo','Descripcion','CUIT de la Terminal/Empresa Concesionaria','Tipo de lugar ','Fecha fin vigencia']] +
                             arr_depo])
        recs = procesar(st, dep_txt, fc, dias_tolerancia)
        conteo = {'AZUL':0,'NEGRO':0,'ROJO':0,'AMARILLO':0,'VERDE':0}
        for r in recs:
            s = r[5]
            if s in conteo:
                conteo[s] += 1
        serie.append([
            yymmdd_to_ddmmyyyy(fc),
            conteo['AZUL'], conteo['NEGRO'],
            conteo['ROJO'], conteo['AMARILLO'], conteo['VERDE'],
        ])
    return serie

# ── Generación del HTML descargable ──────────────────────────────────────────

COLOR_MAP = {
    'VERDE':    '#00bf00',
    'AZUL':     '#297ccf',
    'AMARILLO': '#e6b800',
    'ROJO':     '#bf0000',
    'NEGRO':    '#222222',
}
TEXT_MAP = {
    'VERDE':    '#ffffff',
    'AZUL':     '#ffffff',
    'AMARILLO': '#000000',
    'ROJO':     '#ffffff',
    'NEGRO':    '#ffffff',
}

def _dot(semaforo: str, idx: int) -> str:
    """Círculo de color clicable que abre el modal de detalle."""
    bg = COLOR_MAP.get(semaforo, '#888')
    return (f'<span class="dot" data-idx="{idx}" '
            f'style="display:inline-block;width:18px;height:18px;border-radius:50%;'
            f'background:{bg};cursor:pointer;"></span>')






def generar_reporte(stock_txt: str, depositos_txt: str,
                    fecha_max_yymmdd: str, dias_tolerancia: int = 0) -> bytes:
    """Punto de entrada principal. Devuelve el HTML como bytes."""
    registros = procesar(stock_txt, depositos_txt, fecha_max_yymmdd, dias_tolerancia)
    serie     = calcular_serie_grafico(stock_txt, depositos_txt, fecha_max_yymmdd, dias_tolerancia)
    html = generar_html(registros, fecha_max_yymmdd, serie)
    return html.encode('utf-8')

def generar_html(registros: list, fecha_max: str, serie: list = None) -> str:
    """Genera el HTML completo del reporte con exportación Excel/PDF y modal de evolución histórica."""

    fecha_display = yymmdd_to_ddmmyyyy(fecha_max)

    # Calcular las 15 fechas para los encabezados del modal
    arr15 = []
    f = fecha_max
    for _ in range(14):
        f = calc_fecha(f, -1)
        arr15.insert(0, f)
    arr15.append(fecha_max)
    fechas_display = [yymmdd_to_ddmmyyyy(x) for x in arr15]

    DIAS_ES = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']
    def dia_semana(yymmdd):
        return DIAS_ES[yymmdd_to_date(yymmdd).weekday()]

    # Conteo por semáforo
    conteo = {'VERDE': 0, 'AZUL': 0, 'AMARILLO': 0, 'ROJO': 0, 'NEGRO': 0}
    for r in registros:
        s = r[5]
        if s in conteo:
            conteo[s] += 1
    total = sum(conteo.values())

    def pct(v):
        return f"{v/total*100:.1f}%" if total else "0%"

    # Serializar datos para JS
    import json as _json
    datos_js = []
    for r in registros:
        datos_js.append({
            'adu':    r[0],
            'lot':    r[1],
            'razon':  r[6],
            'cuit':   r[7],
            'tipo':   r[8],
            'ndu':    r[9],
            'dira':   r[10],
            'sem':    r[5],
            'freg':   r[3],
            'fstock': r[4],
            'coment': r[11],
            'u15':    r[16],
            'u15s':   r[17] if len(r) > 17 else r[16],
        })

    datos_js_str  = _json.dumps(datos_js, ensure_ascii=False)
    fechas_js_str = _json.dumps(fechas_display, ensure_ascii=False)
    dias_js_str   = _json.dumps([dia_semana(x) for x in arr15], ensure_ascii=False)
    serie_js_str  = _json.dumps(serie or [], ensure_ascii=False)
    color_js      = _json.dumps(COLOR_MAP)

    # Construir filas HTML
    filas_html = []
    for idx, r in enumerate(registros):
        sem   = r[5]
        u15   = r[16]
        barras = ''.join(
            f'<span style="display:inline-block;width:10px;height:14px;'
            f'background:{"#00bf00" if v else "#ddd"};margin:0 1px;border-radius:2px;"></span>'
            for v in u15
        )
        filas_html.append(
            f'<tr data-sem="{sem}" data-idx="{idx}" data-adu="{r[0]}" data-lot="{r[1]}">'
            f'<td>{r[0]}</td>'
            f'<td>{r[1]}</td>'
            f'<td title="{r[6]}">{r[6]}</td>'
            f'<td>{r[7]}</td>'
            f'<td>{r[9]}</td>'
            f'<td style="text-align:center">{_dot(sem, idx)}</td>'
            f'<td>{r[3]}</td>'
            f'<td>{r[4]}</td>'
            f'<td style="white-space:nowrap">{barras}</td>'
            f'<td style="font-size:.75rem;color:#555" title="{r[11]}">{r[11]}</td>'
            f'</tr>'
        )

    filas_str = '\n'.join(filas_html)

    resumen_items = ''.join(
        f'<div class="resumen-chip" onclick="filtrarEstado(\'{s}\')" title="Filtrar: {s}">'
        f'<span class="resumen-num" style="color:{COLOR_MAP[s]}">{conteo[s]}</span>'
        f'<span class="resumen-label-chip">{s.capitalize()}</span>'
        f'<span class="resumen-pct">({pct(conteo[s])})</span></div>'
        for s in ['VERDE', 'AZUL', 'AMARILLO', 'ROJO', 'NEGRO']
    )

    # Fecha ISO para el endpoint de evolución (YYYY-MM-DD)
    fecha_iso = '20' + fecha_max[:2] + '-' + fecha_max[2:4] + '-' + fecha_max[4:6]

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reporte Stock Depósitos — {fecha_display}</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Segoe UI',Arial,sans-serif;background:#f4f6f9;color:#222;font-size:.85rem}}
.header{{background:#1E2A3B;color:#fff;padding:1rem 1.5rem;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:.75rem}}
.header-left h1{{font-size:1.1rem;font-weight:600}}
.header-left p{{font-size:.75rem;color:#94a3b8;margin-top:.25rem}}
.header-actions{{display:flex;gap:.5rem;flex-wrap:wrap}}
.btn-hdr{{display:inline-flex;align-items:center;gap:.35rem;border:1px solid rgba(255,255,255,.25);border-radius:5px;padding:.4rem .85rem;font-size:.75rem;font-weight:600;cursor:pointer;background:rgba(255,255,255,.08);color:#fff;transition:background .15s}}
.btn-hdr:hover{{background:rgba(255,255,255,.18)}}
.btn-hdr.green{{border-color:rgba(0,191,0,.4);color:#6ee86e}}
.btn-hdr.green:hover{{background:rgba(0,191,0,.12)}}
.resumen{{background:#fff;border-bottom:1px solid #e5e7eb;padding:.65rem 1.5rem;display:flex;align-items:center;gap:.25rem;flex-wrap:wrap}}
.resumen-sep{{font-size:.7rem;color:#cbd5e1;margin:0 .35rem}}
.resumen-chip{{display:inline-flex;align-items:center;gap:.4rem;padding:.35rem .7rem;border-radius:20px;cursor:pointer;border:1px solid #e5e7eb;transition:all .15s;margin:.15rem .1rem}}
.resumen-chip:hover{{background:#f0f4ff;border-color:#a5b4fc}}
.resumen-num{{font-size:1.15rem;font-weight:700;line-height:1}}
.resumen-label-chip{{font-size:.7rem;color:#555;text-transform:uppercase;letter-spacing:.06em}}
.resumen-pct{{font-size:.68rem;color:#9ca3af}}
.toolbar{{padding:.65rem 1.5rem;background:#fff;border-bottom:1px solid #e5e7eb;display:flex;gap:.5rem;align-items:center;flex-wrap:wrap}}
.toolbar input{{border:1px solid #d1d5db;border-radius:4px;padding:.38rem .7rem;font-size:.82rem;width:260px;outline:none}}
.toolbar input:focus{{border-color:#1A56DB;box-shadow:0 0 0 2px rgba(26,86,219,.1)}}
.toolbar select{{border:1px solid #d1d5db;border-radius:4px;padding:.38rem .6rem;font-size:.82rem;outline:none;background:#fff}}
.toolbar select:focus{{border-color:#1A56DB}}
.toolbar-btn{{display:inline-flex;align-items:center;gap:.3rem;border:1px solid #d1d5db;border-radius:4px;padding:.38rem .75rem;font-size:.75rem;font-weight:600;cursor:pointer;background:#fff;color:#374151;transition:all .15s}}
.toolbar-btn:hover{{background:#f3f4f6;border-color:#9ca3af}}
.toolbar-btn.primary{{background:#EBF5FF;border-color:#93c5fd;color:#1A56DB}}
.toolbar-btn.primary:hover{{background:#DBEAFE}}
.counter{{font-size:.75rem;color:#6b7280;margin-left:.25rem}}
.container{{padding:1rem 1.5rem}}
table{{width:100%;border-collapse:collapse;background:#fff;box-shadow:0 1px 4px rgba(0,0,0,.07);border-radius:6px;overflow:hidden;table-layout:fixed}}
thead th{{background:#1E2A3B;color:#fff;padding:.6rem .75rem;text-align:left;font-size:.72rem;white-space:nowrap;cursor:pointer;user-select:none;overflow:hidden;text-overflow:ellipsis}}
thead th:nth-child(1){{width:4%}}
thead th:nth-child(2){{width:6%}}
thead th:nth-child(3){{width:22%}}
thead th:nth-child(4){{width:11%}}
thead th:nth-child(5){{width:9%}}
thead th:nth-child(6){{width:5%}}
thead th:nth-child(7){{width:8%}}
thead th:nth-child(8){{width:8%}}
thead th:nth-child(9){{width:13%}}
thead th:nth-child(10){{width:14%}}
thead th:hover{{background:#2d3f57}}
tbody tr:hover{{background:#f0f4ff}}
tbody td{{padding:.5rem .75rem;border-bottom:1px solid #f0f0f0;vertical-align:middle;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.dot{{transition:transform .15s;cursor:pointer}}
.dot:hover{{transform:scale(1.35)}}
/* Modals */
.modal-overlay{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:200;align-items:center;justify-content:center}}
.modal-overlay.open{{display:flex}}
.modal-box{{background:#fff;border-radius:10px;padding:1.5rem;max-width:900px;width:92vw;max-height:90vh;overflow-y:auto;box-shadow:0 12px 40px rgba(0,0,0,.22);position:relative}}
.modal-box.wide{{max-width:1100px}}
.modal-close{{position:absolute;top:.75rem;right:.75rem;background:#f3f4f6;border:none;border-radius:50%;width:30px;height:30px;font-size:1.1rem;cursor:pointer;line-height:30px;text-align:center;transition:background .15s}}
.modal-close:hover{{background:#e5e7eb}}
.modal-title{{font-size:.95rem;font-weight:700;margin-bottom:.3rem;padding-right:2.5rem;color:#1E2A3B}}
.modal-sub{{font-size:.78rem;color:#6b7280;margin-bottom:1rem}}
.modal-section{{margin-top:1rem}}
.modal-section-title{{font-size:.72rem;font-weight:700;color:#374151;text-transform:uppercase;letter-spacing:.08em;margin-bottom:.5rem}}
.modal-15{{overflow-x:auto}}
.modal-15 table{{font-size:.72rem;border-collapse:collapse;width:max-content;min-width:100%}}
.modal-15 th{{background:#1E2A3B;color:#fff;padding:.4rem .4rem;text-align:center;font-size:.65rem;white-space:nowrap}}
.modal-15 td{{padding:.38rem .4rem;text-align:center;border-bottom:1px solid #f0f0f0}}
.modal-obs{{font-size:.78rem;color:#374151;margin-top:.75rem;padding:.65rem .85rem;background:#f0f7ff;border-radius:6px;border-left:3px solid #1A56DB}}
.legend-row{{display:flex;gap:1rem;flex-wrap:wrap;margin-top:.65rem;font-size:.7rem;color:#555}}
.legend-dot{{display:inline-block;width:12px;height:12px;border-radius:50%;vertical-align:middle;margin-right:.3rem}}
.evol-btn{{margin-top:.85rem;display:inline-flex;align-items:center;gap:.35rem;background:#1E2A3B;color:#fff;border:none;border-radius:5px;padding:.45rem .9rem;font-size:.78rem;font-weight:600;cursor:pointer;transition:background .15s}}
.evol-btn:hover{{background:#2d3f57}}
.evol-loading{{font-size:.78rem;color:#6b7280;margin-top:.75rem;display:none}}
.footer{{text-align:center;font-size:.7rem;color:#9ca3af;padding:1.5rem}}
@media print{{
  .toolbar,.header-actions,.evol-btn{{display:none!important}}
  .modal-overlay{{display:none!important}}
  body{{background:#fff}}
  .container{{padding:0}}
  table{{box-shadow:none}}
}}
</style>
</head>
<body>

<div class="header">
  <div class="header-left">
    <h1>Reporte de transmisión de stock — Depósitos Fiscales</h1>
    <p>Datos al {fecha_display} &nbsp;·&nbsp; {total} depósitos</p>
  </div>
  <div class="header-actions">
    <button class="btn-hdr green" onclick="exportarExcel()">⬇ Excel</button>
    <button class="btn-hdr" onclick="exportarPDF()">🖨 PDF</button>
    <button class="btn-hdr" onclick="abrirGrafico()">📈 Evolución 15d</button>
  </div>
</div>

<div class="resumen">
  {resumen_items}
</div>

<div class="toolbar">
  <input type="text" id="busq" placeholder="Buscar LOT, Razón Social, CUIT…" oninput="filtrar()">
  <select id="filtro-estado" onchange="filtrar()">
    <option value="">Todos los estados</option>
    <option value="VERDE">Verde</option>
    <option value="AZUL">Azul</option>
    <option value="AMARILLO">Amarillo</option>
    <option value="ROJO">Rojo</option>
    <option value="NEGRO">Negro</option>
  </select>
  <span class="counter" id="contador">{total} registros</span>
  <button class="toolbar-btn" onclick="limpiarFiltros()">✕ Limpiar</button>
</div>

<div class="container">
  <table id="tabla">
    <thead>
      <tr>
        <th onclick="sortTable(0)">ADU ↕</th>
        <th onclick="sortTable(1)">LOT ↕</th>
        <th onclick="sortTable(2)">Razón Social ↕</th>
        <th>CUIT</th>
        <th onclick="sortTable(4)">Aduana ↕</th>
        <th>Estado</th>
        <th onclick="sortTable(6)">Últ. F.Reg ↕</th>
        <th onclick="sortTable(7)">Últ. F.Stock ↕</th>
        <th>Últimos 15 días</th>
        <th>Observación</th>
      </tr>
    </thead>
    <tbody id="tbody">
{filas_str}
    </tbody>
  </table>
</div>

<div class="footer">Generado por CosmoTools — DI REPA / ARCA &nbsp;·&nbsp; {fecha_display}</div>

<!-- ── Modal evolución 15d (gráfico de barras apiladas) ── -->
<div class="modal-overlay" id="modal-grafico-overlay" onclick="if(event.target===this)cerrarGrafico()">
  <div class="modal-box wide" style="padding:1.5rem">
    <button class="modal-close" onclick="cerrarGrafico()">×</button>
    <div class="modal-title">Evolución de cumplimiento — últimos 15 días</div>
    <div class="modal-sub">Distribución diaria de estados de transmisión al {fecha_display}</div>
    <canvas id="grafico-barras" height="90"></canvas>
  </div>
</div>

<!-- ── Modal detalle depósito ── -->
<div class="modal-overlay" id="modal-overlay" onclick="if(event.target===this)cerrarModalBtn()">
  <div class="modal-box wide">
    <button class="modal-close" onclick="cerrarModalBtn()">×</button>
    <div class="modal-title" id="modal-title"></div>
    <div class="modal-sub" id="modal-sub"></div>

    <div class="modal-section">
      <div class="modal-section-title">Detalle últimos 15 días</div>
      <div class="modal-15" id="modal-15"></div>
      <div class="legend-row">
        <span><span class="legend-dot" style="background:#00bf00"></span>Transmitió</span>
        <span><span class="legend-dot" style="background:#cc0000"></span>No transmitió (hábil)</span>
        <span><span class="legend-dot" style="background:#1a56db"></span>No hábil (Sáb/Dom/Feriado)</span>
      </div>
    </div>

    <div class="modal-obs" id="modal-obs"></div>

    <button class="evol-btn" id="btn-evolucion" onclick="cargarEvolucion()">
      📊 Ver evolución histórica de este depósito
    </button>
    <div class="evol-loading" id="evol-loading">Cargando historial…</div>

    <div class="modal-section" id="evol-section" style="display:none">
      <div class="modal-section-title">Historial de estados (todos los cortes registrados)</div>
      <canvas id="grafico-evolucion" height="100"></canvas>
    </div>
  </div>
</div>

<script>
// ── Datos embebidos ──────────────────────────────────────────────────────────
var DATOS  = {datos_js_str};
var FECHAS = {fechas_js_str};
var DIAS   = {dias_js_str};
var SERIE  = {serie_js_str};
var COLORS = {color_js};
var FECHA_CORTE_ISO = "{fecha_iso}";

// Índice activo para el modal
var _idxActivo = null;
var _graficoEvol = null;
var _grafico15d  = null;

// ── Utilidades ───────────────────────────────────────────────────────────────
function esFeriadoOFinDeSemana(fechaStr) {{
  var p = fechaStr.split('/');
  var d = new Date(parseInt(p[2]), parseInt(p[1])-1, parseInt(p[0]));
  return d.getDay() === 0 || d.getDay() === 6;
}}

// ── Modal detalle ────────────────────────────────────────────────────────────
function abrirModal(idx) {{
  _idxActivo = idx;
  var d = DATOS[idx];

  document.getElementById('modal-title').textContent = d.adu + '-' + d.lot + ' — ' + d.razon;
  document.getElementById('modal-sub').innerHTML =
    '<strong>CUIT:</strong> ' + d.cuit +
    ' &nbsp;·&nbsp; <strong>Aduana:</strong> ' + d.ndu +
    ' &nbsp;·&nbsp; <strong>Tipo:</strong> ' + (d.tipo||'—') +
    ' &nbsp;·&nbsp; <strong>Última F.Reg:</strong> ' + (d.freg||'—') +
    ' &nbsp;·&nbsp; <strong>Última F.Stock:</strong> ' + (d.fstock||'—');

  // Tabla 15 días
  var thead = '<tr><th style="min-width:90px;text-align:left;padding:.4rem .6rem">Indicador</th>';
  for(var i=0;i<15;i++) {{
    var esFer = esFeriadoOFinDeSemana(FECHAS[i]);
    thead += '<th style="min-width:60px;' + (esFer?'background:#1a56db;':'') + '">' +
             DIAS[i] + '<br><span style="font-weight:400">' + FECHAS[i] + '</span></th>';
  }}
  thead += '</tr>';

  function buildRow(label, arr) {{
    var row = '<tr><td style="font-weight:600;white-space:nowrap;padding:.4rem .6rem">' + label + '</td>';
    for(var i=0;i<15;i++) {{
      var val = arr[i];
      var esFer = esFeriadoOFinDeSemana(FECHAS[i]);
      var color = val ? '#00bf00' : (esFer ? '#1a56db' : '#cc0000');
      row += '<td><span style="display:inline-block;width:16px;height:16px;border-radius:50%;background:' + color + '"></span></td>';
    }}
    return row + '</tr>';
  }}

  var u15s = d.u15s || d.u15;
  document.getElementById('modal-15').innerHTML =
    '<table><thead>' + thead + '</thead><tbody>' +
    buildRow('F. Registro', d.u15) +
    buildRow('F. Stock',    u15s) +
    '</tbody></table>';

  document.getElementById('modal-obs').innerHTML = d.coment || '';

  // Resetear sección evolución
  document.getElementById('evol-section').style.display = 'none';
  document.getElementById('evol-loading').style.display = 'none';
  document.getElementById('btn-evolucion').style.display = 'inline-flex';
  if(_graficoEvol) {{ _graficoEvol.destroy(); _graficoEvol = null; }}

  document.getElementById('modal-overlay').classList.add('open');
}}

function cerrarModalBtn() {{
  document.getElementById('modal-overlay').classList.remove('open');
  _idxActivo = null;
}}

// ── Evolución histórica (consulta BD via endpoint) ───────────────────────────
function cargarEvolucion() {{
  if(_idxActivo === null) return;
  var d = DATOS[_idxActivo];
  document.getElementById('evol-loading').style.display = 'block';
  document.getElementById('btn-evolucion').style.display = 'none';

  fetch('/api/stock/evolucion/' + d.adu + '/' + d.lot)
    .then(function(r){{ return r.json(); }})
    .then(function(data) {{
      document.getElementById('evol-loading').style.display = 'none';
      if(!data.ok) {{
        document.getElementById('evol-loading').textContent = data.error || 'Sin historial disponible.';
        document.getElementById('evol-loading').style.display = 'block';
        return;
      }}
      renderizarEvolucion(data.serie);
    }})
    .catch(function(e) {{
      document.getElementById('evol-loading').textContent = 'Error al cargar: ' + e.message;
      document.getElementById('evol-loading').style.display = 'block';
    }});
}}

var SEM_ORDER = ['VERDE','AZUL','AMARILLO','ROJO','NEGRO'];
var SEM_COLORS = {{'VERDE':'#00bf00','AZUL':'#297ccf','AMARILLO':'#e6b800','ROJO':'#bf0000','NEGRO':'#333333'}};

function renderizarEvolucion(serie) {{
  var seccion = document.getElementById('evol-section');
  seccion.style.display = 'block';

  var labels = serie.map(function(r){{ return r.fecha_corte; }});

  // Agrupar por semáforo: para cada corte, el estado es 1 en ese color, 0 en los demás
  var datasets = SEM_ORDER.map(function(sem) {{
    return {{
      label: sem.charAt(0) + sem.slice(1).toLowerCase(),
      data: serie.map(function(r){{ return r.semaforo === sem ? 1 : 0; }}),
      backgroundColor: SEM_COLORS[sem],
      borderWidth: 0,
    }};
  }});

  var ctx = document.getElementById('grafico-evolucion').getContext('2d');
  if(_graficoEvol) _graficoEvol.destroy();
  _graficoEvol = new Chart(ctx, {{
    type: 'bar',
    data: {{ labels: labels, datasets: datasets }},
    options: {{
      responsive: true,
      plugins: {{
        legend: {{ position: 'bottom', labels: {{ font: {{ size: 11 }} }} }},
        title: {{ display: false }},
        tooltip: {{
          callbacks: {{
            label: function(ctx) {{
              if(ctx.raw === 0) return null;
              var r = serie[ctx.dataIndex];
              return ctx.dataset.label + ' — ' + (r.freg||'—') + ' | Stock: ' + (r.fstock||'—');
            }}
          }}
        }}
      }},
      scales: {{
        x: {{ stacked: true, ticks: {{ font: {{ size: 10 }}, maxRotation: 45 }} }},
        y: {{ stacked: true, display: false, max: 1 }}
      }}
    }}
  }});
}}

// ── Modal gráfico 15d ────────────────────────────────────────────────────────
function abrirGrafico() {{
  document.getElementById('modal-grafico-overlay').classList.add('open');
  if(!_grafico15d) {{
    var ctx = document.getElementById('grafico-barras').getContext('2d');
    var labels    = SERIE.map(function(r){{return r[0];}});
    var datasets  = [
      {{label:'Verde',    data:SERIE.map(function(r){{return r[5]}}), backgroundColor:'#00bf00'}},
      {{label:'Azul',     data:SERIE.map(function(r){{return r[1]}}), backgroundColor:'#297ccf'}},
      {{label:'Amarillo', data:SERIE.map(function(r){{return r[4]}}), backgroundColor:'#e6b800'}},
      {{label:'Rojo',     data:SERIE.map(function(r){{return r[3]}}), backgroundColor:'#bf0000'}},
      {{label:'Negro',    data:SERIE.map(function(r){{return r[2]}}), backgroundColor:'#333333'}},
    ];
    _grafico15d = new Chart(ctx, {{
      type: 'bar',
      data: {{ labels: labels, datasets: datasets }},
      options: {{
        responsive: true,
        plugins: {{
          legend: {{ position:'bottom', labels:{{ font:{{ size:11 }} }} }},
          title: {{ display:true, text:'Evolución de cumplimiento — últimos 15 días', font:{{ size:13 }} }}
        }},
        scales: {{
          x: {{ stacked:true, ticks:{{ font:{{ size:10 }} }} }},
          y: {{ stacked:true, ticks:{{ font:{{ size:10 }} }} }}
        }}
      }}
    }});
  }}
}}

function cerrarGrafico() {{
  document.getElementById('modal-grafico-overlay').classList.remove('open');
}}

// ── Filtrado y ordenamiento ───────────────────────────────────────────────────
document.addEventListener('keydown', function(e) {{
  if(e.key==='Escape') {{
    cerrarModalBtn();
    cerrarGrafico();
  }}
}});

document.getElementById('tbody').addEventListener('click', function(e) {{
  var dot = e.target.closest('.dot');
  if(dot) abrirModal(parseInt(dot.dataset.idx));
}});

function filtrar() {{
  var q   = document.getElementById('busq').value.toLowerCase().trim();
  var est = document.getElementById('filtro-estado').value;
  var rows = document.getElementById('tbody').querySelectorAll('tr');
  var vis = 0;
  rows.forEach(function(r) {{
    // Buscar en LOT(1), Razón Social(2), CUIT(3), Aduana(4)
    var txt = [1,2,3,4].map(function(i){{ return r.cells[i]?r.cells[i].textContent.toLowerCase():''; }}).join(' ');
    var show = (!q || txt.includes(q)) && (!est || r.dataset.sem === est);
    r.style.display = show ? '' : 'none';
    if(show) vis++;
  }});
  document.getElementById('contador').textContent = vis + ' registros';
}}

function filtrarEstado(estado) {{
  document.getElementById('filtro-estado').value = estado;
  filtrar();
  document.getElementById('tabla').scrollIntoView({{behavior:'smooth'}});
}}

function limpiarFiltros() {{
  document.getElementById('busq').value = '';
  document.getElementById('filtro-estado').value = '';
  filtrar();
}}

var sortDir = {{}};
function sortTable(col) {{
  var tbody = document.getElementById('tbody');
  var rows  = Array.from(tbody.querySelectorAll('tr:not([style*="display: none"])'));
  var all   = Array.from(tbody.querySelectorAll('tr'));
  var asc   = !sortDir[col]; sortDir[col] = asc;
  all.sort(function(a,b) {{
    var ta = a.cells[col]?a.cells[col].textContent.trim():'';
    var tb = b.cells[col]?b.cells[col].textContent.trim():'';
    return asc ? ta.localeCompare(tb,'es') : tb.localeCompare(ta,'es');
  }});
  all.forEach(function(r){{ tbody.appendChild(r); }});
}}

// ── Exportación Excel (SheetJS) ──────────────────────────────────────────────
function exportarExcel() {{
  var filas = Array.from(document.getElementById('tbody').querySelectorAll('tr'))
    .filter(function(r){{ return r.style.display !== 'none'; }});

  var data = [['ADU','LOT','Razón Social','CUIT','Aduana','Estado','Últ. F.Registro','Últ. F.Stock','Observación']];
  filas.forEach(function(r) {{
    var idx = parseInt(r.dataset.idx);
    var d = DATOS[idx];
    data.push([d.adu, d.lot, d.razon, d.cuit, d.ndu, d.sem, d.freg||'', d.fstock||'', d.coment||'']);
  }});

  var ws = XLSX.utils.aoa_to_sheet(data);

  // Ancho de columnas
  ws['!cols'] = [4,8,30,14,18,10,14,14,40].map(function(w){{return {{wch:w}}}});

  // Estilo encabezado (solo SheetJS Pro; en la versión free se aplica color manualmente post-open)
  var SEM_XLSX = {{'VERDE':'FF00BF00','AZUL':'FF297CCF','AMARILLO':'FFE6B800','ROJO':'FFBF0000','NEGRO':'FF333333'}};
  for(var i=1;i<data.length;i++) {{
    var cell = ws[XLSX.utils.encode_cell({{r:i,c:5}})];
    if(cell) {{
      cell.s = {{
        fill:{{fgColor:{{rgb:SEM_XLSX[data[i][5]]||'FFCCCCCC'}}}},
        font:{{color:{{rgb:'FFFFFFFF'}},bold:true}},
        alignment:{{horizontal:'center'}}
      }};
    }}
  }}

  var wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, 'Stock Depósitos');
  var filtro = document.getElementById('filtro-estado').value || 'todos';
  var fecha = FECHA_CORTE_ISO.replace(/-/g,'');
  XLSX.writeFile(wb, 'ReporteStock_' + fecha + '_' + filtro + '.xlsx');
}}

// ── Exportación PDF (print CSS) ──────────────────────────────────────────────
function exportarPDF() {{
  // Ocultar filas no visibles antes de imprimir y restaurar después
  var ocultas = [];
  document.querySelectorAll('#tbody tr').forEach(function(r) {{
    if(r.style.display === 'none') ocultas.push(r);
  }});
  ocultas.forEach(function(r){{ r.setAttribute('data-hidden','1'); r.style.display='none'; }});

  // Inyectar título de filtro para impresión
  var info = document.createElement('div');
  info.id = '_print_info';
  info.style.cssText = 'padding:.5rem 1.5rem;font-size:.75rem;color:#555;background:#fff;border-bottom:1px solid #e5e7eb';
  var est = document.getElementById('filtro-estado').value || 'Todos';
  var busq = document.getElementById('busq').value || '';
  info.textContent = 'Filtros aplicados — Estado: ' + est + (busq?' | Búsqueda: "'+busq+'"':'') +
    ' — ' + document.getElementById('contador').textContent;
  document.querySelector('.container').insertAdjacentElement('beforebegin', info);

  window.print();

  // Restaurar
  document.getElementById('_print_info').remove();
  ocultas.forEach(function(r){{ r.removeAttribute('data-hidden'); r.style.display=''; }});
}}
</script>
</body>
</html>"""
