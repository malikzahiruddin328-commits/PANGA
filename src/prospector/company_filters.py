"""Shared company-quality heuristics for target_accounts signal sources
(PRD §16a) - used by clinical_trials.py today, and by every future signal
normalizer (regulatory filings, commercial hiring) so the same company
doesn't need re-filtering per source.

All three keyword lists below are manually curated from general knowledge,
not a live-verified data source - same limitation as the mega-pharma list
had from the start. Treat "survives filtering" as "worth a look", not
"verified independent company"; Zahir's own manual disqualify (via the
Prospector tab) is the real correction mechanism for anything these
keyword lists miss or get wrong.
"""

NON_COMPANY_KEYWORDS = (
    "university", "college", "hospital", "medical center", "health system",
    "institute", "department of veterans affairs", "national institutes of health",
    " nih", "clinic", "foundation", ", m.d.", "school of medicine",
    "cancer center", "health network", "medical associates",
)

# Universally-recognized multinational pharma majors - decades into being
# fully commercial, never a plausible "approaching first launch" signal.
MEGA_PHARMA_KEYWORDS = (
    "pfizer", "merck", "novartis", "roche", "genentech", "sanofi",
    "glaxosmithkline", " gsk", "astrazeneca", "johnson & johnson", "janssen",
    "eli lilly", "abbvie", "bristol-myers", "bristol myers", " bristol ",
    "bayer", "takeda", "gilead", "amgen", "biogen", "novo nordisk", " novo ",
    "boehringer", "teva", "viatris", "regeneron", "hoffmann-la roche",
    # Added 2026-07-30 after a real openFDA batch surfaced these under
    # abbreviated/short sponsor names ("BRISTOL", "NOVO") or ones not on
    # the original US/EU-household-name-biased list - large, established
    # multinationals, same exclusion logic as the rest of this list.
    "sun pharm", "zydus", "amneal", "servier",
)

# Well-known formerly-independent life-sciences companies absorbed via
# M&A - added 2026-07-30 after Zahir spotted "Forest Laboratories" (bought
# by Actavis, 2014) surviving the filter. Not exhaustive - acquisitions
# after this knowledge's cutoff won't be caught, and this only covers
# well-established, high-confidence cases, not every smaller/obscure deal.
KNOWN_ACQUIRED_KEYWORDS = (
    "forest laboratories", "genzyme", "hospira", "allergan", "celgene",
    "shire", "alexion", "medimmune", "pharmacyclics", "cubist pharmaceuticals",
    "ariad pharmaceuticals", "actelion", "loxo oncology", "array biopharma",
    "bioverativ", "spark therapeutics", "tesaro", "c. r. bard", "c.r. bard",
)


def _matches_any(name: str, keywords: tuple) -> bool:
    lower = f" {name.lower()} "
    return any(kw in lower for kw in keywords)


def looks_like_target_company(sponsor_name: str) -> bool:
    """False for academic/government/individual sponsors, known
    mega-pharma majors, and known-acquired/defunct companies; True
    otherwise (the useful middle - real, still-independent companies not
    already obviously fully commercial)."""
    if not sponsor_name:
        return False
    if _matches_any(sponsor_name, NON_COMPANY_KEYWORDS):
        return False
    if _matches_any(sponsor_name, MEGA_PHARMA_KEYWORDS):
        return False
    if _matches_any(sponsor_name, KNOWN_ACQUIRED_KEYWORDS):
        return False
    return True
