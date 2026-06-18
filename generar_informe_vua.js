/**
 * generar_informe_vua.js
 * Genera el informe Word completo del proyecto VUA a partir de datos JSON
 * Uso: node generar_informe_vua.js datos.json salida.docx
 */

const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, HeadingLevel, BorderStyle, WidthType,
  ShadingType, VerticalAlign, PageNumber, PageBreak, LevelFormat,
  TabStopType, TabStopPosition
} = require('docx');
const fs = require('fs');

// ─── CONSTANTES ───────────────────────────────────────────────────────────────
const C = {
  navy:    "242D4F",
  blue:    "1A56DB",
  accent:  "0E7490",
  green:   "057A55",
  warn:    "B45309",
  red:     "DC2626",
  white:   "FFFFFF",
  light:   "F0F4FF",
  lightG:  "F0FFF4",
  lightW:  "FFFBEB",
  gray:    "6B7280",
  border:  "CBD5E1",
  text:    "1E293B",
};

const W = 11906;   // A4
const H = 16838;
const ML = 1701;   // 3cm
const MR = 1134;   // 2cm
const MT = 1134;
const MB = 1134;
const CW = W - ML - MR;  // Ancho de contenido ~9071 DXA

const bdr = (color = C.border) => ({ style: BorderStyle.SINGLE, size: 4, color });
const BORDERS = { top: bdr(), bottom: bdr(), left: bdr(), right: bdr() };
const NO_BORDERS = {
  top: { style: BorderStyle.NONE }, bottom: { style: BorderStyle.NONE },
  left: { style: BorderStyle.NONE }, right: { style: BorderStyle.NONE },
};
const PAD = { top: 80, bottom: 80, left: 140, right: 140 };

// ─── HELPERS ─────────────────────────────────────────────────────────────────

/**
 * Fix 1: normaliza texto de la BD — restaura tildes y caracteres especiales
 * que pueden perderse según el encoding de SQLite o el transporte JSON.
 */
function norm(text) {
  if (!text) return "";
  return text
    // Secuencias UTF-8 mal decodificadas como latin-1
    .replace(/Ã¡/g, "á").replace(/Ã©/g, "é").replace(/Ã­/g, "í")
    .replace(/Ã³/g, "ó").replace(/Ãº/g, "ú").replace(/Ã±/g, "ñ")
    .replace(/Ã/g, "Á").replace(/Ã‰/g, "É").replace(/Ã/g, "Í")
    .replace(/Ã"/g, "Ó").replace(/Ãš/g, "Ú").replace(/Ã'/g, "Ñ")
    .replace(/Â¿/g, "¿").replace(/Â¡/g, "¡")
    // Entidades HTML residuales
    .replace(/&aacute;/g, "á").replace(/&eacute;/g, "é")
    .replace(/&iacute;/g, "í").replace(/&oacute;/g, "ó")
    .replace(/&uacute;/g, "ú").replace(/&ntilde;/g, "ñ")
    .replace(/&Aacute;/g, "Á").replace(/&Eacute;/g, "É")
    .replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
    // Espacios no-break y caracteres de control
    .replace(/\u00a0/g, " ").replace(/[\u0000-\u0008\u000b-\u001f]/g, "")
    .trim();
}

const empty = (sp = 80) => new Paragraph({ spacing: { after: sp }, children: [new TextRun("")] });

const p = (text, opts = {}) => new Paragraph({
  alignment: opts.align || AlignmentType.JUSTIFIED,
  spacing: { after: opts.after ?? 120, before: opts.before ?? 0 },
  children: [new TextRun({
    text: text || "",
    font: "Arial",
    size: opts.size || 22,
    bold: opts.bold || false,
    italics: opts.italic || false,
    color: opts.color || C.text,
  })]
});

const heading = (text, level = 1) => new Paragraph({
  heading: level === 1 ? HeadingLevel.HEADING_1 : HeadingLevel.HEADING_2,
  spacing: { before: level === 1 ? 400 : 280, after: 160 },
  children: [new TextRun({
    text,
    font: "Arial",
    bold: true,
    size: level === 1 ? 28 : 24,
    color: C.navy,
  })]
});

const subheading = (text) => new Paragraph({
  spacing: { before: 200, after: 100 },
  children: [new TextRun({ text, font: "Arial", bold: true, size: 22, color: C.blue })]
});

const label = (lbl, val, opts = {}) => new Paragraph({
  spacing: { after: opts.after ?? 80 },
  children: [
    new TextRun({ text: lbl + ": ", font: "Arial", bold: true, size: 21, color: C.navy }),
    new TextRun({ text: val || "—", font: "Arial", size: 21, color: C.text }),
  ]
});

const divider = () => new Paragraph({
  spacing: { after: 0, before: 0 },
  border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: C.blue, space: 1 } },
  children: [new TextRun("")]
});

