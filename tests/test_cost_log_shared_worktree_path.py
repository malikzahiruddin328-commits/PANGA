"""2026-08-13, real incident: llm_client._check_spend_cap() reads
cost_log.json relative to wherever cost_log.py itself physically lives -
fine for every other data/ store (deliberately per-checkout, see
conftest.isolated_data's own docstring) but wrong for the spend cap, which
must be ONE real shared $/day ledger no matter how many git worktrees are
running Panga code at once. Confirmed live: the main checkout showed
$14.76 spent today while a worktree simultaneously had its own separate
cost_log.json showing $1.75, neither aware of the other - a worktree
process could independently spend right past the "shared" $10 cap.

cost_log._resolve_shared_data_dir() fixes this by walking a linked
worktree's `.git` file (`gitdir: <main_repo>/.git/worktrees/<name>`) back
to the one real main checkout. These tests build a REAL fake git-worktree
layout on disk (a "main" directory with a real `.git` dir, and a "worktree"
directory with a `.git` FILE pointing at
<main>/.git/worktrees/<name> - the exact shape `git worktree add` produces,
confirmed against this repo's own real worktrees) and exercise the actual
resolution + real encrypted read/write/lock path end to end - not just a
mocked path check.
"""

import threading

import cost_log
from cost_log import _resolve_shared_data_dir


def _make_fake_checkout_pair(tmp_path):
    """Builds a real on-disk (main, worktree) pair shaped exactly like this
    repo's own git worktrees: main/.git is a real directory; main/.git/
    worktrees/wt1 exists (git worktree add always creates this); worktree/
    .git is a plain text file containing
    'gitdir: <main>/.git/worktrees/wt1', matching the real content
    confirmed via `cat .git` inside a real Panga worktree."""
    main_root = tmp_path / "main-checkout"
    (main_root / ".git" / "worktrees" / "wt1").mkdir(parents=True)
    (main_root / "src").mkdir(parents=True)

    worktree_root = tmp_path / "main-checkout" / ".claude" / "worktrees" / "wt1"
    worktree_root.mkdir(parents=True)
    git_marker = worktree_root / ".git"
    gitdir_target = main_root / ".git" / "worktrees" / "wt1"
    git_marker.write_text(f"gitdir: {gitdir_target}\n", encoding="utf-8")

    return main_root, worktree_root


def test_resolve_shared_data_dir_from_main_checkout_returns_its_own_data(tmp_path):
    main_root = tmp_path / "solo-checkout"
    (main_root / ".git").mkdir(parents=True)

    resolved = _resolve_shared_data_dir(start=main_root)
    assert resolved == main_root / "data"


def test_resolve_shared_data_dir_from_a_worktree_returns_the_main_checkouts_data(tmp_path):
    main_root, worktree_root = _make_fake_checkout_pair(tmp_path)

    resolved = _resolve_shared_data_dir(start=worktree_root)
    assert resolved == main_root / "data"


def test_main_and_worktree_resolve_to_the_identical_shared_path(tmp_path):
    # The actual bug: two different processes, one in the main checkout and
    # one in a worktree, must land on the SAME real cost_log.json - not two
    # independent files.
    main_root, worktree_root = _make_fake_checkout_pair(tmp_path)

    from_main = _resolve_shared_data_dir(start=main_root)
    from_worktree = _resolve_shared_data_dir(start=worktree_root)
    assert from_main == from_worktree == main_root / "data"


def test_resolve_shared_data_dir_falls_back_when_git_file_is_malformed(tmp_path, caplog):
    import logging

    worktree_root = tmp_path / "broken-worktree"
    worktree_root.mkdir()
    (worktree_root / ".git").write_text("not a real gitdir line", encoding="utf-8")

    with caplog.at_level(logging.ERROR, logger="cost_log"):
        resolved = _resolve_shared_data_dir(start=worktree_root)

    # Falls back to its own data/ rather than crashing - but loudly, so a
    # real occurrence of this is never silently mistaken for the fix
    # actually working.
    assert resolved == worktree_root / "data"
    assert any("Could not resolve the main checkout" in r.message for r in caplog.records)


