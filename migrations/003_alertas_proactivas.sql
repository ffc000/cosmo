-- 003_alertas_proactivas.sql
-- Fase 6 de profesionalización: alertas proactivas por Telegram.
--
-- alerta_vencido_enviada en senasa_acuerdos: evita reenviar el mismo aviso
-- de "compromiso vencido" en cada chequeo (corre cada hora) mientras el
-- acuerdo siga sin marcarse Completado. Se resetea a 0 manualmente si se
-- quiere volver a avisar (no hay UI para esto todavía, a propósito: es un
-- caso raro, no vale la pena la complejidad de una UI por ahora).
ALTER TABLE senasa_acuerdos ADD COLUMN alerta_vencido_enviada INTEGER DEFAULT 0;

-- fin_presupuesto_alertas: dedupe de avisos de presupuesto por rubro. Clave
-- (mes, categoria_id, umbral) para poder avisar en más de un escalón
-- (ej. 80% y 100%) sin repetir el mismo aviso el resto del mes.
CREATE TABLE IF NOT EXISTS fin_presupuesto_alertas (
    mes         TEXT NOT NULL,
    categoria_id TEXT NOT NULL,
    umbral      INTEGER NOT NULL,
    enviado_at  TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (mes, categoria_id, umbral)
);
