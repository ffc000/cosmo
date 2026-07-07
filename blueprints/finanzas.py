"""
blueprints/finanzas.py — Módulo Finanzas personales: tarjetas, categorías,
movimientos (parseados de resúmenes PDF Santander/Galicia), presupuesto,
declaraciones juradas (DDJJ) con datos personales cifrados.

Quinto y último blueprint de la Fase 2 de profesionalización. Es el más
sensible (datos personales de Fer, números de tarjeta cifrados en DDJJ) —
por eso finanzas_owner_required existe además de modulo_required("finanzas").
"""
import os
import re
import json
import uuid
import logging
import sqlite3
from datetime import datetime, date, timedelta

from flask import Blueprint, request, jsonify, render_template, session, send_file

from core import (
    HIST_DB, login_required, modulo_required, finanzas_owner_required,
    notificar_telegram, limiter,
)

finanzas_bp = Blueprint("finanzas", __name__)

import io
import pdfplumber
import finanzas as fin
from extracto_parser import parse_santander, parse_galicia, extraer_total_declarado

fin.init_finanzas_db(HIST_DB)


def _extraer_paginas_pdf(file_storage):
    data = file_storage.read()
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return [p.extract_text() or "" for p in pdf.pages]


@finanzas_bp.route("/finanzas")
@login_required
@finanzas_owner_required
def finanzas_index():
    return render_template("finanzas.html", username=session.get("username", ""))


@finanzas_bp.route("/api/finanzas/tarjetas", methods=["GET"])
@login_required
@finanzas_owner_required
def api_fin_tarjetas():
    return jsonify({"ok": True, "rows": fin.get_tarjetas(HIST_DB)})


@finanzas_bp.route("/api/finanzas/tarjetas", methods=["POST"])
@login_required
@finanzas_owner_required
def api_fin_tarjeta_crear():
    data = request.json or {}
    nombre = data.get("nombre", "").strip()
    motor = data.get("motor", "")
    dia_cierre = data.get("dia_cierre")
    if not nombre or motor not in ("santander", "galicia"):
        return jsonify({"ok": False, "error": "Nombre y motor (santander|galicia) son requeridos"})
    tid = fin.crear_tarjeta(HIST_DB, nombre, motor, dia_cierre)
    return jsonify({"ok": True, "id": tid})


@finanzas_bp.route("/api/finanzas/tarjetas/<tid>/monto", methods=["POST"])
@login_required
@finanzas_owner_required
def api_fin_tarjeta_monto(tid):
    """Fija cuánto hay que pagar de esta tarjeta en un mes dado (del resumen: total o pago mínimo)."""
    data = request.json or {}
    mes = data.get("mes") or date.today().isoformat()[:7]
    monto = data.get("monto_a_pagar")
    if monto is None:
        return jsonify({"ok": False, "error": "Falta el monto"})
    con = sqlite3.connect(HIST_DB)
    con.execute(
        "INSERT INTO fin_tarjetas_montos (tarjeta_id, mes, monto_a_pagar) VALUES (?,?,?) "
        "ON CONFLICT(tarjeta_id, mes) DO UPDATE SET monto_a_pagar=excluded.monto_a_pagar",
        (tid, mes, float(monto)))
    con.commit(); con.close()
    return jsonify({"ok": True})


