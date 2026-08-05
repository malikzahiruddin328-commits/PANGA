"""Tests for tailoring/cta_emails.py's multi-provider fields (provider/
account/web_link) and mark_draft_created's provider-aware draft_link
default - the pieces that changed to support Outlook/IMAP alongside
Gmail. Uses the shared isolated_data fixture (conftest.py) so nothing
touches the real data/ store."""

import tailoring.cta_emails as cta_emails


def test_add_cta_email_defaults_to_gmail_for_backward_compatibility(isolated_data):
    cta_emails.add_cta_email("t1", "Subj", "them@example.com", "snippet", "2026-08-04", "interview_request")
    record = cta_emails.load_cta_emails()[0]
    assert record["provider"] == "gmail"
    assert record["account"] == "gmail"
    assert record["web_link"] == "https://mail.google.com/mail/u/0/#all/t1"


def test_add_cta_email_non_gmail_provider_no_auto_link(isolated_data):
    cta_emails.add_cta_email(
        "c1", "Subj", "them@example.com", "snippet", "2026-08-04", "offer",
        provider="microsoft", account="Outlook",
    )
    record = cta_emails.load_cta_emails()[0]
    assert record["provider"] == "microsoft"
    assert record["account"] == "Outlook"
    assert record["web_link"] is None  # no reliable Outlook deep link - see module docstring


def test_add_cta_email_imap_account_label_is_the_email_address(isolated_data):
    cta_emails.add_cta_email(
        "10", "Subj", "them@example.com", "snippet", "2026-08-04", "rejection",
        provider="imap", account="me@yahoo.com",
    )
    record = cta_emails.load_cta_emails()[0]
    assert record["account"] == "me@yahoo.com"


def test_add_cta_email_upsert_preserves_provider_on_update(isolated_data):
    cta_emails.add_cta_email("c1", "Subj", "them@example.com", "snippet1", "2026-08-04", "offer", provider="microsoft", account="Outlook")
    cta_emails.add_cta_email("c1", "Subj (updated)", "them@example.com", "snippet2", "2026-08-05", "offer", provider="microsoft", account="Outlook")
    emails = cta_emails.load_cta_emails()
    assert len(emails) == 1  # upsert, not a duplicate
    assert emails[0]["snippet"] == "snippet2"
    assert emails[0]["provider"] == "microsoft"


def test_mark_draft_created_gmail_gets_legacy_link(isolated_data):
    cta_emails.add_cta_email("t1", "Subj", "them@example.com", "snippet", "2026-08-04", "offer")
    cta_emails.mark_draft_created("t1", "draft123")
    record = cta_emails.load_cta_emails()[0]
    assert record["draft_link"] == "https://mail.google.com/mail/u/0/#drafts?compose=draft123"


def test_mark_draft_created_non_gmail_no_link_unless_given(isolated_data):
    cta_emails.add_cta_email("c1", "Subj", "them@example.com", "snippet", "2026-08-04", "offer", provider="imap", account="me@yahoo.com")
    cta_emails.mark_draft_created("c1", "42")
    record = cta_emails.load_cta_emails()[0]
    assert record["draft_link"] is None


def test_mark_draft_created_uses_explicit_link_when_given(isolated_data):
    cta_emails.add_cta_email("c1", "Subj", "them@example.com", "snippet", "2026-08-04", "offer", provider="microsoft", account="Outlook")
    cta_emails.mark_draft_created("c1", "d1", draft_link="https://outlook.live.com/mail/0/drafts")
    record = cta_emails.load_cta_emails()[0]
    assert record["draft_link"] == "https://outlook.live.com/mail/0/drafts"