// Celda de tabla
const tc = (text, width, opts = {}) => new TableCell({
  borders: opts.noBorder ? NO_BORDERS : BORDERS,
  width: { size: width, type: WidthType.DXA },
  shading: opts.fill ? { fill: opts.fill, type: ShadingType.CLEAR } : undefined,
  margins: opts.pad || PAD,
  verticalAlign: opts.vAlign || VerticalAlign.TOP,
  children: [new Paragraph({
    alignment: opts.align || AlignmentType.LEFT,
    spacing: { after: 0 },
    children: [new TextRun({
      text: String(text || ""),
      font: "Arial",
      size: opts.size || 20,
      bold: opts.bold || false,
      color: opts.color || C.text,
    })]
  })]
});

// Celda con múltiples párrafos
const tcMulti = (paragraphs, width, opts = {}) => new TableCell({
  borders: opts.noBorder ? NO_BORDERS : BORDERS,
  width: { size: width, type: WidthType.DXA },
  shading: opts.fill ? { fill: opts.fill, type: ShadingType.CLEAR } : undefined,
  margins: PAD,
  verticalAlign: VerticalAlign.TOP,
  children: paragraphs,
});

// Fila de tabla de 2 columnas (etiqueta / valor)
const tr2 = (lbl, val, wL = 2800, wR = 6271, hdr = false) => new TableRow({
  children: [
    tc(lbl, wL, { fill: hdr ? C.navy : C.light, bold: hdr, color: hdr ? C.white : C.navy, size: 20 }),
    tc(val, wR, { fill: hdr ? C.navy : C.white, bold: hdr, color: hdr ? C.white : C.text, size: 20 }),
  ]
});

// Header de tabla (fila)
const trHeader = (cols, widths) => new TableRow({
  tableHeader: true,
  children: cols.map((col, i) => tc(col, widths[i], { fill: C.navy, bold: true, color: C.white, size: 20 }))
});

// Tabla simple
const makeTable = (headers, widths, rows) => new Table({
  width: { size: CW, type: WidthType.DXA },
  columnWidths: widths,
  rows: [
    trHeader(headers, widths),
    ...rows
  ]
});

// Badge de estado
const estadoBadge = (estado) => {
  const s = (estado || "").toLowerCase();
  let fill = C.lightW;
  if (s.includes("complet") || s.includes("definid")) fill = C.lightG;
  else if (s.includes("análisis") || s.includes("analisis") || s.includes("curso") || s.includes("desarrollo")) fill = "EFF6FF";
  return fill;
};

// ─── SECCIONES DEL INFORME ────────────────────────────────────────────────────

