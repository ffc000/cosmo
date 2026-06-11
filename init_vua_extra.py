import sqlite3

HIST_DB = "/data/historial.db"
con = sqlite3.connect(HIST_DB)

# ── Tabla vua_riesgos ─────────────────────────────────────────────────────────
con.execute("""CREATE TABLE IF NOT EXISTS vua_riesgos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo TEXT NOT NULL,
    titulo TEXT NOT NULL,
    descripcion TEXT NOT NULL,
    mitigacion TEXT NOT NULL,
    probabilidad TEXT DEFAULT 'Media',
    impacto TEXT DEFAULT 'Medio',
    activo INTEGER DEFAULT 1,
    orden INTEGER DEFAULT 99
)""")

# ── Tabla vua_correos_rapidos ─────────────────────────────────────────────────
con.execute("""CREATE TABLE IF NOT EXISTS vua_correos_rapidos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    etiqueta TEXT NOT NULL,
    instruccion TEXT NOT NULL,
    activo INTEGER DEFAULT 1,
    orden INTEGER DEFAULT 99
)""")

# ── Tabla vua_info (textos informativos de paneles: normativa, bpmn, etc.) ────
con.execute("""CREATE TABLE IF NOT EXISTS vua_info (
    clave TEXT PRIMARY KEY,
    titulo TEXT NOT NULL,
    contenido TEXT NOT NULL,
    modificado TEXT DEFAULT (datetime('now'))
)""")

con.commit()

# ── Riesgos ───────────────────────────────────────────────────────────────────
riesgos = [
    ("5.1", "Ausencia de marco normativo para nuevos flujos",
     "La normativa vigente (RG 3596/2014 y RG 4517/2019) no contempla: la participacion de un intermediario en la cadena XML, la informacion anticipada para exportacion, ni la informacion anticipada para manifiestos desconsolidados de importacion.",
     "Revision normativa conjunta con VUCEA y areas legales. Ningun eje afectado deberia avanzar a desarrollo hasta contar con el analisis.",
     "Alta", "Alto", 1),
    ("5.2", "Indefinicion del rol juridico de VUCEA como intermediario",
     "Si VUCEA actua como intermediario en la transmision de XML: no esta definido si actua como mandatario o como sujeto obligado, sobre quien recae la sancion ante errores, ni que instrumento juridico regula esa relacion.",
     "Incorporar como agenda prioritaria con participacion de areas legales de DGA y VUCEA. Resultado esperado: dictamen que defina rol, responsabilidades y regimen sancionatorio.",
     "Alta", "Alto", 2),
    ("5.3", "Dependencia de decisiones externas",
     "Varios puntos abiertos requieren decisiones que exceden DI REPA: definicion del acceso al tablero de vuelos (DI ADEZ), resolucion normativa sobre VUCEA (areas legales), correccion de formularios (VUCEA). Demoras pueden generar retrabajos y bloqueo del proyecto.",
     "Fijar frecuencia de reuniones regular de la mesa de trabajo para mantener trazabilidad de compromisos.",
     "Media", "Medio", 3),
    ("5.4", "Riesgo reputacional ante auditoria Aduana de Ezeiza",
     "La Aduana de Ezeiza informo a su auditoria interna que la falta de solucion tecnologica para programacion de vuelos se resuelve via VUA. Si el proyecto sufre demoras o la modalidad de acceso al tablero no se define, la DGA queda expuesta ante la auditoria.",
     "Priorizar la definicion de la modalidad de acceso al tablero de vuelos (GDE o web service) en instancia DI REPA / DI SADU a la brevedad.",
     "Media", "Medio", 4),
]

for codigo, titulo, desc, mitig, prob, imp, orden in riesgos:
    con.execute("""INSERT OR IGNORE INTO vua_riesgos 
        (codigo, titulo, descripcion, mitigacion, probabilidad, impacto, activo, orden)
        VALUES (?,?,?,?,?,?,1,?)""",
        (codigo, titulo, desc, mitig, prob, imp, orden))
    print(f"Riesgo {codigo}: OK")