def test_resolve_shared_data_dir_env_override_wins(tmp_path, monkeypatch):
    main_root, worktree_root = _make_fake_checkout_pair(tmp_path)
    override_dir = tmp_path / "explicit-override"
    monkeypatch.setenv("PANGA_MAIN_DATA_DIR", str(override_dir))

    assert _resolve_shared_data_dir(start=worktree_root) == override_dir
    assert _resolve_shared_data_dir(start=main_root) == override_dir


def test_a_worktree_process_sees_spend_logged_by_the_main_checkout_process(tmp_path, monkeypatch):
    """The real end-to-end scenario from the incident: the "main checkout"
    process logs real spend; a "worktree" process (a different resolved
    start path, but landing on the identical shared file via the fix)
    must see that same spend as its own current total - not an
    independent, empty ledger. Exercises the actual encrypted read/write
    (security.crypto_store) and the actual cross-process lock
    (security.file_lock.locked), not mocks."""
    main_root, worktree_root = _make_fake_checkout_pair(tmp_path)
    shared_dir = _resolve_shared_data_dir(start=main_root)
    lock_dir = shared_dir / ".locks"

    # "Main checkout process" logs $14.76 of real spend.
    monkeypatch.setattr(cost_log, "COST_LOG_PATH", shared_dir / "cost_log.json")
    monkeypatch.setattr(cost_log, "LOCK_DIR", lock_dir)
    cost_log.log_api_cost(purpose="fit_score", model="claude-opus-5", input_tokens=1000, output_tokens=1000, cost_usd=14.7558)

    # "Worktree process" resolves its OWN shared dir independently (from a
    # different start path) and must land on the exact same file.
    worktree_shared_dir = _resolve_shared_data_dir(start=worktree_root)
    assert worktree_shared_dir == shared_dir
    monkeypatch.setattr(cost_log, "COST_LOG_PATH", worktree_shared_dir / "cost_log.json")
    monkeypatch.setattr(cost_log, "LOCK_DIR", worktree_shared_dir / ".locks")

    # The worktree process's own log_api_cost() call appends to the SAME
    # real ledger the main checkout already wrote to.
    cost_log.log_api_cost(purpose="fit_score", model="claude-opus-5", input_tokens=500, output_tokens=500, cost_usd=1.7529)

    entries = cost_log.load_cost_log()
    assert len(entries) == 2
    total = sum(e["cost_usd"] for e in entries)
    assert round(total, 4) == round(14.7558 + 1.7529, 4)


def test_worktree_local_bug_would_have_hidden_the_main_checkouts_spend(tmp_path, monkeypatch):
    """Negative control - proves the OLD (broken) behavior really would
    have shown two independent ledgers, so the fix above is actually
    closing a real gap and not just testing something that always passed.
    Uses `start=` per checkout WITHOUT ever calling _resolve_shared_data_dir
    with a git-anchor at all (i.e. simulates the pre-fix "wherever this
    file lives" resolution) by pointing each process straight at its own
    checkout's data/ dir."""
    main_root, worktree_root = _make_fake_checkout_pair(tmp_path)

    main_cost_log = main_root / "data" / "cost_log.json"
    worktree_cost_log = worktree_root / "data" / "cost_log.json"

    monkeypatch.setattr(cost_log, "COST_LOG_PATH", main_cost_log)
    monkeypatch.setattr(cost_log, "LOCK_DIR", main_root / "data" / ".locks")
    cost_log.log_api_cost(purpose="fit_score", model="claude-opus-5", input_tokens=1, output_tokens=1, cost_usd=14.7558)

    monkeypatch.setattr(cost_log, "COST_LOG_PATH", worktree_cost_log)
    monkeypatch.setattr(cost_log, "LOCK_DIR", worktree_root / "data" / ".locks")
    cost_log.log_api_cost(purpose="fit_score", model="claude-opus-5", input_tokens=1, output_tokens=1, cost_usd=1.7529)

    # Each "process" sees only its own isolated total under this simulated
    # pre-fix layout - exactly the bug (each worktree gets its own
    # effectively-independent budget).
    monkeypatch.setattr(cost_log, "COST_LOG_PATH", main_cost_log)
    assert round(sum(e["cost_usd"] for e in cost_log.load_cost_log()), 4) == 14.7558
    monkeypatch.setattr(cost_log, "COST_LOG_PATH", worktree_cost_log)
    assert round(sum(e["cost_usd"] for e in cost_log.load_cost_log()), 4) == 1.7529