function buildPortada(datos, fecha) {
  return [
    empty(400),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { after: 160 },
      border: { bottom: { style: BorderStyle.SINGLE, size: 12, color: C.navy, space: 4 } },
      children: [new TextRun({ text: "ARCA — ADUANA ARGENTINA", font: "Arial", size: 20, bold: true, color: C.gray })]
    }),
    empty(40),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { after: 80 },
      children: [new TextRun({ text: "PROYECTO VUA", font: "Arial", size: 56, bold: true, color: C.navy })]
    }),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { after: 80 },
      children: [new TextRun({ text: "Ventanilla Única Aeroportuaria", font: "Arial", size: 32, color: C.blue })]
    }),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { after: 400 },
      children: [new TextRun({ text: "Estado de Situación — Carga Aérea", font: "Arial", size: 26, italic: true, color: C.gray })]
    }),
    divider(),
    empty(200),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { after: 80 },
      children: [new TextRun({ text: fecha, font: "Arial", size: 24, color: C.text })]
    }),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { after: 80 },
      children: [new TextRun({ text: "Dirección de Reingeniería de Procesos Aduaneros (DI REPA)", font: "Arial", size: 22, color: C.navy })]
    }),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { after: 0 },
      children: [new TextRun({ text: "Sección Simplificación de Procesos Operativos (DV MPAD)", font: "Arial", size: 20, color: C.gray })]
    }),
    new Paragraph({
      children: [new PageBreak()],
      spacing: { after: 0 }
    }),
  ];
}

function buildResumenEjecutivo(config, secNum = 1) {
  const resumen = (config.find(c => c.clave === 'resumen_ejecutivo') || {}).contenido || "";
  return [
    heading("1. Resumen Ejecutivo"),
    divider(),
    empty(120),
    ...resumen.split('\n').filter(l => l.trim()).map(l => p(l, { after: 100 })),
    empty(200),
  ];
}

function buildAntecedentes(config, secNum = 2) {
  const ant = (config.find(c => c.clave === 'antecedentes') || {}).contenido || "";
  const obj = (config.find(c => c.clave === 'objetivo') || {}).contenido || "";
  const rol = (config.find(c => c.clave === 'rol_dga') || {}).contenido || "";
  const alc = (config.find(c => c.clave === 'alcance_operativo') || {}).contenido || "";
  return [
    heading("2. Antecedentes y Contexto"),
    divider(),
    empty(120),
    subheading("2.1. Antecedentes"),
    ...ant.split('\n').filter(l => l.trim()).map(l => p(l, { after: 80 })),
    empty(120),
    subheading("2.2. Objetivo del Proyecto"),
    ...obj.split('\n').filter(l => l.trim()).map(l => p(l, { after: 80 })),
    empty(120),
    subheading("2.3. Rol de la DGA"),
    ...rol.split('\n').filter(l => l.trim()).map(l => p(l, { after: 80 })),
    empty(120),
    subheading("2.4. Alcance Operativo"),
    ...alc.split('\n').filter(l => l.trim()).map(l => p(l, { after: 80 })),
    empty(200),
  ];
}

function buildEjes(ejes, secNum = 3) {
  const items = [];
  items.push(heading(`${secNum}. Ejes de Trabajo`));
  items.push(divider());
  items.push(empty(120));

  // Tabla resumen de ejes
  const resumenRows = ejes.map(eje => new TableRow({
    children: [
      tc(eje.id, 700, { bold: true, color: C.navy }),
      tc(norm(eje.nombre), 5500),
      tc(norm(eje.estado), 2871, { fill: estadoBadge(eje.estado), align: AlignmentType.CENTER }),
    ]
  }));
  items.push(makeTable(["ID", "Eje", "Estado"], [700, 5500, 2871], resumenRows));
  items.push(empty(300));

  // Detalle de cada eje
  ejes.forEach((eje, idx) => {
    items.push(subheading(`${secNum}.${idx+1}. ${norm(eje.nombre)}`));

    if (eje.descripcion) {
      items.push(p(norm(eje.descripcion), { after: 120 }));
    }

    const bloques = [];

    if (eje.propuesta_vucea) {
      bloques.push(new TableRow({
        children: [
          tcMulti([
            new Paragraph({ spacing: { after: 60 }, children: [new TextRun({ text: "PROPUESTA DE VUCEA", font: "Arial", bold: true, size: 18, color: C.blue })] }),
            new Paragraph({ spacing: { after: 0 }, children: [new TextRun({ text: norm(eje.propuesta_vucea), font: "Arial", size: 20, color: C.text })] }),
          ], CW, { fill: "EFF6FF" })
        ]
      }));
    }

    if (eje.postura_aduana) {
      bloques.push(new TableRow({
        children: [
          tcMulti([
            new Paragraph({ spacing: { after: 60 }, children: [new TextRun({ text: "POSTURA DE ADUANA", font: "Arial", bold: true, size: 18, color: C.warn })] }),
            new Paragraph({ spacing: { after: 0 }, children: [new TextRun({ text: norm(eje.postura_aduana), font: "Arial", size: 20, color: C.text })] }),
          ], CW, { fill: C.lightW })
        ]
      }));
    }

    if (eje.recomendacion) {
      bloques.push(new TableRow({
        children: [
          tcMulti([
            new Paragraph({ spacing: { after: 60 }, children: [new TextRun({ text: "RECOMENDACIÓN DI REPA", font: "Arial", bold: true, size: 18, color: C.green })] }),
            new Paragraph({ spacing: { after: 0 }, children: [new TextRun({ text: norm(eje.recomendacion), font: "Arial", size: 20, color: C.text })] }),
          ], CW, { fill: C.lightG })
        ]
      }));
    }

    if (bloques.length > 0) {
      items.push(new Table({
        width: { size: CW, type: WidthType.DXA },
        columnWidths: [CW],
        rows: bloques,
      }));
    }

    items.push(empty(idx < ejes.length - 1 ? 200 : 100));
  });

  items.push(empty(100));
  return items;
}