@finanzas_bp.route("/api/finanzas/tarjetas/estado_pago")
@login_required
@finanzas_owner_required
def api_fin_tarjetas_estado_pago():
    """Para cada tarjeta con un monto a pagar fijado este mes: cuánto se
    pagó ya (movimientos tipo='pago' de esa tarjeta en el mes) y cuánto falta."""
    mes = request.args.get("mes") or date.today().isoformat()[:7]
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT t.id as tarjeta_id, t.nombre, m.monto_a_pagar, "
        "COALESCE((SELECT SUM(monto_ars) FROM fin_movimientos "
        " WHERE tarjeta_id=t.id AND tipo='pago' AND substr(fecha,1,7)=?), 0) as pagado "
        "FROM fin_tarjetas t JOIN fin_tarjetas_montos m ON m.tarjeta_id=t.id AND m.mes=? "
        "WHERE m.monto_a_pagar > 0", (mes, mes)).fetchall()
    con.close()
    resultado = []
    for r in rows:
        falta = round(r["monto_a_pagar"] - r["pagado"], 2)
        resultado.append({
            "tarjeta_id": r["tarjeta_id"], "nombre": r["nombre"],
            "monto_a_pagar": round(r["monto_a_pagar"], 2), "pagado": round(r["pagado"], 2),
            "falta": max(falta, 0), "saldado": falta <= 0,
        })
    return jsonify({"ok": True, "mes": mes, "rows": resultado})


@finanzas_bp.route("/api/finanzas/upload", methods=["POST"])
@login_required
@finanzas_owner_required
@limiter.limit("30 per hour", error_message="Demasiados uploads de resúmenes.")
def api_fin_upload():
    """Parsea el PDF y devuelve una previsualización editable. Todavía no guarda nada."""
    if "archivo" not in request.files:
        return jsonify({"ok": False, "error": "No se recibió archivo"})
    tarjeta_id = request.form.get("tarjeta_id")
    if not tarjeta_id:
        return jsonify({"ok": False, "error": "Falta seleccionar la tarjeta"})

    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    tarjeta = con.execute("SELECT * FROM fin_tarjetas WHERE id=?", (tarjeta_id,)).fetchone()
    con.close()
    if not tarjeta:
        return jsonify({"ok": False, "error": "Tarjeta no encontrada"})

    archivo = request.files["archivo"]
    if not archivo.filename.lower().endswith(".pdf"):
        return jsonify({"ok": False, "error": "El archivo debe ser un PDF"})

    try:
        paginas = _extraer_paginas_pdf(archivo)
    except Exception as e:
        return jsonify({"ok": False, "error": f"No se pudo leer el PDF: {e}"})

    motor = tarjeta["motor"]
    try:
        if motor == "santander":
            movimientos = parse_santander(paginas, date.today().year)
        elif motor == "galicia":
            movimientos = parse_galicia(paginas)
        else:
            return jsonify({"ok": False, "error": f"La tarjeta '{tarjeta['nombre']}' no tiene un motor de parseo válido"})
    except Exception as e:
        logging.error(f"FINANZAS PARSE ERROR | {e}")
        return jsonify({"ok": False, "error": f"Error parseando el resumen: {e}"})

    if not movimientos:
        return jsonify({"ok": False, "error": "No se encontraron movimientos en el PDF. ¿Es el resumen correcto para esta tarjeta?"})

    total_ars, total_usd = extraer_total_declarado(paginas, motor)
    calc_ars = round(sum(m["monto_ars"] for m in movimientos if m["tipo"] == "consumo"), 2)
    calc_usd = round(sum(m["monto_usd"] for m in movimientos if m["tipo"] == "consumo"), 2)
    validado = total_ars is not None and abs(calc_ars - total_ars) <= 1.0

    fechas_consumo = [m["fecha"] for m in movimientos if m["tipo"] == "consumo"]
    periodo_desde = min(fechas_consumo) if fechas_consumo else None
    periodo_hasta = max(fechas_consumo) if fechas_consumo else None

    # Pre-categorizar para la previsualización (todavía no se guarda en BD)
    posibles_dup_total = 0
    for m in movimientos:
        if m["tipo"] == "consumo":
            m["categoria_id"] = fin.categorizar(HIST_DB, m["descripcion"])
        elif m["tipo"] == "cargo":
            m["categoria_id"] = "cargos_tarjeta"
        else:
            m["categoria_id"] = None
        m["posibles_duplicados"] = fin.buscar_posible_duplicado(HIST_DB, tarjeta_id, m["fecha"], m["monto_ars"])
        if m["posibles_duplicados"]:
            posibles_dup_total += 1

    return jsonify({
        "ok": True,
        "tarjeta_id": tarjeta_id,
        "archivo_nombre": archivo.filename,
        "posibles_duplicados_total": posibles_dup_total,
        "movimientos": movimientos,
        "total_declarado_ars": total_ars,
        "total_declarado_usd": total_usd,
        "total_calculado_ars": calc_ars,
        "total_calculado_usd": calc_usd,
        "validado": validado,
        "periodo_desde": periodo_desde,
        "periodo_hasta": periodo_hasta,
    })


