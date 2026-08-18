"""Deterministic British-to-American spelling backstop for generated resume
text (Zahir's real ask, 2026-08-18): the drafting SYSTEM_PROMPT already
instructs "American spellings throughout", but per this repo's own
standing CLAUDE.md principle #3 ("AI output checked by a literal/
deterministic downstream rule is fragile without a code-level backstop") -
a prompt instruction alone has proven unreliable elsewhere in this codebase
multiple times, and Zahir's own source profile data has a real, live risk
of British spellings leaking through (UK-education background, London
university) that a US-employer-facing resume should never surface. This is
that code-level backstop: applied as a deterministic post-process pass on
generated resume_text, not relied on instead of the prompt instruction -
both together, same "prompt AND code" pattern the rank-prefix/degree-
synonym/keyword-verification fixes already established.

Deliberately scoped narrowly to MECHANICAL spelling variants of the same
word (organisation/organization, colour/color, -ise/-ize, -yse/-yze,
-our/-or, doubled-consonant inflections like travelled/traveled) - never
touches genuine British/American VOCABULARY differences (CV vs resume,
mobile vs cell phone, holiday vs vacation) since those require real
judgment about which word the writer meant, not a mechanical spelling
correction; per Zahir's own explicit instruction, that's out of scope
here.

Not exhaustive - a reasonably-sized curated list covering the spelling
patterns most likely to appear in resume/cover-letter prose (leadership,
program/project language, business-process verbs like organize/optimize/
prioritize/finalize). Extend the word lists below if a real miss is found
live, same "grows over time" posture as this app's other curated
deny-lists (e.g. drafting.py's soft-skill/degree-marker lists)."""

import re

# Verbs whose British form ends "-ise" and American form ends "-ize" -
# inflected forms (base/-ed/-es/-ing/-er/-ers) are generated from these,
# not hand-typed per form, so adding one entry here covers its whole
# family. Restricted to words genuinely common in professional/resume
# prose - not an attempt at full dictionary coverage.
_ISE_IZE_VERBS = [
    "organise", "specialise", "recognise", "realise", "utilise", "optimise",
    "prioritise", "mobilise", "capitalise", "standardise", "modernise",
    "customise", "maximise", "minimise", "summarise", "emphasise",
    "authorise", "apologise", "criticise", "theorise", "categorise",
    "characterise", "familiarise", "socialise", "centralise",
    "decentralise", "rationalise", "globalise", "digitise", "finalise",
    "initialise", "formalise", "generalise", "localise", "normalise",
    "personalise", "visualise", "synthesise", "energise", "strategise",
    "monetise", "unionise", "harmonise",
]
# Note: words like "supervise", "advise", "revise", "surprise", "comprise",
# "compromise", "devise", "exercise" are NOT -ise/-ize variants at all -
# both British and American English spell them with "-ise"/"-se" - so they
# are deliberately excluded from this list rather than mis-mapped.

# Verbs whose British form ends "-yse" and American form ends "-yze".
_YSE_YZE_VERBS = ["analyse", "paralyse", "catalyse"]

# Nouns genuinely formed as British "-isation" / American "-ization" for
# the verbs above where that noun form is real, common usage (not every
# -ise verb above has a natural -isation noun in normal use, e.g.
# "recognise" -> "recognition", not "recognisation" - only listing the
# ones that are real words).
_ISATION_IZATION_STEMS = [
    "organ", "special", "standard", "modern", "custom", "maxim", "minim",
    "author", "categor", "central", "decentral", "rational", "global",
    "digit", "formal", "general", "local", "normal", "personal", "visual",
    "harmon", "monet", "union",
]

