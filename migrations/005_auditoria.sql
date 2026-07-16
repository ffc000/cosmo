-- 005_auditoria.sql
-- Fase 9: auditoría centralizada. Antes los logs estaban dispersos --
-- accesos.log (texto plano, no queryable), ref_aduanas_log (solo esa
-- tabla), job_status_db (solo jobs) -- sin una vista única de "quién hizo
-- qué, cuándo, en toda la plataforma". Esta tabla es el destino común;
-- registrar_auditoria() en core.py es el helper que la alimenta.
CREATE TABLE IF NOT EXISTS auditoria (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha    TEXT NOT NULL DEFAULT (datetime('now')),
    usuario  TEXT NOT NULL,
    modulo   TEXT NOT NULL,   -- 'auth', 'sintia', 'vua', 'senasa', 'stock', 'training', 'finanzas', 'admin'
    accion   TEXT NOT NULL,   -- 'login_ok', 'login_fail', 'logout', 'informe_generado', 'usuario_creado', etc.
    detalle  TEXT DEFAULT '',
    ip       TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_auditoria_fecha ON auditoria(fecha DESC);
CREATE INDEX IF NOT EXISTS idx_auditoria_usuario ON auditoria(usuario);
CREATE INDEX IF NOT EXISTS idx_auditoria_modulo ON auditoria(modulo);
