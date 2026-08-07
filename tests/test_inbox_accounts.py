"""Tests for inbox_accounts.py's unified adapter - each provider's
list_recent_unreviewed()/mark_reviewed()/mark_cta()/archive() delegate to
gmail_client/microsoft_client/imap_client, monkeypatched here at the
function level (those clients' own protocol correctness is already
covered by their dedicated test files)."""

import gmail_client
import imap_client
import microsoft_client

import inbox_accounts


# ---- GmailAccount ----

def test_gmail_list_recent_unreviewed_builds_query_and_maps_fields(monkeypatch):
    captured = {}

    def fake_search_threads(query, max_results=50):
        captured["query"] = query
        return [{"thread_id": "t1", "message_id": "m1", "subject": "Subj", "sender": "a@b.com", "date": "2026-08-04", "snippet": "snip"}]
    monkeypatch.setattr(gmail_client, "search_threads", fake_search_threads)

    acct = inbox_accounts.GmailAccount()
    results = acct.list_recent_unreviewed()
    assert len(results) == 1
    assert results[0].ref == "t1"
    assert results[0].message_id == "m1"
    assert "-label:Panga/Reviewed" in captured["query"]
    assert "newer_than:2d" in captured["query"]


def test_gmail_mark_cta_ensures_labels_once_and_applies_both(monkeypatch):
    ensure_calls = []
    label_calls = []
    monkeypatch.setattr(gmail_client, "ensure_label", lambda name: ensure_calls.append(name) or f"id-{name}")
    monkeypatch.setattr(gmail_client, "label_thread", lambda ref, ids: label_calls.append((ref, ids)))

    acct = inbox_accounts.GmailAccount()
    acct.mark_cta("t1")
    acct.mark_reviewed("t2")  # second call - must NOT re-ensure labels
    assert ensure_calls == ["Panga/Reviewed", "Panga/Call-to-Action", "Panga/Handled"]
    assert label_calls == [("t1", ["id-Panga/Reviewed", "id-Panga/Call-to-Action"]), ("t2", ["id-Panga/Reviewed"])]


def test_gmail_archive_unlabels_inbox_and_labels_handled(monkeypatch):
    calls = []
    monkeypatch.setattr(gmail_client, "ensure_label", lambda name: f"id-{name}")
    monkeypatch.setattr(gmail_client, "unlabel_thread", lambda ref, ids: calls.append(("unlabel", ref, ids)))
    monkeypatch.setattr(gmail_client, "label_thread", lambda ref, ids: calls.append(("label", ref, ids)))

    inbox_accounts.GmailAccount().archive("t1")
    assert calls == [("unlabel", "t1", ["INBOX"]), ("label", "t1", ["id-Panga/Handled"])]


# ---- MicrosoftAccount ----

def test_microsoft_list_recent_unreviewed_filters_by_category(monkeypatch):
    monkeypatch.setattr(microsoft_client, "list_recent", lambda days: [
        {"message_id": "m1", "subject": "A", "sender": "a@b.com", "date": "d1", "snippet": "s1", "categories": ["Panga/Reviewed"]},
        {"message_id": "m2", "subject": "B", "sender": "b@b.com", "date": "d2", "snippet": "s2", "categories": []},
    ])
    results = inbox_accounts.MicrosoftAccount().list_recent_unreviewed()
    assert len(results) == 1
    assert results[0].ref == "m2"
    assert results[0].message_id == "m2"  # same value for both - Graph marking is per-message


def test_microsoft_mark_cta_registers_categories_once(monkeypatch):
    ensure_calls = []
    label_calls = []
    monkeypatch.setattr(microsoft_client, "ensure_label", lambda name: ensure_calls.append(name))
    monkeypatch.setattr(microsoft_client, "label_thread", lambda ref, names: label_calls.append((ref, names)))

    acct = inbox_accounts.MicrosoftAccount()
    acct.mark_cta("m1")
    acct.mark_reviewed("m2")
    assert ensure_calls == ["Panga/Reviewed", "Panga/Call-to-Action"]  # only once
    assert label_calls == [("m1", ["Panga/Reviewed", "Panga/Call-to-Action"]), ("m2", ["Panga/Reviewed"])]


def test_microsoft_web_link_and_draft_link_are_none(monkeypatch):
    monkeypatch.setattr(microsoft_client, "create_draft", lambda to, subject, body, reply_to_message_id=None: "draft1")
    acct = inbox_accounts.MicrosoftAccount()
    assert acct.web_link("m1") is None
    draft_id, draft_link = acct.create_reply_draft("to@x.com", "Subj", "Body", "m1")
    assert draft_id == "draft1"
    assert draft_link is None