function buildRiesgos(riesgos, secNum = 4) {
  const items = [];
  items.push(heading(`${secNum}. Riesgos Identificados`));
  items.push(divider());
  items.push(empty(120));

  const colorRiesgo = (val) => {
    const v = (val || "").toLowerCase();
    if (v === "alto" || v === "alta") return C.red;
    if (v === "medio" || v === "media") return C.warn;
    return C.green;
  };

  riesgos.forEach((r, idx) => {
    // Fila de encabezado del riesgo
    items.push(new Table({
      width: { size: CW, type: WidthType.DXA },
      columnWidths: [700, 5500, 1385, 1386],
      rows: [
        // Header del riesgo
        new TableRow({
          children: [
            tc(norm(r.codigo) || `${secNum}.${idx+1}`, 700, { fill: C.navy, bold: true, color: C.white, size: 20 }),
            tc(norm(r.titulo), 5500, { fill: C.navy, bold: true, color: C.white, size: 20 }),
            tc(`Prob: ${r.probabilidad || "N/D"}`, 1385, { fill: colorRiesgo(r.probabilidad), bold: true, color: C.white, size: 18, align: AlignmentType.CENTER }),
            tc(`Imp: ${r.impacto || "N/D"}`, 1386, { fill: colorRiesgo(r.impacto), bold: true, color: C.white, size: 18, align: AlignmentType.CENTER }),
          ]
        }),
        // Descripción
        new TableRow({
          children: [
            tc("Descripción", 700, { fill: C.light, bold: true, color: C.navy, size: 18 }),
            tcMulti([new Paragraph({ spacing: { after: 0 }, children: [new TextRun({ text: norm(r.descripcion), font: "Arial", size: 20 })] })], 7771),
          ]
        }),
        // Mitigación
        new TableRow({
          children: [
            tc("Mitigación", 700, { fill: C.lightG, bold: true, color: C.green, size: 18 }),
            tcMulti([new Paragraph({ spacing: { after: 0 }, children: [new TextRun({ text: norm(r.mitigacion), font: "Arial", size: 20 })] })], 7771, { fill: C.lightG }),
          ]
        }),
      ]
    }));
    if (idx < riesgos.length - 1) items.push(empty(120));
  });

  items.push(empty(200));
  return items;
}

function buildEquipo(equipo, secNum = 5) {
  const items = [];
  items.push(heading(`${secNum}. Equipo del Proyecto`));
  items.push(divider());
  items.push(empty(120));

  // Agrupar por organismo
  const grupos = {};
  equipo.forEach(m => {
    const org = m.organismo || "Sin organismo";
    if (!grupos[org]) grupos[org] = [];
    grupos[org].push(m);
  });

  Object.entries(grupos).forEach(([org, miembros]) => {
    items.push(subheading(org));
    const rows = miembros.map(m => new TableRow({
      children: [
        tc(norm(m.nombre), 2800, { bold: true, color: C.navy }),
        tc(norm(m.cargo), 4000),
        tc(m.email || "", 2271, { color: C.blue, size: 19 }),
      ]
    }));
    items.push(makeTable(["Nombre", "Cargo", "Email"], [2800, 4000, 2271], rows));
    items.push(empty(120));
  });

  items.push(empty(100));
  return items;
}