@finanzas_bp.route("/api/finanzas/confirmar", methods=["POST"])
@login_required
@finanzas_owner_required
def api_fin_confirmar():
    """Guarda en BD los movimientos que el usuario ya revisó/corrigió en la previsualización."""
    data = request.json or {}
    tarjeta_id = data.get("tarjeta_id")
    movimientos = data.get("movimientos", [])
    if not tarjeta_id or not movimientos:
        return jsonify({"ok": False, "error": "Faltan datos"})

    try:
        resumen_id = fin.guardar_resumen(
            HIST_DB, tarjeta_id, data.get("archivo_nombre", ""),
            data.get("periodo_desde"), data.get("periodo_hasta"),
            data.get("total_declarado_ars") or 0, data.get("total_calculado_ars") or 0,
            bool(data.get("validado")), session.get("username", "?"))
        guardados = fin.guardar_movimientos(HIST_DB, tarjeta_id, resumen_id, movimientos)
    except (KeyError, TypeError, ValueError) as e:
        logging.error(f"FINANZAS CONFIRMAR ERROR | {e}")
        return jsonify({"ok": False, "error": f"Los datos recibidos no tienen el formato esperado: {e}"})

    logging.info(f"FINANZAS UPLOAD | user={session.get('username')} | tarjeta={tarjeta_id} | movs={len(guardados)}")
    notificar_telegram(f"💳 Resumen '{data.get('archivo_nombre','')}' cargado por {session.get('username')} — {len(guardados)} movimientos")
    return jsonify({"ok": True, "resumen_id": resumen_id, "guardados": len(guardados)})


@finanzas_bp.route("/api/finanzas/movimiento_manual", methods=["POST"])
@login_required
@finanzas_owner_required
def api_fin_movimiento_manual():
    """Carga manual de un gasto (ej. transferencia bancaria) o de un pago
    hecho sobre una tarjeta, sin pasar por un PDF."""
    data = request.json or {}
    fecha = data.get("fecha")
    descripcion = (data.get("descripcion") or "").strip()
    monto_ars = data.get("monto_ars")
    categoria_id = data.get("categoria_id")
    tipo = data.get("tipo", "consumo")
    if tipo not in ("consumo", "pago"):
        tipo = "consumo"
    tarjeta_id = data.get("tarjeta_id") if tipo == "pago" else None
    forzar = bool(data.get("forzar"))
    if not fecha or not descripcion or monto_ars is None:
        return jsonify({"ok": False, "error": "Faltan fecha, descripción o monto"})
    if tipo == "pago" and not tarjeta_id:
        return jsonify({"ok": False, "error": "Elegí a qué tarjeta corresponde el pago"})

    if not forzar:
        dups = fin.buscar_posible_duplicado(HIST_DB, tarjeta_id or "manual", fecha, float(monto_ars))
        if dups:
            return jsonify({"ok": False, "posible_duplicado": True, "coincidencias": dups,
                             "error": "Ya existe un movimiento con monto y fecha similares. "
                                      "Reenviá con forzar=true si igual querés cargarlo."})

    mov = {
        "fecha": fecha, "descripcion": descripcion, "comprobante": "",
        "monto_ars": float(monto_ars), "monto_usd": 0.0,
        "cuota_actual": None, "cuota_total": None, "tipo": tipo,
    }
    guardados = fin.guardar_movimientos(HIST_DB, tarjeta_id or "manual", None, [mov], origen="manual")
    if tipo == "consumo":
        cat_final = categoria_id or fin.categorizar(HIST_DB, descripcion)
        if cat_final:
            fin.recategorizar_movimiento(HIST_DB, guardados[0]["id"], cat_final, aprender=False)
            guardados[0]["categoria_id"] = cat_final
    return jsonify({"ok": True, "movimiento": guardados[0]})