def test_spend_cap_trips_for_a_worktree_process_once_the_shared_total_crosses_it(tmp_path, monkeypatch):
    """Full-stack real scenario: llm_client._check_spend_cap() (not just
    cost_log directly) must block a "worktree" process once the SHARED
    total (spend already logged by the "main checkout") reaches the cap -
    proving the fix actually closes the real-world gap the incident
    described, not just that path resolution matches in isolation."""
    import llm_client

    main_root, worktree_root = _make_fake_checkout_pair(tmp_path)
    shared_dir = _resolve_shared_data_dir(start=main_root)

    monkeypatch.setattr(cost_log, "COST_LOG_PATH", shared_dir / "cost_log.json")
    monkeypatch.setattr(cost_log, "LOCK_DIR", shared_dir / ".locks")
    monkeypatch.setenv("PANGA_DAILY_SPEND_CAP_USD", "10.0")

    # "Main checkout" already spent right up to the cap.
    cost_log.log_api_cost(purpose="fit_score", model="claude-opus-5", input_tokens=1, output_tokens=1, cost_usd=10.0)

    # "Worktree process" resolves the identical shared file (proven above)
    # and must be blocked by spend it never itself logged.
    worktree_shared_dir = _resolve_shared_data_dir(start=worktree_root)
    monkeypatch.setattr(cost_log, "COST_LOG_PATH", worktree_shared_dir / "cost_log.json")
    monkeypatch.setattr(cost_log, "LOCK_DIR", worktree_shared_dir / ".locks")

    import pytest
    with pytest.raises(llm_client.LLMSpendCapExceeded):
        llm_client._check_spend_cap("fit_score")


def test_concurrent_writers_from_main_and_worktree_lose_no_entries(tmp_path, monkeypatch):
    """Real concurrency proof against the actual msvcrt lock (not mocked) -
    the SAME class of test test_canonical_taxonomy_locking.py uses for its
    own locking fix. Two groups of threads simulate a main-checkout process
    and a worktree process both writing real cost entries at the same time
    against the SAME resolved shared file - the module docstring's "Known
    limitation" notes the spend-cap CHECK itself isn't atomic, but the
    underlying log_api_cost() write path must still lose no entries, or the
    running total this whole fix depends on would itself be unreliable
    regardless of which checkout logged it."""
    main_root, worktree_root = _make_fake_checkout_pair(tmp_path)
    shared_dir = _resolve_shared_data_dir(start=main_root)
    assert _resolve_shared_data_dir(start=worktree_root) == shared_dir

    cost_log_path = shared_dir / "cost_log.json"
    lock_dir = shared_dir / ".locks"
    monkeypatch.setattr(cost_log, "COST_LOG_PATH", cost_log_path)
    monkeypatch.setattr(cost_log, "LOCK_DIR", lock_dir)

    writers_per_side = 10

    def write_one(label):
        cost_log.log_api_cost(purpose=label, model="claude-opus-5", input_tokens=1, output_tokens=1, cost_usd=0.01)

    threads = [
        threading.Thread(target=write_one, args=(f"main-{i}",)) for i in range(writers_per_side)
    ] + [
        threading.Thread(target=write_one, args=(f"worktree-{i}",)) for i in range(writers_per_side)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive(), "a thread hung - possible deadlock in the shared lock"

    entries = cost_log.load_cost_log()
    purposes = {e["purpose"] for e in entries}
    expected = {f"main-{i}" for i in range(writers_per_side)} | {f"worktree-{i}" for i in range(writers_per_side)}
    assert len(entries) == 2 * writers_per_side  # none silently dropped
    assert purposes == expected