function buildCronologia(cronologia, secNum = 6) {
  const items = [];
  items.push(heading(`${secNum}. Cronología de Actividades`));
  items.push(divider());
  items.push(empty(120));

  const colorEstado = (estado) => {
    const s = (estado || "").toLowerCase();
    if (s.includes("complet")) return { fill: C.lightG, color: C.green };
    if (s.includes("curso") || s.includes("progreso")) return { fill: "EFF6FF", color: C.blue };
    return { fill: C.lightW, color: C.warn };
  };

  const rows = cronologia.map(item => {
    const cs = colorEstado(item.estado);
    return new TableRow({
      children: [
        tc(item.fecha, 1200, { size: 19, color: C.gray }),
        tc(norm(item.actividad), 4500),
        tc(item.participantes || "", 2000, { size: 18, color: C.gray }),
        tc(item.estado, 1371, { fill: cs.fill, color: cs.color, size: 18, align: AlignmentType.CENTER }),
      ]
    });
  });

  items.push(makeTable(
    ["Fecha", "Actividad", "Participantes", "Estado"],
    [1200, 4500, 2000, 1371],
    rows
  ));
  items.push(empty(200));
  return items;
}

function buildGlosario(glosario, secNum = 7) {
  const items = [];
  items.push(heading(`${secNum}. Glosario`));
  items.push(divider());
  items.push(empty(120));

  const rows = glosario.map(g => new TableRow({
    children: [
      tc(norm(g.termino), 2200, { bold: true, color: C.navy, fill: C.light }),
      tc(norm(g.definicion), 5600),
      tc(g.categoria || "", 1271, { size: 18, color: C.gray, align: AlignmentType.CENTER }),
    ]
  }));

  items.push(makeTable(["Término", "Definición", "Categoría"], [2200, 5600, 1271], rows));
  items.push(empty(200));
  return items;
}

function buildMinutas(minutas, secNum = 8) {
  if (!minutas || minutas.length === 0) return [];
  const items = [];
  items.push(heading(`${secNum}. Minutas de Reuniones`));
  items.push(divider());
  items.push(empty(120));

  const rows = minutas.map(m => {
    // Fix 3: participantes se guarda como JSON array en la BD
    let partic = m.participantes || "";
    if (typeof partic === "string" && partic.startsWith("[")) {
      try {
        const arr = JSON.parse(partic);
        partic = arr.map(p => typeof p === "object" ? (p.nombre || p) : p).join(", ");
      } catch(e) { /* usar como string */ }
    } else if (Array.isArray(partic)) {
      partic = partic.map(p => typeof p === "object" ? (p.nombre || p) : p).join(", ");
    }
    return new TableRow({
      children: [
        tc(norm(m.fecha) || "", 1300, { size: 19, color: C.gray }),
        tc(norm(m.asunto) || "", 4000, { bold: true }),
        tc(norm(m.lugar) || "", 1800, { size: 18, color: C.gray }),
        tc(norm(partic), 1971, { size: 18, color: C.gray }),
      ]
    });
  });

  items.push(makeTable(["Fecha", "Asunto", "Lugar", "Participantes"], [1300, 4000, 1800, 1971], rows));
  items.push(empty(200));
  return items;
}

