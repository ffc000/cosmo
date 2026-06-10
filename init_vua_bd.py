import sqlite3, os

HIST_DB = "/data/historial.db"
con = sqlite3.connect(HIST_DB)

con.execute("""CREATE TABLE IF NOT EXISTS vua_config (
    clave TEXT PRIMARY KEY,
    titulo TEXT NOT NULL,
    contenido TEXT NOT NULL,
    modificado TEXT DEFAULT (datetime('now'))
)""")

con.execute("""CREATE TABLE IF NOT EXISTS vua_equipo (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    cargo TEXT NOT NULL,
    organismo TEXT NOT NULL,
    email TEXT DEFAULT '',
    activo INTEGER DEFAULT 1,
    orden INTEGER DEFAULT 99
)""")

con.execute("""CREATE TABLE IF NOT EXISTS vua_glosario (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    termino TEXT NOT NULL,
    definicion TEXT NOT NULL,
    categoria TEXT DEFAULT 'general',
    orden INTEGER DEFAULT 99
)""")

con.commit()

config_items = [
    ("resumen_ejecutivo", "Resumen ejecutivo", "La Dirección General de Aduanas participa, a través de la Dirección de Reingeniería de Procesos Aduaneros (DI REPA), en el proyecto Ventanilla Única Aeroportuaria (VUA) impulsado por la Unidad Ejecutora VUCEA. En su segunda etapa, el proyecto tiene por objeto desarrollar un portal de gestión centralizado para los procesos de carga y descarga en aeropuertos con intervención aduanera, con foco inicial en el Aeropuerto Internacional de Ezeiza.\n\nDesde octubre de 2025, DI REPA coordina una mesa de trabajo interinstitucional que involucra a Sistemas Aduaneros (DI SADU) y la Aduana de Ezeiza (DI ADEZ) con VUCEA, con el objetivo de relevar los circuitos operativos aduaneros, definir el alcance de la integración y establecer las condiciones técnicas y normativas bajo las cuales la DGA participará en la plataforma. A la fecha se han realizado más de quince reuniones, se han documentado los circuitos de importación y exportación de carga aérea, y se han identificado los principales ejes de integración y sus condicionantes.\n\nEl principal riesgo identificado es de naturaleza normativa: varios de los desarrollos propuestos por VUCEA no cuentan con marco regulatorio vigente que respalde la participación de nuevos actores en la cadena de transmisión. DI REPA recomienda resolver estos aspectos como condición previa al avance técnico en los ejes afectados."),
    ("antecedentes", "Antecedentes", "El presente informe se enmarca en una relación de trabajo interinstitucional entre la DGA y VUCEA iniciada en 2023, en el contexto de la primera etapa del proyecto VUA.\n\nEn esa etapa, DI REPA lideró el relevamiento, análisis y diseño de una plataforma de gestión para vuelos civiles y privados en el Aeropuerto de San Fernando, involucrando a todos los organismos con competencia aeroportuaria: PSA, DNM, SENASA, DSAFRO, ORSNA, ANAC y EANA. El resultado fue un documento de diseño funcional entregado formalmente a VUCEA para su desarrollo e implementación.\n\nEsa experiencia estableció las bases metodológicas y el vínculo institucional sobre los que se apoya la segunda etapa, que amplía el alcance hacia los procesos de carga aérea en Ezeiza."),
    ("objetivo", "Objetivo del proyecto", "El proyecto VUA Carga tiene por objeto integrar en una plataforma digital única los procesos asociados al ingreso y egreso de carga aérea en aeropuertos con intervención aduanera, permitiendo a los distintos organismos de control y al sector privado operar, consultar e intercambiar información en un entorno centralizado.\n\nDesde la perspectiva de VUCEA, la plataforma busca digitalizar y simplificar la operatoria actual, reducir la documentación en papel, eliminar la duplicidad de datos entre organismos y ofrecer a los operadores un único punto de ingreso para gestionar sus trámites aeroportuarios."),
    ("rol_dga", "Rol de la Dirección General de Aduanas", "La DGA interviene en este proyecto en su carácter de organismo de control con competencia exclusiva sobre los procesos aduaneros de importación y exportación de carga aérea. Su participación no implica la delegación de funciones de control ni de potestad sancionatoria, sino la definición de las condiciones técnicas, operativas y normativas bajo las cuales la plataforma VUA puede interactuar con los sistemas aduaneros.\n\nEn ese marco, DI REPA actúa como referente técnico-funcional de la DGA, con la participación de DI SADU en los aspectos de integración sistémica y de DI ADEZ en la validación operativa de los circuitos relevados."),
    ("alcance_operativo", "Alcance operativo", "El foco del proyecto en esta etapa es el Aeropuerto Internacional de Ezeiza. Los circuitos bajo análisis son:\n\nIMPORTACIÓN: desde la transmisión de información anticipada por parte de los sujetos obligados hasta la presentación automática del Manifiesto de Importación (MANI SIM), incluyendo la desconsolidación de carga.\n\nEXPORTACIÓN: desde la oficialización de la destinación aduanera hasta el registro del cierre de vuelo, incluyendo la generación y presentación del Manifiesto de Exportación (MANE).\n\nAmbos circuitos han sido documentados por DI REPA mediante diagramas de proceso (BPMN)."),
]

