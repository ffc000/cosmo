import sqlite3

HIST_DB = "/data/historial.db"
con = sqlite3.connect(HIST_DB)

# Agregar columnas nuevas a vua_ejes si no existen
cols = [r[1] for r in con.execute("PRAGMA table_info(vua_ejes)").fetchall()]
print("Columnas actuales:", cols)

for col, tipo in [
    ("descripcion", "TEXT DEFAULT ''"),
    ("propuesta_vucea", "TEXT DEFAULT ''"),
    ("postura_aduana", "TEXT DEFAULT ''"),
    ("recomendacion", "TEXT DEFAULT ''"),
]:
    if col not in cols:
        con.execute(f"ALTER TABLE vua_ejes ADD COLUMN {col} {tipo}")
        print(f"Columna '{col}' agregada")

# Cargar contenido de cada eje
ejes_data = [
    (1,
     "La transmision de informacion anticipada de carga aerea se realiza mediante mensajes XML estandar IATA (XFFM, XFWB, XFHL, XFZB), enviados directamente por los sujetos obligados a los sistemas aduaneros. Los sujetos obligados son el ATA MT, el ATA CVC y el ATA AGT, quienes transmiten la informacion con al menos cuatro horas de anticipacion al arribo, bajo apercibimiento de las sanciones previstas en el Codigo Aduanero.",
     "Centralizar en la plataforma VUA la recepcion de los mensajes XML de informacion anticipada, actuando como punto unico de entrada para los sujetos obligados y como canal de transmision hacia los sistemas aduaneros.",
     "La DGA reconoce la potencial utilidad de una plataforma intermediaria como herramienta de facilitacion. Sin embargo, la incorporacion de VUCEA como intermediario genera preguntas juridico-institucionales sin respuesta normativa vigente: (1) Si VUCEA actua como mandatario de los sujetos obligados o como sujeto obligado en si mismo. (2) En caso de que el XML llegue con errores o fuera del plazo, si la sancion recae sobre el ATA MT/CVC/AGT o sobre VUCEA. (3) Que instrumento juridico regula la relacion entre VUCEA y los sujetos obligados.",
     "Impulsar la elaboracion de un analisis normativo conjunto con VUCEA que defina el encuadre juridico de la participacion como intermediario, como condicion previa a la integracion tecnica."),

    (2,
     "Actualmente la Aduana de Ezeiza gestiona la programacion de vuelos de carga a traves del sistema TAMS. Dicho sistema no esta pensado para centralizar la informacion de todas las aerolineas ni para proveer disponibilidad en tiempo real. Esta situacion fue observada por una auditoria interna de la Aduana de Ezeiza, que senalo la falta de una solucion tecnologica adecuada. La Aduana informo a dicha auditoria que la resolucion se esta canalizando a traves de la plataforma VUA.",
     "Desarrollar un tablero de programacion de vuelos dentro de la plataforma VUA que centralice la informacion en tiempo real. VUCEA confirmo que el desarrollo esta en curso, con integracion a Aeropuertos Argentina 2000 (via web service) y London Supply (via archivo). Propuso acceso mediante usuarios GDE APN o web service.",
     "La DGA valora positivamente este desarrollo. Identifico dos usos concretos: (1) la Aduana de Ezeiza lo utilizara para la asignacion anticipada de personal de control; (2) se proyecta integrar la informacion con sistemas propios para control y gestion de riesgo en MALVINA. Dado que el objetivo no es solo la visualizacion sino la integracion sistemica, la arquitectura mas adecuada es que ARCA reciba los datos a traves de un web service. El mecanismo tecnico se encuentra en definicion interna entre DI REPA y DI SADU.",
     "Priorizar la definicion de la modalidad de acceso a la brevedad, dado que este modulo esta en desarrollo activo y una decision tardia podria generar retrabajos."),

    (3,
     "El Manifiesto de Exportacion (MANE) es el documento aduanero que registra la carga embarcada en cada vuelo de exportacion. Su generacion, registracion, ratificacion y presentacion estan a cargo del ATA MT y los ATA CVC. VUCEA formulo consultas sobre los campos que lo diferencian del MANI y sobre la vinculacion entre el permiso de embarque y el plan de vuelo.",
     "Incorporar la gestion del MANE a la plataforma VUA como parte del flujo de exportacion, integrandolo con otros tramites aeroportuarios ya contemplados en la plataforma.",
     "La DGA senala dos condicionantes: (1) la informacion anticipada para exportacion no se encuentra normada actualmente — a diferencia de importacion (RG 3596/2014 y 4517/2019), no existe un marco equivalente para la via exportadora; (2) el MANE presenta particularidades tecnicas que requieren analisis especifico. DI REPA se comprometio a remitir a VUCEA la documentacion tecnica del MANE.",
     "Abordar el analisis del MANE en una instancia especifica, una vez que VUCEA haya completado su evaluacion preliminar. El avance debe condicionarse a la resolucion previa del marco normativo para informacion anticipada de exportacion."),

    (4,
     "El manifiesto desconsolidado de importacion es registrado por el ATA Desconsolidador (Forwarder) en el sistema aduanero. El proceso presenta un cuello de botella: el Forwarder solo puede registrar su manifiesto en SIM una vez que el TCA (Terminal de Cargas Argentina) ha registrado el ingreso de la guia master al deposito, generando demoras que se agravan en dias inhabiles por requerir presencia fisica de personal aduanero.",
     "Incorporar el circuito de manifiestos desconsolidados de importacion a la plataforma VUA, en el marco del relevamiento general de los procesos de carga aeroportuaria.",
     "Al igual que el MANE, la informacion anticipada para manifiestos desconsolidados no se encuentra normada actualmente. El circuito vigente es enteramente presencial en su etapa de presentacion ante Aduana. Se senala adicionalmente que existe un Web Service (WSE) para la transmision de datos de guias hijas que se encuentra operativo, aunque su adopcion no es masiva. Este desarrollo podria constituir la base tecnica de una futura integracion sistemica.",
     "Incluir este circuito en la agenda normativa a desarrollar conjuntamente con VUCEA. En paralelo, evaluar la posibilidad de impulsar la adopcion masiva del WSE existente como paso previo e independiente de la integracion con VUA."),

    (5,
     "En el marco del relevamiento tecnico, VUCEA compartio formularios de carga correspondientes a la Guia Madre (equivalente al mensaje XFWB del estandar IATA Cargo-XML) para analisis por parte de DI REPA. Dichos formularios forman parte de la interfaz que VUCEA propone ofrecer a los operadores dentro de la plataforma VUA.",
     "Incorporar formularios de carga en la plataforma VUA que permitan a los operadores ingresar los datos de la Guia Madre, con la eventual transmision de esa informacion a Aduana.",
     "Plano 1 — Estandar de transmision: Aduana recibe los mensajes XML en formato IATA enviados por los sujetos obligados bajo las condiciones de las RG 3596/2014 y 4517/2019. Este estandar es no negociable. Plano 2 — Formulario asistido: La DGA reconoce la utilidad de ofrecer un formulario para operadores sin capacidad tecnica de generar XML. En ese caso, VUCEA debe transformar los datos en mensajes XML que cumplan el estandar IATA antes de transmitirlos. La responsabilidad sobre la calidad y oportunidad del mensaje recae sobre VUCEA.",
     "Comunicar formalmente a VUCEA las observaciones tecnicas identificadas. Los campos faltantes y las ambiguedades senaladas son condicion necesaria para que los mensajes XML sean aceptados. Establecer un mecanismo de validacion tecnica conjunta antes de la puesta en produccion."),
]

for eje_id, desc, prop, postura, recom in ejes_data:
    con.execute("""UPDATE vua_ejes SET 
        descripcion=?, propuesta_vucea=?, postura_aduana=?, recomendacion=?
        WHERE id=?""", (desc, prop, postura, recom, eje_id))
    print(f"Eje {eje_id}: actualizado")

con.commit()
con.close()
print("OK — vua_ejes ampliada y cargada")