// ─── FUNCIÓN PRINCIPAL ────────────────────────────────────────────────────────
function buildDocument(datos) {
  const fecha = new Date().toLocaleDateString('es-AR', { day: '2-digit', month: 'long', year: 'numeric' });

  const {
    config = [], ejes = [], equipo = [], cronologia = [],
    glosario = [], riesgos = [], minutas = []
  } = datos;

  // Fix 2: numeración dinámica de secciones
  const tienAntecedentes = config.some(c => c.clave === 'antecedentes' && c.contenido);
  let sec = 1; // El resumen ejecutivo siempre es sección 1
  const secResumen    = sec++;      // 1
  const secAntec      = sec++;      // 2
  const secEjes       = sec++;      // 3
  const secRiesgos    = sec++;      // 4
  const secEquipo     = sec++;      // 5
  const secCrono      = sec++;      // 6
  const secGlosario   = sec++;      // 7
  const secMinutas    = minutas.length > 0 ? sec++ : null;

  const content = [
    ...buildPortada(datos, fecha),
    ...buildResumenEjecutivo(config, secResumen),
    ...buildAntecedentes(config, secAntec),
    ...buildEjes(ejes, secEjes),
    ...buildRiesgos(riesgos, secRiesgos),
    ...buildEquipo(equipo, secEquipo),
    ...buildCronologia(cronologia, secCrono),
    ...buildGlosario(glosario, secGlosario),
    ...(secMinutas ? buildMinutas(minutas, secMinutas) : []),
  ];

  return new Document({
    styles: {
      default: { document: { run: { font: "Arial", size: 22 } } },
      paragraphStyles: [
        { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
          run: { size: 28, bold: true, font: "Arial", color: C.navy },
          paragraph: { spacing: { before: 400, after: 160 }, outlineLevel: 0 } },
        { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
          run: { size: 24, bold: true, font: "Arial", color: C.navy },
          paragraph: { spacing: { before: 280, after: 140 }, outlineLevel: 1 } },
      ]
    },
    numbering: {
      config: [
        { reference: "bullets",
          levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
            style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
      ]
    },
    sections: [{
      properties: {
        page: {
          size: { width: W, height: H },
          margin: { top: MT, right: MR, bottom: MB, left: ML }
        }
      },
      headers: {
        default: new Header({
          children: [new Paragraph({
            spacing: { after: 0 },
            border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: C.border, space: 4 } },
            children: [
              new TextRun({ text: "Proyecto VUA — Estado de Situación", font: "Arial", size: 18, color: C.gray }),
              new TextRun({ text: "   |   DI REPA / DV MPAD — ARCA", font: "Arial", size: 18, color: C.gray }),
            ]
          })]
        })
      },
      footers: {
        default: new Footer({
          children: [new Paragraph({
            spacing: { after: 0 },
            border: { top: { style: BorderStyle.SINGLE, size: 4, color: C.border, space: 4 } },
            tabStops: [{ type: TabStopType.RIGHT, position: CW }],
            children: [
              new TextRun({ text: fecha, font: "Arial", size: 18, color: C.gray }),
              new TextRun({ text: "\t", font: "Arial", size: 18 }),
              new TextRun({ children: ["Página ", PageNumber.CURRENT], font: "Arial", size: 18, color: C.gray }),
            ]
          })]
        })
      },
      children: content,
    }]
  });
}


// ─── SECCIÓN EXTRA: MINUTA COMPLETA ──────────────────────────────────────────
// Mejora 5: genera una minuta con el mismo sistema de estilos del informe VUA

