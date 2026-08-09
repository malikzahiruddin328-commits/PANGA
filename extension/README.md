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
5. If it found the job description, you'll see the title/company and a
   "Send to Panga" button - click it.
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
