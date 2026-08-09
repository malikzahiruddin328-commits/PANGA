// Panga Job Capture - content script (runs on LinkedIn/Dice job pages only,
// see manifest.json's content_scripts.matches). Extracts title/company/
// description on request from the popup; never sends anything over the
// network itself (see background.js's comment on why).
//
// Extraction strategy, in order:
// 1. The page's own schema.org JobPosting JSON-LD (<script type=
//    "application/ld+json">) - confirmed present on Dice's job-detail pages
//    2026-08-08. LinkedIn does NOT ship this (confirmed live 2026-08-09 by
//    the hub session against a real logged-in job page - zero ld+json
//    scripts even after the description had fully loaded), so this only
//    ever actually fires for Dice - kept first/shared rather than made
//    Dice-only in case LinkedIn adds it later.
// 2. A short list of known DOM selectors per site.
// 3. LinkedIn only: document.body.innerText sliced between heading/footer
//    boundary phrases. LinkedIn's real class names are build-hashed (the
//    hub session confirmed 2026-08-09 that even "stable-looking" classes
//    like .jobs-description__content come back empty), so a fixed selector
//    list can't be trusted there long-term - innerText with boundary
//    phrases is less brittle than chasing hashed classes, at the cost of
//    being more heuristic. Both DOM_FALLBACKS.linkedin selectors AND this
//    boundary list are UNVERIFIED against the exact classes/wording LinkedIn
//    ships today - see extension/README.md's maintainer note.
//
// LinkedIn also lazy-loads the description itself - confirmed live
// 2026-08-09 (hub session): a fresh page load has ~1,490 chars of body text
// (just header/nav/footer) and ZERO ld+json scripts; after scrolling and
// waiting ~2s, body text grew to 12,181 chars with the real JD present.
// ensureLinkedInDescriptionLoaded() below reproduces that scroll-and-wait
// before any extraction attempt runs on LinkedIn.
//
// If nothing finds a real description, extraction fails LOUDLY (the popup
// shows "couldn't find it") rather than sending a truncated/wrong guess
// into Panga silently - same "never silently overwrite" principle as the
// paste-JD screen on the Panga side. A result under MIN_DESCRIPTION_LENGTH
// is treated as a failed extraction, not a short-but-real JD, since a
// boundary-marker miss on a still-loading page is far more likely than a
// real posting that short.

const MIN_DESCRIPTION_LENGTH = 200;

function stripHtml(html) {
  const container = document.createElement("div");
  container.innerHTML = html
    .replace(/<\/(p|li|div|h[1-6])>/gi, "\n")
    .replace(/<br\s*\/?>/gi, "\n");
  const text = container.textContent || "";
  return text.replace(/\r/g, "").replace(/\n{3,}/g, "\n\n").trim();
}

function extractFromJsonLd() {
  const scripts = document.querySelectorAll('script[type="application/ld+json"]');
  for (const script of scripts) {
    let data;
    try {
      data = JSON.parse(script.textContent);
    } catch (e) {
      continue; // not parseable - try the next script tag
    }
    const candidates = Array.isArray(data) ? data : [data];
    for (const item of candidates) {
      if (item && item["@type"] === "JobPosting" && item.description) {
        const description = stripHtml(item.description);
        if (description.length >= MIN_DESCRIPTION_LENGTH) {
          return {
            title: item.title || "",
            company: (item.hiringOrganization && item.hiringOrganization.name) || "",
            description,
          };
        }
      }
    }
  }
  return null;
}

const DOM_FALLBACKS = {
  "dice.com": {
    title: ["h1"],
    company: ['[data-testid="job-detail-header-card"] a[href*="company-profile"]'],
    description: ['[data-testid="jobDescriptionHtml"]', "#jobDescription"],
  },
  "linkedin.com": {
    title: [
      ".job-details-jobs-unified-top-card__job-title",
      "h1.t-24",
      ".jobs-unified-top-card__job-title",
    ],
    company: [
      ".job-details-jobs-unified-top-card__company-name",
      ".jobs-unified-top-card__company-name",
    ],
    description: [
      "#job-details",
      ".jobs-description__content",
      ".jobs-box__html-content",
      ".jobs-description-content__text",
    ],
  },
};

