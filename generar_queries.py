"""
generar_queries.py — Extracción de datos SQL para el informe SINTIA.
Extraído de generar.py (Fase 3 de profesionalización).
"""
import re
from datetime import datetime
from db_utils import get_db, dat_actual
from generar_utils import CAT_LIKE, PAISES, PAISES_CONSOLIDADO, MESES, n, fmt, mes_label_largo, formatear_demora

def _sql_case_categorias(campo="mensaje"):
    """CASE WHEN reutilizable para categorización de rechazos."""
    return f"""CASE
        WHEN {campo} LIKE '%ERROR ATRIBUTO/PARAMETRO INVALIDO%' THEN 'ERROR ATRIBUTO/PARAMETRO INVALIDO'
        WHEN {campo} LIKE '%EVENTO DISTINTO DE  PATAI O OFTAI%' THEN 'EVENTO DISTINTO DE PATAI/OFTAI'
        WHEN {campo} LIKE '%EVENTO YA RECIBIDO%' THEN 'EVENTO YA RECIBIDO'
        WHEN {campo} LIKE '%NRO DE MIC INEXISTENTE%' THEN 'NRO DE MIC INEXISTENTE'
        WHEN {campo} LIKE '%Nro de Mic existente%' THEN 'NRO DE MIC EXISTENTE'
        WHEN {campo} LIKE '%CONTENEDORESVACIOS%' OR {campo} LIKE '%CONTENEDORVACIO%' THEN 'CONTENEDOR_VACIO'
        WHEN {campo} LIKE '%PAIS DE PASO DUPLICADO%' THEN 'PAIS_DE_PASO_DUPLICADO'
        WHEN {campo} LIKE '%FECHA DEL EVENTO %' THEN 'FECHA_DEL_EVENTO'
        WHEN {campo} LIKE '%CODCIUPART%' THEN 'CODCIUPART'
        WHEN {campo} LIKE '%CODCIUENT%' THEN 'CODCIUENT'
        WHEN {campo} LIKE '%PAISESDEPASO%CODCIUSAL%' THEN 'PAISESDEPASO.CODCIUSAL'
        WHEN {campo} LIKE '%CODCIUSAL%' THEN 'CODCIUSAL'
        WHEN {campo} LIKE '%VEHICULO%' THEN 'VEHICULO'
        WHEN {campo} LIKE '%CONTENEDOR%' THEN 'CONTENEDOR'
        WHEN {campo} LIKE '%CONSIGNATARIO%' THEN 'CONSIGNATARIO'
        WHEN {campo} LIKE '%CODDIVISASEG%' THEN 'CODDIVISASEG'
        WHEN {campo} LIKE '%PORTE%DUPLICADO%' THEN 'CARTA_PORTE_DUPLICADO'
        WHEN {campo} LIKE '%PORTE%' AND {campo} NOT LIKE '%DUPLICADO%' THEN 'CARTA PORTE'
        WHEN {campo} LIKE '%PAISESDEPASO.CODADUENT%' THEN 'PAISESDEPASO.CODADUENT'
        WHEN {campo} LIKE '%PAISESDEPASO%' THEN 'PAISESDEPASO'
        WHEN {campo} LIKE '%CODADUEMI%' THEN 'CODADUEMI'
        WHEN {campo} LIKE '%CODADUSAL%' THEN 'CODADUSAL'
        WHEN {campo} LIKE '%CODADUPART%' THEN 'CODADUPART'
        WHEN {campo} LIKE '%FECHLLEGPREV%' THEN 'FECHLLEGPREV'
        WHEN {campo} LIKE '%PESOBRUTOTOTAL%' THEN 'PESOBRUTOTOTAL'
        WHEN {campo} LIKE '%DESCRUTITINERARIOS%' THEN 'DESCRUTITINERARIOS'
        WHEN {campo} LIKE '%TIPDOCIDENT%' THEN 'TIPDOCIDENT'
        WHEN {campo} LIKE '%PAISDEST.CODCIUDEST%' THEN 'PAISDEST.CODCIUDEST'
        WHEN {campo} LIKE '%PAISDEST.CODADUDEST%' THEN 'PAISDEST.CODADUDEST'
        WHEN {campo} LIKE '%PAISDEST.CODADUENT%' THEN 'PAISDEST.CODADUENT'
        WHEN {campo} LIKE '%DESTINACION%' AND {campo} NOT LIKE '%INDNCM%' THEN 'DESTINACION'
        WHEN {campo} LIKE '%INDNCM%' THEN 'INDNCM'
        WHEN {campo} LIKE '%CONDUCTOR.NOMBRE%' THEN 'CONDUCTOR.NOMBRE'
        WHEN {campo} LIKE '%CRT%' THEN 'CRT'
        ELSE 'OTROS' END"""

