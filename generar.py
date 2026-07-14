"""
generar.py — Orquestador del informe SINTIA + punto de entrada público.
Adaptado del script original generar_informe_sintia.py para uso como módulo web.

Fase 3 de profesionalización: este archivo antes tenía ~1620 líneas mezclando
queries SQL, gráficos matplotlib, armado de Word/Excel y prompts de IA en un
solo módulo. Se partió en:
  generar_utils.py      — constantes + helpers puros (fmt, pct, PAISES, MESES...)
  generar_queries.py    — extracción de datos (correr_queries, calcular_totales)
  generar_graficos.py   — gráficos matplotlib
  generar_ia.py         — narrativa/conclusión con Claude + verificación numérica
  generar_documento.py  — armado final de Word/Excel
  generar.py (acá)      — orquesta todo lo anterior (generar_informe)

Todo lo que antes se importaba como `generar.fmt`, `generar.PAISES`,
`generar._sql_case_categorias`, etc. se sigue pudiendo importar igual desde
`generar` — quedan re-exportados más abajo — para no romper nada en app.py,
los blueprints, ni los tests existentes.
"""
import os

# Re-exports: mantienen el mismo namespace público que tenía este archivo
# antes del split (ej. generar.fmt, generar.PAISES, generar.DOCX_OK...).
from generar_utils import *  # noqa: F401,F403
from generar_utils import PAISES, MESES  # nombres usados explícitamente acá abajo
from generar_queries import _sql_case_categorias, correr_queries, calcular_totales, correr_queries_consolidado
from generar_graficos import *  # noqa: F401,F403
from generar_graficos import MPL_OK
from generar_ia import *  # noqa: F401,F403
from generar_ia import ANT_OK
from generar_documento import *  # noqa: F401,F403
from generar_documento import DOCX_OK, XLSX_OK, _generar_word, _generar_excel, _generar_word_consolidado, _generar_excel_consolidado

