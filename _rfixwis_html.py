"""
_rfixwis_html.py — Arma el HTML descargable del reporte de stock (tabla +
filtros + gráfico de evolución, todo en un único archivo autocontenido con
JS embebido, para que se pueda abrir directamente en el navegador sin
depender del servidor).
Extraído de _RFIXWIS.py (Fase 3 de profesionalización).
"""
import html as _html
from _rfixwis_datos import FERIADOS
from _rfixwis_fechas import calc_fecha, yymmdd_to_date, yymmdd_to_ddmmyyyy
from _rfixwis_procesar import procesar, calcular_serie_grafico

COLOR_MAP = {
    'VERDE':    '#00bf00',
    'AZUL':     '#29b6f6',
    'AMARILLO': '#e6b800',
    'ROJO':     '#bf0000',
    'NEGRO':    '#222222',
}

TEXT_MAP = {
    'VERDE':    '#ffffff',
    'AZUL':     '#ffffff',
    'AMARILLO': '#000000',
    'ROJO':     '#ffffff',
    'NEGRO':    '#ffffff',
}

def _dot(semaforo: str, idx: int) -> str:
    """Círculo de color clicable que abre el modal de detalle."""
    bg = COLOR_MAP.get(semaforo, '#888')
    return (f'<span class="dot" data-idx="{idx}" '
            f'style="display:inline-block;width:18px;height:18px;border-radius:50%;'
            f'background:{bg};cursor:pointer;"></span>')

def generar_reporte(stock_txt: str, depositos_txt: str,
                    fecha_max_yymmdd: str, dias_tolerancia: int = 0) -> bytes:
    """Punto de entrada principal. Devuelve el HTML como bytes."""
    registros = procesar(stock_txt, depositos_txt, fecha_max_yymmdd, dias_tolerancia)
    serie     = calcular_serie_grafico(stock_txt, depositos_txt, fecha_max_yymmdd, dias_tolerancia)
    html = generar_html(registros, fecha_max_yymmdd, serie)
    return html.encode('utf-8')

