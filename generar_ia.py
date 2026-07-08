"""
generar_ia.py — Narrativa y conclusión generadas con Claude para el informe
SINTIA, más la verificación numérica posterior (fact-checking de lo que
escribió el modelo contra los números calculados por SQL — ver
verificar_narrativa_denominadores / verificar_conclusion).
Extraído de generar.py (Fase 3 de profesionalización).
"""
import re

try:
    import anthropic, httpx
    ANT_OK = True
except ImportError:
    ANT_OK = False

from generar_utils import GLOSARIO, fmt, pct, pct_f, n, _dir, _pp

def _llamar_ia(client, prompt, max_tokens):
    """temperature baja (no 0 para no matar toda variación de estilo, pero sí
    reducir la chance de que se desvíe de las reglas estrictas del prompt) +
    un reintento ante error transitorio (timeout/rate limit) de la API."""
    import time
    ultimo_error = None
    for intento in range(2):
        try:
            return client.messages.create(
                model="claude-haiku-4-5-20251001", max_tokens=max_tokens,
                temperature=0.2,
                messages=[{"role": "user", "content": prompt}])
        except Exception as e:
            ultimo_error = e
            if intento == 0:
                time.sleep(2)
    raise ultimo_error
def generar_narrativa_ia(datos, api_key, contexto_extra=""):
    try:
        client = anthropic.Anthropic(api_key=api_key, http_client=httpx.Client(follow_redirects=True))
        prompt = f"""ADVERTENCIA: los datos numéricos de este prompt son los ÚNICOS válidos. Si tu memoria de entrenamiento tiene números distintos para este informe, ignoralos completamente.\nSos un analista de comercio exterior de ARCA (Aduana Argentina).
{GLOSARIO}
Redactá los párrafos narrativos para un informe formal sobre el estado de situación del sistema SINTIA.
El informe describe la transmisión anticipada del MIC-DTA entre {datos['pais_nombre']} y Argentina durante {datos['periodo']}.
"""
        try:
            _tot_r = int(datos['total_rechazos'].replace('.',''))
            _tot_g = int(datos['g_total'].replace('.',''))
            pct_str_rechazos = f"{round(100*_tot_r/_tot_g,1):.1f}".replace(".",",") if _tot_g>0 else "0,0"
        except Exception:
            pct_str_rechazos = "N/D"
        prompt += f"""
Datos del período:
- Total ingresos: {datos['g_total']}
- Transmitidos correctamente: {datos['g_trans']} ({datos['pct_trans']})
- No transmitidos: {datos['g_no_trans']} ({datos['pct_no_trans']})
- Tard\u00edos: {datos['g_tardio']} ({datos['pct_tardio']})
- Rechazos: {datos['total_rechazos']} sobre el total de {datos['g_total']} ingresos ({pct_str_rechazos}% del total de ingresos). IMPORTANTE: los rechazos NO son un subconjunto de los transmitidos; son eventos independientes que afectan al total de ingresos. NUNCA calcules rechazos como porcentaje de los transmitidos correctamente.
- Cargados: {datos['c_trans']} trans ({datos['pct_c_trans']}), {datos['c_no_trans']} no trans ({datos['pct_c_no_trans']}), {datos['c_tardio']} tardío ({datos['pct_c_tardio']}) de {datos['c_total']} total
- Lastre: {datos['l_trans']} trans ({datos['pct_l_trans']}), {datos['l_no_trans']} no trans ({datos['pct_l_no_trans']}), {datos['l_tardio']} tardío ({datos['pct_l_tardio']}) de {datos['l_total']} total
- Evoluci\u00f3n mensual: {datos['ev_mensual_texto']}

Gener\u00e1 exactamente 3 bloques separados por "---BLOQUE---":
1. Introducci\u00f3n: exactamente 5 p\u00e1rrafos con esta estructura:
   - P\u00e1rrafo 1: finalidad del informe, mencionar SINTIA, circuito {datos['pais_nombre']}-Argentina, cruzamiento con PAD.
   - P\u00e1rrafo 2: per\u00edodo analizado, total de ingresos, continuidad respecto al a\u00f1o anterior.
   - P\u00e1rrafo 3: rol del PAD y de SINTIA en la transmisi\u00f3n anticipada del MIC-DTA, importancia de la sincronizaci\u00f3n.
   - P\u00e1rrafo 4: resumen de desv\u00edos detectados (% trans, % tard\u00edo, % no trans, cantidad de rechazos).
   - P\u00e1rrafo 5: frase de cierre indicando que a continuaci\u00f3n se desarrollan los indicadores.
2. Estado de situaci\u00f3n (3-4 oraciones, an\u00e1lisis n\u00fameros, comparaci\u00f3n cargado vs lastre, tendencia)
3. Rechazos (1-2 oraciones, tono neutral — describí el volumen y su relación con el total sin calificarlo como "problema sistemático" o "deficiencia grave"; los rechazos pueden ser parte normal de la operatoria)

Estilo: formal, t\u00e9cnico, espa\u00f1ol rioplatense, tono mesurado y profesional. Sin markdown (no uses #, *, **, _, etc.). S\u00ed us\u00e1 '---BLOQUE---' como separador entre los 3 bloques. No repetir el nombre de la secci\u00f3n al inicio del texto. Evitar anglicismos (usar "desempe\u00f1o" en lugar de "performance", etc.). Evit\u00e1 adjetivos alarmistas como "cr\u00edtico", "grave", "alarmante", "preocupante", "sistem\u00e1tico/a" referido a fallas, o "deficiencias graves" — describ\u00ed los hechos en t\u00e9rminos neutrales y, si corresponde, sugerí seguimiento con lenguaje constructivo en lugar de calificar la situaci\u00f3n como un problema.

Cada p\u00e1rrafo del bloque 1 debe tener entre 60 y 90 palabras aproximadamente.

REGLAS ESTRICTAS DE FORMATO:
- El período analizado es EXACTAMENTE {datos['periodo']}. NO menciones meses fuera de ese período. Si los datos llegan hasta junio, NO digas "julio" ni "datos de julio no disponibles".
- SIEMPRE comenzar la introducción con "El presente informe..." — NUNCA omitir el artículo.
- Mantener tercera persona en todo el documento — PROHIBIDO "dispongamos" o cualquier primera persona del plural.
- PROHIBIDO el imperativo de voseo en recomendaciones: NO uses "Coordiná", "Recomendá", "Implementá". Usá infinitivo ("Coordinar", "Implementar") o forma impersonal ("Se recomienda").
- NO incluyas títulos ni etiquetas sueltas como "INTRODUCCIÓN", "ESTADO DE SITUACIÓN", "RECHAZOS", "Bloque 1", "1.", etc. Empezá directo con el texto de cada bloque.
- Usá EXACTAMENTE los porcentajes y números que te di. No recalcules ni redondees diferente.
- Cuando menciones un porcentaje, siempre aclará el denominador: "X% de los cargados" o "X% del total de operaciones" — nunca digas solo "X% del total" si el denominador real es un subconjunto (como cargados o lastre).
- Revisá la ortografía antes de responder. No uses gerundios mal formados ni palabras inventadas."""
        if contexto_extra:
            prompt += contexto_extra
        msg = _llamar_ia(client, prompt, 1800)
        raw_text = msg.content[0].text
        # Garantizar exactamente 3 bloques — si hay más, colapsar los extra al primero
        partes = [p.strip() for p in raw_text.split("---BLOQUE---") if p.strip()]
        partes = [p.replace("---BLOQUE---","").strip() for p in partes]
        partes = [p for p in partes if p]
        if len(partes) > 3:
            # Colapsar bloques extra al primero
            partes = ["\n\n".join(partes[:-2]), partes[-2], partes[-1]]
        elif len(partes) < 3:
            # Rellenar bloques faltantes con cadena vacía
            while len(partes) < 3: partes.append("")
        # Limpiar encabezados sobrantes
        encabezados = ["INTRODUCCIÓN", "INTRODUCCION", "ESTADO DE SITUACIÓN", "ESTADO DE SITUACION",
                       "RECHAZOS", "BLOQUE 1", "BLOQUE 2", "BLOQUE 3", "Bloque 1", "Bloque 2", "Bloque 3"]
        def limpiar_encabezado(texto):
            lineas = texto.split("\n")
            while lineas and lineas[0].strip().rstrip(":").strip() in encabezados:
                lineas.pop(0)
            return "\n".join(lineas).strip()
        result = [limpiar_encabezado(p) for p in partes]
        result = [p for p in result if p]
        return [limpiar_salida_ia(b) if b else b for b in result]
    except Exception:
        return None