# ---- IMAPAccount ----

def test_imap_list_recent_unreviewed_filters_by_keyword_flag(monkeypatch):
    reviewed = imap_client.MessageSummary(uid="1", subject="A", sender="a@b.com", date="d1", snippet="", flags=frozenset({"PangaReviewed"}))
    fresh = imap_client.MessageSummary(uid="2", subject="B", sender="b@b.com", date="d2", snippet="", flags=frozenset(), message_id_header="<x@y.com>")
    monkeypatch.setattr(imap_client, "search_recent", lambda email, since_days: [reviewed, fresh])

    results = inbox_accounts.IMAPAccount("me@yahoo.com").list_recent_unreviewed()
    assert len(results) == 1
    assert results[0].ref == "2"
    assert results[0].message_id == "<x@y.com>"
    assert results[0].account == "me@yahoo.com"


def test_imap_mark_cta_sets_both_keywords(monkeypatch):
    calls = []
    monkeypatch.setattr(imap_client, "add_keyword", lambda email, ref, kw: calls.append((email, ref, kw)))
    inbox_accounts.IMAPAccount("me@yahoo.com").mark_cta("10")
    assert calls == [("me@yahoo.com", "10", "PangaReviewed"), ("me@yahoo.com", "10", "PangaCTA")]


def test_imap_archive_moves_to_handled_folder(monkeypatch):
    calls = []
    monkeypatch.setattr(imap_client, "ensure_folder", lambda email, name: calls.append(("ensure", name)))
    monkeypatch.setattr(imap_client, "move_to_folder", lambda email, ref, dest: calls.append(("move", ref, dest)))
    inbox_accounts.IMAPAccount("me@yahoo.com").archive("10")
    assert calls == [("ensure", "Panga.Handled"), ("move", "10", "Panga.Handled")]


# ---- configured_accounts() ----

def test_configured_accounts_ordering_and_filtering(monkeypatch):
    monkeypatch.setattr(gmail_client, "is_configured", lambda: True)
    monkeypatch.setattr(microsoft_client, "is_configured", lambda: False)
    monkeypatch.setattr(imap_client, "list_configured_accounts", lambda: ["z@yahoo.com", "a@btinternet.com"])

    accounts = inbox_accounts.configured_accounts()
    assert [type(a).__name__ for a in accounts] == ["GmailAccount", "IMAPAccount", "IMAPAccount"]
    imap_accounts = [a for a in accounts if isinstance(a, inbox_accounts.IMAPAccount)]
    assert [a.account for a in imap_accounts] == ["a@btinternet.com", "z@yahoo.com"]  # alphabetical


def test_configured_accounts_empty_when_nothing_configured(monkeypatch):
    monkeypatch.setattr(gmail_client, "is_configured", lambda: False)
    monkeypatch.setattr(microsoft_client, "is_configured", lambda: False)
    monkeypatch.setattr(imap_client, "list_configured_accounts", lambda: [])
    assert inbox_accounts.configured_accounts() == []


# ---- Job-alert tracking (2026-08-07) - deliberately independent of the
# REVIEWED_LABEL/IMAP_REVIEWED_KEYWORD tracking above, see inbox_accounts.py's
# module comment on JOB_ALERT_LABEL for why sharing it would starve one scan.

def test_gmail_job_alert_candidates_builds_from_clause_and_excludes_own_label(monkeypatch):
    captured = {}

    def fake_search_threads(query, max_results=50):
        captured["query"] = query
        return [{"thread_id": "t1", "message_id": "m1", "subject": "5 jobs for you", "sender": "jobs@linkedin.com", "date": "d", "snippet": "s"}]
    monkeypatch.setattr(gmail_client, "search_threads", fake_search_threads)

    acct = inbox_accounts.GmailAccount()
    results = acct.list_job_alert_candidates(["jobalerts-noreply@linkedin.com", "lensa.com"])
    assert len(results) == 1
    assert "from:jobalerts-noreply@linkedin.com OR from:lensa.com" in captured["query"]
    assert "-label:Panga/JobAlertReviewed" in captured["query"]
    assert "-label:Panga/Reviewed" not in captured["query"]  # independent of the CTA scan's own marker