def generar_html(registros: list, fecha_max: str, serie: list = None, tendencia: dict = None) -> str:
    """Genera el HTML completo del reporte con exportación Excel/PDF y modal de evolución histórica."""

    fecha_display = yymmdd_to_ddmmyyyy(fecha_max)

    # Calcular las 15 fechas para los encabezados del modal
    arr15 = []
    f = fecha_max
    for _ in range(14):
        f = calc_fecha(f, -1)
        arr15.insert(0, f)
    arr15.append(fecha_max)
    fechas_display = [yymmdd_to_ddmmyyyy(x) for x in arr15]

    DIAS_ES = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']
    def dia_semana(yymmdd):
        return DIAS_ES[yymmdd_to_date(yymmdd).weekday()]

    # Conteo por semáforo
    conteo = {'VERDE': 0, 'AZUL': 0, 'AMARILLO': 0, 'ROJO': 0, 'NEGRO': 0}
    for r in registros:
        s = r[5]
        if s in conteo:
            conteo[s] += 1
    total = sum(conteo.values())

    def pct(v):
        return f"{v/total*100:.1f}%" if total else "0%"

    # Serializar datos para JS
    import json as _json
    datos_js = []
    for r in registros:
        lot_id = f"{r[0]}-{r[1]}"
        td     = (tendencia or {}).get(lot_id, {})
        datos_js.append({
            'adu':      r[0],
            'lot':      r[1],
            'razon':    r[6],
            'cuit':     r[7],
            'tipo':     r[8],
            'ndu':      r[9],
            'dira':     r[10],
            'sem':      r[5],
            'freg':     r[3],
            'fstock':   r[4],
            'coment':   r[11],
            'u15':      r[16],
            'u15s':     r[17] if len(r) > 17 else r[16],
            'treg':           td.get('treg',         '—'),
            'tstock':         td.get('tstock',       '—'),
            'pct_reg':        td.get('pct_reg',      '—'),
            'pct_stock':      td.get('pct_stock',    '—'),
            'pct_reg_prev':   td.get('pct_reg_prev',   None),
            'pct_stock_prev': td.get('pct_stock_prev', None),
            'freg_t':         td.get('freg_t',   0),
            'freg_nt':        td.get('freg_nt',  0),
            'freg_nh':        td.get('freg_nh',  0),
            'fstock_t':       td.get('fstock_t',  0),
            'fstock_nt':      td.get('fstock_nt', 0),
            'fstock_nh':      td.get('fstock_nh', 0),
        })

    datos_js_str  = _json.dumps(datos_js, ensure_ascii=False)
    fechas_js_str = _json.dumps(fechas_display, ensure_ascii=False)
    dias_js_str   = _json.dumps([dia_semana(x) for x in arr15], ensure_ascii=False)
    feriados_js_str = _json.dumps([int(x) in FERIADOS for x in arr15])
    serie_js_str  = _json.dumps(serie or [], ensure_ascii=False)
    color_js      = _json.dumps(COLOR_MAP)
    tend_js_str   = _json.dumps(tendencia or {}, ensure_ascii=False)

    # Construir filas HTML
    filas_html = []
    for idx, r in enumerate(registros):
        sem    = r[5]
        u15_reg   = r[16]
        u15_stock = r[17] if len(r) > 17 else [0]*15
        lot_id = f"{r[0]}-{r[1]}"
        td     = (tendencia or {}).get(lot_id, {})
        barras = ''.join(
            (lambda creg, cstk: (
                f'<span style="display:inline-block;width:10px;height:14px;'
                f'background:{"#00bf00" if (creg and cstk) else ("#f0ad00" if (creg or cstk) else "#e03131")};'
                f'margin:0 1px;border-radius:2px;"></span>'
            ))(creg, cstk)
            for creg, cstk in zip(u15_reg, u15_stock)
        )
        adu_e, lot_e   = _html.escape(str(r[0])), _html.escape(str(r[1]))
        ndu_e          = _html.escape(str(r[9]))
        dira_e         = _html.escape(str(r[10]))
        razon_e        = _html.escape(str(r[6]))
        cuit_e         = _html.escape(str(r[7]))
        freg_e, fstock_e = _html.escape(str(r[3])), _html.escape(str(r[4]))
        coment_title_e = _html.escape(str(r[11]))
        # Todo el texto se escapa (por si algún día trae datos externos), y
        # recién después se "desescapa" puntualmente el <strong>PLAN BARRIDO</strong>
        # que agrega el propio código más arriba — nunca viene de datos externos.
        coment_e       = (coment_title_e
            .replace('&lt;strong&gt;', '<strong>')
            .replace('&lt;/strong&gt;', '</strong>'))
        filas_html.append(
            f'<tr data-sem="{sem}" data-idx="{idx}" data-adu="{adu_e}" data-lot="{lot_e}">'
            f'<td>{adu_e}</td>'
            f'<td title="{ndu_e}">{ndu_e}</td>'
            f'<td>{dira_e}</td>'
            f'<td>{lot_e}</td>'
            f'<td title="{razon_e}">{razon_e}</td>'
            f'<td>{cuit_e}</td>'
            f'<td style="text-align:center">{_dot(sem, idx)}</td>'
            f'<td>{freg_e}</td>'
            f'<td>{fstock_e}</td>'
            f'<td style="white-space:nowrap">{barras}</td>'
            f'<td style="font-size:.75rem;color:#555" title="{coment_title_e}">{coment_e}</td>'
            f'</tr>'
        )

    filas_str = '\n'.join(filas_html)

    LABEL_MAP = {'VERDE':'Verde','AZUL':'Celeste','AMARILLO':'Amarillo','ROJO':'Rojo','NEGRO':'Negro'}
    resumen_items = ''.join(
        f'<div class="resumen-chip" onclick="filtrarEstado(\'{s}\')" title="Filtrar: {s}">'
        f'<span class="resumen-num" style="color:{COLOR_MAP[s]}">{conteo[s]}</span>'
        f'<span class="resumen-label-chip">{LABEL_MAP[s]}</span>'
        f'<span class="resumen-pct">({pct(conteo[s])})</span></div>'
        for s in ['VERDE', 'AZUL', 'AMARILLO', 'ROJO', 'NEGRO']
    )


    # Fecha ISO para el endpoint de evolución (YYYY-MM-DD)
    fecha_iso = '20' + fecha_max[:2] + '-' + fecha_max[2:4] + '-' + fecha_max[4:6]

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reporte Stock Depósitos — {fecha_display}</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Segoe UI',Arial,sans-serif;background:#f4f6f9;color:#222;font-size:.85rem}}
.header{{background:#1E2A3B;color:#fff;padding:1rem 1.5rem;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:.75rem}}
.header-left h1{{font-size:1.1rem;font-weight:600}}
.header-left p{{font-size:.75rem;color:#94a3b8;margin-top:.25rem}}
.header-actions{{display:flex;gap:.5rem;flex-wrap:wrap}}
.btn-hdr{{display:inline-flex;align-items:center;gap:.35rem;border:1px solid rgba(255,255,255,.25);border-radius:5px;padding:.4rem .85rem;font-size:.75rem;font-weight:600;cursor:pointer;background:rgba(255,255,255,.08);color:#fff;transition:background .15s}}
.btn-hdr:hover{{background:rgba(255,255,255,.18)}}
.btn-hdr.green{{border-color:rgba(0,191,0,.4);color:#6ee86e}}
.btn-hdr.green:hover{{background:rgba(0,191,0,.12)}}
.resumen{{background:#fff;border-bottom:1px solid #e5e7eb;padding:.65rem 1.5rem;display:flex;align-items:center;gap:.25rem;flex-wrap:wrap}}
.resumen-sep{{font-size:.7rem;color:#cbd5e1;margin:0 .35rem}}
.resumen-chip{{display:inline-flex;align-items:center;gap:.4rem;padding:.35rem .7rem;border-radius:20px;cursor:pointer;border:1px solid #e5e7eb;transition:all .15s;margin:.15rem .1rem}}
.resumen-chip:hover{{background:#f0f4ff;border-color:#a5b4fc}}
.resumen-num{{font-size:1.15rem;font-weight:700;line-height:1}}
.resumen-label-chip{{font-size:.7rem;color:#555;text-transform:uppercase;letter-spacing:.06em}}
.resumen-pct{{font-size:.68rem;color:#9ca3af}}
.toolbar{{padding:.65rem 1.5rem;background:#fff;border-bottom:1px solid #e5e7eb;display:flex;gap:.5rem;align-items:center;flex-wrap:wrap}}
.toolbar input{{border:1px solid #d1d5db;border-radius:4px;padding:.38rem .7rem;font-size:.82rem;width:260px;outline:none}}
.toolbar input:focus{{border-color:#1A56DB;box-shadow:0 0 0 2px rgba(26,86,219,.1)}}
.toolbar select{{border:1px solid #d1d5db;border-radius:4px;padding:.38rem .6rem;font-size:.82rem;outline:none;background:#fff}}
.toolbar select:focus{{border-color:#1A56DB}}
.toolbar-btn{{display:inline-flex;align-items:center;gap:.3rem;border:1px solid #d1d5db;border-radius:4px;padding:.38rem .75rem;font-size:.75rem;font-weight:600;cursor:pointer;background:#fff;color:#374151;transition:all .15s}}
.toolbar-btn:hover{{background:#f3f4f6;border-color:#9ca3af}}
.toolbar-btn.primary{{background:#EBF5FF;border-color:#93c5fd;color:#1A56DB}}
.toolbar-btn.primary:hover{{background:#DBEAFE}}
.counter{{font-size:.75rem;color:#6b7280;margin-left:.25rem}}
.container{{padding:1rem 1.5rem}}
table{{width:100%;border-collapse:collapse;background:#fff;box-shadow:0 1px 4px rgba(0,0,0,.07);border-radius:6px;overflow:hidden;table-layout:fixed}}
thead th{{background:#1E2A3B;color:#fff;padding:.6rem .75rem;text-align:left;font-size:.72rem;white-space:nowrap;cursor:pointer;user-select:none;overflow:hidden;text-overflow:ellipsis}}
thead th:nth-child(1){{width:5%}}
thead th:nth-child(2){{width:11%}}
thead th:nth-child(3){{width:8%}}
thead th:nth-child(4){{width:4%}}
thead th:nth-child(5){{width:17%}}
thead th:nth-child(6){{width:6%}}
thead th:nth-child(7){{width:5%}}
thead th:nth-child(8){{width:6.5%}}
thead th:nth-child(9){{width:6.5%}}
thead th:nth-child(10){{width:11%}}
thead th:nth-child(11){{width:20%}}
thead th:hover{{background:#2d3f57}}
tbody tr:hover{{background:#f0f4ff}}
tbody td{{padding:.5rem .75rem;border-bottom:1px solid #f0f0f0;vertical-align:middle;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.dot{{transition:transform .15s;cursor:pointer}}
.dot:hover{{transform:scale(1.35)}}
/* Modals */
.modal-overlay{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:200;align-items:center;justify-content:center}}
.modal-overlay.open{{display:flex}}
.modal-box{{background:#fff;border-radius:10px;padding:1.5rem;max-width:900px;width:92vw;max-height:90vh;overflow-y:auto;box-shadow:0 12px 40px rgba(0,0,0,.22);position:relative}}
.modal-box.wide{{max-width:1100px}}
.modal-close{{position:absolute;top:.75rem;right:.75rem;background:#f3f4f6;border:none;border-radius:50%;width:30px;height:30px;font-size:1.1rem;cursor:pointer;line-height:30px;text-align:center;transition:background .15s}}
.modal-close:hover{{background:#e5e7eb}}
.modal-title{{font-size:.95rem;font-weight:700;margin-bottom:.3rem;padding-right:2.5rem;color:#1E2A3B}}
.modal-sub{{font-size:.78rem;color:#6b7280;margin-bottom:1rem}}
.modal-section{{margin-top:1rem}}
.modal-section-title{{font-size:.72rem;font-weight:700;color:#374151;text-transform:uppercase;letter-spacing:.08em;margin-bottom:.5rem}}
.modal-15{{overflow-x:auto}}
.modal-15 table{{font-size:.72rem;border-collapse:collapse;width:max-content;min-width:100%}}
.modal-15 th{{background:#1E2A3B;color:#fff;padding:.4rem .4rem;text-align:center;font-size:.65rem;white-space:nowrap}}
.modal-15 td{{padding:.38rem .4rem;text-align:center;border-bottom:1px solid #f0f0f0}}
.modal-obs{{font-size:.78rem;color:#374151;margin-top:.75rem;padding:.65rem .85rem;background:#f0f7ff;border-radius:6px;border-left:3px solid #1A56DB}}
.legend-row{{display:flex;gap:1rem;flex-wrap:wrap;margin-top:.65rem;font-size:.7rem;color:#555}}
.legend-dot{{display:inline-block;width:12px;height:12px;border-radius:50%;vertical-align:middle;margin-right:.3rem}}

.footer{{text-align:center;font-size:.7rem;color:#9ca3af;padding:1.5rem}}
@media print{{
  @page{{ size: landscape; margin: 10mm; }}
  .toolbar,.header-actions,.evol-btn{{display:none!important}}
  .modal-overlay{{display:none!important}}
  body{{background:#fff}}
  .container{{padding:0}}
  table{{box-shadow:none;font-size:9px;width:100%}}
  th,td{{padding:.25rem .35rem!important}}
}}
</style>
</head>
<body>

<div class="header">
  <div class="header-left">
    <h1>Reporte de transmisión de stock — Depósitos Fiscales</h1>
    <p>Datos al {fecha_display} &nbsp;·&nbsp; {total} depósitos</p>
  </div>
  <div class="header-actions">
    <button class="btn-hdr green" onclick="exportarExcel()">⬇ Excel</button>
    <button class="btn-hdr" onclick="exportarPDF()">🖨 PDF</button>
    <button class="btn-hdr" onclick="abrirGrafico()">📈 Evolución 15d</button>
  </div>
</div>

<div class="resumen">
  {resumen_items}
</div>


<div class="toolbar">
  <input type="text" id="busq" placeholder="Buscar LOT, Razón Social, CUIT…" oninput="filtrar()">
  <select id="filtro-estado" onchange="filtrar()">
    <option value="">Todos los estados</option>
    <option value="VERDE">Verde</option>
    <option value="AZUL">Celeste</option>
    <option value="AMARILLO">Amarillo</option>
    <option value="ROJO">Rojo</option>
    <option value="NEGRO">Negro</option>
  </select>
  <span class="counter" id="contador">{total} registros</span>
  <button class="toolbar-btn" onclick="limpiarFiltros()">✕ Limpiar</button>
</div>

<div class="container">
  <table id="tabla">
    <thead>
      <tr>
        <th onclick="sortTable(0)">ADU ↕</th>
        <th onclick="sortTable(1)">Aduana ↕</th>
        <th onclick="sortTable(2)">Dir. Regional ↕</th>
        <th onclick="sortTable(3)">LOT ↕</th>
        <th onclick="sortTable(4)">Razón Social ↕</th>
        <th>CUIT</th>
        <th>Estado</th>
        <th onclick="sortTable(7)">Últ. F.Reg ↕</th>
        <th onclick="sortTable(8)">Últ. F.Stock ↕</th>
        <th title="Verde: transmitió registro y stock ese día · Amarillo: transmitió solo uno de los dos · Rojo: no transmitió ninguno">Últimos 15 días</th>
        <th>Observación</th>
      </tr>
    </thead>
    <tbody id="tbody">
{filas_str}
    </tbody>
  </table>
</div>

<div class="footer">Generado por CosmoTools — DI REPA / ARCA &nbsp;·&nbsp; {fecha_display}</div>

<!-- ── Modal evolución 15d (gráfico de barras apiladas) ── -->
<div class="modal-overlay" id="modal-grafico-overlay" onclick="if(event.target===this)cerrarGrafico()">
  <div class="modal-box wide" style="padding:1.5rem">
    <button class="modal-close" onclick="cerrarGrafico()">×</button>
    <div class="modal-title">Evolución de cumplimiento — últimos 15 días</div>
    <div class="modal-sub">Distribución diaria de estados de transmisión al {fecha_display}</div>
    <canvas id="grafico-barras" height="90"></canvas>
  </div>
</div>

<!-- ── Modal detalle depósito ── -->
<div class="modal-overlay" id="modal-overlay" onclick="if(event.target===this)cerrarModalBtn()">
  <div class="modal-box wide">
    <button class="modal-close" onclick="cerrarModalBtn()">×</button>
    <div class="modal-title" id="modal-title"></div>
    <div class="modal-sub" id="modal-sub"></div>

    <div class="modal-section">
      <div class="modal-section-title">Detalle últimos 15 días</div>
      <div class="modal-15" id="modal-15"></div>
      <div class="legend-row">
        <span><span class="legend-dot" style="background:#00bf00"></span>Transmitió</span>
        <span><span class="legend-dot" style="background:#cc0000"></span>No transmitió (hábil)</span>
        <span><span class="legend-dot" style="background:#1a56db"></span>No hábil (Sáb/Dom/Feriado)</span>
      </div>
    </div>

    <div class="modal-obs" id="modal-obs"></div>

  </div>
</div>

<script>
// ── Datos embebidos ──────────────────────────────────────────────────────────
var DATOS  = {datos_js_str};
var FECHAS = {fechas_js_str};
var DIAS   = {dias_js_str};
var FERIADOS = {feriados_js_str};
var SERIE  = {serie_js_str};
var COLORS = {color_js};
var FECHA_CORTE_ISO = "{fecha_iso}";
var TENDENCIA = {tend_js_str};

// Índice activo para el modal
var _idxActivo = null;
var _grafico15d = null;

// ── Utilidades ───────────────────────────────────────────────────────────────
function esFeriadoOFinDeSemana(fechaStr, idx) {{
  var p = fechaStr.split('/');
  var d = new Date(parseInt(p[2]), parseInt(p[1])-1, parseInt(p[0]));
  if (d.getDay() === 0 || d.getDay() === 6) return true;
  return !!(FERIADOS && FERIADOS[idx]);
}}

// ── Modal detalle ────────────────────────────────────────────────────────────
function abrirModal(idx) {{
  _idxActivo = idx;
  var d = DATOS[idx];

  document.getElementById('modal-title').textContent = d.adu + '-' + d.lot + ' — ' + d.razon;
  function _tendLine(label, trans, no_trans, no_hab, pct, pct_prev, sym) {{
    var detalle = trans + ' transmitió · ' + no_trans + ' no transmitió · ' + no_hab + ' no hábil';
    var cumpl   = '<strong>Cumplimiento: ' + (pct||'—') + '</strong>';
    var prev    = '';
    if (pct_prev !== null && pct_prev !== undefined) {{
      var words = {{'↑':'Mejoró','↓':'Desmejoró','→':'Sin cambio'}};
      prev = '<span style="color:#6b7280"> · Período anterior: ' + pct_prev + ' → ' + (words[sym]||sym) + ' ' + (sym||'') + '</span>';
    }}
    return '<span style="display:block;margin-bottom:.25rem"><strong>' + label + '</strong> &nbsp; ' + detalle + ' &nbsp;·&nbsp; ' + cumpl + prev + '</span>';
  }}
  document.getElementById('modal-sub').innerHTML =
    '<strong>CUIT:</strong> ' + d.cuit +
    ' &nbsp;·&nbsp; <strong>Aduana:</strong> ' + d.ndu +
    ' &nbsp;·&nbsp; <strong>Tipo:</strong> ' + (d.tipo||'—') +
    ' &nbsp;·&nbsp; <strong>Última F.Reg:</strong> ' + (d.freg||'—') +
    ' &nbsp;·&nbsp; <strong>Última F.Stock:</strong> ' + (d.fstock||'—') +
    '<div style="margin-top:.5rem;font-size:.75rem;color:#374151;line-height:1.8;padding:.5rem .75rem;background:#f8fafc;border-radius:5px;border-left:3px solid #1A56DB">' +
    _tendLine('F. Registro', d.freg_t||0, d.freg_nt||0, d.freg_nh||0, d.pct_reg, d.pct_reg_prev, d.treg) +
    _tendLine('F. Stock',    d.fstock_t||0, d.fstock_nt||0, d.fstock_nh||0, d.pct_stock, d.pct_stock_prev, d.tstock) +
    '</div>';

  // Tabla 15 días
  var thead = '<tr><th style="min-width:90px;text-align:left;padding:.4rem .6rem">Indicador</th>';
  for(var i=0;i<15;i++) {{
    var esFer = esFeriadoOFinDeSemana(FECHAS[i], i);
    thead += '<th style="min-width:60px;' + (esFer?'background:#1a56db;':'') + '">' +
             DIAS[i] + '<br><span style="font-weight:400">' + FECHAS[i] + '</span></th>';
  }}
  thead += '</tr>';

  function buildRow(label, arr) {{
    var row = '<tr><td style="font-weight:600;white-space:nowrap;padding:.4rem .6rem">' + label + '</td>';
    for(var i=0;i<15;i++) {{
      var val = arr[i];
      var esFer = esFeriadoOFinDeSemana(FECHAS[i], i);
      var color = val ? '#00bf00' : (esFer ? '#1a56db' : '#cc0000');
      row += '<td><span style="display:inline-block;width:16px;height:16px;border-radius:50%;background:' + color + '"></span></td>';
    }}
    return row + '</tr>';
  }}

  var u15s = d.u15s || d.u15;
  document.getElementById('modal-15').innerHTML =
    '<table><thead>' + thead + '</thead><tbody>' +
    buildRow('F. Registro', d.u15) +
    buildRow('F. Stock',    u15s) +
    '</tbody></table>';

  document.getElementById('modal-obs').innerHTML = d.coment || '';

  document.getElementById('modal-overlay').classList.add('open');
}}

function cerrarModalBtn() {{
  document.getElementById('modal-overlay').classList.remove('open');
  _idxActivo = null;
}}

// ── Modal gráfico 15d ────────────────────────────────────────────────────────
function abrirGrafico() {{
  document.getElementById('modal-grafico-overlay').classList.add('open');
  if(!_grafico15d) {{
    var ctx = document.getElementById('grafico-barras').getContext('2d');
    var labels    = SERIE.map(function(r){{return r[0];}});
    var datasets  = [
      {{label:'Verde',    data:SERIE.map(function(r){{return r[5]}}), backgroundColor:'#00bf00'}},
      {{label:'Celeste',     data:SERIE.map(function(r){{return r[1]}}), backgroundColor:'#29b6f6'}},
      {{label:'Amarillo', data:SERIE.map(function(r){{return r[4]}}), backgroundColor:'#e6b800'}},
      {{label:'Rojo',     data:SERIE.map(function(r){{return r[3]}}), backgroundColor:'#bf0000'}},
      {{label:'Negro',    data:SERIE.map(function(r){{return r[2]}}), backgroundColor:'#333333'}},
    ];
    _grafico15d = new Chart(ctx, {{
      type: 'bar',
      data: {{ labels: labels, datasets: datasets }},
      options: {{
        responsive: true,
        plugins: {{
          legend: {{ position:'bottom', labels:{{ font:{{ size:11 }} }} }},
          title: {{ display:true, text:'Evolución de cumplimiento — últimos 15 días', font:{{ size:13 }} }}
        }},
        scales: {{
          x: {{ stacked:true, ticks:{{ font:{{ size:10 }} }} }},
          y: {{ stacked:true, ticks:{{ font:{{ size:10 }} }} }}
        }}
      }}
    }});
  }}
}}

function cerrarGrafico() {{
  document.getElementById('modal-grafico-overlay').classList.remove('open');
}}

// ── Filtrado y ordenamiento ───────────────────────────────────────────────────
document.addEventListener('keydown', function(e) {{
  if(e.key==='Escape') {{
    cerrarModalBtn();
    cerrarGrafico();
  }}
}});

document.getElementById('tbody').addEventListener('click', function(e) {{
  var dot = e.target.closest('.dot');
  if(dot) abrirModal(parseInt(dot.dataset.idx));
}});

function filtrar() {{
  var q   = document.getElementById('busq').value.toLowerCase().trim();
  var est = document.getElementById('filtro-estado').value;
  var rows = document.getElementById('tbody').querySelectorAll('tr');
  var vis = 0;
  rows.forEach(function(r) {{
    // Buscar en ADU(0), Aduana(1), Dir. Regional(2), LOT(3), Razón Social(4), CUIT(5)
    var txt = [0,1,2,3,4,5].map(function(i){{ return r.cells[i]?r.cells[i].textContent.toLowerCase():''; }}).join(' ');
    var show = (!q || txt.includes(q)) && (!est || r.dataset.sem === est);
    r.style.display = show ? '' : 'none';
    if(show) vis++;
  }});
  document.getElementById('contador').textContent = vis + ' registros';
}}

function filtrarEstado(estado) {{
  document.getElementById('filtro-estado').value = estado;
  filtrar();
  document.getElementById('tabla').scrollIntoView({{behavior:'smooth'}});
}}

function limpiarFiltros() {{
  document.getElementById('busq').value = '';
  document.getElementById('filtro-estado').value = '';
  filtrar();
}}

var sortDir = {{}};
function sortTable(col) {{
  var tbody = document.getElementById('tbody');
  var rows  = Array.from(tbody.querySelectorAll('tr:not([style*="display: none"])'));
  var all   = Array.from(tbody.querySelectorAll('tr'));
  var asc   = !sortDir[col]; sortDir[col] = asc;
  all.sort(function(a,b) {{
    var ta = a.cells[col]?a.cells[col].textContent.trim():'';
    var tb = b.cells[col]?b.cells[col].textContent.trim():'';
    return asc ? ta.localeCompare(tb,'es') : tb.localeCompare(ta,'es');
  }});
  all.forEach(function(r){{ tbody.appendChild(r); }});
}}

// ── Exportación Excel (SheetJS) ──────────────────────────────────────────────
function exportarExcel() {{
  var filas = Array.from(document.getElementById('tbody').querySelectorAll('tr'))
    .filter(function(r){{ return r.style.display !== 'none'; }});

  var data = [['ADU','Aduana','Dir. Regional','LOT','Razón Social','CUIT','Estado','Últ. F.Registro','Últ. F.Stock','Reg. últ.15d','Stock últ.15d','Observación']];
  filas.forEach(function(r) {{
    var idx = parseInt(r.dataset.idx);
    var d = DATOS[idx];
    data.push([d.adu, d.ndu, d.dira, d.lot, d.razon, d.cuit, d.sem, d.freg||'', d.fstock||'',
               (d.u15||[]).join(''), (d.u15s||[]).join(''), d.coment||'']);
  }});

  var ws = XLSX.utils.aoa_to_sheet(data);

  // Ancho de columnas
  ws['!cols'] = [4,26,18,8,30,14,10,14,14,14,14,40].map(function(w){{return {{wch:w}}}});

  // Estilo encabezado (solo SheetJS Pro; en la versión free se aplica color manualmente post-open)
  var SEM_XLSX = {{'VERDE':'FF00BF00','AZUL':'FF29B6F6','AMARILLO':'FFE6B800','ROJO':'FFBF0000','NEGRO':'FF333333'}};
  for(var i=1;i<data.length;i++) {{
    var cell = ws[XLSX.utils.encode_cell({{r:i,c:6}})];
    if(cell) {{
      cell.s = {{
        fill:{{fgColor:{{rgb:SEM_XLSX[data[i][6]]||'FFCCCCCC'}}}},
        font:{{color:{{rgb:'FFFFFFFF'}},bold:true}},
        alignment:{{horizontal:'center'}}
      }};
    }}
  }}

  var wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, 'Stock Depósitos');
  var filtro = document.getElementById('filtro-estado').value || 'todos';
  var fecha = FECHA_CORTE_ISO.replace(/-/g,'');
  XLSX.writeFile(wb, 'ReporteStock_' + fecha + '_' + filtro + '.xlsx');
}}

// ── Exportación PDF (print CSS) ──────────────────────────────────────────────
function exportarPDF() {{
  // Ocultar filas no visibles antes de imprimir y restaurar después
  var ocultas = [];
  document.querySelectorAll('#tbody tr').forEach(function(r) {{
    if(r.style.display === 'none') ocultas.push(r);
  }});
  ocultas.forEach(function(r){{ r.setAttribute('data-hidden','1'); r.style.display='none'; }});

  // Inyectar título de filtro para impresión
  var info = document.createElement('div');
  info.id = '_print_info';
  info.style.cssText = 'padding:.5rem 1.5rem;font-size:.75rem;color:#555;background:#fff;border-bottom:1px solid #e5e7eb';
  var est = document.getElementById('filtro-estado').value || 'Todos';
  var busq = document.getElementById('busq').value || '';
  info.textContent = 'Filtros aplicados — Estado: ' + est + (busq?' | Búsqueda: "'+busq+'"':'') +
    ' — ' + document.getElementById('contador').textContent;
  document.querySelector('.container').insertAdjacentElement('beforebegin', info);

  window.print();

  // Restaurar
  document.getElementById('_print_info').remove();
  ocultas.forEach(function(r){{ r.removeAttribute('data-hidden'); r.style.display=''; }});
}}
</script>
</body>
</html>"""
