"""Normalizes existing jobs.json postings into target_account signals
(PRD §16a, signal type 3: commercial-build hiring elsewhere). Not a new
external source - a new lens over jobs Panga already searches (USAJOBS/
ZipRecruiter/Dice/Indeed/company-site ATS APIs): tags a job as a signal
about its POSTING company, separate from scoring it as a fit for Zahir.

Real finding (2026-07-30): USAJOBS postings need excluding entirely - a
broad keyword match caught "Semester of Service Volunteer - Commercial
Diplomacy Analyst" at the Department of State, a government internship
program, not a company commercial-hiring signal. Also found IQVIA
(a ~$15B contract-research/data-analytics company, not remotely
pre-commercial) matching repeatedly - added to company_filters'
mega-pharma-adjacent exclusions. One known, undocumented-away gap left as-
is: a non-pharma company in an unrelated industry can still slip through
if its job title happens to match a keyword (e.g. a security-services
company's "SVP Commercial Growth" posting) - there's no industry field to
filter on, same "review and disqualify" limitation as the other signals.
"""
from prospector.company_filters import looks_like_target_company

COMMERCIAL_HIRING_KEYWORDS = (
    "vp commercial", "vice president, commercial", "vice president commercial",
    "chief commercial officer", "market access", "commercial operations",
    "commercial lead", "launch lead", "head of commercial", "commercialization",
    "commercial strategy", "commercial growth",
)


def looks_like_commercial_hiring_title(title: str) -> bool:
    if not title:
        return False
    lower = title.lower()
    return any(kw in lower for kw in COMMERCIAL_HIRING_KEYWORDS)


def normalize_commercial_hiring_signals(jobs: list[dict]) -> list[dict]:
    """USAJOBS excluded (government postings, not company signals). One
    signal per company (first matching posting) - a company with several
    matching postings in the store shouldn't flood its signal list with
    near-duplicates; qualification is by distinct signal TYPE anyway, not
    raw signal count, so this doesn't affect that logic."""
    out = []
    seen_companies = set()
    for job in jobs:
        if job.get("source") == "USAJOBS":
            continue
        title = job.get("title")
        org = job.get("organization")
        if not org or not looks_like_commercial_hiring_title(title) or not looks_like_target_company(org):
            continue
        key = org.lower()
        if key in seen_companies:
            continue
        seen_companies.add(key)
        out.append({
            "company_name": org,
            "signal_type": "commercial_hiring",
            "source": "jobs",
            "detail": f'Posted "{title}" - a commercial-build hiring signal',
            "ref": f"{job.get('source')}:{job.get('job_id')}",
        })
    return out
