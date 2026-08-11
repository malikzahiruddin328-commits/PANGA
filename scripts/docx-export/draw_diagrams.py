"""Hand-drawn box-and-arrow diagrams for the Panga FRS DOCX, via PIL.
No mermaid-cli/graphviz/LibreOffice are available in this environment,
so this is a deliberate, documented fallback rather than a hand-wave.
"""
import os
from PIL import Image, ImageDraw, ImageFont

OUT_DIR = r"C:\Users\User\Desktop\Myra\Panga\.claude\worktrees\brd-frs-docx-v1\docs\diagrams"
os.makedirs(OUT_DIR, exist_ok=True)

# Colors (professional, print-friendly)
BG = (255, 255, 255)
BOX_FILL = (235, 242, 250)
BOX_BORDER = (51, 92, 138)
TEXT = (20, 30, 40)
ARROW = (70, 90, 110)
ACCENT_FILL = (252, 240, 220)
ACCENT_BORDER = (176, 120, 30)
LABEL = (90, 100, 110)

def load_font(size, bold=False):
    candidates = [
        r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
    ]
    for c in candidates:
        if os.path.exists(c):
            return ImageFont.truetype(c, size)
    return ImageFont.load_default()

F_TITLE = load_font(30, bold=True)
F_BOX = load_font(20, bold=True)
F_SUB = load_font(15)
F_LABEL = load_font(14)


def wrap_text(draw, text, font, max_width):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if draw.textlength(test, font=font) <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def box(draw, x, y, w, h, title, subtitle=None, fill=BOX_FILL, border=BOX_BORDER, radius=14):
    draw.rounded_rectangle([x, y, x + w, y + h], radius=radius, fill=fill, outline=border, width=2)
    title_lines = wrap_text(draw, title, F_BOX, w - 24)
    sub_lines = wrap_text(draw, subtitle, F_SUB, w - 24) if subtitle else []
    total_h = len(title_lines) * 24 + (len(sub_lines) * 19 + 6 if sub_lines else 0)
    ty = y + (h - total_h) / 2
    for line in title_lines:
        tw = draw.textlength(line, font=F_BOX)
        draw.text((x + (w - tw) / 2, ty), line, font=F_BOX, fill=TEXT)
        ty += 24
    if sub_lines:
        ty += 4
        for line in sub_lines:
            tw = draw.textlength(line, font=F_SUB)
            draw.text((x + (w - tw) / 2, ty), line, font=F_SUB, fill=LABEL)
            ty += 19
    return (x, y, x + w, y + h)


