"""
blueprints/stock.py — Módulo Stock Depósitos (control de transmisión de
mercadería en depósitos fiscales, cruzando archivos STOCK y DEPOSITOS).

Extraído de app.py como primer blueprint de la Fase 2 de profesionalización
(separar los ~200 endpoints de app.py en módulos por área). Los siguientes
candidatos son training, vua, senasa y finanzas — se hacen de a uno para
poder correr los tests entre cada extracción.
"""
import os
import time
import hashlib
import logging
from datetime import datetime, date, timedelta

from flask import Blueprint, request, jsonify, render_template, session, Response, send_file

from core import HIST_DB, STOCK_REPORTS_DIR, login_required, modulo_required, get_db

stock_bp = Blueprint("stock", __name__)

# ══════════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════
# MÓDULO STOCK DEPÓSITOS
# ══════════════════════════════════════════════════════════════════════════════
_stock_jobs = {}

# ── BD Stock ──────────────────────────────────────────────────────────────────
def init_stock_db():
    """Crea las tablas de historial de stock si no existen."""
    with get_db(HIST_DB) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS stock_reportes (
            id          TEXT PRIMARY KEY,
            fecha_corte TEXT NOT NULL,
            fecha_gen   TEXT DEFAULT (datetime('now')),
            dias_tol    INTEGER DEFAULT 0,
            usuario     TEXT DEFAULT '',
            total       INTEGER DEFAULT 0,
            verde       INTEGER DEFAULT 0,
            azul        INTEGER DEFAULT 0,
            amarillo    INTEGER DEFAULT 0,
            rojo        INTEGER DEFAULT 0,
            negro       INTEGER DEFAULT 0
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS stock_registros (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            reporte_id  TEXT NOT NULL REFERENCES stock_reportes(id),
            codadu      TEXT,
            codlot      TEXT,
            razon_social TEXT,
            cuit        TEXT,
            tipo        TEXT,
            nombre_adu  TEXT,
            nombre_dira TEXT,
            semaforo    TEXT,
            comentario  TEXT,
            freg        TEXT,
            fstock      TEXT
        )""")
        con.execute("""CREATE INDEX IF NOT EXISTS idx_stock_reg_lot
            ON stock_registros(codadu, codlot)""")
        con.execute("""CREATE INDEX IF NOT EXISTS idx_stock_reg_reporte
            ON stock_registros(reporte_id)""")
        con.execute("""CREATE TABLE IF NOT EXISTS stock_tendencia (
            id                   TEXT NOT NULL,
            reporte_id           TEXT NOT NULL REFERENCES stock_reportes(id),
            freg_transmitio      INTEGER DEFAULT 0,
            freg_no_transmitio   INTEGER DEFAULT 0,
            freg_no_habil        INTEGER DEFAULT 0,
            fstock_transmitio    INTEGER DEFAULT 0,
            fstock_no_transmitio INTEGER DEFAULT 0,
            fstock_no_habil      INTEGER DEFAULT 0,
            pct_reg              REAL DEFAULT NULL,
            pct_stock            REAL DEFAULT NULL,
            PRIMARY KEY (id, reporte_id)
        )""")
        con.execute("""CREATE INDEX IF NOT EXISTS idx_stock_tend_id
            ON stock_tendencia(id)""")
        # Columna file_path agregada en v2 — ALTER TABLE idempotente
        try:
            con.execute("ALTER TABLE stock_reportes ADD COLUMN file_path TEXT DEFAULT ''")
        except Exception:
            pass  # Ya existe
        # Columnas pct agregadas en v3
        for _col in ["pct_reg REAL", "pct_stock REAL"]:
            try:
                con.execute(f"ALTER TABLE stock_tendencia ADD COLUMN {_col} DEFAULT NULL")
            except Exception:
                pass

init_stock_db()

def _cargar_mod_rfixwis():
    """Carga el módulo _RFIXWIS.py, que vive en la raíz del proyecto (junto a
    app.py) — no en blueprints/, de donde corre este archivo."""
    import importlib.util, os as _os
    raiz_proyecto = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    script_path = _os.path.join(raiz_proyecto, "_RFIXWIS.py")
    spec = importlib.util.spec_from_file_location("rfixwis", script_path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def _guardar_reporte_bd(reporte_id, fecha_corte, dias_tol, usuario, registros, file_path=""):
    """Persiste una corrida completa en stock_reportes + stock_registros."""
    conteo = {"VERDE": 0, "AZUL": 0, "AMARILLO": 0, "ROJO": 0, "NEGRO": 0}
    for r in registros:
        s = r[5] if len(r) > 5 else "NEGRO"
        if s in conteo:
            conteo[s] += 1
    total = sum(conteo.values())

    with get_db(HIST_DB) as con:
        # Upsert del reporte (puede re-generarse el mismo día)
        con.execute("""INSERT OR REPLACE INTO stock_reportes
            (id, fecha_corte, dias_tol, usuario, total, verde, azul, amarillo, rojo, negro, file_path)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (reporte_id, fecha_corte, dias_tol, usuario, total,
             conteo["VERDE"], conteo["AZUL"], conteo["AMARILLO"],
             conteo["ROJO"], conteo["NEGRO"], file_path))

        # Borrar registros anteriores del mismo reporte_id antes de reinsertar
        con.execute("DELETE FROM stock_registros WHERE reporte_id=?", (reporte_id,))

        batch = []
        for r in registros:
            batch.append((
                reporte_id,
                r[0],   # codadu
                r[1],   # codlot
                r[6],   # razon_social
                r[7],   # cuit
                r[8],   # tipo
                r[9],   # nombre_adu
                r[10],  # nombre_dira
                r[5],   # semaforo
                r[11],  # comentario
                r[3],   # freg  (ya en DD/MM/YYYY)
                r[4],   # fstock
            ))
        con.executemany("""INSERT INTO stock_registros
            (reporte_id, codadu, codlot, razon_social, cuit, tipo,
             nombre_adu, nombre_dira, semaforo, comentario, freg, fstock)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", batch)

    return conteo, total

# ── Rutas ──────────────────────────────────────────────────────────────────────

@stock_bp.route("/stock")
@login_required
@modulo_required("stock")
def stock_index():
    return render_template("stock.html", username=session.get("username", ""))


@stock_bp.route("/api/stock/generar", methods=["POST"])
@login_required
@modulo_required("stock")
def api_stock_generar():
    import hashlib
    from datetime import datetime

    stock_file = request.files.get("stock")
    depo_file  = request.files.get("depositos")
    fecha_max  = request.form.get("fecha_max", "")
    dias_tol   = int(request.form.get("dias_tol", 5))

    if not stock_file or not depo_file:
        return jsonify({"ok": False, "error": "Faltan archivos"})

    try:
        stock_txt = stock_file.read().decode("utf-8", errors="replace")
        depo_txt  = depo_file.read().decode("utf-8", errors="replace")

        try:
            fecha_dt = datetime.strptime(fecha_max, "%Y-%m-%d") if fecha_max else datetime.today()
        except ValueError:
            fecha_dt = datetime.today()
        fecha_yymmdd = fecha_dt.strftime("%y%m%d")
        fecha_iso    = fecha_dt.strftime("%Y-%m-%d")

        mod       = _cargar_mod_rfixwis()
        registros = mod.procesar(stock_txt, depo_txt, fecha_yymmdd, dias_tol)
        serie     = mod.calcular_serie_grafico(stock_txt, depo_txt, fecha_yymmdd, dias_tol)

        # ID basado en fecha+tolerancia para que el mismo día/params sobreescriba
        reporte_id = hashlib.md5(f"{fecha_iso}|{dias_tol}".encode()).hexdigest()[:12]

        # ── Calcular tendencia por LOT ──────────────────────────────────────────
        # Reconstruir arr15 (misma lógica que _RFIXWIS.py)
        def _calc_fecha_bk(yymmdd, delta):
            from datetime import date, timedelta
            y, m, d = 2000+int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
            nd = date(y, m, d) + timedelta(days=delta)
            return f"{nd.year-2000:02d}{nd.month:02d}{nd.day:02d}"

        arr15_bk = []
        f_bk = fecha_yymmdd
        for _ in range(14):
            f_bk = _calc_fecha_bk(f_bk, -1)
            arr15_bk.insert(0, f_bk)
        arr15_bk.append(fecha_yymmdd)

        def _contar_u15(arr_bits):
            trans = no_trans = no_hab = 0
            for i, bit in enumerate(arr_bits):
                es_nh = bool(mod.es_no_habil(int(arr15_bk[i])))
                if bit == 1:
                    trans += 1
                elif es_nh:
                    no_hab += 1
                else:
                    no_trans += 1
            habiles = trans + no_trans
            pct = round(trans / habiles * 100, 1) if habiles > 0 else None
            return trans, no_trans, no_hab, pct

        tend_batch = []
        tend_data  = {}   # {lot_id: {freg_t, freg_nt, freg_nh, fstock_t, fstock_nt, fstock_nh}}
        for r in registros:
            lot_id  = f"{r[0]}-{r[1]}"
            u15     = r[16] if len(r) > 16 else [0]*15
            u15s    = r[17] if len(r) > 17 else u15
            ft, fnt, fnh, pct_r   = _contar_u15(u15)
            st, snt, snh, pct_s   = _contar_u15(u15s)
            tend_data[lot_id] = {
                'freg_t': ft, 'freg_nt': fnt, 'freg_nh': fnh, 'pct_reg': pct_r,
                'fstock_t': st, 'fstock_nt': snt, 'fstock_nh': snh, 'pct_stock': pct_s,
            }
            tend_batch.append((lot_id, reporte_id, ft, fnt, fnh, st, snt, snh, pct_r, pct_s))

        with get_db(HIST_DB) as con_t:
            con_t.execute("DELETE FROM stock_tendencia WHERE reporte_id=?", (reporte_id,))
            con_t.executemany("""INSERT OR REPLACE INTO stock_tendencia
                (id, reporte_id, freg_transmitio, freg_no_transmitio, freg_no_habil,
                 fstock_transmitio, fstock_no_transmitio, fstock_no_habil, pct_reg, pct_stock)
                VALUES (?,?,?,?,?,?,?,?,?,?)""", tend_batch)

            # ── Consultar reporte anterior por LOT para calcular tendencia ──────────
            lot_ids = list(tend_data.keys())
            tend_prev = {}
            if lot_ids:
                placeholders = ','.join('?' * len(lot_ids))
                rows_prev = con_t.execute(f"""
                    SELECT t.id, t.pct_reg, t.pct_stock
                    FROM stock_tendencia t
                    JOIN stock_reportes p ON t.reporte_id = p.id
                    WHERE t.id IN ({placeholders})
                      AND p.fecha_corte < ?
                      AND t.reporte_id != ?
                    ORDER BY p.fecha_corte DESC
                """, lot_ids + [fecha_iso, reporte_id]).fetchall()
                seen = set()
                for row in rows_prev:
                    lid = row[0]
                    if lid not in seen:
                        tend_prev[lid] = {'pct_reg': row[1], 'pct_stock': row[2]}
                        seen.add(lid)

        # Calcular símbolo tendencia
        def _tend_symbol(curr, prev):
            if curr is None or prev is None: return '—'
            if curr > prev:  return '↑'
            if curr < prev:  return '↓'
            return '→'

        def _fmt_pct(pct):
            return f"{pct:.1f}%" if pct is not None else "—"

        tendencia = {}
        for lot_id, td in tend_data.items():
            prev = tend_prev.get(lot_id)
            tendencia[lot_id] = {
                'treg':          _tend_symbol(td['pct_reg'],   prev['pct_reg']   if prev else None),
                'tstock':        _tend_symbol(td['pct_stock'], prev['pct_stock'] if prev else None),
                'pct_reg':       _fmt_pct(td['pct_reg']),
                'pct_stock':     _fmt_pct(td['pct_stock']),
                'pct_reg_prev':  _fmt_pct(prev['pct_reg'])   if prev else None,
                'pct_stock_prev':_fmt_pct(prev['pct_stock']) if prev else None,
                'freg_t':        td['freg_t'],  'freg_nt': td['freg_nt'],  'freg_nh': td['freg_nh'],
                'fstock_t':      td['fstock_t'],'fstock_nt':td['fstock_nt'],'fstock_nh':td['fstock_nh'],
            }

        html_out  = mod.generar_html(registros, fecha_yymmdd, serie, tendencia)

        # Guardar HTML en disco
        fname_html = f"ReporteStock_{fecha_iso}_{reporte_id}.html"
        file_path  = os.path.join(STOCK_REPORTS_DIR, fname_html)
        with open(file_path, "w", encoding="utf-8") as fh:
            fh.write(html_out)

        conteo, total = _guardar_reporte_bd(
            reporte_id, fecha_iso, dias_tol,
            session.get("username", "?"), registros, file_path
        )

        # Cachear HTML en memoria (para descarga inmediata)
        _stock_jobs[reporte_id] = {
            "html":  html_out,
            "fecha": fecha_iso,
            "_ts":   __import__("time").time(),
        }

        logging.info(f"STOCK GENERAR | user={session.get('username')} | fecha={fecha_iso} | total={total}")

        return jsonify({
            "ok":        True,
            "job_id":    reporte_id,
            "total":     total,
            "conteo":    conteo,
            "reporte_id": reporte_id,
        })

    except Exception as e:
        logging.error(f"STOCK ERROR | {e}")
        return jsonify({"ok": False, "error": str(e)})


@stock_bp.route("/api/stock/download/<job_id>")
@login_required
@modulo_required("stock")
def api_stock_download(job_id):
    from flask import Response, send_file

    # 1. Buscar en caché de memoria
    job = _stock_jobs.get(job_id)
    if job and job.get("html"):
        html  = job["html"]
        fecha = job.get("fecha", job_id)
        return Response(html, mimetype="text/html",
                        headers={"Content-Disposition": f"attachment; filename=ReporteStock_{fecha}.html"})

    # 2. Buscar archivo en disco
    with get_db(HIST_DB, row_factory=True) as con:
        rep = con.execute("SELECT * FROM stock_reportes WHERE id=?", (job_id,)).fetchone()
    if not rep:
        return jsonify({"ok": False, "error": "Reporte no encontrado"}), 404

    fp = rep["file_path"] if rep["file_path"] else ""
    if fp and os.path.exists(fp):
        return send_file(fp, mimetype="text/html", as_attachment=True,
                         download_name=os.path.basename(fp))

    return jsonify({"ok": False, "error": "Archivo no disponible. Regenerá el reporte."}), 410


@stock_bp.route("/api/stock/historial")
@login_required
@modulo_required("stock")
def api_stock_historial():
    """Lista los últimos N reportes generados, con flag de archivo disponible."""
    limit = min(int(request.args.get("limit", 30)), 100)
    with get_db(HIST_DB, row_factory=True) as con:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM stock_reportes ORDER BY fecha_corte DESC, fecha_gen DESC LIMIT ?",
            (limit,)).fetchall()]
    for r in rows:
        r["file_ok"] = bool(r.get("file_path") and os.path.exists(r["file_path"]))
    return jsonify({"ok": True, "rows": rows})


@stock_bp.route("/api/stock/historial/<job_id>", methods=["DELETE"])
@login_required
@modulo_required("stock")
def api_stock_historial_delete(job_id):
    """Elimina un reporte del historial: registro en BD y archivo en disco."""
    with get_db(HIST_DB, row_factory=True) as con:
        rep = con.execute("SELECT * FROM stock_reportes WHERE id=?", (job_id,)).fetchone()
        if not rep:
            return jsonify({"ok": False, "error": "Reporte no encontrado"}), 404

        fp = rep["file_path"] if rep["file_path"] else ""

        # Borrar registros detalle y cabecera
        con.execute("DELETE FROM stock_registros WHERE reporte_id=?", (job_id,))
        con.execute("DELETE FROM stock_reportes WHERE id=?", (job_id,))

    # Borrar archivo físico si existe
    file_deleted = False
    if fp and os.path.exists(fp):
        try:
            os.remove(fp)
            file_deleted = True
        except OSError as e:
            logging.warning(f"STOCK DELETE | no se pudo borrar {fp}: {e}")

    # Limpiar caché en memoria
    _stock_jobs.pop(job_id, None)

    logging.info(f"STOCK DELETE | user={session.get('username')} | id={job_id} | file={file_deleted}")
    return jsonify({"ok": True, "file_deleted": file_deleted})


@stock_bp.route("/api/stock/evolucion/<codadu>/<codlot>")
@login_required
@modulo_required("stock")
def api_stock_evolucion(codadu, codlot):
    """
    Devuelve la serie histórica de estados de un LOT específico
    cruzando stock_registros con stock_reportes (ordenado por fecha_corte ASC).
    """
    with get_db(HIST_DB, row_factory=True) as con:
        rows = [dict(r) for r in con.execute("""
            SELECT
                p.fecha_corte,
                r.semaforo,
                r.comentario,
                r.freg,
                r.fstock,
                r.razon_social,
                r.tipo,
                r.nombre_adu
            FROM stock_registros r
            JOIN stock_reportes p ON r.reporte_id = p.id
            WHERE r.codadu = ? AND r.codlot = ?
            ORDER BY p.fecha_corte ASC
        """, (codadu, codlot)).fetchall()]

    if not rows:
        return jsonify({"ok": False, "error": "Sin historial para este depósito"})

    meta = {
        "codadu":      codadu,
        "codlot":      codlot,
        "razon_social": rows[-1]["razon_social"],
        "tipo":         rows[-1]["tipo"],
        "nombre_adu":   rows[-1]["nombre_adu"],
    }
    return jsonify({"ok": True, "serie": rows, "meta": meta})

