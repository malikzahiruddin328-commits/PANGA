# Panga Job Capture (Chrome extension)

Captures the job description from an already-open LinkedIn or Dice job
posting page you're logged into, and sends it into Panga - so you don't
have to copy-paste it into the "Paste the job description" box yourself.

## What it does

- Shows a green dot in Panga next to "Browser extension connected" whenever
  this extension is installed and running - so you always know whether it's
  actually working, not just whether you think it should be.
- On a LinkedIn job page (`linkedin.com/jobs/view/...`) or a Dice job page
  (`dice.com/job-detail/...`), click the extension's toolbar icon and hit
  "Send to Panga." The job's title, company, and full description get sent
  straight to Panga.
- Next time you open that same job in Panga's paste-JD box, it's already
  filled in with a badge saying "Auto-filled from browser extension" - you
  can still edit it before saving, nothing is ever saved automatically.
- After a successful send, a real Chrome notification confirms it and its
  "Open in Panga" button jumps straight to that job (**pending** - see
  maintainer notes below, needs a small addition on Panga's own side
  before the deep link actually works end-to-end).
- The toolbar icon itself shows a colored dot so you don't have to open the
  popup just to check: no dot on a page that isn't a LinkedIn/Dice job
  posting, an amber dot on a job page whose description hasn't loaded yet
  (LinkedIn before you've scrolled - see "A LinkedIn quirk" below), a green
  dot once it's actually ready to send.

## Installing it (one-time setup)

Chrome extensions you build yourself (not from the Chrome Web Store) are
installed as an "unpacked extension." A few clicks:

1. Open a new Chrome tab and go to: `chrome://extensions`
2. Turn on **Developer mode** - it's a toggle switch, usually in the
   top-right corner of that page.
3. Click the **Load unpacked** button that appears once Developer mode is
   on.
4. In the folder picker, select this folder:
   `C:\Users\User\Desktop\Myra\Panga\extension`
5. "Panga Job Capture" should now show up in your list of extensions. Click
   the puzzle-piece icon in Chrome's toolbar and pin it, so its icon is
   always visible.

That's it - no further setup. It'll keep working every time you open
Chrome, on any LinkedIn or Dice job page, as long as Panga itself is
running (production, at `run_app.bat` / port 8510).

## Using it

1. Make sure Panga is open (check the green/red dot near the top of the
   app to confirm the extension is actually reachable).
2. Browse to a LinkedIn or Dice job posting you're logged into.
3. **On LinkedIn specifically, scroll down the page a bit first** so the
   description actually loads (LinkedIn only loads it once you've
   genuinely scrolled - the extension can't trigger that itself, see "A
   LinkedIn quirk" below). Dice doesn't need this.
4. Click the extension's icon in Chrome's toolbar.
5. If it found the job description, you'll see the title/company, a
   preview (character count + the first ~150 characters, in quotes) so you
   can confirm it grabbed the right thing before sending, and a "Send to
   Panga" button - click it.
6. Switch to Panga, find that job (or add it manually first if it's not in
   your list yet - LinkedIn jobs need to be added once via "Add a job
   manually" with the posting URL, same as before), and its paste-JD box
   will already be filled in.

## A LinkedIn quirk (read this once)

LinkedIn only loads a job's description after you've actually scrolled the
page yourself - not on page load. This isn't a bug in the extension; it's
confirmed (2026-08-09) that LinkedIn's page only responds to a real scroll,
not anything a Chrome extension is allowed to simulate on your behalf. If
you click "Send to Panga" the instant a LinkedIn job page opens, before
scrolling at all, you'll see: **"Scroll down this page so the job
description loads, then try Send to Panga again."** - just scroll down a
little and click the button again. Dice doesn't have this issue at all.

## If something's not working

- **Red dot in Panga, extension not detected**: make sure the extension is
  enabled in `chrome://extensions`, and that Panga (the real app, not a
  dev/test copy) is actually running.
- **"Scroll down this page so the job description loads..."** (LinkedIn
  only): see "A LinkedIn quirk" above - scroll down and try again.
- **"Couldn't find the job description on this page"** (Dice, or LinkedIn
  after scrolling): reload the job page and try again - if it keeps
  happening, the site may have changed its page layout since this was
  built; flag it so it can be fixed rather than working around it by
  pasting manually every time.
- **Nothing happens after "Send to Panga"**: check that Panga is open in a
  browser tab somewhere (it needs to be running, not just installed).
- **"Couldn't read this page - try reloading it."**: fixed automatically as
  of 2026-08-09 - the extension now retries this itself, so you shouldn't
  see it anymore. If it still shows up, reload the LinkedIn/Dice tab once
  and try again; if it keeps happening on the same posting, flag it.

## For whoever's maintaining this (not Zahir)

- `manifest.json` - extension setup (MV3), which pages it runs on.
- `content.js` - runs on the job page itself, extracts title/company/
  description (tries the page's own schema.org JSON-LD first, falls back
  to a short list of known CSS selectors per site).
- `background.js` - service worker; owns all network calls to Panga's local
  listener (`src/extension_bridge.py`, fixed port 8765) since a content
  script's fetch to a plain-http endpoint from an https page hits
  mixed-content blocking. Heartbeats via `chrome.alarms` (survives MV3
  service-worker suspension, unlike `setInterval`).
- `popup.html`/`popup.js` - the toolbar popup UI.

**Toolbar badge (2026-08-09):** `content.js`'s `cheapReadinessCheck()`
reuses the same extraction functions used for the real send (JSON-LD, then
DOM selectors, then - LinkedIn only - the innerText/marker fallback) to
report "ready"/"loading" to the background worker via a `readinessUpdate`
message; `background.js` turns that into a green/amber `chrome.action`
badge for that tab. Deliberately does NOT call
`ensureLinkedInDescriptionLoaded()` - it only reports the page's current,
organic state, never nudges it. **Verified live 2026-08-09:** the
readiness-detection logic itself (`extractFromJsonLd()` returning "ready"
on a real loaded Dice page, "loading" when its JSON-LD is empty) was run
against a real page and both states are correct. **Not yet verified: the
actual toolbar badge color** - that needs a real loaded extension in real
Chrome (`chrome.action.setBadgeText`/`setBadgeBackgroundColor` can't be
exercised from a plain browser tab, only from inside an installed
extension's own context), which this build session has no way to do. Load
the unpacked extension and check the icon turns amber-then-green on a real
job page before fully trusting this piece.

**Popup preview (2026-08-09):** `popup.js`'s `renderPreview()` shows a
character count + the first ~150 chars (quoted, truncated with "…") before
the Send button - builds confidence about what's about to be sent instead
of a black box. Uses `textContent` throughout, never `innerHTML` with the
extracted text interpolated in, so nothing in a JD (arbitrary text from a
third-party page) can be interpreted as markup. Verified live 2026-08-09:
loaded `popup.html` directly and ran the render function against both a
long sample (truncates correctly with "…") and a short one (no ellipsis,
full text shown) - screenshot confirmed the layout renders cleanly at the
popup's actual 300px width. The chrome.tabs/chrome.runtime-dependent parts
of popup.js (the real extraction call, the real send) still need the same
real-extension check as the badge above - this only verifies the preview
rendering itself.

**Post-send notification + deep link (2026-08-09, DONE - both halves
landed):** `background.js`'s `showSentNotification()` fires a
`chrome.notifications` popup after a successful `/capture`, with an "Open
in Panga" button (`onButtonClicked`/`onClicked` both open the same URL,
then clear the notification). The deep-link URL is
`http://localhost:8510/?job_url=<url-encoded posting URL>` - matching by
the job's own `posting_url` (normalized the same way
`extension_bridge.py`'s `_normalize_url()` already does) rather than by
`?job=<source>_<job_id>`, specifically to avoid duplicating Panga's
per-source job_id derivation logic (LinkedIn: URL regex; Dice: content
hash) inside the extension. Panga's side (`src/ui/app.py` reading
`job_url` from `st.query_params`, matching, landing on Results with the
job selected/scrolled-to) was built separately by the session that owns
`app.py` generally and merged as `feature/results-deep-link` - live-
verified by them against a real job's real posting_url. Manifest updated
with the `notifications` permission and real icon files (`extension/icons/`,
generated 2026-08-09 - previously the extension had no icons at all, which
is fine for `chrome.action` but `chrome.notifications` requires a real
`iconUrl`). Not yet verified: the actual `chrome.notifications` popup
rendering itself (real-extension-only API, same limitation as the badge
above) - load the unpacked extension and confirm the notification appears
and its button navigates correctly before fully trusting this piece.

**Dice: confirmed live and working end-to-end**, including a real user
click (2026-08-08 for the extraction logic; 2026-08-09 Zahir sent a real
Tarkett/CIO posting through the whole pipeline and it auto-filled
correctly in Panga). Its JSON-LD extraction path returns clean
title/company/full description directly.

**LinkedIn: extraction logic confirmed correct; the lazy-load itself has a
permanent, un-fixable-by-the-extension limitation.** Timeline, all
confirmed live by the hub session (which has the only working logged-in
LinkedIn session available):
- 2026-08-09: real failure reproduced (Zahir hit "Couldn't find the job
  description on this page"). Root cause: LinkedIn ships **no** JSON-LD
  JobPosting markup at all (the Dice-style SEO-markup assumption was
  wrong), its description is lazy-loaded (a fresh page load has ~1,490
  chars of body text; after a real scroll it's 12,181+), and its CSS
  classes are build-hashed (even "stable-looking" ones like
  `.jobs-description__content` come back empty).
- Marker-based fallback (`LINKEDIN_START_MARKERS`/`LINKEDIN_END_MARKERS`
  slicing `document.body.innerText`) tested against a real loaded page:
  correctly bounded, clean 6,923-char description, no nav/footer junk.
  This part works.
- The lazy-load trigger itself does NOT: tested `scrollBy()`, a synthetic
  `WheelEvent`, and a synthetic `scroll` Event on a genuinely fresh page
  load - **all three produced exactly zero change**
  (`{"scrollBy_alone":0,"plus_synthetic_wheel":0,"plus_synthetic_scroll":0}`).
  LinkedIn gates this on a trusted (`isTrusted: true`) user gesture, which
  no script can fake - and faking one would cross into detection-evasion
  territory this project won't build. **This is permanent, not a bug to
  keep chasing.**

Current behavior (final, 2026-08-09): if the user has already scrolled the
LinkedIn page themselves before clicking "Send to Panga" (checked
instantly, zero added delay), extraction just works. If not, it fails
within ~1.2s with an actionable message telling them to scroll and retry -
see "A LinkedIn quirk" above. This is the correct, honest end state, not
an interim fix waiting on more engineering.

**"Couldn't read this page" - content script never injected (2026-08-09,
FIXED, real root cause confirmed against real data):** real bug from
Zahir on a real listing (MBX Biosciences VP IT). The error message is
popup.js's OWN fallback (`chrome.runtime.lastError` or no response at all
from `chrome.tabs.sendMessage`) - a different failure class from
content.js's two known "couldn't find a description" messages above,
because it means content.js never got a chance to run its extraction
logic at all.

First hypothesis (LinkedIn SPA client-side routing not triggering
injection) turned out to be the wrong mechanism, caught by checking the
job's REAL stored `posting_url` in `data/jobs/jobs.json` instead of the
clean URL initially assumed: it was actually
`https://www.linkedin.com/comm/jobs/view/4445190785/?trackingId=...` -
LinkedIn's `/comm/jobs/view/...` path is an email-tracking redirect
wrapper (this posting was originally captured via the job-alert-email
scan, which stores whatever URL the email link actually contained,
tracking params and all - see CLAUDE.md's "Processing job-alert emails"
section). `content_scripts.matches` only listed
`https://www.linkedin.com/jobs/view/*` - genuinely did NOT match this URL
shape, so Chrome never injected content.js on it at all. Checking
`load_jobs()` against all stored LinkedIn postings found this isn't a
one-off: **41 of 74 (55%) already have this `/comm/jobs/view/` shape** -
this was silently broken for the majority of LinkedIn jobs already in
Panga, not just this one listing.

Fix: added `https://www.linkedin.com/comm/jobs/view/*` to
`content_scripts.matches` and to `popup.js`'s `isSupportedUrl()` regex.
Verified the regex against both URL shapes plus edge cases (a `/comm/`
path on a different domain correctly still rejected). Kept the
on-demand-injection fallback below too, as defense in depth for whatever
NEXT URL-shape variant LinkedIn or an email-sourced link introduces -
`popup.js`'s `ensureContentScriptAndExtract()` falls back to
`chrome.scripting.executeScript()` (new `scripting` permission) to inject
`content.js` on demand, right when the user clicks the extension icon, if
the first message attempt still gets no response - fits this extension's
existing "act on an explicit click, no background surveillance" design
better than a persistent `chrome.webNavigation` listener watching every
tab for SPA route changes. Since a tab can now legitimately receive
`content.js` twice, `content.js` itself gained an idempotency guard (an
IIFE gated on a `window.__pangaContentScriptActive` flag) - without it, a
second real injection would throw a `SyntaxError` re-declaring top-level
`const`s (re-injection runs in the SAME page/window, not a fresh module
scope) and register a duplicate message listener/MutationObserver.
**Verified live 2026-08-09:** ran the actual `content.js` source through
three simulated injections in a Node `vm` sandbox (stubbed
`chrome`/`document`/`MutationObserver`) - only one listener registered,
one observer created, one message sent, no crash. **Not verified:** the
`/comm/jobs/view/` page's actual DOM/extraction behavior once content.js
runs on it (needs a live logged-in LinkedIn session this build couldn't
access) - the URL-matching root cause is confirmed against real stored
data, but whether the page LinkedIn serves at that path has the exact
same shape as the plain `/jobs/view/` path (same lazy-load-on-scroll
behavior, same lack of JSON-LD, etc.) is still an assumption. If Zahir
hits a NEW failure on a `/comm/` URL specifically (not the generic
"couldn't read this page" message, which this fix directly addresses),
that's the signal this assumption needs checking.
