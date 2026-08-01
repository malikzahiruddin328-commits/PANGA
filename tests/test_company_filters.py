from prospector.company_filters import looks_like_target_company


def test_rejects_universities_and_hospitals():
    assert not looks_like_target_company("Stanford University")
    assert not looks_like_target_company("Massachusetts General Hospital")
    assert not looks_like_target_company("Mayo Clinic")


def test_rejects_mega_pharma():
    assert not looks_like_target_company("Pfizer Inc.")
    assert not looks_like_target_company("Novartis Pharmaceuticals")
    assert not looks_like_target_company("IQVIA")


def test_rejects_known_acquired_companies():
    assert not looks_like_target_company("Forest Laboratories")
    assert not looks_like_target_company("Spark Therapeutics")
    assert not looks_like_target_company("VectivBio AG")


def test_rejects_research_institutes_and_cooperative_groups():
    # Real bug found live 2026-07-31: these two slipped through until the
    # keyword list was extended - regression-guard both directly.
    assert not looks_like_target_company("Gustave Roussy, Cancer Campus, Grand Paris")
    assert not looks_like_target_company("Radiation Therapy Oncology Group")


def test_accepts_genuine_small_pharma_company():
    assert looks_like_target_company("Kailera Therapeutics, Inc.")
    assert looks_like_target_company("Aerospike")


def test_rejects_empty_or_none():
    assert not looks_like_target_company("")
    assert not looks_like_target_company(None)
