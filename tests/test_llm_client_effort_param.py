"""Real bug found 2026-08-11 live-validating the fit_score pre-filter's
Haiku domain check: claude-haiku-4-5 rejects the `effort` key in
output_config outright ("This model does not support the effort
parameter"). Every one of 126 real domain-check calls in that validation
run failed on this - silently, since the caller's own fail-open handling
swallowed it, so it went unnoticed until the debug log was read directly.
call_structured's effort param is now Optional; effort=None omits the key
from output_config entirely rather than guessing a value the model will
accept. These tests exercise the real request construction in
call_structured's make_request(), not a mocked call_structured() like
every other test file in this suite - the bug was specifically in what
gets sent to client.messages.stream(), so that's what must be checked.
"""

from types import SimpleNamespace

from llm_client import call_structured


class _FakeStream:
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def __iter__(self):
        return iter([])

    def get_final_message(self):
        return SimpleNamespace(
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            content=[SimpleNamespace(type="text", text='{"ok": true}')],
            stop_reason="end_turn",
        )


def _fake_client(captured_kwargs):
    def stream(**kwargs):
        captured_kwargs.append(kwargs)
        return _FakeStream()

    return SimpleNamespace(messages=SimpleNamespace(stream=stream))


def _call(isolated_data, **overrides):
    captured = []
    client = _fake_client(captured)
    kwargs = dict(
        client=client, system="sys prompt", user_content="hi",
        schema={"type": "object"}, max_tokens=100, model="claude-haiku-4-5",
    )
    kwargs.update(overrides)
    result = call_structured(**kwargs)
    assert result == {"ok": True}
    return captured[0]


def test_effort_none_omits_the_key_from_output_config(isolated_data):
    sent = _call(isolated_data, effort=None)
    assert "effort" not in sent["output_config"]
    assert sent["output_config"]["format"] == {"type": "json_schema", "schema": {"type": "object"}}


def test_effort_high_default_still_included_for_models_that_support_it(isolated_data):
    sent = _call(isolated_data)
    assert sent["output_config"]["effort"] == "high"


def test_explicit_effort_value_still_passed_through(isolated_data):
    sent = _call(isolated_data, effort="low")
    assert sent["output_config"]["effort"] == "low"
