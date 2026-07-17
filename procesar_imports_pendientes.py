#!/usr/bin/env python3
"""
procesar_imports_pendientes.py — Cron job que importa los .sql encolados
por /api/import-sql-agregar (ver app.py) SIN pasar por el ciclo de
request/response HTTP.

Por qué existe: subir un DAT_YYYY grande y esperar la respuesta en la misma
request HTTP se venía chocando, en orden, con: timeout del navegador (10
min), vencimiento de sesión/CSRF a mitad de camino, timeout de gunicorn (30
min), y finalmente MemoryError en el droplet de 1GB. Encolar el archivo
(rápido, /api/import-sql-agregar ya no hace más que validar y guardarlo) y
dejar que este script lo importe fuera del ciclo HTTP evita las cuatro cosas
de una: no hay navegador, sesión ni gunicorn de por medio mientras corre.

Instalación (crontab -e como el usuario que corre sintia, típicamente root
según sintia.service):
    * * * * * /opt/sintia/venv/bin/python3 /opt/sintia/procesar_imports_pendientes.py >> /data/pending_imports/cron.log 2>&1

Corre cada minuto pero solo hace algo si encuentra un .sql encolado.
Procesa como máximo UNO por corrida (si hay varios, los siguientes quedan
para la corrida próxima) y usa un lock file para no pisarse con la corrida
del minuto siguiente si el import en curso tarda más que eso.

Al terminar (haya salido bien o mal) borra el .sql y el .json de metadata
-- el resultado queda en un .result.json aparte, que es lo que consulta
/api/import-sql-agregar/estado y se borra apenas se lee una vez.
"""
import os
import sys
import json
import glob
import time
import shutil
import logging
import subprocess
import threading
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db_utils import DB_PATH, get_db  # noqa: E402 -- sin dependencia de Flask, ver db_utils.py

PENDING_DIR = os.path.join(os.path.dirname(DB_PATH), "pending_imports")
LOCK_PATH = os.path.join(PENDING_DIR, ".lock")
LOCK_MAX_EDAD_SEG = 3 * 3600  # si el lock tiene más de 3h, se asume una corrida colgada y se pisa

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


def _correr_sqlite_import(db_path, tmp_sql_path, timeout=7200):
    """Corre un .sql contra db_path via el binario sqlite3, anteponiendo
    PRAGMA synchronous=OFF / journal_mode=MEMORY (acelera mucho imports
    grandes, evita fsync por escritura).

    Implementación con threads en vez de proc.communicate(): escribimos el
    archivo a stdin en streaming (para no cargarlo entero en RAM) desde el
    thread principal, mientras dos threads aparte drenan stdout/stderr en
    paralelo. Drenarlos en paralelo (no después) es necesario para evitar
    un deadlock real: si sqlite3 escribe suficiente a stderr (ej. muchos
    errores de constraint) mientras nosotros seguimos escribiéndole stdin,
    el buffer de stderr se llena, sqlite3 se bloquea esperando que alguien
    lo lea, y nosotros nos bloqueamos escribiendo stdin -- los dos
    esperándose mutuamente. Cerrar stdin nosotros mismos (para que sqlite3
    vea el EOF) y DESPUÉS llamar a proc.communicate() no funciona: cerrar
    stdin a mano y que communicate() también intente tocarlo después tira
    'ValueError: I/O operation on closed file' (encontrado en producción,
    17/07/2026) -- por eso acá se usa proc.wait() en vez de communicate()."""
    proc = subprocess.Popen(
        ["sqlite3", db_path],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )

    stderr_chunks = []

    def _drenar_stderr():
        for linea in proc.stderr:
            stderr_chunks.append(linea)

    def _drenar_stdout():
        for _ in proc.stdout:
            pass  # no interesa el contenido, pero hay que leerlo para no bloquear al proceso

    hilo_err = threading.Thread(target=_drenar_stderr, daemon=True)
    hilo_out = threading.Thread(target=_drenar_stdout, daemon=True)
    hilo_err.start()
    hilo_out.start()

    try:
        proc.stdin.write("PRAGMA synchronous=OFF;\nPRAGMA journal_mode=MEMORY;\n")
        with open(tmp_sql_path, "r", encoding="utf-8", errors="replace") as fh:
            shutil.copyfileobj(fh, proc.stdin)
    except BrokenPipeError:
        # sqlite3 ya cortó por su cuenta (típicamente terminó con error
        # antes de que le mandáramos todo el archivo) -- no es un problema
        # nuestro, el returncode/stderr de abajo van a contar qué pasó.
        pass
    finally:
        try:
            proc.stdin.close()
        except (BrokenPipeError, ValueError):
            pass

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise
    finally:
        hilo_err.join(timeout=5)
        hilo_out.join(timeout=5)

    return proc.returncode, "".join(stderr_chunks)