@finanzas_bp.route("/api/finanzas/movimientos")
@login_required
@finanzas_owner_required
def api_fin_movimientos():
    mes = request.args.get("mes")
    tarjeta_id = request.args.get("tarjeta_id")
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    q = ("SELECT m.*, c.nombre as categoria_nombre, c.color as categoria_color, "
         "t.nombre as tarjeta_nombre FROM fin_movimientos m "
         "LEFT JOIN fin_categorias c ON m.categoria_id=c.id "
         "LEFT JOIN fin_tarjetas t ON m.tarjeta_id=t.id WHERE 1=1")
    params = []
    if mes:
        q += " AND substr(m.fecha,1,7)=?"; params.append(mes)
    if tarjeta_id:
        q += " AND m.tarjeta_id=?"; params.append(tarjeta_id)
    q += " ORDER BY m.fecha DESC"
    rows = [dict(r) for r in con.execute(q, params).fetchall()]
    con.close()
    return jsonify({"ok": True, "rows": rows})


@finanzas_bp.route("/api/finanzas/movimientos/export")
@login_required
@finanzas_owner_required
def api_fin_movimientos_export():
    """Exporta a Excel los mismos movimientos que /api/finanzas/movimientos,
    con los mismos filtros opcionales de mes/tarjeta."""
    mes = request.args.get("mes")
    tarjeta_id = request.args.get("tarjeta_id")
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    q = ("SELECT m.fecha, m.descripcion, t.nombre as tarjeta, c.nombre as categoria, "
         "m.monto_ars, m.monto_usd, m.cuota_actual, m.cuota_total, m.tipo, m.origen "
         "FROM fin_movimientos m "
         "LEFT JOIN fin_categorias c ON m.categoria_id=c.id "
         "LEFT JOIN fin_tarjetas t ON m.tarjeta_id=t.id WHERE 1=1")
    params = []
    if mes:
        q += " AND substr(m.fecha,1,7)=?"; params.append(mes)
    if tarjeta_id:
        q += " AND m.tarjeta_id=?"; params.append(tarjeta_id)
    q += " ORDER BY m.fecha DESC"
    cur = con.execute(q, params)
    cols = [d[0] for d in cur.description]
    rows = [list(r) for r in cur.fetchall()]
    con.close()
    buf = _exportar_xlsx(cols, rows)
    nombre = f"finanzas_{mes or 'todo'}.xlsx"
    return send_file(buf, as_attachment=True, download_name=nombre,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ── Servicios recurrentes ──────────────────────────────────────────────────────
@finanzas_bp.route("/api/finanzas/servicios")
@login_required
@finanzas_owner_required
def api_fin_servicios_list():
    mes = request.args.get("mes") or datetime.now().strftime("%Y-%m")
    con = sqlite3.connect(HIST_DB)
    rows = _estado_servicios_mes(con, mes)
    con.close()
    return jsonify({"ok": True, "mes": mes, "rows": rows})

@finanzas_bp.route("/api/finanzas/servicios", methods=["POST"])
@login_required
@finanzas_owner_required
def api_fin_servicios_create():
    data = request.json or {}
    nombre = (data.get("nombre") or "").strip()
    patron = (data.get("patron") or "").strip()
    if not nombre:
        return jsonify({"ok": False, "error": "Falta el nombre"})
    con = sqlite3.connect(HIST_DB)
    orden = con.execute("SELECT COALESCE(MAX(orden),0)+1 FROM fin_servicios").fetchone()[0]
    con.execute("INSERT INTO fin_servicios (nombre, patron, orden) VALUES (?,?,?)", (nombre, patron, orden))
    con.commit(); con.close()
    return jsonify({"ok": True})

@finanzas_bp.route("/api/finanzas/servicios/<int:sid>", methods=["DELETE"])
@login_required
@finanzas_owner_required
def api_fin_servicios_delete(sid):
    con = sqlite3.connect(HIST_DB)
    con.execute("UPDATE fin_servicios SET activo=0 WHERE id=?", (sid,))
    con.commit(); con.close()
    return jsonify({"ok": True})

@finanzas_bp.route("/api/finanzas/servicios/<int:sid>/pagar", methods=["POST"])
@login_required
@finanzas_owner_required
def api_fin_servicios_pagar(sid):
    """Marca (o desmarca) el pago manualmente, para el caso en que la
    descripción del movimiento no coincida con ningún patrón automático."""
    data = request.json or {}
    mes = data.get("mes") or datetime.now().strftime("%Y-%m")
    pagado = 1 if data.get("pagado", True) else 0
    con = sqlite3.connect(HIST_DB)
    con.execute("INSERT INTO fin_servicios_pagos (servicio_id, mes, pagado, fecha_pago) VALUES (?,?,?,?) "
                "ON CONFLICT(servicio_id, mes) DO UPDATE SET pagado=excluded.pagado, fecha_pago=excluded.fecha_pago",
                (sid, mes, pagado, datetime.now().strftime("%Y-%m-%d") if pagado else None))
    con.commit(); con.close()
    return jsonify({"ok": True})


@finanzas_bp.route("/api/finanzas/movimientos/<mov_id>/categoria", methods=["POST"])
@login_required
@finanzas_owner_required
def api_fin_recategorizar(mov_id):
    data = request.json or {}
    categoria_id = data.get("categoria_id")
    if not categoria_id:
        return jsonify({"ok": False, "error": "Falta categoria_id"})
    ok = fin.recategorizar_movimiento(HIST_DB, mov_id, categoria_id, aprender=data.get("aprender", True))
    return jsonify({"ok": ok})


@finanzas_bp.route("/api/finanzas/movimientos/<mov_id>", methods=["DELETE"])
@login_required
@finanzas_owner_required
def api_fin_eliminar_movimiento(mov_id):
    ok = fin.eliminar_movimiento(HIST_DB, mov_id)
    if not ok:
        return jsonify({"ok": False, "error": "No se encontró el movimiento"})
    logging.info(f"FINANZAS DELETE | user={session.get('username')} | mov={mov_id}")
    return jsonify({"ok": True})


@finanzas_bp.route("/api/finanzas/categorias", methods=["GET"])
@login_required
@finanzas_owner_required
def api_fin_categorias():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM fin_categorias ORDER BY orden").fetchall()]
    con.close()
    return jsonify({"ok": True, "rows": rows})


@finanzas_bp.route("/api/finanzas/categorias", methods=["POST"])
@login_required
@finanzas_owner_required
def api_fin_crear_categoria():
    data = request.json or {}
    nombre = (data.get("nombre") or "").strip()
    color = data.get("color") or "#9CA3AF"
    presupuesto_mensual = data.get("presupuesto_mensual") or 0
    if not nombre:
        return jsonify({"ok": False, "error": "Falta el nombre de la categoría"})
    try:
        cat_id = fin.crear_categoria(HIST_DB, nombre, color, float(presupuesto_mensual))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True, "id": cat_id})