# ── Helpers ─────────────────────────────────────────────────────────────────────
def correr_queries(ruta_db, pais, anio, mes_d, mes_h, log_fn):
    if not re.match(r'^\d{4}$', str(anio)):
        raise ValueError(f"Año inválido: {anio!r}")
    tabla_real = f"DAT_{anio}"
    desde = f"{anio}-{mes_d}"; hasta = f"{anio}-{mes_h}"; like = f"%{pais}%"
    # per_ult = último mes del período seleccionado (mes_h), no el último mes del sistema
    mes_ult = mes_h  # el período termina en mes_h
    mes_ant = str(int(mes_ult)-1).zfill(2) if int(mes_ult) > 1 else "12"
    anio_per_ant = anio if int(mes_ult) > 1 else str(int(anio)-1)
    per_ult = f"{anio}-{mes_ult}"; per_ant = f"{anio_per_ant}-{mes_ant}"
    log_fn("Conectando a la BD...")
    with get_db(ruta_db, row_factory=True) as con:
        cur = con.cursor()
        # Vista deduplicada (1 fila = 1 operación, la de estado más
        # reciente) -- ver dat_actual_subquery en db_utils.py. Necesaria
        # desde que _procesar_csv conserva el historial completo de
        # estados en vez de pisarlo (decisión 17/07/2026): sin esto,
        # cualquier COUNT(*) acá contaría de más a cada operación que
        # pasó por varios estados. con=con para que el PRAGMA interno
        # consulte la MISMA base que ruta_db (no el DB_PATH global, que
        # en tests puede ser otro archivo).
        tabla = dat_actual(tabla_real, con=con)
        def q(sql, params=()):
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
        anio_ant = str(int(anio)-1); tabla_ant_real = f"DAT_{anio_ant}"
        tiene_anio_ant = bool(q("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tabla_ant_real,)))
        tabla_ant = dat_actual(tabla_ant_real, con=con)
        log_fn("Corriendo queries...")
        totales = q(f"""
            SELECT
                SUM(CASE WHEN CARGADO='SI' AND EST_MIC='TRANS' THEN 1 ELSE 0 END) AS CARGADO_TRANS,
                SUM(CASE WHEN CARGADO='NO' AND EST_MIC='TRANS' THEN 1 ELSE 0 END) AS LASTRE_TRANS,
                SUM(CASE WHEN CARGADO='SI' AND EST_MIC='NO TRANS' THEN 1 ELSE 0 END) AS CARGADO_NO_TRANS,
                SUM(CASE WHEN CARGADO='NO' AND EST_MIC='NO TRANS' THEN 1 ELSE 0 END) AS LASTRE_NO_TRANS,
                SUM(CASE WHEN CARGADO='SI' AND EST_MIC='TRANS TARD' THEN 1 ELSE 0 END) AS CARGADO_TARDIO,
                SUM(CASE WHEN CARGADO='NO' AND EST_MIC='TRANS TARD' THEN 1 ELSE 0 END) AS LASTRE_TARDIO
            FROM {tabla} WHERE MIC LIKE ? AND strftime('%Y-%m',FECHA_INGRESO_ISO) BETWEEN ? AND ?
        """, (like, desde, hasta))
        ev_total = q(f"""SELECT substr(FECHA_INGRESO_ISO,1,7) AS MES, SUM(CASE WHEN CARGADO='SI' THEN 1 ELSE 0 END) AS CARGADO, SUM(CASE WHEN CARGADO='NO' THEN 1 ELSE 0 END) AS LASTRE FROM {tabla} WHERE MIC LIKE ? AND strftime('%Y-%m',FECHA_INGRESO_ISO) BETWEEN ? AND ? GROUP BY MES ORDER BY MES""", (like, desde, hasta))
        ev_trans = q(f"""SELECT substr(FECHA_INGRESO_ISO,1,7) AS MES, SUM(CASE WHEN CARGADO='SI' THEN 1 ELSE 0 END) AS CARGADOS, SUM(CASE WHEN CARGADO='NO' THEN 1 ELSE 0 END) AS LASTRE FROM {tabla} WHERE MIC LIKE ? AND EST_MIC='TRANS' AND strftime('%Y-%m',FECHA_INGRESO_ISO) BETWEEN ? AND ? GROUP BY MES ORDER BY MES""", (like, desde, hasta))
        ev_tardio = q(f"""SELECT substr(FECHA_INGRESO_ISO,1,7) AS MES, SUM(CASE WHEN CARGADO='SI' THEN 1 ELSE 0 END) AS CARGADOS, SUM(CASE WHEN CARGADO='NO' THEN 1 ELSE 0 END) AS LASTRE FROM {tabla} WHERE MIC LIKE ? AND EST_MIC='TRANS TARD' AND strftime('%Y-%m',FECHA_INGRESO_ISO) BETWEEN ? AND ? GROUP BY MES ORDER BY MES""", (like, desde, hasta))
        ev_no_trans = q(f"""SELECT substr(FECHA_INGRESO_ISO,1,7) AS MES, SUM(CASE WHEN CARGADO='SI' THEN 1 ELSE 0 END) AS CARGADOS, SUM(CASE WHEN CARGADO='NO' THEN 1 ELSE 0 END) AS LASTRE FROM {tabla} WHERE MIC LIKE ? AND EST_MIC='NO TRANS' AND strftime('%Y-%m',FECHA_INGRESO_ISO) BETWEEN ? AND ? GROUP BY MES ORDER BY MES""", (like, desde, hasta))
        rechazos_mes = q("""WITH m AS (SELECT NroMic, MAX(strftime('%Y-%m',Fecha_ISO)) AS periodo FROM RECHAZOS WHERE PaisEmisor=? AND Anio=? AND Metodo='OficializarMicDta' AND strftime('%Y-%m',Fecha_ISO) BETWEEN ? AND ? GROUP BY NroMic) SELECT periodo, COUNT(*) AS MIC_RECHAZOS FROM m GROUP BY periodo ORDER BY periodo""", (pais, anio, desde, hasta))
        rechazos_cat = q(f"""
            WITH ranked AS (SELECT mensaje, ROW_NUMBER() OVER (PARTITION BY NroMic ORDER BY Fecha_ISO DESC) AS rn FROM RECHAZOS WHERE PaisEmisor=? AND Anio=? AND Metodo='OficializarMicDta' AND strftime('%Y-%m',Fecha_ISO) BETWEEN ? AND ?),
            res AS (SELECT {_sql_case_categorias()} AS Categoria, COUNT(*) AS Rechazos
            FROM ranked WHERE rn=1 GROUP BY Categoria),
            unioned AS (SELECT 0 AS orden, Categoria, Rechazos FROM res UNION ALL SELECT 1,'TOTAL',SUM(Rechazos) FROM res)
            SELECT Categoria, Rechazos FROM unioned ORDER BY orden, Rechazos DESC, Categoria
        """, (pais, anio, desde, hasta))
        top_cats = [r["Categoria"] for r in rechazos_cat if r["Categoria"]!="TOTAL"][:3]
        rechazos_ej = []
        nromic_vistos = set()
        for cat in top_cats:
            like_cat = CAT_LIKE.get(cat, f"%{cat}%")
            filas = q("""SELECT Fecha_ISO, NroMic, Mensaje FROM (SELECT Fecha_ISO, NroMic, Mensaje, ROW_NUMBER() OVER (PARTITION BY NroMic ORDER BY Fecha_ISO DESC) AS rn FROM RECHAZOS WHERE PaisEmisor=? AND Anio=? AND Metodo='OficializarMicDta' AND strftime('%Y-%m',Fecha_ISO) BETWEEN ? AND ?) WHERE rn=1 AND Mensaje LIKE ? ORDER BY Fecha_ISO DESC LIMIT 5""", (pais, anio, desde, hasta, like_cat))
            for fila in filas:
                if fila.get("NroMic") not in nromic_vistos:
                    nromic_vistos.add(fila.get("NroMic"))
                    rechazos_ej.append(fila)
        def totales_mes(periodo):
            anio_p = periodo[:4]; tabla_p = dat_actual(f"DAT_{anio_p}", con=con)
            rows = q(f"""SELECT SUM(CASE WHEN EST_MIC='TRANS' THEN 1 ELSE 0 END) AS trans, SUM(CASE WHEN EST_MIC='NO TRANS' THEN 1 ELSE 0 END) AS no_trans, SUM(CASE WHEN EST_MIC='TRANS TARD' THEN 1 ELSE 0 END) AS tardio, COUNT(*) AS total FROM {tabla_p} WHERE MIC LIKE ? AND strftime('%Y-%m',FECHA_INGRESO_ISO)=?""", (like, periodo))
            return rows[0] if rows else {}
        datos_ult = totales_mes(per_ult)
        datos_ant = totales_mes(per_ant)
        impoexpo_ult = q(f"""SELECT TIPO_REGISTRO, SUM(CASE WHEN EST_MIC='TRANS' THEN 1 ELSE 0 END) AS trans, SUM(CASE WHEN EST_MIC='NO TRANS' THEN 1 ELSE 0 END) AS no_trans, SUM(CASE WHEN EST_MIC='TRANS TARD' THEN 1 ELSE 0 END) AS tardio, SUM(CASE WHEN CARGADO='SI' THEN 1 ELSE 0 END) AS cargado, SUM(CASE WHEN CARGADO='NO' THEN 1 ELSE 0 END) AS lastre, COUNT(*) AS total FROM {tabla} WHERE MIC LIKE ? AND strftime('%Y-%m',FECHA_INGRESO_ISO)=? GROUP BY TIPO_REGISTRO""", (like, per_ult))
        rechazos_ult_cat = q(f"""
            WITH ranked AS (SELECT mensaje, ROW_NUMBER() OVER (PARTITION BY NroMic ORDER BY Fecha_ISO DESC) AS rn FROM RECHAZOS WHERE PaisEmisor=? AND Anio=? AND Metodo='OficializarMicDta' AND strftime('%Y-%m',Fecha_ISO)=?),
            res AS (SELECT {_sql_case_categorias()} AS Categoria, COUNT(*) AS Rechazos
            FROM ranked WHERE rn=1 GROUP BY Categoria)
            SELECT Categoria, Rechazos FROM res ORDER BY Rechazos DESC
        """, (pais, anio, per_ult))
        rech_ant = q("""WITH p AS (SELECT NroMic, MAX(strftime('%Y-%m',Fecha_ISO)) AS ult_mes FROM RECHAZOS WHERE PaisEmisor=? AND Anio=? AND Metodo='OficializarMicDta' AND strftime('%Y-%m',Fecha_ISO)=? GROUP BY NroMic) SELECT COUNT(*) AS total FROM p""", (pais, anio_per_ant, per_ant))
        total_rech_ant = n(rech_ant[0].get("total",0)) if rech_ant else 0
        datos_interanual = None
        if tiene_anio_ant:
            desde_ant = f"{anio_ant}-{mes_d}"; hasta_ant = f"{anio_ant}-{mes_h}"
            rows_ia = q(f"""SELECT SUM(CASE WHEN EST_MIC='TRANS' THEN 1 ELSE 0 END) AS trans, SUM(CASE WHEN EST_MIC='NO TRANS' THEN 1 ELSE 0 END) AS no_trans, SUM(CASE WHEN EST_MIC='TRANS TARD' THEN 1 ELSE 0 END) AS tardio, COUNT(*) AS total FROM {tabla_ant} WHERE MIC LIKE ? AND strftime('%Y-%m',FECHA_INGRESO_ISO) BETWEEN ? AND ?""", (like, desde_ant, hasta_ant))
            if rows_ia: datos_interanual = rows_ia[0]

    # Detección de anomalías: transmisiones que superan el total de ingresos del mes
    # (puede indicar retransmisiones o desfasaje entre FECHA_INGRESO_ISO y FECHA_TRANS_ISO)
    tots_chk = {r["MES"]: n(r.get("CARGADO",0))+n(r.get("LASTRE",0)) for r in ev_total}
    for r in ev_trans:
        mes_chk = r.get("MES")
        trans_chk = n(r.get("CARGADOS",0)) + n(r.get("LASTRE",0))
        tot_chk = tots_chk.get(mes_chk, 0)
        if tot_chk > 0 and trans_chk > tot_chk:
            log_fn(f"  \u26a0 Anomal\u00eda: {mes_label_largo(mes_chk)} tiene m\u00e1s transmisiones ({fmt(trans_chk)}) que ingresos ({fmt(tot_chk)}) — revisar posibles retransmisiones")

    log_fn("✓ Queries completadas")
    return (totales, ev_total, ev_trans, ev_tardio, ev_no_trans,
            rechazos_mes, rechazos_cat, rechazos_ej,
            datos_ult, datos_ant, datos_interanual,
            per_ult, per_ant, anio_ant,
            impoexpo_ult, rechazos_ult_cat, total_rech_ant)
def calcular_totales(totales):
    r = totales[0] if totales else {}
    cT=n(r.get("CARGADO_TRANS",0)); cN=n(r.get("CARGADO_NO_TRANS",0)); cTd=n(r.get("CARGADO_TARDIO",0))
    lT=n(r.get("LASTRE_TRANS",0));  lN=n(r.get("LASTRE_NO_TRANS",0));  lTd=n(r.get("LASTRE_TARDIO",0))
    cTot=cT+cN+cTd; lTot=lT+lN+lTd
    return (cT,cN,cTd,cTot),(lT,lN,lTd,lTot),(cT+lT,cN+lN,cTd+lTd,cTot+lTot)

# ── Informe consolidado multi-país (Fase 7) ──────────────────────────────────
# A diferencia de correr_queries() -- que es por país + año + rango de meses
# dentro de ese año --, este informe no filtra por país y acepta un rango de
# fechas arbitrario (puede cruzar años calendario), por eso arma un UNION ALL
# de las tablas DAT_<año> involucradas en vez de operar sobre una sola tabla.

_COLUMNAS_CONSOLIDADO = ["MIC", "FECHA_INGRESO_ISO", "CARGADO", "TIPO_REGISTRO", "ADUANA", "VAR_CONTROL",
                         "ULT_ESTADO", "FECHA_ULT_INT"]

# Mismo criterio que _FECHA_ULT_INT_ISO en app.py (informe "Aduanas del
# país") -- duplicado a propósito acá para no crear una dependencia de
# generar_queries.py hacia app.py (este módulo es Flask-agnóstico). Si se
# cambia uno, cambiar el otro. FECHA_ULT_INT viene como "DD-MM-YYYY HH:MM:SS"
# (formato argentino), no ISO -- julianday() de SQLite solo reconoce ISO.
_FECHA_ULT_INT_ISO = (
    "(CASE WHEN FECHA_ULT_INT LIKE '____-__-__%' THEN FECHA_ULT_INT "
    "ELSE substr(FECHA_ULT_INT,7,4) || '-' || substr(FECHA_ULT_INT,4,2) "
    "|| '-' || substr(FECHA_ULT_INT,1,2) || substr(FECHA_ULT_INT,11) END)"
)
UMBRAL_ALERTA_DIAS_CONSOLIDADO = 10  # mismo default que el informe "Aduanas del país"

def comparacion_anual_meses_completos(con):
    """Comparación año contra año, mes por mes, de TODOS los países --
    solo de los meses del año en curso que ya terminaron (no compara el
    mes actual, que está a medio transcurrir y daría una comparación
    injusta contra el mismo mes completo del año anterior).

    Se basa en la fecha real del sistema (no en el rango fecha_d/fecha_h
    del informe): es una foto de "cómo venimos este año vs el año pasado",
    independiente del período que se haya elegido para el resto del
    informe consolidado.

    Devuelve una lista de dicts (uno por mes completo transcurrido):
      mes, mes_label, anio_actual, total_actual, anio_anterior,
      total_anterior, variacion_pct (None si no hay tabla DAT_<año-1>).
    Lista vacía en enero (ningún mes del año en curso terminó todavía) o
    si ni siquiera existe la tabla del año actual."""
    hoy = datetime.today()
    anio_actual = hoy.year
    anio_anterior = anio_actual - 1
    ultimo_mes_completo = hoy.month - 1  # el mes en curso no cuenta, todavía no terminó
    if ultimo_mes_completo < 1:
        return []

    tabla_actual = f"DAT_{anio_actual}"
    tabla_anterior = f"DAT_{anio_anterior}"
    existe = lambda t: bool(con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone())
    if not existe(tabla_actual):
        return []
    hay_anio_anterior = existe(tabla_anterior)

    def total_mes(tabla, periodo):
        row = con.execute(
            f"SELECT COUNT(*) FROM {dat_actual(tabla, con=con)} WHERE strftime('%Y-%m',FECHA_INGRESO_ISO)=?",
            (periodo,)).fetchone()
        return row[0] if row else 0

    resultado = []
    for mes in range(1, ultimo_mes_completo + 1):
        mm = f"{mes:02d}"
        total_actual = total_mes(tabla_actual, f"{anio_actual}-{mm}")
        total_anterior = total_mes(tabla_anterior, f"{anio_anterior}-{mm}") if hay_anio_anterior else None
        variacion_pct = (round(100 * (total_actual - total_anterior) / total_anterior, 1)
                          if total_anterior else None)
        resultado.append({
            "mes": mm, "mes_label": MESES.get(mm, mm),
            "anio_actual": anio_actual, "total_actual": total_actual,
            "anio_anterior": anio_anterior, "total_anterior": total_anterior,
            "variacion_pct": variacion_pct,
        })
    return resultado


def _tablas_dat_en_rango(con, fecha_d, fecha_h):
    """Nombres de tabla DAT_<año> que intersectan [fecha_d, fecha_h]
    (fechas en formato YYYY-MM-DD). Solo incluye las que existen en la BD --
    si falta algún año intermedio, simplemente no aporta datos, no rompe."""
    anio_d, anio_h = int(fecha_d[:4]), int(fecha_h[:4])
    tablas = []
    for a in range(anio_d, anio_h + 1):
        nombre = f"DAT_{a}"
        existe = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (nombre,)).fetchone()
        if existe:
            tablas.append(nombre)
    return tablas

def _columnas_tabla(con, tabla):
    return {r[1] for r in con.execute(f"PRAGMA table_info({tabla})").fetchall()}

def _union_dat_rango(con, tablas, columnas):
    """UNION ALL de las tablas DAT_<año> dadas, proyectando solo `columnas`.
    Si a alguna tabla le falta una columna (años viejos con schema distinto
    -- VAR_CONTROL no siempre existió, ver _calcular_opciones_dat en
    app.py), se completa con NULL en esa columna en vez de romper la query.
    Cada tabla se pasa por dat_actual_subquery (1 fila = 1 operación, la
    del estado más reciente) ANTES de unir -- ver esa función en
    db_utils.py para el porqué."""
    selects = []
    for t in tablas:
        cols_t = _columnas_tabla(con, t)  # PRAGMA necesita el nombre real, no la subquery
        proyeccion = ", ".join(c if c in cols_t else f"NULL AS {c}" for c in columnas)
        selects.append(f"SELECT {proyeccion} FROM {dat_actual(t, con=con)}")
    return "(" + " UNION ALL ".join(selects) + ")"

def _catalogo_aduanas_dira(hist_db):
    """Catálogo {cod: {nombre, indice_dira}} y {indice_dira: nombre} desde
    ref_aduanas/ref_dira -- viven en HIST_DB (historial.db), no en la misma
    base que DAT_<año> (pad.db), así que se cruza acá en Python, mismo
    criterio que ya usa _aduanas_nacional_datos en app.py."""
    if not hist_db:
        return {}, {}
    with get_db(hist_db, row_factory=True) as con:
        cat_aduanas = {r["cod"]: dict(r) for r in con.execute(
            "SELECT cod, nombre, indice_dira FROM ref_aduanas").fetchall()}
        cat_diras = {r["indice"]: r["nombre"] for r in con.execute(
            "SELECT indice, nombre FROM ref_dira").fetchall()}
    return cat_aduanas, cat_diras


def correr_queries_consolidado(ruta_db, fecha_d, fecha_h, log_fn, hist_db=None):
    """Informe consolidado: todas las operaciones de TODOS los países dentro
    de [fecha_d, fecha_h] (inclusive, YYYY-MM-DD), desglosadas por país,
    importación/exportación, aduana, cargado/lastre y variable de control.

    hist_db: ruta a historial.db, opcional. Si se pasa, cada fila de
    por_aduana se enriquece con ADUANA_NOMBRE y DIRA_NOMBRE (catálogo
    ref_aduanas/ref_dira). Si no se pasa (ej. tests, o si esa base no está
    disponible), por_aduana queda solo con el código -- no rompe.

    Devuelve (totales, por_pais, por_aduana, por_var_control, comparacion_anual):
      totales: dict con TOTAL, IMPO, EXPO, CARGADO, LASTRE (agregado general)
      por_pais / por_aduana / por_var_control: listas de dicts, mismo shape
      que totales pero agrupadas (por_var_control solo trae TOTAL, no tiene
      sentido desglosar impo/expo/cargado/lastre otra vez ahí).
      comparacion_anual: ver comparacion_anual_meses_completos() -- año
      actual vs año anterior, mes a mes, solo meses ya terminados. No
      depende de fecha_d/fecha_h, es siempre "a hoy".
    """
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', fecha_d) or not re.match(r'^\d{4}-\d{2}-\d{2}$', fecha_h):
        raise ValueError(f"Fechas inválidas: '{fecha_d}' a '{fecha_h}'. Formato esperado: YYYY-MM-DD.")
    if fecha_d > fecha_h:
        raise ValueError(f"El rango de fechas es inválido: '{fecha_d}' es posterior a '{fecha_h}'.")

    log_fn("Conectando a la BD...")
    with get_db(ruta_db, row_factory=True) as con:
        tablas = _tablas_dat_en_rango(con, fecha_d, fecha_h)
        if not tablas:
            raise ValueError(f"No hay datos cargados para el rango {fecha_d} a {fecha_h}.")
        origen = _union_dat_rango(con, tablas, _COLUMNAS_CONSOLIDADO)

        def q(sql, params=()):
            cur = con.cursor(); cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

        log_fn("Corriendo queries...")
        # El país se deduce del MIC igual que en correr_queries() (MIC LIKE
        # '%XX%'), no hay columna de país propia en DAT_<año>.
        #
        # YPFB (Yacimientos Petrolíferos Fiscales Bolivianos, la petrolera
        # estatal de Bolivia) es un caso especial: encontrado en producción
        # (17/07/2026) que explica el 70% de los MIC que caían en "OTRO/SIN
        # DATO" con letras -- "YPFB" no contiene la secuencia "BO" así que
        # el detector genérico no lo agarraba. Se chequea ANTES que el CASE
        # genérico (aunque en este caso puntual el orden no cambia el
        # resultado, ya que "YPFB" no matchea ningún otro código de país).
        casos_pais = "WHEN MIC LIKE '%YPFB%' THEN 'BO' " + \
            " ".join(f"WHEN MIC LIKE '%{cod}%' THEN '{cod}'" for cod in PAISES_CONSOLIDADO)
        expr_pais = f"CASE {casos_pais} ELSE 'OTRO/SIN DATO' END"

        agregados_sql = """
            COUNT(*) AS TOTAL,
            SUM(CASE WHEN TIPO_REGISTRO='I' THEN 1 ELSE 0 END) AS IMPO,
            SUM(CASE WHEN TIPO_REGISTRO='E' THEN 1 ELSE 0 END) AS EXPO,
            SUM(CASE WHEN CARGADO='SI' THEN 1 ELSE 0 END) AS CARGADO,
            SUM(CASE WHEN CARGADO='NO' THEN 1 ELSE 0 END) AS LASTRE
        """
        where = "WHERE FECHA_INGRESO_ISO BETWEEN ? AND ?"
        params = (fecha_d, fecha_h)

        totales_rows = q(f"SELECT {agregados_sql} FROM {origen} {where}", params)
        totales = totales_rows[0] if totales_rows else {}

        por_pais = q(f"""
            SELECT {expr_pais} AS PAIS, {agregados_sql}
            FROM {origen} {where} GROUP BY PAIS ORDER BY TOTAL DESC
        """, params)

        _demora_expr = f"(julianday({_FECHA_ULT_INT_ISO}) - julianday(FECHA_INGRESO_ISO))"
        umbral = UMBRAL_ALERTA_DIAS_CONSOLIDADO
        por_aduana = q(f"""
            SELECT COALESCE(NULLIF(TRIM(ADUANA),''),'SIN DATO') AS ADUANA, {agregados_sql},
                AVG(CASE WHEN ULT_ESTADO='SAL' AND {_demora_expr} <= ? THEN {_demora_expr} END) AS DEMORA_MEDIA_DIAS,
                SUM(CASE
                    WHEN ULT_ESTADO='SAL' AND {_demora_expr} > ? THEN 1
                    WHEN ULT_ESTADO!='SAL' AND (julianday('now') - julianday(FECHA_INGRESO_ISO)) > ? THEN 1
                    ELSE 0 END) AS EN_ALERTA_PAD
            FROM {origen} {where}
            GROUP BY COALESCE(NULLIF(TRIM(ADUANA),''),'SIN DATO') ORDER BY TOTAL DESC
        """, (umbral, umbral, umbral) + params)

        por_var_control = q(f"""
            SELECT COALESCE(NULLIF(TRIM(VAR_CONTROL),''),'SIN VARIABLE DE CONTROL') AS VAR_CONTROL,
                COUNT(*) AS TOTAL
            FROM {origen} {where}
            GROUP BY COALESCE(NULLIF(TRIM(VAR_CONTROL),''),'SIN VARIABLE DE CONTROL') ORDER BY TOTAL DESC
        """, params)

        comparacion_anual = comparacion_anual_meses_completos(con)

    cat_aduanas, cat_diras = _catalogo_aduanas_dira(hist_db)
    for r in por_aduana:
        r["DEMORA_MEDIA_FMT"] = formatear_demora(r.get("DEMORA_MEDIA_DIAS"))
        cod = r["ADUANA"]
        if cod == "SIN DATO":
            r["ADUANA_NOMBRE"] = "SIN DATO"; r["DIRA_NOMBRE"] = "—"
            continue
        info = cat_aduanas.get(cod)
        r["ADUANA_NOMBRE"] = info["nombre"] if info else f"{cod} (sin nombre en ref_aduanas)"
        dira_indice = info["indice_dira"] if info else None
        r["DIRA_NOMBRE"] = cat_diras.get(dira_indice, "Sin DIRA asignada") if dira_indice else "Sin DIRA asignada"

    log_fn("✓ Queries completadas")
    return totales, por_pais, por_aduana, por_var_control, comparacion_anual
