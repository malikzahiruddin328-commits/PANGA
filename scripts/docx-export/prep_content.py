"""Pre-process BRD/FRS markdown into docx-ready intermediate files:
- BRD: body content as-is (already correctly labels its own gaps)
- FRS: main body = built content only, §18 (not yet built) moved to an
  appendix, diagram placeholders inserted at the right points.
"""
import re

WORKTREE = r"C:\Users\User\Desktop\Myra\Panga\.claude\worktrees\brd-frs-docx-v1"
OUT = r"C:\Users\User\AppData\Local\Temp\claude\C--Users-User-Desktop-Myra\54e2d954-79d4-4599-81ea-230fb8a0222e\scratchpad"

def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()

def split_sections(md, start_marker="## "):
    """Split into (header_line, body_text) preserving order, everything
    before the first '## ' heading is the preamble."""
    lines = md.split("\n")
    sections = []
    preamble = []
    cur_header = None
    cur_body = []
    for line in lines:
        if line.startswith("## "):
            if cur_header is not None:
                sections.append((cur_header, "\n".join(cur_body).strip("\n")))
            elif cur_body:
                preamble = cur_body
            cur_header = line[3:]
            cur_body = []
        else:
            cur_body.append(line)
    if cur_header is not None:
        sections.append((cur_header, "\n".join(cur_body).strip("\n")))
    return preamble, sections

# ---------------------------------------------------------------------------
# BRD
# ---------------------------------------------------------------------------
brd_md = read(WORKTREE + r"\docs\business-requirements-document.md")
preamble, sections = split_sections(brd_md)
out = []
for header, body in sections:
    out.append("## " + header)
    out.append(body)
    out.append("")
with open(OUT + r"\brd-for-docx.md", "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print(f"BRD: {len(sections)} sections")

# ---------------------------------------------------------------------------
# FRS
# ---------------------------------------------------------------------------
frs_md = read(WORKTREE + r"\docs\frs.md")
preamble, sections = split_sections(frs_md)

# section headers as they appear, in file order (already correct order after
# our earlier reorder: ...,15,15a,18,19)
built_order = []
appendix = []
diagram_after = {
    "4. Data Model Direction (relational)": "architecture",
    "3. Core Workflow": "core-workflow",
    "14. Gmail Call-to-Action Handling (built 2026-07-28 through 2026-07-29)": "gmail-cta",
    "19. Cost Governance \u2014 Daily Spend Cap (built 2026-08-11)": "cost-flow",
}

main_out = []
appendix_out = []
for header, body in sections:
    if header.startswith("18. Applications Pivot Table"):
        appendix_out.append("### " + header)
        appendix_out.append(body)
        appendix_out.append("")
        continue
    main_out.append("## " + header)
    main_out.append(body)
    if header in diagram_after:
        main_out.append(f"[[DIAGRAM:{diagram_after[header]}]]")
    main_out.append("")

with open(OUT + r"\frs-main-for-docx.md", "w", encoding="utf-8") as f:
    f.write("\n".join(main_out))
with open(OUT + r"\frs-appendix-for-docx.md", "w", encoding="utf-8") as f:
    f.write("\n".join(appendix_out))
print(f"FRS: {len(sections)} sections total, appendix has {len(appendix_out)>0}")
