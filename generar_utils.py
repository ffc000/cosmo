"""
generar_utils.py — Constantes y helpers puros compartidos por el resto de
los módulos generar_*.py (sin dependencias pesadas: nada de docx/matplotlib/
anthropic acá, para que cualquier submódulo pueda importar esto sin arrastrar
las librerías de generación de documentos).
Extraído de generar.py como parte de la Fase 3 de profesionalización
(separar responsabilidades dentro de generar.py, igual que ya se hizo con
los blueprints de app.py).
"""
from datetime import datetime

# ── Constantes ─────────────────────────────────────────────────────────────────
PAISES = {"BO":"Bolivia","PY":"Paraguay","BR":"Brasil","CL":"Chile","UY":"Uruguay"}
# Solo para el informe consolidado (Fase 7): ahí sí interesa contar las
# operaciones cuyo MIC corresponde a Argentina. En el resto de SINTIA (el
# informe por país, el selector de "país emisor", etc.) no tiene sentido
# -- ese circuito es específicamente entre un país limítrofe y Argentina,
# nunca "Argentina" como emisor -- por eso queda en un dict aparte y no se
# agrega a PAISES directamente. AR va al final a propósito: si un MIC ya
# matcheó BO/PY/BR/CL/UY, ese resultado tiene prioridad (mismo criterio que
# ya usaba el resto del CASE, no cambia comportamiento existente).
PAISES_CONSOLIDADO = dict(PAISES, AR="Argentina")
MESES  = {"01":"Enero","02":"Febrero","03":"Marzo","04":"Abril","05":"Mayo","06":"Junio",
           "07":"Julio","08":"Agosto","09":"Septiembre","10":"Octubre","11":"Noviembre","12":"Diciembre"}
C_TRANS="#1F7DC4"; C_NO_TRANS="#C0392B"; C_TARDIO="#E67E22"
C_CARGADO="#2E86AB"; C_LASTRE="#A8DADC"

# Glosario compartido entre generar_narrativa_ia y generar_conclusion_ia —
# antes estaba duplicado en los dos prompts con redacciones levemente distintas.
GLOSARIO = """GLOSARIO OBLIGATORIO — usá EXACTAMENTE estas siglas y denominaciones, sin inventar expansiones alternativas:
- SINTIA: escribilo siempre como "SINTIA" sin expandir
- MIC-DTA: "Manifiesto Internacional de Cargas - Declaración de Tránsito Aduanero"
- MIC: "Manifiesto Internacional de Cargas" (NO "Manifiesto de Importación de Carga")
- DTA: "Declaración de Tránsito Aduanero" (NO "Declaración Transitoria Aduanera")
- PAD: la primera vez que lo uses escribí "Portal Aduanero (PAD)", luego solo "PAD"
- ARCA: "Agencia de Recaudación y Control Aduanero"
- CRT: "Carta de Porte Internacional por Carretera"
- Terminología Argentina OBLIGATORIA: usar "despachantes de aduana" (NO "agentes aduanales", "agentes aduaneros" ni otras variantes foráneas); "declarante" o "transportista" según el contexto (documento de transporte internacional)
- INDNCM: "indicador de Nomenclatura Común del Mercosur"
- PATAI: "Presentación Anticipada de Transportes de Ingreso" (evento aduanero)
- OFTAI: "Oficialización de Transportes de Ingreso" (evento aduanero)"""
UMBRAL_VERDE=60.0; UMBRAL_AMARILLO=30.0

