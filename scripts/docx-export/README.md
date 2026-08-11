# BRD/FRS DOCX export pipeline

Generates `docs/BRD.docx` and `docs/FRS.docx` from the markdown source of
truth (`docs/business-requirements-document.md` / `docs/frs.md`). Built
2026-08-11 for the first weekly DOCX deliverable (Zahir's request, via
hub). Markdown stays authoritative — these scripts are a derived export,
run weekly, not a replacement for editing the `.md` files directly.

## Pipeline (3 steps, in order)

1. **`prep_content.py`** — splits the markdown into `## `-delimited
   sections, filters FRS down to genuinely built content only (any
   section flagged "not yet built" moves to an appendix instead of being
   cut), and inserts `[[DIAGRAM:name]]` placeholders at the right points.
   Writes intermediate files (`brd-for-docx.md`, `frs-main-for-docx.md`,
   `frs-appendix-for-docx.md`) to a scratch directory.
2. **`draw_diagrams.py`** — hand-draws the 4 diagrams (system
   architecture, core workflow, Gmail CTA handling, cost/spend-cap flow)
   as PNGs via PIL/Pillow and saves them to `docs/diagrams/`. **Real
   environment constraint, not a stylistic choice:** this machine has no
   mermaid-cli, no graphviz, and no LibreOffice/soffice install, so the
   diagrams are hand-drawn box-and-arrow PNGs rather than rendered from
   `.mmd`/`.dot` source. If any of those tools become available later,
   redrawing from real diagram-description source would be a real
   improvement — not required to keep the export working, since the PNGs
   themselves are the actual embedded asset either way.
3. **`build_docx.js`** — uses the `docx` npm package (per the docx skill's
   guidance — `npm install docx` inside this directory if `node_modules`
   isn't present; do not commit `node_modules`) to assemble the final
   `.docx` files: title page, table of contents, real Word tables for the
   BRD's two markdown tables, bullet lists via a numbering config (not
   literal `•` characters), and the diagrams embedded via `ImageRun` at
   their placeholder points. FRS gets an "Appendix A: Planned, Not Yet
   Built" section for anything `prep_content.py` filtered out of the main
   body.

## Redraw only when the underlying feature changes

The 4 diagrams are not regenerated every week by default — only when the
architecture/workflow/CTA-handling/cost-flow behavior they describe
actually changes. Re-run `draw_diagrams.py` (or hand-edit
`docs/diagrams/*.png` directly if the change is small) only in that case.

## Weekly redline (not yet automated — see `docs/backlog-log.md`'s
spend-cap-adjacent process notes for the general pattern)

`docs/BRD.docx`/`docs/FRS.docx` are committed to git as the running
baseline — git's own history is the version lineage, no separate
"last week" copy file. Each week: regenerate fresh content via this
pipeline, then apply the real diff against last week's committed version
as genuine Word tracked-changes (`<w:ins>`/`<w:del>`, per the docx skill's
editing workflow — unzip, edit `word/document.xml`, rezip, validate with
`--author`), not a fresh unmarked overwrite. Once Zahir has reviewed a
given week's redline (confirmed via the hub), run the skill's
`accept_changes.py` and commit that as the new clean baseline.

## Verification note

This environment has no LibreOffice/soffice and no `pandoc`, so the
skill's suggested `soffice --convert-to pdf` + `pdftoppm` visual-check
workflow isn't available here. Verification instead used: (1) `python3 -m
xml.dom.minidom` to confirm `word/document.xml`/`word/numbering.xml` are
well-formed, (2) the docx skill's own `validate.py` (hit an unrelated
Windows-console-encoding bug reading `numbering.xml` as cp1252 instead of
UTF-8 — confirmed via a direct byte-level check that the file itself is
genuinely valid UTF-8 XML, so this reads as the validator's own
environment bug, not a defect in the generated file), and (3)
independent readback via `python-docx` (a separate library from the
`docx` npm package used to build the file) confirming paragraph count,
heading order, table structure, and embedded-image count all match
expectations.
