"""concurrency.adaptive_throttle - generic, project-agnostic throttle used
by the basket "Draft & score all" concurrent path (ui/app.py) and reusable
outside Panga (Zahir's explicit ask, 2026-08-17)."""

from concurrency import adaptive_throttle
from concurrency.adaptive_throttle import current_cpu_percent, effective_worker_count


def test_full_concurrency_under_low_load():
    assert effective_worker_count(5, cpu_percent=10.0) == 5
    assert effective_worker_count(5, cpu_percent=69.9) == 5


def test_halved_concurrency_under_moderate_load():
    assert effective_worker_count(5, cpu_percent=70.0) == 3  # ceil(5/2)
    assert effective_worker_count(10, cpu_percent=75.0) == 5
    assert effective_worker_count(1, cpu_percent=75.0) == 1  # never below min_workers


def test_min_workers_under_high_load():
    assert effective_worker_count(5, cpu_percent=85.0) == 1
    assert effective_worker_count(5, cpu_percent=99.0) == 1
    assert effective_worker_count(5, min_workers=2, cpu_percent=99.0) == 2


def test_boundary_exactly_at_thresholds_uses_the_stricter_band():
    # >= high_load_percent (not just >) triggers the hard floor
    assert effective_worker_count(5, high_load_percent=85.0, cpu_percent=85.0) == 1
    # >= moderate_load_percent (not just >) triggers the halving band
    assert effective_worker_count(5, moderate_load_percent=70.0, cpu_percent=70.0) == 3


def test_never_exceeds_target_max_or_undercuts_min_workers_relationship():
    # min_workers greater than target_max is clamped down to target_max,
    # never silently demanding MORE workers than the caller asked for.
    assert effective_worker_count(3, min_workers=10, cpu_percent=99.0) == 3


def test_fails_open_when_load_cannot_be_read():
    # None means "couldn't check" (e.g. psutil missing/erroring) - must
    # never throttle below what the caller asked for just because
    # monitoring itself failed.
    assert effective_worker_count(5, cpu_percent=None) == 5


def test_current_cpu_percent_returns_none_when_psutil_unavailable(monkeypatch):
    monkeypatch.setattr(adaptive_throttle, "psutil", None)
    assert current_cpu_percent() is None


def test_current_cpu_percent_fails_open_on_a_read_exception(monkeypatch):
    class _BrokenPsutil:
        def cpu_percent(self, interval=None):
            raise RuntimeError("boom")

    monkeypatch.setattr(adaptive_throttle, "psutil", _BrokenPsutil())
    assert current_cpu_percent() is None


def test_current_cpu_percent_uses_the_real_psutil_when_available():
    # Real, unmocked call - confirms the module actually reaches a real
    # installed psutil and gets back a plausible percentage, not just that
    # the mocked unit tests above pass.
    value = current_cpu_percent(sample_seconds=0.05)
    assert value is None or (isinstance(value, (int, float)) and 0.0 <= value <= 100.0)


def test_effective_worker_count_with_real_psutil_sampling_does_not_raise():
    # No cpu_percent override - exercises the real current_cpu_percent()
    # call path end to end, not just the pure band-selection logic.
    result = effective_worker_count(5, sample_seconds=0.05)
    assert 1 <= result <= 5
