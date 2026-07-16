-- 004_dashboard_pendientes.sql
-- Fase 9: widget combinado de pendientes VUA+SENASA en el dashboard.
--
-- vua_pendientes_cache: los "pendientes" de VUA se calculan cruzando
-- minutas con IA (vua_acuerdos_pendientes(), llamada costosa a Claude).
-- Para poder mostrarlos en el dashboard sin llamar a la IA en cada carga
-- de página, se cachea acá el último resultado, actualizado en background
-- cada 6hs (ver _actualizar_vua_pendientes_cache en vua.py) -- mismo
-- criterio que las alertas de Fase 6. Fila única (clave='ultimo').
CREATE TABLE IF NOT EXISTS vua_pendientes_cache (
    clave        TEXT PRIMARY KEY,
    resultado    TEXT NOT NULL,   -- JSON con {pendientes:[...], resueltos_recientes:[...]}
    actualizado  TEXT NOT NULL
);
