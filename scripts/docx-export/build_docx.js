const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType,
  Table, TableRow, TableCell, WidthType, BorderStyle, ImageRun,
  TableOfContents, PageBreak, LevelFormat, ShadingType, Header, Footer,
  PageNumber, VerticalAlign,
} = require("docx");

const US_LETTER = { width: 12240, height: 15840 };
const MARGIN = { top: 1440, bottom: 1440, left: 1440, right: 1440 };
const OUT_DIR = "C:\\Users\\User\\Desktop\\Myra\\Panga\\.claude\\worktrees\\brd-frs-docx-v1\\docs";
const DIAG_DIR = path.join(OUT_DIR, "diagrams");
const SCRATCH = "C:\\Users\\User\\AppData\\Local\\Temp\\claude\\C--Users-User-Desktop-Myra\\54e2d954-79d4-4599-81ea-230fb8a0222e\\scratchpad";

const NUMBERING = {
  config: [
    {
      reference: "bullet-list",
      levels: [
        { level: 0, format: LevelFormat.BULLET, text: "\u2022", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 480, hanging: 240 } } } },
        { level: 1, format: LevelFormat.BULLET, text: "\u25E6", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 960, hanging: 240 } } } },
      ],
    },
  ],
};

// ---- inline markdown -> TextRun[] (bold **x**, code `x`, plain) ----
function inlineRuns(text) {
  const runs = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0, m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) runs.push(new TextRun({ text: text.slice(last, m.index) }));
    const tok = m[0];
    if (tok.startsWith("**")) {
      runs.push(new TextRun({ text: tok.slice(2, -2), bold: true }));
    } else {
      runs.push(new TextRun({ text: tok.slice(1, -1), font: "Consolas", size: 20, color: "AA3377" }));
    }
    last = m.index + tok.length;
  }
  if (last < text.length) runs.push(new TextRun({ text: text.slice(last) }));
  if (runs.length === 0) runs.push(new TextRun({ text: "" }));
  return runs;
}

function parseTableBlock(lines, startIdx) {
  // lines[startIdx] is header row, startIdx+1 is separator
  let i = startIdx;
  const rows = [];
  while (i < lines.length && lines[i].trim().startsWith("|")) {
    if (!/^\|[\s:-]+\|/.test(lines[i]) === true || i !== startIdx + 1) {
      // skip pure separator rows (---|---|---)
      if (!/^\|[\s:|-]+\|$/.test(lines[i].trim())) {
        const cells = lines[i].trim().slice(1, -1).split("|").map(c => c.trim());
        rows.push(cells);
      }
    }
    i++;
  }
  return { rows, nextIdx: i };
}

function buildTable(rows) {
  const nCols = rows[0].length;
  const totalWidth = 9360; // page width minus margins
  const colWidth = Math.floor(totalWidth / nCols);
  const colWidths = new Array(nCols).fill(colWidth);
  const trRows = rows.map((cells, rIdx) => new TableRow({
    tableHeader: rIdx === 0,
    children: cells.map((cellText, cIdx) => new TableCell({
      width: { size: colWidths[cIdx], type: WidthType.DXA },
      shading: rIdx === 0 ? { type: ShadingType.CLEAR, fill: "1F3B5C", color: "auto" } : undefined,
      verticalAlign: VerticalAlign.CENTER,
      margins: { top: 80, bottom: 80, left: 100, right: 100 },
      children: [new Paragraph({
        children: rIdx === 0
          ? [new TextRun({ text: cellText, bold: true, color: "FFFFFF", size: 19 })]
          : inlineRuns(cellText).map(r => { r.options = r.options || {}; return r; }),
        alignment: AlignmentType.LEFT,
      })],
    })),
  }));
  return new Table({
    width: { size: totalWidth, type: WidthType.DXA },
    columnWidths: colWidths,
    rows: trRows,
    borders: {
      top: { style: BorderStyle.SINGLE, size: 4, color: "AAAAAA" },
      bottom: { style: BorderStyle.SINGLE, size: 4, color: "AAAAAA" },
      left: { style: BorderStyle.SINGLE, size: 4, color: "AAAAAA" },
      right: { style: BorderStyle.SINGLE, size: 4, color: "AAAAAA" },
      insideHorizontal: { style: BorderStyle.SINGLE, size: 2, color: "CCCCCC" },
      insideVertical: { style: BorderStyle.SINGLE, size: 2, color: "CCCCCC" },
    },
  });
}

