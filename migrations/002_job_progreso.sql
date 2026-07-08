-- 002_job_progreso.sql — Agrega el % de avance a job_status_db, para poder
-- mostrar una barra de progreso real en el frontend (antes solo había log de
-- texto). Se calcula a partir de los mismos mensajes que ya emite generar.py
-- (ver _pct_desde_mensaje en core.py) — no hace falta tocar generar.py.
ALTER TABLE job_status_db ADD COLUMN progreso INTEGER DEFAULT 0;
