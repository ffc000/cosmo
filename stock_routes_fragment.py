# ══════════════════════════════════════════════════════════════════════════════
# MÓDULO STOCK DEPÓSITOS
# Agregar este bloque a app.py (antes del if __name__ == "__main__")
# También agregar en los nav-items del sidebar de los otros templates:
#   <div class="nav-item" onclick="location.href='/stock'">
#     <span class="nav-icon">🏭</span><span class="nav-label">Stock Depósitos</span>
#   </div>
# ══════════════════════════════════════════════════════════════════════════════

import tempfile as _tmp_stock

# Almacén temporal de reportes generados (mismo patrón que job_status)
# Se limpia con el mismo daemon que job_status.
_stock_jobs = {}

@app.route("/stock")
@login_required
def stock_index():
    return render_template("stock.html", username=session.get("username", ""))


@app.route("/api/stock/generar", methods=["POST"])
@login_required
def stock_generar():
    """
    Recibe los dos TXT, corre el procesamiento en Python y guarda el HTML
    resultante en memoria para descarga inmediata.
    """
    from stock_depositos import generar_reporte, procesar, parsear_stock, parsear_depositos, yymmdd_to_ddmmyyyy

    if "stock" not in request.files or "depositos" not in request.files:
        return jsonify({"ok": False, "error": "Faltan archivos (stock y/o depositos)."})

    f_stock = request.files["stock"]
    f_depo  = request.files["depositos"]

    # Leer fecha_max desde el formulario (viene como YYYY-MM-DD)
    fecha_iso = request.form.get("fecha_max", "")
    if not fecha_iso or len(fecha_iso) != 10:
        return jsonify({"ok": False, "error": "Fecha inválida. Formato esperado: YYYY-MM-DD."})

    # Convertir YYYY-MM-DD → YYMMDD
    fecha_yymmdd = fecha_iso[2:4] + fecha_iso[5:7] + fecha_iso[8:10]

    dias_tol = int(request.form.get("dias_tol", 0))

    try:
        stock_txt = f_stock.read().decode("utf-8", errors="replace")
        depo_txt  = f_depo.read().decode("utf-8", errors="replace")
    except Exception as e:
        return jsonify({"ok": False, "error": f"Error leyendo archivos: {e}"})

    try:
        # Generar HTML
        html_bytes = generar_reporte(stock_txt, depo_txt, fecha_yymmdd, dias_tol)

        # Calcular conteo para mostrar en log
        registros = procesar(stock_txt, depo_txt, fecha_yymmdd, dias_tol)
        conteo = {"VERDE": 0, "AZUL": 0, "AMARILLO": 0, "ROJO": 0, "NEGRO": 0}
        for r in registros:
            s = r[5]
            if s in conteo:
                conteo[s] += 1

    except Exception as e:
        logging.error(f"STOCK ERROR | {e}")
        return jsonify({"ok": False, "error": f"Error en el procesamiento: {e}"})

    # Guardar en memoria
    import time as _time
    job_id = str(uuid.uuid4())[:8]
    _stock_jobs[job_id] = {
        "html":    html_bytes,
        "fecha":   fecha_yymmdd,
        "_ts":     _time.time(),
        "usuario": session.get("username", "?"),
    }

    # Limpiar jobs viejos (> 2hs) de forma inline (sin thread extra)
    ahora = _time.time()
    viejos = [k for k, v in list(_stock_jobs.items()) if v.get("_ts", ahora) < ahora - 7200]
    for k in viejos:
        _stock_jobs.pop(k, None)

    logging.info(f"STOCK OK | user={session.get('username')} | fecha={fecha_yymmdd} | total={len(registros)}")

    return jsonify({
        "ok":     True,
        "job_id": job_id,
        "total":  len(registros),
        "conteo": conteo,
    })


@app.route("/api/stock/download/<job_id>")
@login_required
def stock_download(job_id):
    job = _stock_jobs.get(job_id)
    if not job:
        return "Reporte no encontrado o expirado. Regeneralo.", 404

    fecha = job["fecha"]
    fname = f"ReporteStock_{fecha}.html"

    return send_file(
        io.BytesIO(job["html"]),
        as_attachment=True,
        download_name=fname,
        mimetype="text/html; charset=utf-8",
    )