function firstMatchText(selectors) {
  for (const selector of selectors) {
    const el = document.querySelector(selector);
    if (el && el.textContent.trim()) return el.textContent.trim();
  }
  return "";
}

function hostKeyFor(hostname) {
  return Object.keys(DOM_FALLBACKS).find((h) => hostname.includes(h)) || null;
}

function extractFromDom() {
  const host = hostKeyFor(location.hostname);
  if (!host) return null;
  const selectors = DOM_FALLBACKS[host];
  const description = firstMatchText(selectors.description);
  if (!description || description.length < MIN_DESCRIPTION_LENGTH) return null;
  return {
    title: firstMatchText(selectors.title),
    company: firstMatchText(selectors.company),
    description,
  };
}

// LinkedIn-only innerText fallback (see module docstring). Markers are
// lowercase; matched case-insensitively against the page's own text so
// capitalization differences don't matter.
const LINKEDIN_START_MARKERS = [
  "about the job",
  "position summary",
  "job description",
  "about this role",
  "role overview",
  "the opportunity",
];
const LINKEDIN_END_MARKERS = [
  "how you match",
  "people also viewed",
  "about the company",
  "referrals increase your chances",
  "get notified about new",
  "report this job",
  "this job alert is on",
  "set alert",
  "similar jobs",
  "explore collaborative articles",
];

// document.title on a LinkedIn job page is normally "<Company> hiring
// <Title> in <Location> | LinkedIn" - a fallback for title/company when the
// (already-unverified) DOM_FALLBACKS selectors above come back empty too.
function parseTitleFromDocumentTitle() {
  const raw = document.title.replace(/\s*\|\s*LinkedIn\s*$/i, "").trim();
  const match = raw.match(/^(.+?)\s+hiring\s+(.+?)(?:\s+in\s+.+)?$/i);
  if (!match) return null;
  return { company: match[1].trim(), title: match[2].trim() };
}

function extractFromInnerTextFallback() {
  const text = document.body.innerText || "";
  if (text.length < MIN_DESCRIPTION_LENGTH) return null; // hasn't loaded yet
  const lower = text.toLowerCase();

  let startIdx = -1;
  for (const marker of LINKEDIN_START_MARKERS) {
    const idx = lower.indexOf(marker);
    if (idx !== -1 && (startIdx === -1 || idx < startIdx)) startIdx = idx;
  }
  if (startIdx === -1) return null; // no recognizable JD heading found at all

  let endIdx = text.length;
  for (const marker of LINKEDIN_END_MARKERS) {
    const idx = lower.indexOf(marker, startIdx + 50);
    if (idx !== -1 && idx < endIdx) endIdx = idx;
  }

  const description = text.slice(startIdx, endIdx).trim();
  if (description.length < MIN_DESCRIPTION_LENGTH) return null;

  const domTitleCompany = {
    title: firstMatchText(DOM_FALLBACKS["linkedin.com"].title),
    company: firstMatchText(DOM_FALLBACKS["linkedin.com"].company),
  };
  if (!domTitleCompany.title && !domTitleCompany.company) {
    const parsed = parseTitleFromDocumentTitle();
    if (parsed) Object.assign(domTitleCompany, parsed);
  }

  return { ...domTitleCompany, description };
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// CONFIRMED LIVE 2026-08-09 (hub session, genuinely fresh page load, no
// manual scroll): scrollBy(), a synthetic WheelEvent, and a synthetic
// scroll Event each produced EXACTLY ZERO change in loaded content
// ({"scrollBy_alone":0,"plus_synthetic_wheel":0,"plus_synthetic_scroll":0}).
// LinkedIn's lazy-load is gated on a trusted (isTrusted: true) user
// gesture, not on scroll position or any event a content script can
// dispatch - this is presumably deliberate anti-automation on LinkedIn's
// side, not a bug in how these events were fired. There is no legitimate
// way for an extension to fake a trusted gesture (that would be exactly
// the kind of detection-evasion technique this project won't build), so
// this is a PERMANENT limitation, not a bug to keep chasing: if the user
// hasn't scrolled the page themselves before clicking "Send to Panga",
// extraction fails with a clear, actionable message telling them to
// scroll and retry - never a silent wrong/truncated guess.
//
// Still worth a best-effort scrollBy() first (free, harmless, and covers
// the case some other extension/page behavior treats it as sufficient in
// a real browser even though it didn't here) plus a short poll for the
// case the user is mid-scroll right as they click, but NOT the longer
// multi-trigger loop this used to be - that just burned 4 wasted seconds
// on every cold click once the triggers were confirmed inert.
async function ensureLinkedInDescriptionLoaded(maxWaitMs = 1200, pollIntervalMs = 200) {
  const startY = window.scrollY;
  const deadline = Date.now() + maxWaitMs;

  window.scrollBy(0, 400);
  while (Date.now() < deadline) {
    const text = document.body.innerText || "";
    const lower = text.toLowerCase();
    const hasMarker = LINKEDIN_START_MARKERS.some((marker) => lower.includes(marker));
    if (hasMarker && text.length >= MIN_DESCRIPTION_LENGTH) break;
    await sleep(pollIntervalMs);
  }

  window.scrollTo(0, startY);
  await sleep(150);
}

async function extractJobData() {
  const jsonLd = extractFromJsonLd();
  if (jsonLd) return jsonLd;

  const host = hostKeyFor(location.hostname);
  if (host === "linkedin.com") {
    await ensureLinkedInDescriptionLoaded();
  }

  const dom = extractFromDom();
  if (dom) return dom;

  if (host === "linkedin.com") {
    return extractFromInnerTextFallback();
  }
  return null;
}

function extractionFailedMessage() {
  const host = hostKeyFor(location.hostname);
  if (host === "linkedin.com") {
    // LinkedIn only loads the description after a real (trusted) scroll
    // gesture - confirmed live 2026-08-09, no script-triggered event can
    // substitute for one (see ensureLinkedInDescriptionLoaded's comment).
    // Actionable, not generic - this IS the fix most of the time.
    return "Scroll down this page so the job description loads, then try Send to Panga again.";
  }
  return "Couldn't find the job description on this page - try reloading it, or paste it into Panga manually.";
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message && message.type === "extractJob") {
    extractJobData().then((data) => {
      if (data) {
        sendResponse({ ok: true, data });
      } else {
        sendResponse({ ok: false, error: extractionFailedMessage() });
      }
    });
    return true; // keep the message channel open for the async response above
  }
});