def calcular_frases(datos):
    """
    Recibe el mismo dict que va a la IA y devuelve frases comparativas
    pre-calculadas con dirección y números correctos garantizados.
    """
    def n_(v):
        try: return int(str(v).replace(".", "").replace(",", ""))
        except: return 0
    def f_(v): return str(v)

    frases = {}

    # ── Volumen ─────────────────────────────────────────────────────
    ult_t  = n_(datos.get("ult_total", 0))
    ant_t  = n_(datos.get("ant_total", 0))
    if ant_t > 0:
        var_vol = round((ult_t - ant_t) / ant_t * 100, 1)
        dir_vol = _dir(var_vol, "incremento", "caída", "nivel similar")
        frases["frase_volumen_var"] = (
            f"una {dir_vol} de {_pp(var_vol)}% respecto a {datos.get('mes_ant_nombre','el mes anterior')} "
            f"({f_(datos.get('ant_total',''))} operaciones)"
        )
    else:
        frases["frase_volumen_var"] = "sin datos del mes anterior para comparar"

    # ── Transmisión total ────────────────────────────────────────────
    pct_ult = datos.get("ult_pct_trans", "0,0%")
    pct_ant = datos.get("ant_pct_trans", "0,0%")
    def to_f(s):
        try: return float(str(s).replace("%","").replace(",","."))
        except: return 0.0
    diff_trans = round(to_f(pct_ult) - to_f(pct_ant), 1)
    dir_trans  = _dir(diff_trans, "mejorando", "retrocediendo", "sin variación")
    if abs(diff_trans) < 0.05:
        frases["frase_trans_var"] = (
            f"sin variaci\u00f3n respecto a {datos.get('mes_ant_nombre','')} "
            f"(de {pct_ant} a {pct_ult})"
        )
    else:
        frases["frase_trans_var"] = (
            f"{dir_trans} {_pp(diff_trans)} pp respecto a {datos.get('mes_ant_nombre','')} "
            f"(de {pct_ant} a {pct_ult})"
        )

    # ── Cargados ─────────────────────────────────────────────────────
    carg_tot = n_(datos.get("ult_carg_tot_n", 0))
    if carg_tot > 0:
        frases["frase_carg_detalle"] = (
            f"{datos.get('ult_pct_trans_carg','')} transmitidos ({f_(datos.get('ult_carg_trans_n',''))} de {f_(datos.get('ult_carg_tot_n',''))}), "
            f"{datos.get('ult_pct_no_trans_carg','')} no transmitidos ({f_(datos.get('ult_carg_no_trans_n',''))}), "
            f"{datos.get('ult_pct_tardio_carg','')} tardíos ({f_(datos.get('ult_carg_tardio_n',''))})"
        )
    else:
        frases["frase_carg_detalle"] = "sin datos de cargados"

    # ── Lastre — el más crítico ──────────────────────────────────────
    last_tr  = n_(datos.get("ult_last_trans_n", 0))
    last_tot = n_(datos.get("ult_last_tot_n", 0))

    # Tasa de lastre no transmitido: mes actual vs anterior
    pct_last_trans_ult = to_f(datos.get("ult_pct_trans_last", "0,0%"))
    pct_last_trans_ant = to_f(datos.get("ant_last_pct_trans", "0,0%"))
    diff_last_trans = round(pct_last_trans_ult - pct_last_trans_ant, 1)

    pct_last_nt_ult = to_f(datos.get("ult_pct_no_trans_last", "0,0%"))
    # Calculamos % no trans del mes anterior directamente
    ant_last_tr  = n_(datos.get("ant_last_trans_n", 0))
    ant_last_tot = n_(datos.get("ant_last_tot_n", 0))
    ant_last_td  = n_(datos.get("ant_last_tardio_n", 0)) if "ant_last_tardio_n" in datos else 0
    # Usar ant_last_notrans_n directo si está disponible, evita errores por tardíos no informados
    ant_last_nt_direct = n_(datos.get("ant_last_notrans_n", "0"))
    if ant_last_nt_direct > 0 and ant_last_tot > 0:
        pct_last_nt_ant = round(100 * ant_last_nt_direct / ant_last_tot, 1)
    elif ant_last_tot > 0:
        pct_last_nt_ant = round(100 * (ant_last_tot - ant_last_tr - ant_last_td) / ant_last_tot, 1)
    else:
        pct_last_nt_ant = 0.0
    diff_last_nt = round(pct_last_nt_ult - pct_last_nt_ant, 1)

    if last_tot > 0:
        # Frase de detalle de lastre
        if last_tr == 0:
            frases["frase_lastre_detalle"] = (
                f"ninguna operación lastre fue transmitida (0 de {f_(datos.get('ult_last_tot_n',''))}), "
                f"{datos.get('ult_pct_no_trans_last','')} no transmitidas ({f_(datos.get('ult_last_notrans_n',''))})"
            )
        else:
            frases["frase_lastre_detalle"] = (
                f"{datos.get('ult_pct_trans_last','')} transmitido ({f_(datos.get('ult_last_trans_n',''))} de {f_(datos.get('ult_last_tot_n',''))}), "
                f"{datos.get('ult_pct_no_trans_last','')} no transmitido ({f_(datos.get('ult_last_notrans_n',''))}), "
                f"{datos.get('ult_pct_tardio_last','')} tardío ({f_(datos.get('ult_last_tardio_n',''))})"
            )

        # Frase de variación de lastre TRANSMITIDO
        dir_last = _dir(diff_last_trans, "mejoró", "retrocedió", "se mantuvo")
        ant_last_pct = datos.get("ant_last_pct_trans", "N/D")
        if abs(diff_last_trans) < 0.05:
            frases["frase_lastre_trans_var"] = (
                f"sin variaci\u00f3n respecto a {datos.get('mes_ant_nombre','')} "
                f"(de {ant_last_pct} a {datos.get('ult_pct_trans_last','')})"
            )
        else:
            frases["frase_lastre_trans_var"] = (
                f"{dir_last} {_pp(abs(diff_last_trans))} pp respecto a {datos.get('mes_ant_nombre','')} "
                f"(de {ant_last_pct} a {datos.get('ult_pct_trans_last','')})"
            )

        # Frase de variación de lastre NO TRANSMITIDO
        if pct_last_nt_ant > 0:
            dir_last_nt = _dir(diff_last_nt, "subieron", "bajaron", "se mantuvieron")
            frases["frase_lastre_notrans_var"] = (
                f"los no transmitidos en lastre {dir_last_nt} {_pp(abs(diff_last_nt))} pp "
                f"(de {str(pct_last_nt_ant).replace('.',',')}% a "
                f"{str(pct_last_nt_ult).replace('.',',')}%)"
            )
        else:
            frases["frase_lastre_notrans_var"] = (
                f"los no transmitidos en lastre representaron {datos.get('ult_pct_no_trans_last','')} "
                f"({f_(datos.get('ult_last_notrans_n',''))} operaciones)"
            )
    else:
        frases["frase_lastre_detalle"]    = "sin operaciones lastre en el período"
        frases["frase_lastre_trans_var"]  = "N/D"
        frases["frase_lastre_notrans_var"] = "N/D"

    # ── Rechazos ─────────────────────────────────────────────────────
    rech_ult = n_(datos.get("ult_rechazos", 0))
    rech_ant = n_(datos.get("ant_rechazos", 0))
    if rech_ant > 0:
        var_rech = rech_ult - rech_ant
        dir_rech = _dir(-var_rech, "disminución", "aumento", "nivel estable")  # invertido: menos rechazos = mejora
        pct_var_rech = round(abs(var_rech) / rech_ant * 100, 0)
        frases["frase_rech_var"] = (
            f"{dir_rech} de {fmt(abs(var_rech))} rechazos respecto a "
            f"{datos.get('mes_ant_nombre','')} ({f_(datos.get('ant_rechazos',''))}), "
            f"equivalente al {int(pct_var_rech)}%"
        )
    else:
        frases["frase_rech_var"] = f"{rech_ult} rechazos en el período"

    return frases


