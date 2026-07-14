"""
generar_queries.py — Extracción de datos SQL para el informe SINTIA.
Extraído de generar.py (Fase 3 de profesionalización).
"""
import re
from db_utils import get_db
from generar_utils import CAT_LIKE, n, fmt, mes_label_largo

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
    tabla = f"DAT_{anio}"
    desde = f"{anio}-{mes_d}"; hasta = f"{anio}-{mes_h}"; like = f"%{pais}%"
    # per_ult = último mes del período seleccionado (mes_h), no el último mes del sistema
    mes_ult = mes_h  # el período termina en mes_h
    mes_ant = str(int(mes_ult)-1).zfill(2) if int(mes_ult) > 1 else "12"
    anio_per_ant = anio if int(mes_ult) > 1 else str(int(anio)-1)
    per_ult = f"{anio}-{mes_ult}"; per_ant = f"{anio_per_ant}-{mes_ant}"
    log_fn("Conectando a la BD...")
    with get_db(ruta_db, row_factory=True) as con:
        cur = con.cursor()
        def q(sql, params=()):
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
        anio_ant = str(int(anio)-1); tabla_ant = f"DAT_{anio_ant}"
        tiene_anio_ant = bool(q("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tabla_ant,)))
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
            anio_p = periodo[:4]; tabla_p = f"DAT_{anio_p}"
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
