"""Covers tailoring.us_spelling.apply_us_spelling_backstop() - the
deterministic code-level backstop (2026-08-18, Zahir's real ask) for
generated resume_text, added alongside a strengthened SYSTEM_PROMPT
instruction per this repo's own CLAUDE.md principle #3 ("AI output checked
by a literal/deterministic downstream rule is fragile without a code-level
backstop"). See test_drafting.py's
test_finalize_resume_draft_applies_the_us_spelling_backstop for the
integration point (drafting._finalize_resume_draft)."""

from tailoring.us_spelling import apply_us_spelling_backstop


def test_common_isation_ization_words_are_converted():
    assert apply_us_spelling_backstop("Led the organisation of cross-functional teams.") == \
        "Led the organization of cross-functional teams."
    assert "specialization" in apply_us_spelling_backstop("Deep specialisation in cloud migration.")


def test_ise_ize_verb_family_is_converted():
    text = apply_us_spelling_backstop(
        "Organised and prioritised the roadmap to optimise delivery and standardise reporting."
    )
    assert "Organized" in text
    assert "prioritized" in text
    assert "optimize" in text
    assert "standardize" in text
    assert "standardise" not in text


def test_yse_yze_verb_family_is_converted():
    text = apply_us_spelling_backstop("Analysed the data to recognise trends and realise cost savings.")
    assert "Analyzed" in text
    assert "recognize" in text
    assert "realize" in text


def test_our_or_words_are_converted():
    text = apply_us_spelling_backstop("Strong colour sense, a favourite among the team, driven by real labour.")
    assert "color" in text
    assert "favorite" in text
    assert "labor" in text
    assert "colour" not in text
    assert "favourite" not in text
    assert "labour" not in text


def test_doubled_consonant_inflections_are_converted():
    text = apply_us_spelling_backstop("Travelled extensively while modelling and counselling teams.")
    assert "Traveled" in text
    assert "modeling" in text
    assert "counseling" in text


def test_programme_and_centre_and_defence_are_converted():
    text = apply_us_spelling_backstop("Delivered the programme from the regional centre, covering defence procurement.")
    assert "program" in text
    assert "center" in text
    assert "defense" in text


def test_capitalization_is_preserved_title_case():
    assert apply_us_spelling_backstop("Organised the launch.") == "Organized the launch."


def test_capitalization_is_preserved_all_caps():
    assert apply_us_spelling_backstop("ORGANISED THE LAUNCH.") == "ORGANIZED THE LAUNCH."


def test_capitalization_is_preserved_lowercase():
    assert apply_us_spelling_backstop("we organised the launch.") == "we organized the launch."


def test_already_american_text_is_left_untouched():
    text = "Organized and prioritized the roadmap, delivered the program on time, colors on brand."
    assert apply_us_spelling_backstop(text) == text


def test_applying_twice_is_idempotent():
    text = "Organised the programme and colour scheme."
    once = apply_us_spelling_backstop(text)
    twice = apply_us_spelling_backstop(once)
    assert once == twice


def test_does_not_touch_words_that_are_not_real_british_variants():
    # "supervise", "advise", "revise", "surprise", "comprise", "exercise"
    # are NOT -ise/-ize variants at all - both dialects spell them with
    # "-ise"/"-se". Also checks whole-word boundaries: a British spelling
    # embedded as a substring of an unrelated word must not be touched.
    text = "He was surprised to supervise, advise, and revise the exercise before it was comprised of tours."
    assert apply_us_spelling_backstop(text) == text


def test_empty_and_none_safe():
    assert apply_us_spelling_backstop("") == ""


def test_vocabulary_differences_are_deliberately_untouched():
    # Out of scope per Zahir's explicit instruction - only mechanical
    # spelling variants of the SAME word are corrected, never genuine
    # British/American vocabulary swaps (CV vs resume, mobile vs cell
    # phone, holiday vs vacation) - those need real judgment, not a
    # mechanical spelling fix.
    text = "Updated my CV ahead of the interview, then took a holiday before my next mobile call."
    assert apply_us_spelling_backstop(text) == text