@finanzas_bp.route("/api/finanzas/categorias/<cat_id>/presupuesto", methods=["POST"])
@login_required
@finanzas_owner_required
def api_fin_set_presupuesto_cat(cat_id):
    data = request.json or {}
    monto = data.get("monto")
    mes = data.get("mes") or date.today().isoformat()[:7]
    if monto is None:
        return jsonify({"ok": False, "error": "Falta monto"})
    monto = float(monto)

    total_mes = fin.get_presupuesto_total(HIST_DB, mes)
    if total_mes is not None:
        suma_otras = fin.suma_presupuesto_categorias(HIST_DB, excluir_categoria_id=cat_id)
        if suma_otras + monto > total_mes + 0.01:
            disponible = round(total_mes - suma_otras, 2)
            return jsonify({
                "ok": False,
                "error": f"Superarías el presupuesto total del mes (${total_mes:,.2f}). "
                         f"A esta categoría le queda como máximo ${disponible:,.2f}."
            })

    fin.set_presupuesto_categoria(HIST_DB, cat_id, monto)
    return jsonify({"ok": True})


@finanzas_bp.route("/api/finanzas/presupuesto", methods=["POST"])
@login_required
@finanzas_owner_required
def api_fin_set_presupuesto_total():
    data = request.json or {}
    mes = data.get("mes")
    monto = data.get("monto")
    if not mes or monto is None:
        return jsonify({"ok": False, "error": "Faltan mes y monto"})
    monto = float(monto)

    suma_cats = fin.suma_presupuesto_categorias(HIST_DB)
    if suma_cats > monto + 0.01:
        return jsonify({
            "ok": False,
            "error": f"La suma de presupuestos por categoría (${suma_cats:,.2f}) ya supera ese total. "
                     f"Bajá alguna categoría primero o poné un total mayor."
        })

    fin.set_presupuesto_total(HIST_DB, mes, monto)
    return jsonify({"ok": True})


