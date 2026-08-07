"""Guards two standing UI rules that have each drifted more than once
already - a plain source-text sweep rather than an AppTest run, since this
catches a violation before it ever executes instead of only the specific
code paths a test happens to exercise.

1. No st.caption() anywhere in Panga's UI code (established 2026-07-30,
   re-broken and re-fixed twice since: once caught mid-session 2026-07-31,
   once by Mirror's fine-needle audit 2026-08-06 - which also caught a
   brand new instance introduced by the very next commit after that fix).
   Captions render at a fixed, low-opacity gray that measures under WCAG
   1.4.3's 4.5:1 contrast minimum against this app's own theme colors -
   use st.markdown() instead, even for de-emphasized/secondary copy.

2. No st.success()/st.info()/st.warning() immediately followed by
   st.rerun() (established 2026-07-30 after a real "I click the button and
   nothing happens" report - the message renders for a fraction of a
   second before the rerun wipes the page, so it's never actually seen;
   ~11 instances fixed app-wide at the time). Mirror's fine-needle audit
   caught one straggler 2026-08-06 - st.toast() survives a rerun, use that
   instead when the same code path needs to both show a message and
   rerun.
"""

import re
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
_CAPTION_CALL_RE = re.compile(r"\bst\.caption\s*\(")
_FLASH_MESSAGE_RE = re.compile(r"\bst\.(success|info|warning)\s*\(")
_RERUN_RE = re.compile(r"^st\.rerun\(\s*\)\s*$")


def test_no_st_caption_calls_anywhere_in_src():
    offenders = []
    for path in SRC_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if _CAPTION_CALL_RE.search(text):
            offenders.append(str(path.relative_to(SRC_DIR)))
    assert not offenders, f"st.caption() found in: {offenders} - use st.markdown() instead (see this project's standing readability rule)"


def test_no_flash_and_vanish_status_messages():
    offenders = []
    for path in SRC_DIR.rglob("*.py"):
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if not _FLASH_MESSAGE_RE.search(line):
                continue
            for next_line in lines[i + 1:i + 3]:
                if not next_line.strip():
                    continue
                if _RERUN_RE.match(next_line.strip()):
                    offenders.append(f"{path.relative_to(SRC_DIR)}:{i + 1}")
                break
    assert not offenders, f"st.success/info/warning immediately followed by st.rerun() at: {offenders} - use st.toast() instead, it survives a rerun"