# ── Post-procesamiento de salida IA (Pasos 2, 3) ────────────────────────────────
# Correcciones fijas: typos conocidos y problemas recurrentes
_CORRECCIONES_FIJAS = [
    # Typos de siglas
    (r"\bPATIA\b",                    "PATAI"),
    (r"\bOFTAIS\b",                   "OFTAI"),
    # Guión faltante en subtítulo BR
    (r"sin transmisión situación crítica", "sin transmisión \u2014 situación crítica"),
    # Headings markdown que la IA introduce en lugar de solo negrita
    (r"^#{1,6}\s+\*\*",             "**",     re.MULTILINE),
    (r"^#{1,6}\s+",                    "",       re.MULTILINE),
    # Anglicismos recurrentes
    (r"\bperformance\b",              "desempeño"),
    (r"\bfeedback\b",                 "retroalimentación"),
    # Errores ortográficos conocidos
    (r"\bingressad",                   "ingresad"),
    (r"\bsosteniéndose\b",            "sosteniéndose"),
    # Fix 2: "intersemestral" para comparación mensual
    (r"\bIntersemestral\b",           "intermensual"),
    (r"\bintersemestral\b",           "intermensual"),
    # Fix 3: singular de "rechazo" cuando el sujeto es plural
    (r"(\d+) rechazo\)",              r"\1 rechazos)"),
    (r"(\d+) rechazo,",               r"\1 rechazos,"),
    (r"(\d+) rechazo\.",               r"\1 rechazos."),
    # Fix 5: "Cartas de Porte Internacional por Carretera" → singular
    (r"Cartas de Porte Internacional por Carretera", "Carta de Porte Internacional por Carretera"),
]
def limpiar_salida_ia(texto):
    """
    Paso 2: elimina formato markdown espurio.
    Paso 3: aplica correcciones fijas de typos y siglas.
    """
    if not texto:
        return texto
    for item in _CORRECCIONES_FIJAS:
        patron, reemplazo = item[0], item[1]
        flags = item[2] if len(item) > 2 else 0
        texto = re.sub(patron, reemplazo, texto, flags=flags)
    # Limpiar líneas vacías triples o más
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()
def generar_conclusion_ia(datos, api_key, contexto_extra=""):
    try:
        client = anthropic.Anthropic(api_key=api_key, http_client=httpx.Client(follow_redirects=True))
        interanual = ""
        if datos.get("tiene_interanual"):
            interanual = f"\nComparativo interanual \u2014 mismo per\u00edodo {datos['anio_ant']}:\n- Total: {datos['g_total_ant']} | Trans: {datos['pct_trans_ant']} | No trans: {datos['pct_no_trans_ant']} | Tard\u00edo: {datos['pct_tardio_ant']}"
        prompt = f"""Sos un analista de comercio exterior de ARCA (Aduana Argentina).
{GLOSARIO}
Redactá la secci\u00f3n "Conclusiones y estado actual" de un informe SINTIA para el circuito {datos['pais_nombre']}-Argentina.

\u2550\u2550\u2550 DATOS DEL \u00daLTIMO MES: {datos['mes_ult_nombre']} \u2550\u2550\u2550
Volumen: {datos['ult_total']} operaciones ({datos['ult_impo']} importaciones, {datos['ult_expo']} exportaciones)
Transmisi\u00f3n: {datos['ult_trans']} transmitidos ({datos['ult_pct_trans']}), {datos['ult_no_trans']} no transmitidos ({datos['ult_pct_no_trans']}), {datos['ult_tardio']} tard\u00edos ({datos['ult_pct_tardio']})
  \u2014 Cargados (usar EXACTAMENTE): {datos['frase_carg_detalle']}
  \u2014 Lastre (usar EXACTAMENTE estos n\u00fameros y direcci\u00f3n): {datos['frase_lastre_detalle']}
  \u2014 Variaci\u00f3n lastre transmitido (usar EXACTAMENTE): {datos['frase_lastre_trans_var']}
  \u2014 Variaci\u00f3n lastre no transmitido (usar EXACTAMENTE): {datos['frase_lastre_notrans_var']}
REGLA ABSOLUTA: las cuatro frases anteriores fueron calculadas por el sistema con los valores y direcci\u00f3n correctos. Incorporalas en la redacci\u00f3n sin modificar n\u00fameros, porcentajes ni el sentido de la variaci\u00f3n. NUNCA recalcules direcci\u00f3n de cambio de lastre por tu cuenta.
Rechazos del mes: total={datos['ult_rechazos']}, duplicados={datos['ult_rech_duplicados']}, operativos reales={datos['ult_rech_operativos']}. Variación (usar EXACTAMENTE): {datos['frase_rech_var']}. NO recalcules ni reformules la variación de rechazos.
Rechazos operativos (sin duplicados): {datos['ult_rech_operativos']}
Top categor\u00edas (SOLO estas, no inventes otras ni cambies los n\u00fameros): {datos['ult_rech_top_cats']}
Total rechazos verificado: {datos['ult_rech_total_check']} (us\u00e1 EXACTAMENTE este n\u00famero)

\u2550\u2550\u2550 MES ANTERIOR: {datos['mes_ant_nombre']} \u2550\u2550\u2550
Volumen: {datos['ant_total']} operaciones
Transmisi\u00f3n: {datos['ant_trans']} ({datos['ant_pct_trans']}), no trans {datos['ant_no_trans']} ({datos['ant_pct_no_trans']}), tard\u00edos {datos['ant_tardio']} ({datos['ant_pct_tardio']})
Rechazos: {datos['ant_rechazos']} — ESTE ES EL ÚNICO NÚMERO VÁLIDO PARA {datos['mes_ant_nombre']}. Si recordás otro número de versiones anteriores del informe, ignoralo. El mes anterior tuvo exactamente {datos['ant_rechazos']} rechazos totales

\u2550\u2550\u2550 ACUMULADO DEL PER\u00cdODO ({datos['periodo']}, {datos['cant_meses']} meses, promedio {datos['promedio_mensual']} ops/mes) \u2550\u2550\u2550
Total: {datos['g_total']} | Trans: {datos['g_trans']} ({datos['pct_trans']}) | No trans: {datos['g_no_trans']} ({datos['pct_no_trans']}) | Tard\u00edo: {datos['g_tardio']} ({datos['pct_tardio']})
{interanual}

REGLAS PARA LAS CALIFICACIONES EN SUBTÍTULOS:\n- Transmisión anticipada: basá la calificación EXCLUSIVAMENTE en la variación del % de transmitidos vs mes anterior.\n  {datos['var_trans_pp']} pp = la diferencia. Regla:\n  * más de +5pp: \"mejora significativa\"\n  * +1 a +5pp: \"leve mejora\"\n  * -1 a +1pp: \"nivel estable\" (si ambos meses tienen 0,0% de transmisión, usá \"sin transmisión en el período\" en lugar de cualquier calificación de mejora/retroceso)\n  * -1 a -5pp: \"leve retroceso\"\n  * menos de -5pp: \"retroceso\"\n  CASO ESPECIAL: si ult_pct_trans es \"0,0%\" no uses frases como \"mejora\" si no hubo variación real. Describí la situación en términos neutrales, sin calificarla de crítica o de fallo — puede responder a la dinámica propia del circuito en ese período. Tampoco uses \"cayeron al 100%\" ni \"subieron al 0%\" — decí directamente los valores absolutos.\n- Rechazos: si bajaron vs mes anterior → mejora; si subieron → variación a revisar; siempre mencionar los duplicados.\n- La dirección del texto debe coincidir con la dirección de los números: si un indicador bajó, no uses \"repunte\" ni \"aumento\"; si subió, no uses \"baja\" ni \"caída\".\n- Para rechazos: si subieron de un mes a otro, descríbelo como un aumento que conviene revisar, sin necesidad de calificarlo como grave. Si bajaron los operativos reales, es una mejora.\n- Porcentajes mes-mes: si el % actual es mayor al del mes anterior, es una mejora; si es menor, es un retroceso. Verificá la dirección antes de escribir.\n\nEstilo general: mantené un tono profesional, mesurado y descriptivo, sin adjetivos alarmistas (evitá palabras como \"crítico\", \"grave\", \"alarmante\", \"fallo sistémico\", \"preocupante\"). Describí los hechos y, cuando corresponda, sugerí seguimiento con un lenguaje constructivo.\n\nRedactá la sección con esta estructura EXACTA (usá estos títulos en negrita):\n\n**Impacto del mes de {datos['mes_ult_nombre']} — {datos['pais_nombre']}/Argentina (SINTIA)**\n\n**Volumen operativo**\n[Párrafo: total operaciones, desglose impo/expo, comparación con mes anterior. Si junio tiene volumen notoriamente menor, aclará que es mes parcial.]\n\n**Transmisión anticipada — [calificación según regla arriba, en minúsculas]**\nESCRIBI el siguiente p\u00e1rrafo usando EXACTAMENTE estas frases del sistema (no las reformules). Los datos entre [corchetes] son FIJOS y deben aparecer textualmente:\nLa transmisi\u00f3n en {datos["mes_ult_nombre"]} alcanz\u00f3 {datos["ult_pct_trans"]} ({datos["ult_trans"]} de {datos["ult_total"]} operaciones), {datos["frase_trans_var"]}. Cargados: {datos["frase_carg_detalle"]}. Lastre: {datos["frase_lastre_detalle"]}. {datos["frase_lastre_trans_var"]}. {datos["frase_lastre_notrans_var"]}. Acumulado semestral: {datos["pct_trans"]}.\nPod\u00e9s agregar 1-2 oraciones de contexto anal\u00edtico pero SIN modificar los datos anteriores.\n\n**Rechazos — [calificación breve en minúsculas]**\n[Párrafo: total, duplicados vs operativos reales, top categorías]\n\n**Conclusión**\n[2-3 observaciones priorizadas con recomendaciones en forma impersonal o infinitivo, en tono constructivo. PROHIBIDO el imperativo voseante: NO uses "Coordiná", "Recomendá", "Implementá" ni similares. Usá infinitivo ("Coordinar con...", "Se recomienda...", "Conviene dar seguimiento a...") o forma impersonal.]\n\nEstilo: formal, técnico, español rioplatense, tono mesurado y profesional. Sin markdown extra, solo títulos en negrita. Evitar anglicismos. Revisá la ortografía y la gramática antes de responder. Palabras comunes mal escritas a evitar: "ingressadas" (correcto: "ingresadas"), "campña" (correcto: "campaña"), "sosteniéndose" (no "sosteniene"). Verificá la concordancia de número: sujetos plurales requieren verbos plurales (ej: "los despachantes dispongan", no "disponga"). Los meses se escriben en minúscula en español (enero, febrero... no Enero, Febrero). El período analizado es {datos['periodo']} — NO menciones meses fuera de ese rango. "trimestre final" no tiene sentido en un informe semestral — no lo uses. PROHIBIDO hacer proyecciones temporales con años futuros ("junio 2027", "primer trimestre 2027", etc.) — si mencionás seguimiento futuro, usá frases como "en los próximos meses" o "durante el segundo semestre de {datos['anio']}". PROHIBIDO usar frases como "un aumento de -N" o "una disminución de +N" — si el valor bajó usá "disminución de N" (positivo), si subió usá "aumento de N" (positivo). DISTINGUIR interanual de intermensual: "interanual" = comparación con el mismo período del año anterior (solo si tiene_interanual=True); "intermensual" o "respecto al mes anterior" = comparación con el mes inmediatamente anterior. PROHIBIDO usar "interanual" para comparaciones mes-a-mes."""
        if contexto_extra:
            prompt += contexto_extra
        msg = _llamar_ia(client, prompt, 1600)
        return msg.content[0].text.strip()
    except Exception:
        return None