@finanzas_bp.route("/api/finanzas/resumen")
@login_required
@finanzas_owner_required
def api_fin_resumen():
    mes = request.args.get("mes") or date.today().isoformat()[:7]
    return jsonify({
        "ok": True,
        "resumen": fin.resumen_mes(HIST_DB, mes),
        "categorias": fin.gasto_por_categoria(HIST_DB, mes),
    })


@finanzas_bp.route("/api/finanzas/comparativo")
@login_required
@finanzas_owner_required
def api_fin_comparativo():
    mes = request.args.get("mes") or date.today().isoformat()[:7]
    return jsonify({"ok": True, **fin.comparativo_por_categoria(HIST_DB, mes)})


@finanzas_bp.route("/api/finanzas/proyeccion")
@login_required
@finanzas_owner_required
def api_fin_proyeccion():
    mes = request.args.get("mes") or date.today().isoformat()[:7]
    return jsonify({"ok": True, "proyeccion": fin.proyeccion_cierre_mes(HIST_DB, mes)})


@finanzas_bp.route("/api/finanzas/atipicos")
@login_required
@finanzas_owner_required
def api_fin_atipicos():
    mes = request.args.get("mes") or date.today().isoformat()[:7]
    return jsonify({"ok": True, "rows": fin.gastos_atipicos(HIST_DB, mes)})


@finanzas_bp.route("/api/finanzas/evolucion_anual")
@login_required
@finanzas_owner_required
def api_fin_evolucion_anual():
    mes = request.args.get("mes") or date.today().isoformat()[:7]
    return jsonify({"ok": True, "rows": fin.evolucion_anual(HIST_DB, mes)})


@finanzas_bp.route("/api/finanzas/resumenes_subidos")
@login_required
@finanzas_owner_required
def api_fin_resumenes_subidos():
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT r.*, t.nombre as tarjeta_nombre FROM fin_resumenes r "
        "LEFT JOIN fin_tarjetas t ON r.tarjeta_id=t.id ORDER BY r.creado DESC LIMIT 50"
    ).fetchall()]
    con.close()
    return jsonify({"ok": True, "rows": rows})


# ── Declaraciones Juradas ────────────────────────────────────────────────────