CAT_LIKE = {
    "ERROR ATRIBUTO/PARAMETRO INVALIDO":"%ERROR ATRIBUTO/PARAMETRO INVALIDO%",
    "EVENTO DISTINTO DE PATAI/OFTAI":"%EVENTO DISTINTO DE  PATAI O OFTAI%",
    "EVENTO YA RECIBIDO":"%EVENTO YA RECIBIDO%",
    "NRO DE MIC INEXISTENTE":"%NRO DE MIC INEXISTENTE%",
    "NRO DE MIC EXISTENTE":"%Nro de Mic existente%",
    "CONTENEDOR_VACIO":"%CONTENEDORVACIO%",
    "PAIS_DE_PASO_DUPLICADO":"%PAIS DE PASO DUPLICADO%",
    "FECHA_DEL_EVENTO":"%FECHA DEL EVENTO %",
    "CODCIUPART":"%CODCIUPART%","CODCIUENT":"%CODCIUENT%",
    "PAISESDEPASO.CODCIUSAL":"%PAISESDEPASO%CODCIUSAL%","CODCIUSAL":"%CODCIUSAL%",
    "VEHICULO":"%VEHICULO%","CONTENEDOR":"%CONTENEDOR%","CONSIGNATARIO":"%CONSIGNATARIO%",
    "CODDIVISASEG":"%CODDIVISASEG%","CARTA_PORTE_DUPLICADO":"%PORTE%DUPLICADO%",
    "CARTA PORTE":"%PORTE%","PAISESDEPASO.CODADUENT":"%PAISESDEPASO.CODADUENT%",
    "PAISESDEPASO":"%PAISESDEPASO%","CODADUEMI":"%CODADUEMI%","CODADUSAL":"%CODADUSAL%",
    "CODADUPART":"%CODADUPART%","FECHLLEGPREV":"%FECHLLEGPREV%",
    "PESOBRUTOTOTAL":"%PESOBRUTOTOTAL%","DESCRUTITINERARIOS":"%DESCRUTITINERARIOS%",
    "TIPDOCIDENT":"%TIPDOCIDENT%","PAISDEST.CODCIUDEST":"%PAISDEST.CODCIUDEST%",
    "PAISDEST.CODADUDEST":"%PAISDEST.CODADUDEST%","PAISDEST.CODADUENT":"%PAISDEST.CODADUENT%",
    "DESTINACION":"%DESTINACION%","INDNCM":"%INDNCM%",
    "CONDUCTOR.NOMBRE":"%CONDUCTOR.NOMBRE%","CRT":"%CRT%","OTROS":"%%",
}

def fmt(v):
    try: return f"{int(v or 0):,}".replace(",",".")
    except: return str(v or 0)
def pct(a, total):
    if not total: return "0,0%"
    return f"{100*float(a)/float(total):.1f}%".replace(".",",")
def pct_f(a, total):
    if not total: return 0.0
    return round(100*float(a)/float(total), 1)
def n(v): return int(v or 0)
def pl(cantidad, singular, plural=None):
    """Pluraliza según cantidad -- reemplaza los "país(es)"/"variable(s)"
    con paréntesis sin resolver que quedaban en el texto de los informes
    (encontrado en el consolidado, 17/07/2026). Uso: f"{cant} {pl(cant,
    'variable', 'variables')}"."""
    if plural is None:
        plural = singular + "s"
    return singular if n(cantidad) == 1 else plural
def periodo_texto(anio, mes_d, mes_h):
    if mes_d == mes_h: return f"{MESES[mes_d]} {anio}"
    return f"{MESES[mes_d]} \u2013 {MESES[mes_h]} {anio}"
def mes_label(periodo):
    yy, mm = periodo.split("-")
    return f"{MESES.get(mm,mm)[:3]} {yy[2:]}"
def mes_label_largo(periodo):
    yy, mm = periodo.split("-")
    return f"{MESES.get(mm,mm)} {yy}"
def ultimo_mes_completo():
    hoy = datetime.today()
    if hoy.month == 1: return "12", str(hoy.year-1)
    return str(hoy.month-1).zfill(2), str(hoy.year)
def color_semaforo(pct_val):
    v = float(pct_val)
    if v >= UMBRAL_VERDE: return "C6EFCE"
    if v >= UMBRAL_AMARILLO: return "FFEB9C"
    return "FFC7CE"
def _dir(diff, positivo="mejoró", negativo="retrocedió", neutro="se mantuvo"):
    """Elige el verbo correcto según la dirección del cambio."""
    if diff > 0.05: return positivo
    if diff < -0.05: return negativo
    return neutro
def _pp(diff):
    """Formatea una diferencia en pp con signo y coma decimal."""
    return f"{abs(diff):.1f}".replace(".", ",")

def formatear_demora(dias):
    """Convierte una demora en días (float, con fracción) a un texto legible
    en horas/minutos/segundos -- 0.75 días no dice nada de un vistazo, pero
    '18h 00m 00s' sí. Duplicada a propósito de _formatear_demora en app.py
    (Fase 9: se necesita también desde generar_documento.py/generar_queries.py,
    que son Flask-agnósticos y no pueden importar de app.py sin crear una
    dependencia circular). Si se cambia uno, cambiar el otro."""
    if dias is None:
        return None
    total_seg = round(dias * 86400)
    h, resto = divmod(total_seg, 3600)
    m, s = divmod(resto, 60)
    return f"{h}h {m:02d}m {s:02d}s"