def generar_informe(ruta_db, pais, anio, mes_d, mes_h, usar_ia, api_key, carpeta, log_fn, contexto_extra=""):
    nombre_base=f"Informe_SINTIA_{pais}_{anio}_{mes_d}-{mes_h}"
    version=1
    while os.path.exists(os.path.join(carpeta,f"{nombre_base}_v{version}.docx")): version+=1

    (totales,ev_total,ev_trans,ev_tardio,ev_no_trans,
     rechazos_mes,rechazos_cat,rechazos_ej,
     datos_ult,datos_ant,datos_interanual,
     per_ult,per_ant,anio_ant,
     impoexpo_ult,rechazos_ult_cat,total_rech_ant) = correr_queries(ruta_db,pais,anio,mes_d,mes_h,log_fn)

    narrativa_ia=None; conclusion_ia=None

    if usar_ia and api_key and ANT_OK:
        (cT,cN,cTd,cTot),(lT,lN,lTd,lTot),(gT,gN,gTd,gTot)=calcular_totales(totales)
        total_rechazos=sum(n(r.get("MIC_RECHAZOS",0)) for r in rechazos_mes)
        ev_txt="; ".join([f"{mes_label_largo(r['MES'])}: {n(r.get('CARGADO',0))+n(r.get('LASTRE',0))} ops" for r in ev_total])
        log_fn("Generando narrativa con IA...")
        narrativa_ia=generar_narrativa_ia({
            "pais_nombre":PAISES.get(pais,pais),"periodo":periodo_texto(anio,mes_d,per_ult[-2:] if per_ult else mes_h),
            "g_total":fmt(gTot),"g_trans":fmt(gT),"g_no_trans":fmt(gN),"g_tardio":fmt(gTd),
            "pct_trans":pct(gT,gTot),"pct_no_trans":pct(gN,gTot),"pct_tardio":pct(gTd,gTot),
            "cant_meses":str(int(mes_h)-int(mes_d)+1),
            "promedio_mensual":fmt(round(gTot/(int(mes_h)-int(mes_d)+1))) if int(mes_h)>=int(mes_d) else "N/D",
            "c_trans":fmt(cT),"c_no_trans":fmt(cN),"c_tardio":fmt(cTd),"c_total":fmt(cTot),
            "pct_c_trans":pct(cT,cTot),"pct_c_no_trans":pct(cN,cTot),"pct_c_tardio":pct(cTd,cTot),
            "l_trans":fmt(lT),"l_no_trans":fmt(lN),"l_tardio":fmt(lTd),"l_total":fmt(lTot),
            "pct_l_trans":pct(lT,lTot),"pct_l_no_trans":pct(lN,lTot),"pct_l_tardio":pct(lTd,lTot),
            "total_rechazos":fmt(total_rechazos),"ev_mensual_texto":ev_txt,
        }, api_key, contexto_extra)
        if narrativa_ia: log_fn("✓ Narrativa generada")
        else: log_fn("  Narrativa no disponible, usando texto est\u00e1ndar")

        if narrativa_ia and len(narrativa_ia) >= 2:
            narrativa_ia[1] = verificar_narrativa_denominadores(
                narrativa_ia[1],
                [pct(cT,cTot), pct(cN,cTot), pct(cTd,cTot)],
                [pct(lT,lTot), pct(lN,lTot), pct(lTd,lTot)],
                log_fn)

        log_fn("Generando conclusi\u00f3n con IA...")
        ult_t=n(datos_ult.get("total",0)) if datos_ult else 0
        ult_tr=n(datos_ult.get("trans",0)) if datos_ult else 0
        ult_nt=n(datos_ult.get("no_trans",0)) if datos_ult else 0
        ult_td=n(datos_ult.get("tardio",0)) if datos_ult else 0
        ant_t=n(datos_ant.get("total",0)) if datos_ant else 0
        ant_tr=n(datos_ant.get("trans",0)) if datos_ant else 0
        ant_nt=n(datos_ant.get("no_trans",0)) if datos_ant else 0
        ant_td=n(datos_ant.get("tardio",0)) if datos_ant else 0
        impo_ult=next((r for r in impoexpo_ult if r.get("TIPO_REGISTRO","").upper()=="I"),{})
        expo_ult=next((r for r in impoexpo_ult if r.get("TIPO_REGISTRO","").upper()=="E"),{})
        ult_carg_tot=n(impo_ult.get("cargado",0))+n(expo_ult.get("cargado",0))
        ult_last_tot=n(impo_ult.get("lastre",0))+n(expo_ult.get("lastre",0))
        ev_trans_ult_ia=next((r for r in ev_trans if r.get("MES")==per_ult),{})
        ev_tardio_ult_ia=next((r for r in ev_tardio if r.get("MES")==per_ult),{})
        carg_trans_n_ia=n(ev_trans_ult_ia.get("CARGADOS",0))
        last_trans_n_ia=n(ev_trans_ult_ia.get("LASTRE",0))
        carg_tr=carg_trans_n_ia
        # Usar rechazos_mes para el total del mes ult (misma fuente que la tabla)
        ult_rech_total_mes=next((n(r.get("MIC_RECHAZOS",0)) for r in rechazos_mes if r.get("periodo")==per_ult),0)
        ult_rech_total_cat=sum(n(r.get("Rechazos",0)) for r in rechazos_ult_cat)
        ult_rech_total=ult_rech_total_mes if ult_rech_total_mes>0 else ult_rech_total_cat
        ult_rech_dup=next((n(r.get("Rechazos",0)) for r in rechazos_ult_cat if r.get("Categoria")=="NRO DE MIC EXISTENTE"),0)
        # Recalcular op con el total de la tabla para consistencia
        ult_rech_op_cat=ult_rech_total_cat-ult_rech_dup
        ult_rech_op=ult_rech_total_mes-ult_rech_dup if ult_rech_total_mes>0 and ult_rech_total_mes>=ult_rech_dup else ult_rech_op_cat
        ult_rech_top=", ".join([f"{r['Categoria']}: {r['Rechazos']}" for r in rechazos_ult_cat if r.get("Categoria")!="NRO DE MIC EXISTENTE"][:7])
        tots_mes={r["MES"]:n(r.get("CARGADO",0))+n(r.get("LASTRE",0)) for r in ev_total}
        trs_mes={r["MES"]:n(r.get("CARGADOS",0))+n(r.get("LASTRE",0)) for r in ev_trans}
        tds_mes={r["MES"]:n(r.get("CARGADOS",0))+n(r.get("LASTRE",0)) for r in ev_tardio}
        nts_mes={r["MES"]:n(r.get("CARGADOS",0))+n(r.get("LASTRE",0)) for r in ev_no_trans}
        ev_tabla_lineas=[f"{mes_label(r['MES'])}: total={fmt(tots_mes.get(r['MES'],0))}, trans={pct(trs_mes.get(r['MES'],0),tots_mes.get(r['MES'],0))}, no_trans={pct(nts_mes.get(r['MES'],0),tots_mes.get(r['MES'],0))}, tardio={pct(tds_mes.get(r['MES'],0),tots_mes.get(r['MES'],0))}" for r in ev_total]
        ia_t=n(datos_interanual.get("total",0)) if datos_interanual else 0
        ia_tr=n(datos_interanual.get("trans",0)) if datos_interanual else 0
        ia_nt=n(datos_interanual.get("no_trans",0)) if datos_interanual else 0
        ia_td=n(datos_interanual.get("tardio",0)) if datos_interanual else 0
        # Calcular ant_rechazos desde rechazos_mes (misma fuente que la tabla)
        ant_rech_mes_val=next((n(r.get("MIC_RECHAZOS",0)) for r in rechazos_mes if r.get("periodo")==per_ant),0)
        ant_rech_final=ant_rech_mes_val if ant_rech_mes_val>0 else total_rech_ant
        # Datos de lastre del mes anterior (para variación correcta lastre vs lastre)
        ev_trans_ant_ia=next((r for r in ev_trans if r.get("MES")==per_ant),{})
        ev_total_ant_ia=next((r for r in ev_total if r.get("MES")==per_ant),{})
        ant_last_trans_n=n(ev_trans_ant_ia.get("LASTRE",0))
        ant_last_tot_n=n(ev_total_ant_ia.get("LASTRE",0))
        ev_tardio_ant_ia2=next((r for r in ev_tardio if r.get("MES")==per_ant),{})
        ant_last_tardio_n2=n(ev_tardio_ant_ia2.get("LASTRE",0))
        ant_last_notrans_n=ant_last_tot_n-ant_last_trans_n-ant_last_tardio_n2
        # Valor absoluto correcto de lastre no transmitido del mes actual (tot - trans - tardio)
        ult_last_tardio_n_ia=n(ev_tardio_ult_ia.get("LASTRE",0))
        ult_last_notrans_n=ult_last_tot-last_trans_n_ia-ult_last_tardio_n_ia
        # Paso 1: pre-calcular frases comparativas con dirección garantizada
        _frases_conc = calcular_frases({
            "ult_total":fmt(ult_t), "ant_total":fmt(ant_t),
            "mes_ult_nombre":mes_label_largo(per_ult), "mes_ant_nombre":mes_label_largo(per_ant),
            "ult_pct_trans":pct(ult_tr,ult_t), "ant_pct_trans":pct(ant_tr,ant_t),
            "ult_carg_trans_n":fmt(carg_tr), "ult_carg_tot_n":fmt(ult_carg_tot),
            "ult_carg_no_trans_n":fmt(ult_carg_tot-carg_tr-n(ev_tardio_ult_ia.get("CARGADOS",0))) if ult_carg_tot else "0",
            "ult_carg_tardio_n":fmt(n(ev_tardio_ult_ia.get("CARGADOS",0))),
            "ult_pct_trans_carg":pct(carg_tr,ult_carg_tot) if ult_carg_tot else "N/D",
            "ult_pct_no_trans_carg":pct(ult_carg_tot-carg_tr-n(ev_tardio_ult_ia.get("CARGADOS",0)),ult_carg_tot) if ult_carg_tot else "N/D",
            "ult_pct_tardio_carg":pct(n(ev_tardio_ult_ia.get("CARGADOS",0)),ult_carg_tot) if ult_carg_tot else "N/D",
            "ult_last_trans_n":fmt(last_trans_n_ia), "ult_last_tot_n":fmt(ult_last_tot),
            "ult_last_notrans_n":fmt(ult_last_notrans_n), "ult_last_tardio_n":fmt(ult_last_tardio_n_ia),
            "ult_pct_trans_last":pct(last_trans_n_ia,ult_last_tot) if ult_last_tot else "N/D",
            "ult_pct_no_trans_last":pct(ult_last_notrans_n,ult_last_tot) if ult_last_tot else "N/D",
            "ult_pct_tardio_last":pct(ult_last_tardio_n_ia,ult_last_tot) if ult_last_tot else "N/D",
            "ant_last_trans_n":fmt(ant_last_trans_n), "ant_last_tot_n":fmt(ant_last_tot_n),
            "ant_last_pct_trans":pct(ant_last_trans_n,ant_last_tot_n) if ant_last_tot_n else "N/D",
            "ant_last_notrans_n":fmt(ant_last_notrans_n) if ant_last_tot_n else "0",
            "ult_rechazos":fmt(ult_rech_total), "ant_rechazos":fmt(ant_rech_final),
            "ult_rech_duplicados":fmt(ult_rech_dup), "ult_rech_operativos":fmt(ult_rech_op),
        })
        conclusion_ia=generar_conclusion_ia({
            "pais_nombre":PAISES.get(pais,pais),"periodo":periodo_texto(anio,mes_d,per_ult[-2:] if per_ult else mes_h),
            "mes_ult_nombre":mes_label_largo(per_ult),"mes_ant_nombre":mes_label_largo(per_ant),
            "ult_total":fmt(ult_t),"ult_impo":fmt(n(impo_ult.get("total",0))),"ult_pct_impo":pct(n(impo_ult.get("total",0)),ult_t) if ult_t else "N/D","ult_expo":fmt(n(expo_ult.get("total",0))),"ult_pct_expo":pct(n(expo_ult.get("total",0)),ult_t) if ult_t else "N/D",
            "ult_trans":fmt(ult_tr),"ult_pct_trans":pct(ult_tr,ult_t),
            "ult_no_trans":fmt(ult_nt),"ult_pct_no_trans":pct(ult_nt,ult_t),
            "ult_tardio":fmt(ult_td),"ult_pct_tardio":pct(ult_td,ult_t),
            "ult_pct_trans_carg":pct(carg_tr,ult_carg_tot) if ult_carg_tot else "N/D",
            "ult_pct_no_trans_carg":pct(ult_carg_tot-carg_tr-n(ev_tardio_ult_ia.get("CARGADOS",0)),ult_carg_tot) if ult_carg_tot else "N/D",
            "ult_pct_tardio_carg":pct(n(ev_tardio_ult_ia.get("CARGADOS",0)),ult_carg_tot) if ult_carg_tot else "N/D",
            "ult_carg_trans_n":fmt(carg_tr),
            "ult_carg_no_trans_n":fmt(ult_carg_tot-carg_tr-n(ev_tardio_ult_ia.get("CARGADOS",0))) if ult_carg_tot else "N/D",
            "ult_carg_tardio_n":fmt(n(ev_tardio_ult_ia.get("CARGADOS",0))),
            "ult_carg_lastre_tardio_n":fmt(n(ev_tardio_ult_ia.get("LASTRE",0))),
            "ult_carg_tot_n":fmt(ult_carg_tot),
            "ult_pct_trans_last":pct(last_trans_n_ia,ult_last_tot) if ult_last_tot else "N/D",
            "ult_last_trans_n":fmt(last_trans_n_ia),"ult_last_tot_n":fmt(ult_last_tot),
            "var_trans_pp":f"{round(pct_f(ult_tr,ult_t)-pct_f(ant_tr,ant_t),1):+.1f}".replace(".",","),
            "var_carg_trans_pp":f"{round(pct_f(carg_tr,ult_carg_tot)-pct_f(ant_tr,ant_t),1):+.1f}".replace(".",",") if ult_carg_tot and ant_t else "N/D",
            "var_last_trans_pp":f"{round(pct_f(last_trans_n_ia,ult_last_tot)-pct_f(ant_last_trans_n,ant_last_tot_n),1):+.1f}".replace(".",",") if ult_last_tot and ant_last_tot_n else "N/D","ant_last_pct_trans":pct(ant_last_trans_n,ant_last_tot_n) if ant_last_tot_n else "N/D","ant_last_trans_n":fmt(ant_last_trans_n),"ant_last_tot_n":fmt(ant_last_tot_n),"ult_last_notrans_n":fmt(ult_last_notrans_n),"ult_last_tardio_n":fmt(ult_last_tardio_n_ia),
            "ult_pct_no_trans_last":pct(ult_last_tot-last_trans_n_ia-n(ev_tardio_ult_ia.get("LASTRE",0)),ult_last_tot) if ult_last_tot else "N/D",
            "ult_pct_tardio_last":pct(n(ev_tardio_ult_ia.get("LASTRE",0)),ult_last_tot) if ult_last_tot else "N/D",
            "ult_rechazos":fmt(ult_rech_total),"ult_rech_duplicados":fmt(ult_rech_dup),
            "ult_rech_operativos":fmt(ult_rech_op),"ult_rech_top_cats":ult_rech_top,
            "ult_rech_total_check":fmt(ult_rech_total_mes if ult_rech_total_mes>0 else ult_rech_total_cat),
            "ant_total":fmt(ant_t),"ant_trans":fmt(ant_tr),"ant_pct_trans":pct(ant_tr,ant_t),
            "ant_no_trans":fmt(ant_nt),"ant_pct_no_trans":pct(ant_nt,ant_t),
            "ant_tardio":fmt(ant_td),"ant_pct_tardio":pct(ant_td,ant_t),
            "ant_rechazos":fmt(ant_rech_final),
            "var_rech_abs":fmt(abs(ult_rech_total-ant_rech_final)),
            "var_rech_abs_num":abs(ult_rech_total-ant_rech_final),
            "var_rech_abs_texto":f"{fmt(abs(ult_rech_total-ant_rech_final))} rechazos",
            "var_rech_dir":"aumento" if ult_rech_total>ant_rech_final else "disminución",
            "var_rech_pct":(f"{round(abs(ult_rech_total-ant_rech_final)/ant_rech_final*100,0):.0f}%" if ant_rech_final>0 else "N/D"),"ev_tabla_texto":" | ".join(ev_tabla_lineas),
            "g_total":fmt(gTot),"g_trans":fmt(gT),"g_no_trans":fmt(gN),"g_tardio":fmt(gTd),
            "pct_trans":pct(gT,gTot),"pct_no_trans":pct(gN,gTot),"pct_tardio":pct(gTd,gTot),
            "cant_meses":str(int(mes_h)-int(mes_d)+1),
            "promedio_mensual":fmt(round(gTot/(int(mes_h)-int(mes_d)+1))) if int(mes_h)>=int(mes_d) else "N/D",
            "tiene_interanual":datos_interanual is not None,"anio_ant":anio_ant,"anio":anio,
            "g_total_ant":fmt(ia_t),"pct_trans_ant":pct(ia_tr,ia_t),
            "pct_no_trans_ant":pct(ia_nt,ia_t),"pct_tardio_ant":pct(ia_td,ia_t),
            # Paso 1: frases pre-calculadas con dirección garantizada
            "frase_volumen_var":_frases_conc.get("frase_volumen_var",""),
            "frase_trans_var":_frases_conc.get("frase_trans_var",""),
            "frase_carg_detalle":_frases_conc.get("frase_carg_detalle",""),
            "frase_lastre_detalle":_frases_conc.get("frase_lastre_detalle",""),
            "frase_lastre_trans_var":_frases_conc.get("frase_lastre_trans_var",""),
            "frase_lastre_notrans_var":_frases_conc.get("frase_lastre_notrans_var",""),
            "frase_rech_var":_frases_conc.get("frase_rech_var",""),
        }, api_key, contexto_extra)
        if conclusion_ia:
            conclusion_ia, _hay_err = verificar_conclusion(conclusion_ia, {
                "ult_last_notrans_n": fmt(ult_last_notrans_n),
                "ult_pct_no_trans_last": pct(ult_last_notrans_n, ult_last_tot) if ult_last_tot else "N/D",
                "frase_lastre_trans_var": _frases_conc.get("frase_lastre_trans_var",""),
                "ult_rechazos": fmt(ult_rech_total),
                "ant_rechazos": fmt(ant_rech_final),
            }, log_fn)
            log_fn("\u2713 Conclusi\u00f3n generada" + (" (\u26a0 con advertencias)" if _hay_err else ""))
        else: log_fn("  Conclusi\u00f3n no disponible, usando texto est\u00e1ndar")

    log_fn("Generando archivos...")
    archivos=[]
    if DOCX_OK:
        ruta=_generar_word(pais,anio,mes_d,mes_h,version,totales,ev_total,ev_trans,ev_tardio,ev_no_trans,
            rechazos_mes,rechazos_cat,rechazos_ej,datos_ult,datos_ant,datos_interanual,
            per_ult,per_ant,anio_ant,impoexpo_ult,rechazos_ult_cat,total_rech_ant,
            narrativa_ia,conclusion_ia,carpeta,log_fn)
        archivos.append(ruta)
    if XLSX_OK:
        ruta=_generar_excel(pais,anio,mes_d,mes_h,version,totales,ev_total,ev_trans,ev_tardio,ev_no_trans,
            rechazos_mes,rechazos_cat,rechazos_ej,datos_ult,datos_ant,datos_interanual,
            per_ult,per_ant,anio_ant,carpeta,log_fn)
        archivos.append(ruta)
    log_fn(f"✓ Proceso completado \u2014 {len(archivos)} archivo(s) listos para descargar")
    return archivos