def arrow(draw, x1, y1, x2, y2, label=None, dashed=False):
    if dashed:
        total = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        n = max(int(total // 10), 1)
        for i in range(n):
            t0, t1 = i / n, i / n + 0.5 / n
            if i % 2 == 0:
                continue
            xa, ya = x1 + (x2 - x1) * t0, y1 + (y2 - y1) * t0
            xb, yb = x1 + (x2 - x1) * t1, y1 + (y2 - y1) * t1
            draw.line([xa, ya, xb, yb], fill=ARROW, width=2)
    else:
        draw.line([x1, y1, x2, y2], fill=ARROW, width=2)
    # arrowhead
    import math
    ang = math.atan2(y2 - y1, x2 - x1)
    ah = 9
    for da in (0.5, -0.5):
        a2 = ang + math.pi - da
        draw.line([x2, y2, x2 + ah * math.cos(a2), y2 + ah * math.sin(a2)], fill=ARROW, width=2)
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        lw = draw.textlength(label, font=F_LABEL)
        draw.rectangle([mx - lw / 2 - 4, my - 11, mx + lw / 2 + 4, my + 9], fill=BG)
        draw.text((mx - lw / 2, my - 9), label, font=F_LABEL, fill=LABEL)


def title_block(draw, w, text):
    tw = draw.textlength(text, font=F_TITLE)
    draw.text(((w - tw) / 2, 22), text, font=F_TITLE, fill=TEXT)


def new_canvas(w, h):
    img = Image.new("RGB", (w, h), BG)
    return img, ImageDraw.Draw(img)



# ---------------------------------------------------------------------------
# 1. System architecture / data flow (simplified, no crossing arrows)
# ---------------------------------------------------------------------------
W, H = 1300, 1150
img, d = new_canvas(W, H)
title_block(d, W, "Panga \u2014 System Architecture & Data Flow")

col1_x, col2_x = 90, 690
bw, bh = 500, 95
mid_x = W / 2

b_sources = box(d, col1_x, 100, bw, bh, "Job Sources", "USAJOBS / boards / ATS sites / LinkedIn manual entry")
b_profile_in = box(d, col2_x, 100, bw, bh, "Profile Intake", "profile/ingest.py + interview.py")

b_jobstore = box(d, col1_x, 260, bw, bh, "search/job_store.py", "jobs.json (encrypted)")
b_profstore = box(d, col2_x, 260, bw, bh, "profile/storage.py", "master_profile.json (encrypted)")

rank_w = 500
b_rank = box(d, mid_x - rank_w / 2, 420, rank_w, bh, "ranking/prioritize.py", "fit_score, cross-source dedup")

ui_w = 500
b_ui = box(d, mid_x - ui_w / 2, 580, ui_w, bh, "ui/app.py \u2014 Results tab", "Streamlit, browser-based")

row_w = 370
row_y = 760
rx1, rx2, rx3 = 60, 470, 880
b_drafting = box(d, rx1, row_y, row_w, bh, "tailoring/drafting.py", "Direct Anthropic API", fill=ACCENT_FILL, border=ACCENT_BORDER)
b_apps = box(d, rx2, row_y, row_w, bh, "tailoring/applications.py", "applications.json (encrypted)")
b_docx = box(d, rx3, row_y, row_w, bh, "docx_export.py + dossier.py", ".docx files + workspace folder")

b_other = box(d, mid_x - 550, 920, 1100, 90, "Also on the Results tab", "Gmail CTA scan (cta_emails.py)  \u00b7  Prospector  \u00b7  Interview Prep")

b_spendcap = box(d, rx1, 1030, row_w, 90, "llm_client.py \u2014 spend cap", "Blocks new AI calls once the daily cost cap is hit", fill=ACCENT_FILL, border=ACCENT_BORDER)

arrow(d, col1_x + bw / 2, 195, col1_x + bw / 2, 260)
arrow(d, col2_x + bw / 2, 195, col2_x + bw / 2, 260)
arrow(d, col1_x + bw / 2, 355, mid_x - 80, 420)
arrow(d, col2_x + bw / 2, 355, mid_x + 80, 420)
arrow(d, mid_x, 515, mid_x, 580)
arrow(d, mid_x - 120, 675, rx1 + row_w / 2, 760, "Generate documents")
arrow(d, rx1 + row_w, row_y + bh / 2, rx2, row_y + bh / 2)
arrow(d, rx2 + row_w, row_y + bh / 2, rx3, row_y + bh / 2)
arrow(d, mid_x, 675, mid_x, 920)
arrow(d, rx1 + row_w / 2, row_y + bh, rx1 + row_w / 2, 1030)

img.save(os.path.join(OUT_DIR, "diagram-architecture.png"))
print("saved diagram-architecture.png")

# 2. Core user workflow (FRS §3)
# ---------------------------------------------------------------------------
W, H = 1500, 500
img, d = new_canvas(W, H)
title_block(d, W, "Panga — Core User Workflow (FRS \u00a73)")

steps = [
    ("1. Onboarding", "Base resume + career aspirations"),
    ("2. Gap-Probing\nInterview", "Targeted questions vs. role/industry skill profile"),
    ("3. Master Profile\nBuild", "Nuanced answers stored permanently"),
    ("4. Job Discovery", "Boards + industry sources + USAJOBS.gov"),
    ("5. Fit + Tailoring", "0-100 compatibility score, tailored resume/cover letter"),
    ("6. Review Queue", "User reviews and manually submits"),
]
bw, bh, gap = 220, 150, 20
x = 30
y = 150
boxes = []
for i, (t, s) in enumerate(steps):
    t_clean = t.replace("\n", " ")
    boxes.append(box(d, x, y, bw, bh, t_clean, s))
    x += bw + gap
for i in range(len(boxes) - 1):
    x1 = boxes[i][2]
    x2 = boxes[i + 1][0]
    yc = (boxes[i][1] + boxes[i][3]) / 2
    arrow(d, x1, yc, x2, yc)

img.save(os.path.join(OUT_DIR, "diagram-core-workflow.png"))
print("saved diagram-core-workflow.png")

# ---------------------------------------------------------------------------
# 3. Gmail CTA handling (FRS §14)
# ---------------------------------------------------------------------------
W, H = 1300, 750
img, d = new_canvas(W, H)
title_block(d, W, "Panga — Gmail Call-to-Action Handling (FRS \u00a714)")

bw, bh = 340, 110
b_inbox = box(d, 60, 130, bw, bh, "Zahir's Gmail Inbox", "Interview invites, offers, rejections, questions")
b_scan = box(d, 480, 130, bw, bh, "panga-gmail-cta-scan", "3x/day (8am/12pm/4pm) — classify + label + auto-match \u2018applied\u2019 confirmations")
b_dashboard = box(d, 900, 130, bw, bh, "Call to Action tab (ui/app.py)", "Dashboard mirror — one place to work through everything")
b_click = box(d, 900, 340, bw, bh, "Zahir clicks", "Dismiss, or Draft reply")
b_fulfill = box(d, 480, 340, bw, bh, "panga-cta-fulfillment", "2x/day (8am/4pm) — archives / drafts a real Gmail reply")
b_gmail2 = box(d, 60, 340, bw, bh, "Gmail", "Draft created \u2014 never auto-sent, Zahir reviews & sends")
b_clear = box(d, 480, 550, bw, bh, "Dashboard self-clears", "Fulfillment task notices the sent draft and clears the item")

arrow(d, 60 + bw, 185, 480, 185)
arrow(d, 480 + bw, 185, 900, 185)
arrow(d, 900 + bw / 2, 240, 900 + bw / 2, 340)
arrow(d, 900, 395, 480 + bw, 395)
arrow(d, 480, 395, 60 + bw, 395)
arrow(d, 480 + bw / 2, 450, 480 + bw / 2, 550, "notices sent draft")
arrow(d, 480 + 40, 550, 480 + 40, 240, dashed=True)

img.save(os.path.join(OUT_DIR, "diagram-gmail-cta.png"))
print("saved diagram-gmail-cta.png")

# ---------------------------------------------------------------------------
# 4. Cost / spend-cap flow
# ---------------------------------------------------------------------------
W, H = 1400, 650
img, d = new_canvas(W, H)
title_block(d, W, "Panga — Daily Spend Cap (Runtime Circuit Breaker)")

bw, bh = 300, 110
b_call = box(d, 60, 140, bw, bh, "AI call requested", "e.g. fit_score, drafting")
b_check = box(d, 440, 140, bw, bh, "_check_spend_cap()", "Reads today's real cost_log spend")
b_diamond_cx, b_diamond_cy = 900, 195
diamond = [(b_diamond_cx, b_diamond_cy - 70), (b_diamond_cx + 170, b_diamond_cy),
           (b_diamond_cx, b_diamond_cy + 70), (b_diamond_cx - 170, b_diamond_cy)]
d.polygon(diamond, fill=ACCENT_FILL, outline=ACCENT_BORDER, width=2)
for line, dy in [("Spend <", -10), ("cap?", 10)]:
    tw = d.textlength(line, font=F_BOX)
    d.text((b_diamond_cx - tw / 2, b_diamond_cy + dy - 10), line, font=F_BOX, fill=TEXT)

b_proceed = box(d, 1180, 40, 180, 110, "Proceed", "Call runs; cost logged on completion", fill=BOX_FILL)
b_block = box(d, 1180, 260, 180, 110, "Block + raise", "LLMSpendCapExceeded — new calls only", fill=(250, 226, 226), border=(160, 50, 50))
b_critical = box(d, 900, 420, 300, 100, "First block of the day: CRITICAL log", "panga_debug.log", fill=(250, 226, 226), border=(160, 50, 50))
b_notify = box(d, 440, 420, 300, 100, "Daily notification", "Leads with a warning if the cap tripped today")
b_inflight = box(d, 60, 420, 300, 100, "Already in-flight calls", "Finish normally \u2014 never cancelled mid-call")

arrow(d, 60 + bw, 195, 440, 195)
arrow(d, 440 + bw, 195, b_diamond_cx - 170, b_diamond_cy)
arrow(d, b_diamond_cx + 170, b_diamond_cy - 30, 1180, 95, "Yes")
arrow(d, b_diamond_cx + 170, b_diamond_cy + 30, 1180, 315, "No")
arrow(d, 1180 + 90, 370, 1050, 420)
arrow(d, 900, 470, 740, 470)
arrow(d, 210, 250, 210, 420, dashed=True)

img.save(os.path.join(OUT_DIR, "diagram-cost-flow.png"))
print("saved diagram-cost-flow.png")

print("all diagrams generated")