@finanzas_bp.route("/api/finanzas/ddjj", methods=["GET"])
@login_required
@finanzas_owner_required
def api_ddjj_list():
    return jsonify({"ok": True, "rows": fin.listar_ddjj(HIST_DB)})


@finanzas_bp.route("/api/finanzas/ddjj", methods=["POST"])
@login_required
@finanzas_owner_required
def api_ddjj_crear():
    data = request.json or {}
    try:
        anio = int(data.get("anio"))
        fecha_cierre = str(data.get("fecha_cierre", "")).strip()
        valor_dolar = float(data.get("valor_dolar"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "anio, fecha_cierre y valor_dolar son requeridos y numéricos"}), 400
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", fecha_cierre):
        return jsonify({"ok": False, "error": "fecha_cierre debe ser AAAA-MM-DD"}), 400
    try:
        did = fin.crear_ddjj(HIST_DB, anio, fecha_cierre, valor_dolar)
    except sqlite3.IntegrityError:
        return jsonify({"ok": False, "error": f"Ya existe una DDJJ para el año {anio}"}), 400
    logging.info(f"DDJJ CREATE | user={session.get('username')} | anio={anio} | dolar={valor_dolar}")
    return jsonify({"ok": True, "id": did})


@finanzas_bp.route("/api/finanzas/ddjj/<ddjj_id>", methods=["PUT"])
@login_required
@finanzas_owner_required
def api_ddjj_actualizar(ddjj_id):
    data = request.json or {}
    campos = {k: v for k, v in data.items() if k in ("fecha_cierre", "valor_dolar", "estado", "fecha_presentacion")}
    fin.actualizar_ddjj(HIST_DB, ddjj_id, **campos)
    logging.info(f"DDJJ UPDATE | user={session.get('username')} | id={ddjj_id} | campos={list(campos.keys())}")
    return jsonify({"ok": True})


@finanzas_bp.route("/api/finanzas/ddjj/<ddjj_id>", methods=["DELETE"])
@login_required
@finanzas_owner_required
def api_ddjj_borrar(ddjj_id):
    fin.borrar_ddjj(HIST_DB, ddjj_id)
    logging.info(f"DDJJ DELETE | user={session.get('username')} | id={ddjj_id}")
    return jsonify({"ok": True})


@finanzas_bp.route("/api/finanzas/ddjj/<ddjj_id>/dinero", methods=["GET"])
@login_required
@finanzas_owner_required
def api_ddjj_dinero_list(ddjj_id):
    return jsonify({"ok": True, "rows": fin.listar_dinero_ddjj(HIST_DB, ddjj_id)})


@finanzas_bp.route("/api/finanzas/ddjj/<ddjj_id>/dinero", methods=["POST"])
@login_required
@finanzas_owner_required
def api_ddjj_dinero_crear(ddjj_id):
    data = request.json or {}
    try:
        rid = fin.crear_dinero_ddjj(
            HIST_DB, ddjj_id,
            fecha=str(data.get("fecha", "")).strip(),
            banco=str(data.get("banco", "")).strip(),
            cuenta=str(data.get("cuenta", "")).strip(),
            cbu=str(data.get("cbu", "")).strip(),
            moneda=str(data.get("moneda", "")).strip().upper(),
            importe=float(data.get("importe")),
        )
    except (TypeError, ValueError) as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "id": rid})


@finanzas_bp.route("/api/finanzas/ddjj/dinero/<reg_id>", methods=["DELETE"])
@login_required
@finanzas_owner_required
def api_ddjj_dinero_borrar(reg_id):
    fin.borrar_dinero_ddjj(HIST_DB, reg_id)
    return jsonify({"ok": True})


@finanzas_bp.route("/api/finanzas/ddjj/<ddjj_id>/propiedades", methods=["GET"])
@login_required
@finanzas_owner_required
def api_ddjj_propiedades_list(ddjj_id):
    return jsonify({"ok": True, "rows": fin.listar_propiedades_ddjj(HIST_DB, ddjj_id)})