// Toolbar icon badge state (2026-08-09, Zahir-approved polish pass) - lets
// the user tell at a glance whether a page is ready to send without
// opening the popup. Deliberately does NOT call ensureLinkedInDescriptionLoaded()
// - this only reports the page's CURRENT, organic state (has the user
// actually scrolled yet), never forces a scroll itself. A background
// readiness poller nudging the page on every mutation would be the same
// automation LinkedIn's trusted-gesture gating already proved doesn't work
// (see ensureLinkedInDescriptionLoaded's comment) - and would be actively
// misleading here besides, since a badge that turns green on its own
// wouldn't reflect anything the user did.
function cheapReadinessCheck() {
  if (extractFromJsonLd() || extractFromDom()) return "ready";

  const host = hostKeyFor(location.hostname);
  if (host === "linkedin.com") {
    const text = document.body.innerText || "";
    const lower = text.toLowerCase();
    const hasMarker = LINKEDIN_START_MARKERS.some((marker) => lower.includes(marker));
    if (hasMarker && text.length >= MIN_DESCRIPTION_LENGTH) return "ready";
  }
  // Matched a job-page URL pattern (content_scripts.matches) but nothing
  // extractable yet - Dice this early is rare (server-rendered) but not
  // impossible on a slow load; LinkedIn before scrolling is the common case.
  return "loading";
}

function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

let lastReportedReadiness = null;
function reportReadiness() {
  const state = cheapReadinessCheck();
  if (state === lastReportedReadiness) return; // don't spam identical updates
  lastReportedReadiness = state;
  chrome.runtime.sendMessage({ type: "readinessUpdate", state });
}

reportReadiness();
// LinkedIn's real content arrives via a DOM mutation once the user scrolls
// (there's no load/network event to hook - see the lazy-load comment
// above), so a MutationObserver is the only way to notice it happened.
// Debounced since a real content load can fire dozens of mutations in a
// burst.
new MutationObserver(debounce(reportReadiness, 500)).observe(document.body, {
  childList: true,
  subtree: true,
});
