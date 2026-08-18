"""Real crash, 2026-08-17: a background job-search run wrote to
jobs.json/applications.json (via security.file_lock.locked(), several
minutes long) while the live Streamlit session concurrently read the same
files with no lock at all (this module's own documented "reads don't need
the lock, only read-modify-write does" convention - true only if a write
is atomic from a reader's point of view). The old write_bytes() did
path.write_bytes(...) directly, which truncates the file to zero bytes
before writing the new content - a concurrent reader landing in that
window sees an empty/partial file, fails decryption, and raises. 8510
crashed during exactly that kind of overlap the same day this was found.

This test reproduces the race directly: one thread hammers write_json() on
a single file while another concurrently hammers read_json() on the same
path, for real, for a few hundred iterations - proving every successful
read returns fully valid, decryptable, parseable JSON (either an older or
newer write, never a torn one), and that no reader ever raises."""

import threading
import time

from security.crypto_store import read_json, write_json


def test_concurrent_reads_never_see_a_torn_write(tmp_path):
    path = tmp_path / "concurrent.json"
    write_json(path, {"n": 0, "payload": "x" * 5000})  # seed a real first version

    stop = threading.Event()
    read_errors = []
    write_errors = []
    writes_done = [0]

    def writer():
        n = 1
        while not stop.is_set() and n <= 300:
            try:
                write_json(path, {"n": n, "payload": "x" * 5000})
            except Exception as exc:  # noqa: BLE001 - real bug shape is "raises at all"
                write_errors.append(exc)
                stop.set()
                return
            writes_done[0] = n
            n += 1

    def reader():
        while not stop.is_set():
            try:
                data = read_json(path, default=None)
                if data is not None:
                    assert set(data.keys()) == {"n", "payload"}
                    assert len(data["payload"]) == 5000
            except Exception as exc:  # noqa: BLE001 - real bug shape is "raises at all"
                read_errors.append(exc)
                stop.set()

    writer_thread = threading.Thread(target=writer)
    reader_threads = [threading.Thread(target=reader) for _ in range(4)]
    writer_thread.start()
    for t in reader_threads:
        t.start()

    writer_thread.join(timeout=30)
    stop.set()
    for t in reader_threads:
        t.join(timeout=5)

    assert writes_done[0] > 0, "writer never completed a single write - test setup is broken"
    assert write_errors == [], f"writer exhausted its retry budget under contention: {write_errors!r}"
    assert read_errors == [], f"a concurrent read saw a torn/corrupt write: {read_errors!r}"

    # No leftover .tmp-* file from an interrupted/failed replace.
    leftovers = list(tmp_path.glob("concurrent.json.tmp-*"))
    assert leftovers == [], f"a temp file was left behind: {leftovers}"


def test_write_leaves_no_temp_file_on_success(tmp_path):
    path = tmp_path / "clean.json"
    write_json(path, {"a": 1})
    write_json(path, {"a": 2})
    assert read_json(path) == {"a": 2}
    assert list(tmp_path.glob("clean.json.tmp-*")) == []


def test_read_during_slow_write_gets_old_or_new_never_partial(tmp_path, monkeypatch):
    """Directly targets the exact old bug: force the write to pause between
    "temp file written" and "os.replace onto the real path" (simulating a
    slow/contended filesystem), and confirm a read during that pause still
    sees the OLD file fully intact - never the truncated state the old
    path.write_bytes() implementation would have produced."""
    import os as os_module

    path = tmp_path / "slow.json"
    write_json(path, {"version": "old", "payload": "y" * 2000})

    real_replace = os_module.replace
    paused = threading.Event()
    resume = threading.Event()

    def slow_replace(src, dst):
        paused.set()
        resume.wait(timeout=5)
        real_replace(src, dst)

    monkeypatch.setattr(os_module, "replace", slow_replace)

    def do_write():
        write_json(path, {"version": "new", "payload": "z" * 2000})

    writer_thread = threading.Thread(target=do_write)
    writer_thread.start()
    assert paused.wait(timeout=5), "writer never reached the paused replace() call"

    # The real production race: a read happening exactly while the new
    # content is staged but not yet swapped in.
    mid_write_read = read_json(path)
    assert mid_write_read == {"version": "old", "payload": "y" * 2000}

    resume.set()
    writer_thread.join(timeout=5)

    assert read_json(path) == {"version": "new", "payload": "z" * 2000}