@finanzas_bp.route("/api/finanzas/ddjj/<ddjj_id>/propiedades", methods=["POST"])
@login_required
@finanzas_owner_required
def api_ddjj_propiedades_crear(ddjj_id):
    data = request.json or {}
    try:
        rid = fin.crear_propiedad_ddjj(
            HIST_DB, ddjj_id,
            direccion=str(data.get("direccion", "")).strip(),
            fecha_adquisicion=str(data.get("fecha_adquisicion", "")).strip(),
            superficie=float(data.get("superficie") or 0),
            base_imponible=float(data.get("base_imponible") or 0),
            valor_compra_actualizado=float(data.get("valor_compra_actualizado") or 0),
        )
    except (TypeError, ValueError) as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "id": rid})


@finanzas_bp.route("/api/finanzas/ddjj/propiedades/<reg_id>", methods=["DELETE"])
@login_required
@finanzas_owner_required
def api_ddjj_propiedades_borrar(reg_id):
    fin.borrar_propiedad_ddjj(HIST_DB, reg_id)
    return jsonify({"ok": True})


@finanzas_bp.route("/api/finanzas/ddjj/<ddjj_id>/tarjetas", methods=["GET"])
@login_required
@finanzas_owner_required
def api_ddjj_tarjetas_list(ddjj_id):
    rows = fin.listar_tarjetas_ddjj(HIST_DB, app.secret_key, ddjj_id, revelar=False)
    return jsonify({"ok": True, "rows": rows})


@finanzas_bp.route("/api/finanzas/ddjj/tarjetas/<tarjeta_id>/revelar", methods=["POST"])
@login_required
@finanzas_owner_required
@limiter.limit("20 per 15 minutes", error_message="Demasiados intentos de ver números completos.")
def api_ddjj_tarjetas_revelar(tarjeta_id):
    con = sqlite3.connect(HIST_DB); con.row_factory = sqlite3.Row
    row = con.execute("SELECT ddjj_id FROM fin_ddjj_tarjetas WHERE id=?", (tarjeta_id,)).fetchone()
    con.close()
    if not row:
        return jsonify({"ok": False, "error": "No existe esa tarjeta"}), 404
    logging.info(f"DDJJ TARJETA REVEAL | user={session.get('username')} | tarjeta_id={tarjeta_id}")
    rows = fin.listar_tarjetas_ddjj(HIST_DB, app.secret_key, row["ddjj_id"], revelar=True)
    encontrada = next((r for r in rows if r["id"] == tarjeta_id), None)
    if not encontrada:
        return jsonify({"ok": False, "error": "No se pudo descifrar"}), 500
    return jsonify({"ok": True, "numero": encontrada["numero"]})


@finanzas_bp.route("/api/finanzas/ddjj/<ddjj_id>/tarjetas", methods=["POST"])
@login_required
@finanzas_owner_required
def api_ddjj_tarjetas_crear(ddjj_id):
    data = request.json or {}
    try:
        tid = fin.crear_tarjeta_ddjj(
            HIST_DB, app.secret_key, ddjj_id,
            emisor=str(data.get("emisor", "")).strip(),
            numero=str(data.get("numero", "")).strip(),
        )
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    logging.info(f"DDJJ TARJETA CREATE | user={session.get('username')} | ddjj_id={ddjj_id} | emisor={data.get('emisor')}")
    return jsonify({"ok": True, "id": tid})


@finanzas_bp.route("/api/finanzas/ddjj/tarjetas/<tarjeta_id>", methods=["DELETE"])
@login_required
@finanzas_owner_required
def api_ddjj_tarjetas_borrar(tarjeta_id):
    fin.borrar_tarjeta_ddjj(HIST_DB, tarjeta_id)
    logging.info(f"DDJJ TARJETA DELETE | user={session.get('username')} | id={tarjeta_id}")
    return jsonify({"ok": True})

