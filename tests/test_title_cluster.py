import yaml

from search.title_cluster import load_title_clusters, resolve_title_cluster


def _write_settings(path, title_clusters):
    path.write_text(yaml.safe_dump({"title_clusters": title_clusters}), encoding="utf-8")


def test_load_title_clusters_empty_when_missing(isolated_data):
    import search.title_cluster as title_cluster
    assert not title_cluster.SETTINGS_PATH.exists()
    assert load_title_clusters() == []


def test_load_title_clusters_returns_configured_list(isolated_data):
    import search.title_cluster as title_cluster
    _write_settings(title_cluster.SETTINGS_PATH, [
        {"name": "Executive IT Leadership", "titles": ["CIO", "Head of IT", "VP Enterprise Architecture"]},
    ])
    clusters = load_title_clusters()
    assert clusters == [{"name": "Executive IT Leadership", "titles": ["CIO", "Head of IT", "VP Enterprise Architecture"]}]


def test_load_title_clusters_skips_malformed_entries(isolated_data):
    import search.title_cluster as title_cluster
    _write_settings(title_cluster.SETTINGS_PATH, [
        {"name": "Good", "titles": ["CIO"]},
        {"name": "", "titles": ["Missing name"]},
        {"name": "No titles list"},
        "not even a dict",
        {"name": "Bad titles type", "titles": "CIO"},
    ])
    clusters = load_title_clusters()
    assert clusters == [{"name": "Good", "titles": ["CIO"]}]


def test_resolve_title_cluster_matches_case_insensitive_substring():
    clusters = [{"name": "Executive IT Leadership", "titles": ["CIO", "Head of IT", "VP Enterprise Architecture"]}]
    assert resolve_title_cluster("Chief Information Officer (CIO)", clusters) == "Executive IT Leadership"
    assert resolve_title_cluster("SVP, Head of IT and Digital", clusters) == "Executive IT Leadership"
    assert resolve_title_cluster("vp enterprise architecture", clusters) == "Executive IT Leadership"


def test_resolve_title_cluster_no_match_returns_none():
    clusters = [{"name": "Executive IT Leadership", "titles": ["CIO", "Head of IT"]}]
    assert resolve_title_cluster("Senior Systems Engineer", clusters) is None
    assert resolve_title_cluster("", clusters) is None
    assert resolve_title_cluster(None, clusters) is None


def test_resolve_title_cluster_first_match_wins_in_configured_order():
    clusters = [
        {"name": "Cluster A", "titles": ["Director"]},
        {"name": "Cluster B", "titles": ["IT Director"]},
    ]
    assert resolve_title_cluster("IT Director", clusters) == "Cluster A"


def test_resolve_title_cluster_reads_config_when_no_clusters_passed(isolated_data):
    import search.title_cluster as title_cluster
    _write_settings(title_cluster.SETTINGS_PATH, [{"name": "Exec IT", "titles": ["CIO"]}])
    assert resolve_title_cluster("Global CIO") == "Exec IT"