def procesar_uno():
    metas = sorted(p for p in glob.glob(os.path.join(PENDING_DIR, "*.json"))
                    if not p.endswith(".result.json"))
    if not metas:
        return

    meta_path = metas[0]
    job_id = os.path.basename(meta_path)[:-5]  # quita ".json"
    sql_path = os.path.join(PENDING_DIR, f"{job_id}.sql")
    result_path = os.path.join(PENDING_DIR, f"{job_id}.result.json")

    if not os.path.exists(sql_path):
        logging.warning(f"job={job_id} sin .sql asociado (huérfano) -- se descarta la metadata")
        os.remove(meta_path)
        return

    with open(meta_path, encoding="utf-8") as fh:
        meta = json.load(fh)
    tablas_en_sql = meta.get("tablas", [])
    logging.info(f"Procesando job={job_id} | usuario={meta.get('usuario')} | tablas={tablas_en_sql}")

    resultado = {"procesado": datetime.now().isoformat()}
    try:
        # Re-chequeo de colisión: puede haber cambiado la base entre que se
        # encoló (validación en app.py) y que se procesa acá.
        tablas_existentes = set()
        if os.path.exists(DB_PATH):
            with get_db(DB_PATH) as con:
                tablas_existentes = {r[0] for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        colision = set(tablas_en_sql) & tablas_existentes
        if colision:
            resultado.update(ok=False, error=(
                f"El .sql crea o borra la(s) tabla(s) {', '.join(sorted(colision))}, que ya existen "
                f"en la base (se crearon/cargaron después de encolar este archivo). No se tocó nada."))
            logging.warning(f"job={job_id} bloqueado por colisión tardía: {sorted(colision)}")
        else:
            returncode, stderr = _correr_sqlite_import(DB_PATH, sql_path, timeout=7200)
            if returncode != 0 and stderr:
                resultado.update(ok=False, error=stderr[:500])
                logging.error(f"job={job_id} falló: {stderr[:500]}")
            else:
                size = round(os.path.getsize(DB_PATH) / (1024**3), 2)
                resultado.update(ok=True, size_gb=size, tablas=sorted(tablas_en_sql))
                logging.info(f"job={job_id} OK | tablas={sorted(tablas_en_sql)} | size={size}GB")
    except Exception as e:
        resultado.update(ok=False, error=str(e))
        logging.exception(f"job={job_id} excepción inesperada")

    with open(result_path, "w", encoding="utf-8") as fh:
        json.dump(resultado, fh)

    # Por pedido: una vez que el .sql ya pasó a la tabla (bien o mal -- si
    # falló, el motivo ya quedó en el .result.json) se borra. Reintentar
    # requiere volver a subirlo desde el admin.
    os.remove(sql_path)
    os.remove(meta_path)


def main():
    os.makedirs(PENDING_DIR, exist_ok=True)
    if os.path.exists(LOCK_PATH):
        edad = time.time() - os.path.getmtime(LOCK_PATH)
        if edad < LOCK_MAX_EDAD_SEG:
            logging.info("Ya hay un import en curso (lock activo) -- salgo sin hacer nada.")
            return
        logging.warning(f"Lock de {edad/3600:.1f}h -- se asume una corrida colgada y se pisa.")

    with open(LOCK_PATH, "w") as fh:
        fh.write(str(os.getpid()))
    try:
        procesar_uno()
    finally:
        if os.path.exists(LOCK_PATH):
            os.remove(LOCK_PATH)


if __name__ == "__main__":
    main()