function buildMinutaCompleta(datos) {
  const fecha  = datos.fecha  || new Date().toLocaleDateString('es-AR', { day:'2-digit', month:'long', year:'numeric' });
  const asunto = datos.asunto || "";
  const lugar  = datos.lugar  || "";

  const items = [];

  // Encabezado
  items.push(new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 80 },
    children: [new TextRun({ text: "ACTA DE REUNIÓN", font: "Arial", size: 32, bold: true, color: C.navy })]
  }));
  items.push(new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 200 },
    children: [new TextRun({ text: "PROYECTO VUA — Ventanilla Única Aeroportuaria", font: "Arial", size: 22, color: C.blue, italic: true })]
  }));
  items.push(divider());
  items.push(empty(160));

  // Metadatos
  items.push(new Table({
    width: { size: CW, type: WidthType.DXA },
    columnWidths: [2000, 7071],
    rows: [
      tr2("Asunto",  asunto),
      tr2("Fecha",   fecha),
      tr2("Lugar",   lugar),
    ]
  }));
  items.push(empty(200));

  // Participantes
  if (datos.participantes && datos.participantes.length > 0) {
    items.push(subheading("Participantes"));
    const rows = datos.participantes.map(p => new TableRow({
      children: [
        tc(p.nombre || "", 3200, { bold: true, color: C.navy }),
        tc(p.cargo  || "", 5871),
      ]
    }));
    items.push(makeTable(["Nombre", "Cargo / Organismo"], [3200, 5871], rows));
    items.push(empty(200));
  }

  // Temas, Acuerdos, Próximos pasos
  const secciones = [
    { titulo: "Temas tratados",  items: datos.temas    || [] },
    { titulo: "Acuerdos",        items: datos.acuerdos || [] },
    { titulo: "Próximos pasos",  items: datos.proximos || [] },
  ];

  secciones.forEach(sec => {
    if (!sec.items.length) return;
    items.push(subheading(sec.titulo));
    sec.items.forEach((item, idx) => {
      items.push(new Paragraph({
        spacing: { after: 80 },
        numbering: { reference: "bullets", level: 0 },
        children: [new TextRun({ text: item, font: "Arial", size: 22, color: C.text })]
      }));
    });
    items.push(empty(120));
  });

  // Pendientes de reuniones anteriores (si los hay)
  if (datos.pendientes_anteriores && datos.pendientes_anteriores.length > 0) {
    items.push(subheading("Pendientes de reuniones anteriores"));
    datos.pendientes_anteriores.forEach(item => {
      items.push(new Paragraph({
        spacing: { after: 80 },
        numbering: { reference: "bullets", level: 0 },
        children: [new TextRun({ text: item, font: "Arial", size: 22, color: C.warn })]
      }));
    });
    items.push(empty(120));
  }

  items.push(divider());
  items.push(empty(200));


  return new Document({
    styles: {
      default: { document: { run: { font: "Arial", size: 22 } } },
    },
    numbering: {
      config: [{
        reference: "bullets",
        levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } } }]
      }]
    },
    sections: [{
      properties: {
        page: {
          size: { width: W, height: H },
          margin: { top: MT, right: MR, bottom: MB, left: ML }
        }
      },
      headers: {
        default: new Header({
          children: [new Paragraph({
            spacing: { after: 0 },
            border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: C.border, space: 4 } },
            children: [
              new TextRun({ text: `Acta: ${asunto}`, font: "Arial", size: 18, color: C.gray }),
              new TextRun({ text: `   |   ${fecha}   |   DI REPA / DV MPAD — ARCA`, font: "Arial", size: 18, color: C.gray }),
            ]
          })]
        })
      },
      footers: {
        default: new Footer({
          children: [new Paragraph({
            spacing: { after: 0 },
            border: { top: { style: BorderStyle.SINGLE, size: 4, color: C.border, space: 4 } },
            tabStops: [{ type: TabStopType.RIGHT, position: CW }],
            children: [
              new TextRun({ text: "Proyecto VUA — ARCA", font: "Arial", size: 18, color: C.gray }),
              new TextRun({ text: "\t", font: "Arial", size: 18 }),
              new TextRun({ children: ["Página ", PageNumber.CURRENT], font: "Arial", size: 18, color: C.gray }),
            ]
          })]
        })
      },
      children: items,
    }]
  });
}

// ─── ENTRY POINT ─────────────────────────────────────────────────────────────
const args = process.argv.slice(2);
if (args.length < 2) {
  console.error("Uso: node generar_informe_vua.js datos.json salida.docx");
  process.exit(1);
}

const datos = JSON.parse(fs.readFileSync(args[0], 'utf8'));
// Mejora 5: soporte para modo minuta además del informe completo
const modo = args[2] || "informe";
const doc = modo === "minuta" ? buildMinutaCompleta(datos) : buildDocument(datos);
Packer.toBuffer(doc).then(buf => {
  fs.writeFileSync(args[1], buf);
  console.log("OK:" + args[1]);
}).catch(e => {
  console.error("ERROR:" + e.message);
  process.exit(1);
});
