"""
_rfixwis_procesar.py — Lógica principal: cruza stock + depósitos, calcula el
semáforo (verde/azul/amarillo/rojo/negro) de cada depósito y arma la serie
para el gráfico de evolución.
Extraído de _RFIXWIS.py (Fase 3 de profesionalización).
"""
from _rfixwis_datos import ARR_BARRIDO, adu_vs_dira, nombre_adu_y_dira
from _rfixwis_fechas import calc_fecha, es_no_habil, yymmdd_to_ddmmyyyy
from _rfixwis_parser import parsear_stock, parsear_depositos

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