for clave, titulo, contenido in config_items:
    con.execute("INSERT OR IGNORE INTO vua_config (clave, titulo, contenido) VALUES (?,?,?)", (clave, titulo, contenido))

equipo = [
    ("Diego Bugallo", "Jefe Dpto. Facilitación y Simplificación de Comercio", "DI REPA", "", 1),
    ("Martín Macías", "Jefe Div. Modernización de Procesos Aduaneros", "DI REPA", "", 2),
    ("Hernán Cascón", "Supervisor de Informática Aduanera", "DI SADU", "", 3),
    ("Maximiliano Luengo", "Consejero técnico", "DI ADEZ", "", 4),
    ("Pablo Gómez Valdez", "Consejero técnico", "DI ADEZ", "", 5),
    ("Federico Cáceres", "Referente / Sec. Simplificación de Procesos Operativos", "DI REPA", "", 6),
    ("Fabiola Cochello", "Directora VUCEA", "VUCEA", "", 10),
    ("Vanesa Franco", "Jefa de Procesos", "VUCEA", "", 11),
    ("Gonzalo Rossendiz", "Analista", "VUCEA", "", 12),
    ("Ignacio Socas", "Analista", "VUCEA", "", 13),
]

for nombre, cargo, org, email, orden in equipo:
    con.execute("INSERT OR IGNORE INTO vua_equipo (nombre, cargo, organismo, email, activo, orden) VALUES (?,?,?,?,1,?)", (nombre, cargo, org, email, orden))

glosario = [
    ("VUA", "Ventanilla Única Aeroportuaria. Plataforma digital centralizada para la gestión de procesos aeroportuarios.", "proyecto", 1),
    ("VUCEA", "Unidad Ejecutora de la VUA. Organismo responsable del desarrollo e implementación de la plataforma.", "actores", 2),
    ("DI REPA", "Dirección de Reingeniería de Procesos Aduaneros. Referente técnico-funcional de la DGA en el proyecto VUA.", "actores", 3),
    ("DI SADU", "Dirección de Sistemas Aduaneros. Responsable de los aspectos de integración sistémica.", "actores", 4),
    ("DI ADEZ", "Aduana de Ezeiza. Participa en la validación operativa de los circuitos relevados.", "actores", 5),
    ("MANI SIM", "Manifiesto de Importación — Sistema de Información de Manifiestos. Se genera automáticamente a partir de los XML de información anticipada (RG 4517/2019).", "normativa", 10),
    ("MANE", "Manifiesto de Exportación. Registra la carga embarcada en cada vuelo de exportación. No cuenta con marco normativo para información anticipada.", "normativa", 11),
    ("MIC-DTA", "Manifiesto Internacional de Cargas - Declaración de Tránsito Aduanero. Documento del transporte terrestre.", "normativa", 12),
    ("XFFM", "Freight Forwarder Message. Mensaje XML IATA con el plan de carga del vuelo. Obligatorio para ATA MT y ATA CVC.", "xml", 20),
    ("XFWB", "Freight Waybill. Mensaje XML IATA equivalente a la guía aérea master. Obligatorio para ATA MT y ATA CVC.", "xml", 21),
    ("XFHL", "House Manifest. Mensaje XML IATA con las guías house de una guía master. Obligatorio para ATA AGT.", "xml", 22),
    ("XFZB", "Mensaje XML IATA enviado por el Forwarder. Complementa al XFHL.", "xml", 23),
    ("ATA MT", "Agente del transportista propietario del medio de transporte. Debe transmitir XFFM y XFWB con al menos 4hs de anticipación.", "actores", 30),
    ("ATA CVC", "Agente del transportista en vuelo compartido (code-share). Mismas obligaciones que el ATA MT.", "actores", 31),
    ("ATA AGT", "Agente de carga internacional / Forwarder. Debe transmitir XFHL y XFZB.", "actores", 32),
    ("TAMS", "Sistema de gestión de programación de vuelos de la Aduana de Ezeiza. No centraliza todas las aerolíneas.", "sistemas", 40),
    ("TCA", "Terminal de Cargas Argentina. Su registro de ingreso de la guía master es condición para el manifiesto desconsolidado.", "actores", 41),
    ("PAD", "Portal Aduanero. Sistema central de registro de operaciones de ARCA.", "sistemas", 42),
    ("OCI", "Other Customs and Security Information. Campo del XFWB para declarar el CUIT del consignatario. Formato: OCI/AR/IMP//CUIT12345678901.", "xml", 43),
    ("RG 3596/2014", "Resolución General AFIP. Regula la transmisión de información anticipada vía XML IATA para la vía aérea. Define sujetos obligados y plazo mínimo de 4hs.", "normativa", 50),
    ("RG 4517/2019", "Resolución General AFIP. Reingeniería del Manifiesto SIM aéreo. Automatiza su generación e incorpora el CUIT del consignatario en el XFWB.", "normativa", 51),
]

for termino, definicion, categoria, orden in glosario:
    con.execute("INSERT OR IGNORE INTO vua_glosario (termino, definicion, categoria, orden) VALUES (?,?,?,?)", (termino, definicion, categoria, orden))

con.commit(); con.close()
print("OK — vua_config, vua_equipo, vua_glosario creadas y pobladas")