def generar_informe_consolidado(ruta_db, fecha_d, fecha_h, carpeta, log_fn, hist_db=None):
    """Informe consolidado multi-país (Fase 7): todas las operaciones de
    todos los países dentro de [fecha_d, fecha_h] (YYYY-MM-DD), desglosadas
    por país, importación/exportación, aduana, cargado/lastre y variable de
    control. A diferencia de generar_informe(), no es por país/año/mes, no
    lleva narrativa/conclusión con IA (es un informe estadístico, no
    interpretativo), y admite un rango de fechas que puede cruzar años.

    hist_db: ruta a historial.db, opcional -- si se pasa, la tabla "por
    aduana" se enriquece con nombre y DIRA (ver correr_queries_consolidado).
    """
    nombre_base = f"Informe_SINTIA_Consolidado_{fecha_d}_{fecha_h}"
    version = 1
    while os.path.exists(os.path.join(carpeta, f"{nombre_base}_v{version}.docx")): version += 1

    totales, por_pais, por_aduana, por_var_control = correr_queries_consolidado(
        ruta_db, fecha_d, fecha_h, log_fn, hist_db=hist_db)

    log_fn("Generando archivos...")
    archivos = []
    if DOCX_OK:
        ruta = _generar_word_consolidado(fecha_d, fecha_h, version, totales, por_pais, por_aduana,
                                          por_var_control, carpeta, log_fn)
        archivos.append(ruta)
    if XLSX_OK:
        ruta = _generar_excel_consolidado(fecha_d, fecha_h, version, totales, por_pais, por_aduana,
                                           por_var_control, carpeta, log_fn)
        archivos.append(ruta)
    log_fn(f"✓ Proceso completado — {len(archivos)} archivo(s) listos para descargar")
    return archivos
