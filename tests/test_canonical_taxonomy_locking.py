"""Real proof of the taxonomy file-locking fix (2026-08-11, RM caught the
gap live mid-merge while Zahir was actively interviewing through a
separate concurrent session). Same proof pattern as
test_linkedin_storage_lock.py: a RecordingLock mock proving the real lock
primitive is actually used, a real multi-thread test against the actual
msvcrt lock proving no concurrent entry is lost, AND (the extra step this
class of bug needs) a deterministic reproduction of the real loss on the
OLD unlocked primitives, so the fix is proven against a real failure, not
just asserted to look right."""

import threading

import skills.canonical_taxonomy as ct
from skills.canonical_taxonomy import (
    add_canonical_entry,
    load_taxonomy,
    resolve_or_create_canonical_id,
    run_locked_bulk_mutation,
    save_taxonomy,
)


class RecordingLock:
    def __init__(self, calls, name):
        self.calls = calls
        self.name = name

    def __enter__(self):
        self.calls.append(("enter", self.name))
        return self

    def __exit__(self, *exc):
        self.calls.append(("exit", self.name))
        return False


def test_resolve_or_create_canonical_id_runs_inside_the_lock(isolated_data, monkeypatch):
    calls = []
    monkeypatch.setattr(ct, "locked", lambda name: RecordingLock(calls, name))
    resolve_or_create_canonical_id("Some new skill", "Uncategorized")
    assert calls[0] == ("enter", "canonical_taxonomy")
    assert calls[-1] == ("exit", "canonical_taxonomy")


def test_run_locked_bulk_mutation_runs_inside_the_lock(isolated_data, monkeypatch):
    calls = []
    monkeypatch.setattr(ct, "locked", lambda name: RecordingLock(calls, name))
    run_locked_bulk_mutation(lambda taxonomy: add_canonical_entry(taxonomy, "Cat", "X"))
    assert calls[0] == ("enter", "canonical_taxonomy")
    assert calls[-1] == ("exit", "canonical_taxonomy")


def test_the_race_is_real_on_the_old_unlocked_primitives(isolated_data):
    """Deterministic reproduction (not timing-dependent luck) of the exact
    real bug RM caught: two "writers" both load the SAME old taxonomy
    before either saves, then both save - whichever saves second silently
    discards the first one's new entry, with no error. A barrier forces
    both loads to complete before either write, so this fails reliably
    every run, proving the race is real rather than asserting it from
    code inspection alone."""
    save_taxonomy({"_meta": {}})  # real starting state, empty
    barrier = threading.Barrier(2, timeout=5)

    def racy_writer(label):
        taxonomy = load_taxonomy()  # both threads read the SAME old state
        barrier.wait()  # neither proceeds to write until both have read
        add_canonical_entry(taxonomy, "Cat", label)
        try:
            save_taxonomy(taxonomy)  # second writer here overwrites the first's entry
        except PermissionError:
            # Real, observed second failure mode of the SAME unlocked race
            # on Windows (2026-08-11): os.replace() from two threads onto
            # the same destination can also raise WinError 5 outright,
            # not just silently overwrite - an even more severe risk in a
            # live save path (an unhandled exception, not just lost data).
            # Expected here (this test exists specifically to prove the
            # race is real); the locked path this fix adds prevents both
            # failure modes by serializing access, never reached by
            # production code once callers go through
            # resolve_or_create_canonical_id()/run_locked_bulk_mutation().
            pass

    t1 = threading.Thread(target=racy_writer, args=("First new skill",))
    t2 = threading.Thread(target=racy_writer, args=("Second new skill",))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    final = load_taxonomy()
    all_labels = {e["canonical_label"] for entries in final.values() if isinstance(entries, list) for e in entries}
    # Real, reproduced data loss: only ONE of the two new entries survives.
    assert len(all_labels) == 1


def test_concurrent_resolve_or_create_loses_no_entries(isolated_data):
    """Real proof against the actual msvcrt lock (not a mock): 8 threads
    each resolving a DIFFERENT new label concurrently through
    resolve_or_create_canonical_id() - if the lock isn't genuinely
    serializing the load-modify-save sequence, some new entries silently
    vanish, same failure mode reproduced deterministically above."""
    save_taxonomy({"_meta": {}})
    threads_n = 8
    labels = [f"Concurrent skill {i}" for i in range(threads_n)]
    results = {}
    lock = threading.Lock()

    def worker(label):
        canonical_id = resolve_or_create_canonical_id(label, "Cat")
        with lock:
            results[label] = canonical_id

    threads = [threading.Thread(target=worker, args=(label,)) for label in labels]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive(), "a thread hung - possible deadlock in locked()"

    final = load_taxonomy()
    all_labels = {e["canonical_label"] for entries in final.values() if isinstance(entries, list) for e in entries}
    assert all_labels == set(labels)  # every single one survived, none lost to the race
    assert len(set(results.values())) == threads_n  # every thread got a distinct real id, no accidental merge