def verificar_narrativa_denominadores(texto, pcts_cargados, pcts_lastre, log_fn):
    """El bloque 'Estado de situación' (generar_narrativa_ia) no pasaba por
    ninguna verificación numérica posterior — a diferencia de la Conclusión,
    que sí la tiene. Esto permitía que un porcentaje de lastre quedara mal
    etiquetado como "de los cargados" (o viceversa) sin que nada lo corrija.
    Corrige quirúrgicamente el denominador cuando no coincide con el valor."""
    if not texto:
        return texto
    correcciones = 0
    mapeo = [(v, "de los cargados") for v in pcts_cargados if v] + \
            [(v, "de lastre") for v in pcts_lastre if v]
    for valor, etiqueta_correcta in mapeo:
        patron = re.escape(valor) + r"(\s+de\s+(?:los\s+cargados|lastre|el\s+lastre))"
        def _fix(m, correcta=etiqueta_correcta):
            nonlocal correcciones
            if correcta not in m.group(1):
                correcciones += 1
                return valor + " " + correcta
            return m.group(0)
        texto = re.sub(patron, _fix, texto)
    if correcciones:
        log_fn(f"  \u26a0 Estado de situaci\u00f3n: {correcciones} denominador(es) cargados/lastre corregido(s)")
    return texto