function diagramParagraph(name, caption) {
  const file = path.join(DIAG_DIR, `diagram-${name}.png`);
  const buf = fs.readFileSync(file);
  // read PNG dims from IHDR
  const w = buf.readUInt32BE(16), h = buf.readUInt32BE(20);
  const targetW = 560; // points-ish (docx-js uses px for ImageRun transformation)
  const targetH = Math.round((h / w) * targetW);
  return [
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { before: 200, after: 100 },
      children: [new ImageRun({ type: "png", data: buf, transformation: { width: targetW, height: targetH } })],
    }),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      children: [new TextRun({ text: caption, italics: true, size: 18, color: "666666" })],
      spacing: { after: 200 },
    }),
  ];
}

const DIAGRAM_CAPTIONS = {
  architecture: "Figure: System architecture & data flow",
  "core-workflow": "Figure: Core user workflow (FRS \u00a73)",
  "gmail-cta": "Figure: Gmail call-to-action handling (FRS \u00a714)",
  "cost-flow": "Figure: Daily spend cap \u2014 runtime circuit breaker",
};

// ---- markdown block -> Paragraph[]/Table[] ----
function mdToBlocks(md, headingBase) {
  // headingBase: 1 => "## " is Heading1, "### " is Heading2 (used for BRD main body & FRS main body)
  const lines = md.split("\n");
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.trim() === "") { i++; continue; }
    if (line.startsWith("[[DIAGRAM:")) {
      const name = line.match(/\[\[DIAGRAM:([a-z-]+)\]\]/)[1];
      blocks.push(...diagramParagraph(name, DIAGRAM_CAPTIONS[name] || ""));
      i++; continue;
    }
    if (line.startsWith("## ")) {
      blocks.push(new Paragraph({ text: line.slice(3), heading: headingBase === 1 ? HeadingLevel.HEADING_1 : HeadingLevel.HEADING_2, spacing: { before: 320, after: 160 } }));
      i++; continue;
    }
    if (line.startsWith("### ")) {
      blocks.push(new Paragraph({ text: line.slice(4), heading: headingBase === 1 ? HeadingLevel.HEADING_2 : HeadingLevel.HEADING_3, spacing: { before: 260, after: 120 } }));
      i++; continue;
    }
    if (line.trim().startsWith("|")) {
      const { rows, nextIdx } = parseTableBlock(lines, i);
      blocks.push(buildTable(rows));
      blocks.push(new Paragraph({ text: "", spacing: { after: 160 } }));
      i = nextIdx; continue;
    }
    if (/^- /.test(line.trim()) || /^\*\*/.test(line.trim()) && line.trim().startsWith("- ")) {
      // bullet (only top-level " - ", nested "  - " handled by indent detection)
      const indent = line.match(/^(\s*)/)[1].length;
      const text = line.trim().replace(/^- /, "");
      blocks.push(new Paragraph({
        children: inlineRuns(text),
        numbering: { reference: "bullet-list", level: indent >= 2 ? 1 : 0 },
        spacing: { after: 60 },
      }));
      i++; continue;
    }
    if (/^\d+\.\s/.test(line.trim())) {
      blocks.push(new Paragraph({ children: inlineRuns(line.trim()), spacing: { after: 60 }, indent: { left: 360 } }));
      i++; continue;
    }
    if (line.trim() === "---") { i++; continue; }
    // plain paragraph
    blocks.push(new Paragraph({ children: inlineRuns(line), spacing: { after: 120 } }));
    i++;
  }
  return blocks;
}