# One-off word pairs that don't follow the -ise/-ize or -isation/-ization
# pattern - the classic "-our" -> "-or", doubled-consonant, and other
# irregular British/American spelling divergences most likely to show up
# in professional resume/cover-letter prose.
_ONE_OFF_PAIRS = [
    ("colour", "color"), ("colours", "colors"), ("coloured", "colored"),
    ("colouring", "coloring"),
    ("favour", "favor"), ("favours", "favors"), ("favoured", "favored"),
    ("favouring", "favoring"), ("favourite", "favorite"),
    ("favourable", "favorable"), ("favourably", "favorably"),
    ("behaviour", "behavior"), ("behaviours", "behaviors"),
    ("behavioural", "behavioral"),
    ("programme", "program"), ("programmes", "programs"),
    ("centre", "center"), ("centres", "centers"), ("centred", "centered"),
    ("centring", "centering"),
    ("defence", "defense"), ("defences", "defenses"),
    ("licence", "license"), ("licences", "licenses"),
    ("practise", "practice"), ("practised", "practiced"),
    ("practising", "practicing"),
    ("fulfil", "fulfill"), ("fulfilment", "fulfillment"),
    ("fulfilments", "fulfillments"),
    ("enrolment", "enrollment"), ("enrolments", "enrollments"),
    ("skilful", "skillful"), ("wilful", "willful"),
    ("travelled", "traveled"), ("travelling", "traveling"),
    ("traveller", "traveler"), ("travellers", "travelers"),
    ("modelled", "modeled"), ("modelling", "modeling"),
    ("modeller", "modeler"),
    ("counselled", "counseled"), ("counselling", "counseling"),
    ("counsellor", "counselor"), ("counsellors", "counselors"),
    ("labelled", "labeled"), ("labelling", "labeling"),
    ("signalled", "signaled"), ("signalling", "signaling"),
    ("cancelled", "canceled"), ("cancelling", "canceling"),
    ("levelled", "leveled"), ("levelling", "leveling"),
    ("endeavour", "endeavor"), ("endeavours", "endeavors"),
    ("endeavoured", "endeavored"), ("endeavouring", "endeavoring"),
    ("honour", "honor"), ("honours", "honors"), ("honoured", "honored"),
    ("honouring", "honoring"), ("honourable", "honorable"),
    ("neighbour", "neighbor"), ("neighbours", "neighbors"),
    ("neighbourhood", "neighborhood"), ("neighbourhoods", "neighborhoods"),
    ("labour", "labor"), ("labours", "labors"), ("laboured", "labored"),
    ("labouring", "laboring"),
    ("rumour", "rumor"), ("rumours", "rumors"),
    ("vigour", "vigor"), ("vapour", "vapor"), ("flavour", "flavor"),
    ("flavours", "flavors"),
    ("humour", "humor"), ("humoured", "humored"),
    ("armour", "armor"), ("armoured", "armored"),
    ("ageing", "aging"),
    ("cheque", "check"), ("cheques", "checks"),
    ("tyre", "tire"), ("tyres", "tires"),
    ("kerb", "curb"), ("kerbs", "curbs"),
    ("mould", "mold"), ("moulded", "molded"), ("moulding", "molding"),
    ("learnt", "learned"), ("spelt", "spelled"),
    ("judgement", "judgment"), ("judgements", "judgments"),
    ("catalogue", "catalog"), ("catalogues", "catalogs"),
    ("dialogue", "dialog"), ("dialogues", "dialogs"),
    ("aeroplane", "airplane"), ("aeroplanes", "airplanes"),
    ("acknowledgement", "acknowledgment"),
    ("acknowledgements", "acknowledgments"),
]


def _build_pairs() -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = list(_ONE_OFF_PAIRS)
    for ise_verb in _ISE_IZE_VERBS:
        stem = ise_verb[:-2]  # drop trailing "se"
        ize_verb = stem + "ze"
        pairs.append((ise_verb, ize_verb))
        pairs.append((ise_verb + "d", ize_verb + "d"))
        pairs.append((ise_verb + "s", ize_verb + "s"))
        # "-ing" form: British is the base stem + "ing" (organis + ing =
        # organising); American is the same stem with the final "s"
        # swapped for "z" (organiz + ing = organizing).
        pairs.append((stem + "ing", stem[:-1] + "zing"))
        pairs.append((stem + "er", stem[:-1] + "zer"))
        pairs.append((stem + "ers", stem[:-1] + "zers"))
    for yse_verb in _YSE_YZE_VERBS:
        stem = yse_verb[:-2]  # drop trailing "se"
        yze_verb = stem + "ze"
        pairs.append((yse_verb, yze_verb))
        pairs.append((yse_verb + "d", yze_verb + "d"))
        pairs.append((yse_verb + "s", yze_verb + "s"))
        pairs.append((stem + "ing", stem[:-1] + "zing"))
        pairs.append((stem + "er", stem[:-1] + "zer"))
        pairs.append((stem + "ers", stem[:-1] + "zers"))
    for stem in _ISATION_IZATION_STEMS:
        pairs.append((stem + "isation", stem + "ization"))
        pairs.append((stem + "isations", stem + "izations"))
    return pairs


# Built once at import time - the pattern list never changes at runtime,
# same "compute the expensive/derived thing once" posture as this
# codebase's other module-level compiled-regex constants (e.g. drafting.
# py's _RANK_PREFIX_RE).
_SPELLING_PAIRS = _build_pairs()
_COMPILED_PATTERNS = [
    (re.compile(r"\b" + re.escape(british) + r"\b", re.IGNORECASE), american)
    for british, american in _SPELLING_PAIRS
]


def _match_case(source_word: str, replacement: str) -> str:
    """Preserves the matched word's original capitalization pattern on the
    replacement - ALL CAPS stays ALL CAPS, Title Case stays Title Case,
    lowercase stays lowercase. Falls back to the replacement's own casing
    for anything else (e.g. mixed/odd casing), same conservative default
    every other case-preserving substitution in this codebase uses."""
    if source_word.isupper():
        return replacement.upper()
    if source_word[:1].isupper() and source_word[1:].islower():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def apply_us_spelling_backstop(text: str) -> str:
    """Deterministically rewrites common British spelling variants in `text`
    to their American equivalents, preserving each match's original
    capitalization. Pure, side-effect-free, and idempotent (running it
    twice on already-American text is always a no-op) - safe to call
    unconditionally as a post-process pass on every generated resume_text,
    not just ones suspected of containing British spellings.

    Whole-word matching only (\\b...\\b) - never touches a British spelling
    that's a substring of a longer, unrelated word (e.g. "our" inside
    "tour" or "colour" inside a hypothetical longer compound isn't
    touched unless the compound itself is in the mapping)."""
    if not text:
        return text
    result = text
    for pattern, american in _COMPILED_PATTERNS:
        result = pattern.sub(lambda m, _american=american: _match_case(m.group(0), _american), result)
    return result