# ── Correos rápidos ───────────────────────────────────────────────────────────
correos = [
    ("Solicitar XFWB a VUCEA",
     "Escribi un correo formal a Fabiola Cochello de VUCEA solicitando los formularios XFWB corregidos con el campo OCI del CUIT consignatario y los campos faltantes identificados en el analisis tecnico",
     1, 1),
    ("Update a Martin — SENASA",
     "Escribi un correo informal a Martin Macias para contarle el estado de la reunion con SENASA y los proximos pasos de la integracion en PAD",
     1, 2),
    ("Convocar reunion MANE",
     "Escribi un correo formal convocando a una reunion de mesa de trabajo sobre el MANE con DI ADEZ y VUCEA para la semana proxima",
     1, 3),
    ("Observaciones XFWB a VUCEA",
     "Escribi un correo formal a VUCEA comunicando las observaciones tecnicas sobre el formulario Guia Madre (XFWB), indicando los campos faltantes y las correcciones necesarias",
     1, 4),
    ("Informar estado del proyecto",
     "Escribi un correo formal a Diego Bugallo con un resumen ejecutivo del estado actual del proyecto VUA, los avances de la mesa de trabajo y los puntos pendientes de resolucion",
     1, 5),
]

for etiqueta, instruccion, activo, orden in correos:
    con.execute("""INSERT OR IGNORE INTO vua_correos_rapidos 
        (etiqueta, instruccion, activo, orden) VALUES (?,?,?,?)""",
        (etiqueta, instruccion, activo, orden))
    print(f"Correo rapido '{etiqueta}': OK")

# ── Info de paneles ───────────────────────────────────────────────────────────
info_items = [
    ("normativa_descripcion", "Normativa aplicable",
     "RG 3596/2014 — Transmision anticipada de informacion de carga aerea via XML IATA. Define sujetos obligados (ATA MT, ATA CVC, ATA AGT), mensajes requeridos (XFFM, XFWB, XFHL, XFZB) y plazo minimo de 4 horas antes del arribo.\n\nRG 4517/2019 — Reingenieria del Manifiesto SIM aereo. Automatiza la generacion y presentacion del MANI, e incorpora el requisito del CUIT del consignatario en el XFWB (campo OCI/AR/IMP//CUIT).\n\nRG 5797/2025 — Analizada por su potencial implicancia en los flujos de informacion de VUA.\n\nCodigo Aduanero Arts. 131/160 — Obligacion de documentacion en arribo y partida de aeronaves.\n\nDecision CMC 50/04 — Facultad de administraciones aduaneras para requerir informacion anticipada de carga (MERCOSUR).\n\nIATA RP 1670 — Estandar tecnico para intercambio electronico de datos entre aerolineas y organismos gubernamentales."),
    ("bpmn_descripcion", "Validador BPMN",
     "Valida diagramas de proceso (BPMN) contra la normativa aduanera vigente para carga aerea:\n\n- RG 3596/2014: transmision anticipada de informacion, sujetos obligados, plazos de 4hs\n- RG 4517/2019: Manifiesto SIM automatico, CUIT consignatario en XFWB\n- RG 5756/2025: MANE, registro de exportacion, cierre de vuelo\n\nEl validador detecta actores faltantes, mensajes XML no declarados, plazos incorrectos y roles mal asignados segun el circuito (IMPO/EXPO)."),
]

for clave, titulo, contenido in info_items:
    con.execute("INSERT OR IGNORE INTO vua_info (clave, titulo, contenido) VALUES (?,?,?)",
        (clave, titulo, contenido))
    print(f"Info '{clave}': OK")

con.commit()
con.close()
print("\nOK — vua_riesgos, vua_correos_rapidos, vua_info creadas y pobladas")

# ── Consultas frecuentes normativa ────────────────────────────────────────────
con2 = sqlite3.connect(HIST_DB)

con2.execute("""CREATE TABLE IF NOT EXISTS vua_consultas_frecuentes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    etiqueta TEXT NOT NULL,
    pregunta TEXT NOT NULL,
    activo INTEGER DEFAULT 1,
    orden INTEGER DEFAULT 99
)""")
con2.commit()

consultas = [
    ("MANE vs MANI", "¿Cuáles son las diferencias entre el MANE y el MANI?", 1),
    ("XML del ATA MT", "¿Qué mensajes XML debe transmitir el ATA MT y en qué plazo?", 2),
    ("CUIT en XFWB", "¿Por qué el CUIT del consignatario es obligatorio en el XFWB?", 3),
    ("Rol jurídico VUCEA", "¿Qué pasa si VUCEA transmite los XML en representación de las aerolíneas?", 4),
    ("Marco normativo IA", "¿Qué norma regula la transmisión anticipada de información de carga aérea?", 5),
]
for etiqueta, pregunta, orden in consultas:
    con2.execute("INSERT OR IGNORE INTO vua_consultas_frecuentes (etiqueta, pregunta, activo, orden) VALUES (?,?,1,?)",
        (etiqueta, pregunta, orden))
    print(f"Consulta '{etiqueta}': OK")

con2.commit(); con2.close()
print("OK — vua_consultas_frecuentes creada y poblada")