function titlePage(title, subtitle) {
  return [
    new Paragraph({ text: "", spacing: { before: 2000 } }),
    new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "Panga", bold: true, size: 32, color: "1F3B5C" })] }),
    new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 200 }, children: [new TextRun({ text: title, bold: true, size: 56 })] }),
    new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 300 }, children: [new TextRun({ text: subtitle, size: 24, color: "555555" })] }),
    new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 600 }, children: [new TextRun({ text: "Generated 2026-08-11 \u00b7 v1", size: 20, color: "888888", italics: true })] }),
    new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 100 }, children: [new TextRun({ text: "Source of truth: docs/business-requirements-document.md + docs/frs.md (markdown) \u2014 this is a derived, weekly-refreshed export.", size: 16, color: "999999", italics: true })] }),
    new Paragraph({ children: [new PageBreak()] }),
  ];
}

function tocPage() {
  return [
    new Paragraph({ text: "Table of Contents", heading: HeadingLevel.HEADING_1 }),
    new TableOfContents("Table of Contents", { hyperlink: true, headingStyleRange: "1-3" }),
    new Paragraph({ children: [new PageBreak()] }),
  ];
}

function footerBlock() {
  return {
    default: new Footer({
      children: [new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [new TextRun({ text: "Panga \u2014 internal, ", size: 16, color: "999999" }),
          new TextRun({ text: "page ", size: 16, color: "999999" }),
          new TextRun({ children: [PageNumber.CURRENT], size: 16, color: "999999" })],
      })],
    }),
  };
}

// ---------------------------------------------------------------------------
// BRD
// ---------------------------------------------------------------------------
const brdMd = fs.readFileSync(path.join(SCRATCH, "brd-for-docx.md"), "utf-8");
const brdBody = mdToBlocks(brdMd, 1);
const brdDoc = new Document({
  numbering: NUMBERING,
  sections: [{
    properties: { page: { size: US_LETTER, margin: MARGIN } },
    footers: footerBlock(),
    children: [
      ...titlePage("Business Requirements Document", "Business-level decisions \u2014 product, pricing, licensing, distribution, compliance"),
      ...tocPage(),
      ...brdBody,
    ],
  }],
});

// ---------------------------------------------------------------------------
// FRS
// ---------------------------------------------------------------------------
const frsMainMd = fs.readFileSync(path.join(SCRATCH, "frs-main-for-docx.md"), "utf-8");
const frsAppendixMd = fs.readFileSync(path.join(SCRATCH, "frs-appendix-for-docx.md"), "utf-8");
const frsBody = mdToBlocks(frsMainMd, 1);
const frsAppendix = mdToBlocks(frsAppendixMd, 1); // ### maps to Heading2 via headingBase=1 branch already

const frsDoc = new Document({
  numbering: NUMBERING,
  sections: [{
    properties: { page: { size: US_LETTER, margin: MARGIN } },
    footers: footerBlock(),
    children: [
      ...titlePage("Functional Requirements Specification", "Current-state functional & technical spec \u2014 built and shipped capability only"),
      ...tocPage(),
      ...frsBody,
      new Paragraph({ children: [new PageBreak()] }),
      new Paragraph({ text: "Appendix A: Planned, Not Yet Built", heading: HeadingLevel.HEADING_1, spacing: { before: 320, after: 160 } }),
      new Paragraph({
        children: inlineRuns("Design work that exists but hasn't shipped yet \u2014 listed here for context, clearly separated from current-state functionality above. Build status is tracked in docs/backlog-log.md."),
        spacing: { after: 200 },
      }),
      ...frsAppendix,
    ],
  }],
});

async function main() {
  fs.writeFileSync(path.join(OUT_DIR, "BRD.docx"), await Packer.toBuffer(brdDoc));
  fs.writeFileSync(path.join(OUT_DIR, "FRS.docx"), await Packer.toBuffer(frsDoc));
  console.log("wrote BRD.docx and FRS.docx");
}
main().catch(e => { console.error(e); process.exit(1); });
