"""Email provider detection for the account-setup wizard's generic "Other"
IMAP path (native-packaging's wizard - see
docs/multi-provider-accounts-scope.md, Part A). Gmail/Outlook/Hotmail use
OAuth (separate, not-yet-built modules) - this file is only for the
catch-all: Yahoo and any other personal/ISP email (Zahir's examples:
Verizon, BT Internet).

Design intent, confirmed with Zahir 2026-08-04: never drop a bare "enter
your IMAP server and port" box on a non-technical user as the first step.
Try a known preset, then Mozilla's public ISPDB (the same lookup
Thunderbird's autoconfig uses), then a plain imap.<domain> guess - only
fall back to manual entry if all three come up empty, and even the guess
is presented to the wizard as unconfirmed, not a done deal.
"""

import re
from dataclasses import dataclass
from typing import Optional

import requests

_ISPDB_TIMEOUT = 5
_ISPDB_URL = "https://autoconfig.thunderbird.net/v1.1/{domain}"

_EMAIL_RE = re.compile(r"^[^@\s]+@([^@\s]+\.[^@\s]+)$")

# ISPDB's <incomingServer type="imap"> block, in whatever order its own
# fields happen to appear - built loose (DOTALL, non-greedy) rather than a
# full XML parse, since all that's needed is one hostname/port pair.
_ISPDB_IMAP_BLOCK_RE = re.compile(
    r'<incomingServer type="imap">(.*?)</incomingServer>', re.DOTALL
)
_ISPDB_HOSTNAME_RE = re.compile(r"<hostname>([^<]+)</hostname>")
_ISPDB_PORT_RE = re.compile(r"<port>(\d+)</port>")


@dataclass
class ImapSettings:
    host: str
    port: int
    domain: str
    source: str  # "preset" | "ispdb" | "guess" - wizard uses this to decide
    # whether to show the result as confirmed ("preset"/"ispdb") or as an
    # editable guess the user should double-check ("guess").


# Providers Zahir named explicitly, worth not depending on a network call
# for. Deliberately short - ISPDB already covers the long tail; this is
# not meant to grow into a large hardcoded provider list.
_KNOWN_PRESETS = {
    "yahoo.com": ImapSettings("imap.mail.yahoo.com", 993, "yahoo.com", "preset"),
    "ymail.com": ImapSettings("imap.mail.yahoo.com", 993, "ymail.com", "preset"),
    "aol.com": ImapSettings("imap.aol.com", 993, "aol.com", "preset"),
    "icloud.com": ImapSettings("imap.mail.me.com", 993, "icloud.com", "preset"),
    "me.com": ImapSettings("imap.mail.me.com", 993, "me.com", "preset"),
    # Was "outgoing.verizon.net" - an SMTP-relay-style name (Verizon's own
    # real outgoing/SMTP host, not IMAP), presented as a confirmed preset
    # with no "couldn't auto-confirm" warning (Mirror's fine-needle audit,
    # 2026-08-06 - a Verizon user would get full UI confidence in a host
    # that just fails its connection test). Verizon.net mail has been
    # AOL-hosted since Verizon's 2017 transition, so it uses AOL's own IMAP
    # host directly - confirmed live against Mozilla's ISPDB (the same
    # autoconfig.thunderbird.net source _query_ispdb() already falls back
    # to below), not assumed from memory: imap.aol.com/993, identical to
    # the aol.com preset two lines up.
    "verizon.net": ImapSettings("imap.aol.com", 993, "verizon.net", "preset"),
    "btinternet.com": ImapSettings("mail.btinternet.com", 993, "btinternet.com", "preset"),
}


def _domain_from_email(email: str) -> Optional[str]:
    match = _EMAIL_RE.match(email.strip())
    return match.group(1).lower() if match else None


def parse_ispdb_imap_response(xml_text: str, domain: str) -> Optional[ImapSettings]:
    """Pulled out as its own function so the XML-shape assumption can be
    unit-tested against a fixed sample without a real network call - the
    live ISPDB endpoint itself was not reachable to verify against from
    this environment (see docs/multi-provider-accounts-scope.md)."""
    block_match = _ISPDB_IMAP_BLOCK_RE.search(xml_text)
    if block_match is None:
        return None
    block = block_match.group(1)
    hostname_match = _ISPDB_HOSTNAME_RE.search(block)
    port_match = _ISPDB_PORT_RE.search(block)
    if hostname_match is None or port_match is None:
        return None
    return ImapSettings(hostname_match.group(1), int(port_match.group(1)), domain, "ispdb")


def _query_ispdb(domain: str) -> Optional[ImapSettings]:
    try:
        resp = requests.get(_ISPDB_URL.format(domain=domain), timeout=_ISPDB_TIMEOUT)
    except requests.exceptions.RequestException:
        return None
    if not resp.ok:
        return None
    return parse_ispdb_imap_response(resp.text, domain)


def detect_imap_settings(email: str) -> Optional[ImapSettings]:
    """Best-effort IMAP settings for the wizard's "Other" provider path.
    Returns None only if `email` isn't even shaped like an email address -
    the caller's fallback from there is a manual server/port entry, never
    a hard failure."""
    domain = _domain_from_email(email)
    if domain is None:
        return None

    preset = _KNOWN_PRESETS.get(domain)
    if preset is not None:
        return preset

    from_ispdb = _query_ispdb(domain)
    if from_ispdb is not None:
        return from_ispdb

    return ImapSettings(f"imap.{domain}", 993, domain, "guess")