def verificar_conclusion(texto, datos_esperados, log_fn):
    """
    Verifica números y direcciones en el texto generado por la IA.
    Loguea discrepancias sin bloquear. Retorna (texto, hay_errores).
    """
    if not texto:
        return texto, False
    import re as _re

    def _n(v):
        try: return int(str(v).replace(".", "").replace(",", ""))
        except: return None

    errores = []

    # 1. Verificar absoluto lastre no transmitido y corregir quirúrgicamente si falta
    esp_nt = _n(datos_esperados.get("ult_last_notrans_n"))
    esp_nt_fmt = datos_esperados.get("ult_last_notrans_n", "")  # ya formateado con puntos
    esp_nt_pct = datos_esperados.get("ult_pct_no_trans_last", "")
    if esp_nt and esp_nt > 0:
        # a) Verificar si hay número incorrecto en el texto
        for m in _re.finditer(r"(?<![\d\.])(\d[\d\.]{2,9})(?![\d\.])\s*(?:operaciones?\s*)?no\s+transmitidas?\s+(?:en\s+)?lastre|lastre[^.]{0,80}(?<![\d\.])(\d[\d\.]{2,9})(?![\d\.])\s*no\s+transmitid", texto, _re.IGNORECASE):
            encontrado = _n(m.group(1) or m.group(2))
            if encontrado and abs(encontrado - esp_nt) > 1:
                errores.append(f"lastre_no_trans: texto tiene {encontrado}, esperado {esp_nt}")
        # b) Corrección quirúrgica si el absoluto no figura en el texto
        if esp_nt_fmt and esp_nt_fmt not in texto:
            if esp_nt_pct and esp_nt_pct in texto:
                # Insertar absoluto junto al porcentaje la primera vez que aparece
                texto = texto.replace(esp_nt_pct, f"{esp_nt_pct} ({esp_nt_fmt} operaciones)", 1)
                errores.append(f"lastre_no_trans_omitido: insertado {esp_nt_fmt} junto a {esp_nt_pct}")
            else:
                errores.append(f"lastre_no_trans_omitido: {esp_nt_fmt} ausente del texto")

    # 2. Verificar dirección de variación de lastre
    frase_var = datos_esperados.get("frase_lastre_trans_var", "")
    if "mejoró" in frase_var:
        if _re.search(r"retrocedi[oó]|deterioro|ca[ií]da.{0,40}lastre|empeor.{0,40}lastre", texto, _re.IGNORECASE):
            errores.append("dir_lastre: texto dice retroceso pero la variación fue mejora")
    elif "retrocedió" in frase_var:
        if _re.search(r"mejor[oó].{0,40}lastre|increment.{0,40}lastre", texto, _re.IGNORECASE):
            errores.append("dir_lastre: texto dice mejora pero la variación fue retroceso")

    # 3. Verificar dirección de rechazos
    rech_u = _n(datos_esperados.get("ult_rechazos"))
    rech_a = _n(datos_esperados.get("ant_rechazos"))
    if rech_u is not None and rech_a is not None and rech_a > 0:
        if rech_u < rech_a:
            if _re.search(r"retroceso.{0,30}rechazo|aument.{0,30}rechazo", texto, _re.IGNORECASE):
                errores.append("dir_rechazos: texto dice retroceso/aumento pero bajaron")
        else:
            if _re.search(r"mejor[oó].{0,30}rechazo|reducci[oó]n.{0,30}rechazo", texto, _re.IGNORECASE):
                errores.append("dir_rechazos: texto dice mejora/reducción pero subieron")

    if errores:
        for e in errores:
            log_fn(f"  \u26a0 Verificaci\u00f3n: {e}")
        return texto, True
    return texto, False
