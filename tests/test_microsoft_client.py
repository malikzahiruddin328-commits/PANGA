"""Tests for microsoft_client.py. Pure helpers (_strip_html,
_message_summary) are tested directly; the Graph-API-touching functions
are tested against a FakeResponse/monkeypatched requests.get/post/patch,
verifying the actual request shape and response handling - no live Azure
app registration or Graph tenant was reachable to verify against from
this environment (flagged, same pattern as email_providers.py/
imap_client.py's tests)."""

import pytest

import microsoft_client


def test_strip_html_removes_style_and_tags():
    html = "<html><style>.x{color:red}</style><body>Hello <b>world</b></body></html>"
    result = microsoft_client._strip_html(html)
    assert "color:red" not in result
    assert "Hello" in result and "world" in result


def test_message_summary_handles_missing_from():
    msg = {"id": "m1", "conversationId": "c1", "subject": "Hi", "receivedDateTime": "2026-08-04T00:00:00Z", "bodyPreview": "preview"}
    summary = microsoft_client._message_summary(msg)
    assert summary["sender"] == ""
    assert summary["subject"] == "Hi"


def test_message_summary_defaults_subject():
    msg = {"id": "m1", "from": {"emailAddress": {"address": "a@b.com"}}}
    summary = microsoft_client._message_summary(msg)
    assert summary["subject"] == "(no subject)"
    assert summary["sender"] == "a@b.com"


class FakeResponse:
    def __init__(self, json_body, status_code=200):
        self._json = json_body
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.text = str(json_body)

    def json(self):
        return self._json


@pytest.fixture
def fake_graph(monkeypatch):
    monkeypatch.setattr(microsoft_client, "_headers", lambda: {"Authorization": "Bearer fake"})
    calls = []
    return calls


def test_search_threads_dedupes_by_conversation(monkeypatch, fake_graph):
    def fake_get(url, headers, params, timeout):
        fake_graph.append((url, params))
        return FakeResponse({"value": [
            {"id": "m1", "conversationId": "c1", "subject": "First", "receivedDateTime": "2026-08-04T10:00:00Z"},
            {"id": "m2", "conversationId": "c1", "subject": "First (older copy)", "receivedDateTime": "2026-08-04T09:00:00Z"},
            {"id": "m3", "conversationId": "c2", "subject": "Second", "receivedDateTime": "2026-08-03T10:00:00Z"},
        ]})
    monkeypatch.setattr(microsoft_client.requests, "get", fake_get)
    results = microsoft_client.search_threads("interview")
    assert len(results) == 2
    assert results[0]["conversation_id"] == "c1"
    assert results[1]["conversation_id"] == "c2"
    assert fake_graph[0][1]["$search"] == '"interview"'


def test_get_thread_sorts_oldest_first_and_strips_html(monkeypatch):
    monkeypatch.setattr(microsoft_client, "_headers", lambda: {})

    def fake_get(url, headers, params, timeout):
        return FakeResponse({"value": [
            {"id": "m2", "receivedDateTime": "2026-08-04T10:00:00Z", "body": {"contentType": "text", "content": "second"}},
            {"id": "m1", "receivedDateTime": "2026-08-04T09:00:00Z", "body": {"contentType": "html", "content": "<b>first</b><style>.x{}</style>"}},
        ]})
    monkeypatch.setattr(microsoft_client.requests, "get", fake_get)
    result = microsoft_client.get_thread("c1")
    assert [m["message_id"] for m in result["messages"]] == ["m1", "m2"]
    assert "<b>" not in result["messages"][0]["body"]
    assert "first" in result["messages"][0]["body"]


def test_create_draft_new_message_has_no_isdraft_field(monkeypatch):
    monkeypatch.setattr(microsoft_client, "_headers", lambda: {})
    posted = {}

    def fake_post(url, headers, json, timeout):
        posted["url"] = url
        posted["body"] = json
        return FakeResponse({"id": "draft1"})
    monkeypatch.setattr(microsoft_client.requests, "post", fake_post)
    draft_id = microsoft_client.create_draft("them@example.com", "Subj", "Body")
    assert draft_id == "draft1"
    assert "isDraft" not in posted["body"]
    assert posted["url"].endswith("/me/messages")


def test_create_draft_reply_uses_create_reply_then_patches(monkeypatch):
    monkeypatch.setattr(microsoft_client, "_headers", lambda: {})
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(("post", url, json))
        return FakeResponse({"id": "draft2"})

    def fake_patch(url, headers, json, timeout):
        calls.append(("patch", url, json))
        return FakeResponse({}, status_code=204)
    monkeypatch.setattr(microsoft_client.requests, "post", fake_post)
    monkeypatch.setattr(microsoft_client.requests, "patch", fake_patch)
    draft_id = microsoft_client.create_draft("them@example.com", "Re: Subj", "Reply body", reply_to_message_id="orig1")
    assert draft_id == "draft2"
    assert calls[0][1].endswith("/me/messages/orig1/createReply")
    assert calls[1][1].endswith("/me/messages/draft2")
    assert calls[1][2]["body"]["content"] == "Reply body"


