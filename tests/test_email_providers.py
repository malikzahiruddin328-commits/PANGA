"""Pure-logic tests for email_providers.py's IMAP auto-detect path - no
network calls (ISPDB's real-response shape is tested against a fixed
sample via parse_ispdb_imap_response, not a live request)."""

from email_providers import detect_imap_settings, parse_ispdb_imap_response

SAMPLE_ISPDB_XML = """<?xml version="1.0"?>
<clientConfig version="1.1">
  <emailProvider id="example.com">
    <incomingServer type="imap">
      <hostname>imap.example.com</hostname>
      <port>993</port>
      <socketType>SSL</socketType>
    </incomingServer>
    <outgoingServer type="smtp">
      <hostname>smtp.example.com</hostname>
      <port>465</port>
    </outgoingServer>
  </emailProvider>
</clientConfig>
"""


def test_known_preset_yahoo():
    result = detect_imap_settings("someone@yahoo.com")
    assert result.host == "imap.mail.yahoo.com"
    assert result.port == 993
    assert result.source == "preset"


def test_known_preset_case_insensitive_domain():
    result = detect_imap_settings("someone@BTInternet.com")
    assert result.host == "mail.btinternet.com"
    assert result.source == "preset"


def test_malformed_email_returns_none():
    assert detect_imap_settings("not-an-email") is None
    assert detect_imap_settings("") is None


def test_ispdb_xml_parsing():
    result = parse_ispdb_imap_response(SAMPLE_ISPDB_XML, "example.com")
    assert result.host == "imap.example.com"
    assert result.port == 993
    assert result.source == "ispdb"


def test_ispdb_xml_parsing_missing_imap_block():
    xml_no_imap = "<clientConfig><emailProvider></emailProvider></clientConfig>"
    assert parse_ispdb_imap_response(xml_no_imap, "example.com") is None


def test_guess_fallback_for_unknown_domain(monkeypatch):
    import email_providers

    monkeypatch.setattr(email_providers, "_query_ispdb", lambda domain: None)
    result = detect_imap_settings("someone@some-unheard-of-isp.example")
    assert result.host == "imap.some-unheard-of-isp.example"
    assert result.port == 993
    assert result.source == "guess"