def test_gmail_job_alert_candidates_empty_senders_short_circuits(monkeypatch):
    called = []
    monkeypatch.setattr(gmail_client, "search_threads", lambda query, max_results=50: called.append(1))
    assert inbox_accounts.GmailAccount().list_job_alert_candidates([]) == []
    assert called == []  # never even queries Gmail


def test_gmail_mark_job_alert_reviewed_uses_its_own_label_not_reviewed(monkeypatch):
    ensure_calls = []
    label_calls = []
    monkeypatch.setattr(gmail_client, "ensure_label", lambda name: ensure_calls.append(name) or f"id-{name}")
    monkeypatch.setattr(gmail_client, "label_thread", lambda ref, ids: label_calls.append((ref, ids)))

    inbox_accounts.GmailAccount().mark_job_alert_reviewed("t1")
    assert ensure_calls == ["Panga/JobAlertReviewed"]
    assert label_calls == [("t1", ["id-Panga/JobAlertReviewed"])]


def test_microsoft_job_alert_candidates_filters_by_sender_and_category(monkeypatch):
    monkeypatch.setattr(microsoft_client, "list_recent", lambda days: [
        {"message_id": "m1", "subject": "Jobs", "sender": "Jobs <jobs@linkedin.com>", "date": "d1", "snippet": "s1", "categories": []},
        {"message_id": "m2", "subject": "Jobs", "sender": "Jobs <jobs@linkedin.com>", "date": "d2", "snippet": "s2", "categories": ["Panga/JobAlertReviewed"]},
        {"message_id": "m3", "subject": "Other", "sender": "friend@personal.com", "date": "d3", "snippet": "s3", "categories": []},
    ])
    results = inbox_accounts.MicrosoftAccount().list_job_alert_candidates(["linkedin.com"])
    assert [r.ref for r in results] == ["m1"]  # m2 already marked, m3 not a configured sender


def test_microsoft_job_alert_candidates_empty_senders_short_circuits(monkeypatch):
    called = []
    monkeypatch.setattr(microsoft_client, "list_recent", lambda days: called.append(1))
    assert inbox_accounts.MicrosoftAccount().list_job_alert_candidates([]) == []
    assert called == []


def test_microsoft_mark_job_alert_reviewed_registers_category_once(monkeypatch):
    ensure_calls = []
    label_calls = []
    monkeypatch.setattr(microsoft_client, "ensure_label", lambda name: ensure_calls.append(name))
    monkeypatch.setattr(microsoft_client, "label_thread", lambda ref, names: label_calls.append((ref, names)))

    acct = inbox_accounts.MicrosoftAccount()
    acct.mark_job_alert_reviewed("m1")
    acct.mark_job_alert_reviewed("m2")
    assert ensure_calls == ["Panga/JobAlertReviewed"]  # only once
    assert label_calls == [("m1", ["Panga/JobAlertReviewed"]), ("m2", ["Panga/JobAlertReviewed"])]


def test_imap_job_alert_candidates_filters_by_sender_and_keyword(monkeypatch):
    already_reviewed = imap_client.MessageSummary(uid="1", subject="Jobs", sender="jobs@linkedin.com", date="d1", snippet="", flags=frozenset({"PangaJobAlertReviewed"}))
    matching = imap_client.MessageSummary(uid="2", subject="Jobs", sender="jobs@linkedin.com", date="d2", snippet="", flags=frozenset())
    unrelated = imap_client.MessageSummary(uid="3", subject="Hi", sender="friend@personal.com", date="d3", snippet="", flags=frozenset())
    monkeypatch.setattr(imap_client, "search_recent", lambda email, since_days: [already_reviewed, matching, unrelated])

    results = inbox_accounts.IMAPAccount("me@yahoo.com").list_job_alert_candidates(["linkedin.com"])
    assert [r.ref for r in results] == ["2"]


def test_imap_job_alert_candidates_empty_senders_short_circuits(monkeypatch):
    called = []
    monkeypatch.setattr(imap_client, "search_recent", lambda email, since_days: called.append(1))
    assert inbox_accounts.IMAPAccount("me@yahoo.com").list_job_alert_candidates([]) == []
    assert called == []


def test_imap_mark_job_alert_reviewed_uses_its_own_keyword(monkeypatch):
    calls = []
    monkeypatch.setattr(imap_client, "add_keyword", lambda email, ref, kw: calls.append((email, ref, kw)))
    inbox_accounts.IMAPAccount("me@yahoo.com").mark_job_alert_reviewed("10")
    assert calls == [("me@yahoo.com", "10", "PangaJobAlertReviewed")]