def test_list_drafts_follows_pagination(monkeypatch):
    monkeypatch.setattr(microsoft_client, "_headers", lambda: {})
    pages = [
        {"value": [{"id": "d1"}], "@odata.nextLink": f"{microsoft_client.GRAPH_BASE}/me/mailFolders/drafts/messages?$skip=1"},
        {"value": [{"id": "d2"}]},
    ]

    def fake_get(url, headers, params, timeout):
        return FakeResponse(pages.pop(0))
    monkeypatch.setattr(microsoft_client.requests, "get", fake_get)
    assert microsoft_client.list_drafts() == ["d1", "d2"]


def test_label_thread_merges_categories(monkeypatch):
    monkeypatch.setattr(microsoft_client, "_headers", lambda: {})

    def fake_get(url, headers, params, timeout):
        return FakeResponse({"categories": ["Existing"]})

    patched = {}

    def fake_patch(url, headers, json, timeout):
        patched["body"] = json
        return FakeResponse({}, status_code=204)
    monkeypatch.setattr(microsoft_client.requests, "get", fake_get)
    monkeypatch.setattr(microsoft_client.requests, "patch", fake_patch)
    microsoft_client.label_thread("m1", ["Panga/Reviewed"])
    assert patched["body"]["categories"] == ["Existing", "Panga/Reviewed"]


def test_list_recent_dedupes_and_includes_categories(monkeypatch):
    monkeypatch.setattr(microsoft_client, "_headers", lambda: {})
    captured_params = {}

    def fake_get(url, headers, params, timeout):
        captured_params.update(params)
        return FakeResponse({"value": [
            {"id": "m1", "conversationId": "c1", "subject": "First", "receivedDateTime": "2026-08-04T10:00:00Z", "categories": ["Panga/Reviewed"]},
            {"id": "m2", "conversationId": "c1", "subject": "First (older copy)", "receivedDateTime": "2026-08-04T09:00:00Z", "categories": []},
            {"id": "m3", "conversationId": "c2", "subject": "Second", "receivedDateTime": "2026-08-03T10:00:00Z", "categories": []},
        ]})
    monkeypatch.setattr(microsoft_client.requests, "get", fake_get)
    results = microsoft_client.list_recent(since_days=2)
    assert len(results) == 2
    assert results[0]["categories"] == ["Panga/Reviewed"]
    assert results[1]["categories"] == []
    assert "$filter" in captured_params
    assert "receivedDateTime ge" in captured_params["$filter"]


def test_get_message_body_strips_html(monkeypatch):
    monkeypatch.setattr(microsoft_client, "_headers", lambda: {})

    def fake_get(url, headers, params, timeout):
        return FakeResponse({"body": {"contentType": "html", "content": "<b>Hi</b> <style>.x{}</style>there"}})
    monkeypatch.setattr(microsoft_client.requests, "get", fake_get)
    body = microsoft_client.get_message_body("m1")
    assert "Hi" in body and "there" in body
    assert "<b>" not in body and ".x{}" not in body


def test_create_draft_extracts_bare_address_from_display_name_format(monkeypatch):
    monkeypatch.setattr(microsoft_client, "_headers", lambda: {})
    posted = {}

    def fake_post(url, headers, json, timeout):
        posted["body"] = json
        return FakeResponse({"id": "draft1"})
    monkeypatch.setattr(microsoft_client.requests, "post", fake_post)
    microsoft_client.create_draft("Some Recruiter <recruiter@example.com>", "Subj", "Body")
    assert posted["body"]["toRecipients"] == [{"emailAddress": {"address": "recruiter@example.com"}}]


def test_create_draft_reply_extracts_bare_address_too(monkeypatch):
    monkeypatch.setattr(microsoft_client, "_headers", lambda: {})
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(("post", json))
        return FakeResponse({"id": "draft2"})

    def fake_patch(url, headers, json, timeout):
        calls.append(("patch", json))
        return FakeResponse({}, status_code=204)
    monkeypatch.setattr(microsoft_client.requests, "post", fake_post)
    monkeypatch.setattr(microsoft_client.requests, "patch", fake_patch)
    microsoft_client.create_draft("Some Recruiter <recruiter@example.com>", "Re: Subj", "Body", reply_to_message_id="orig1")
    patch_body = calls[1][1]
    assert patch_body["toRecipients"] == [{"emailAddress": {"address": "recruiter@example.com"}}]
