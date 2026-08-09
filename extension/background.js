// Panga Job Capture - background service worker.
//
// All network traffic to Panga's local listener (src/extension_bridge.py)
// goes through here, not the content script - content scripts fetching an
// http:// endpoint from an https:// page are subject to mixed-content
// blocking, while this service worker is its own extension context with
// its own host_permissions (see manifest.json) and isn't. Content scripts
// only ever extract DOM data and message it here.
//
// Heartbeat is driven by chrome.alarms, not setInterval - MV3 service
// workers get killed after ~30s idle, and a setInterval timer dies with
// them. chrome.alarms survives worker suspension and re-wakes the worker
// on each tick, so the heartbeat keeps firing even with no popup/tab
// interaction, which is the whole point (Panga needs to know the extension
// itself is alive, independent of what page is open).

const BRIDGE_ORIGIN = "http://127.0.0.1:8765";
const HEARTBEAT_ALARM = "panga-heartbeat";

chrome.alarms.create(HEARTBEAT_ALARM, { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === HEARTBEAT_ALARM) sendHeartbeat();
});
chrome.runtime.onInstalled.addListener(sendHeartbeat);
chrome.runtime.onStartup.addListener(sendHeartbeat);
sendHeartbeat();

async function sendHeartbeat() {
  try {
    await fetch(`${BRIDGE_ORIGIN}/heartbeat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: "extension" }),
    });
  } catch (e) {
    // Panga isn't running / not listening right now - expected whenever
    // Zahir hasn't opened the app, not something to surface as an error.
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message && message.type === "sendToPanga") {
    sendCapture(message.payload).then(sendResponse);
    return true; // keep the async channel open for sendResponse
  }
  if (message && message.type === "readinessUpdate" && sender.tab) {
    setBadgeForTab(sender.tab.id, message.state);
  }
});

async function sendCapture(payload) {
  try {
    const res = await fetch(`${BRIDGE_ORIGIN}/capture`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      return { ok: false, error: body.error || `Panga rejected the request (${res.status}).` };
    }
    showSentNotification(payload);
    return { ok: true };
  } catch (e) {
    return { ok: false, error: "Couldn't reach Panga - make sure the app is open." };
  }
}

// Post-send notification with a deep link (2026-08-09, Zahir-approved
// polish pass): the in-popup "Sent" state disappears the moment the user
// closes the popup, so there was no way to get back to that exact job in
// Panga without hunting for it themselves. A real chrome.notifications
// popup persists past the popup closing and its button jumps straight to
// the job.
//
// PANGA_APP_URL is hardcoded to production's fixed port (8510, see
// CLAUDE.md's port convention) rather than derived from anything - matches
// the same assumption the bridge port itself already makes (this
// extension only ever really talks to whichever Panga process holds 8765,
// normally production).
//
// Deep-link contract: `?job_url=<url-encoded posting URL>`, matched
// server-side by normalizing and comparing against each job's own
// posting_url - NOT `?job=<source>_<job_id>`, which would require
// duplicating Panga's per-source job_id derivation (a URL regex for
// LinkedIn, a content hash for Dice) inside this extension. Coordinated
// with the session that owns src/ui/app.py before landing the Panga-side
// handler for this - see extension/README.md's maintainer notes for status.
const PANGA_APP_URL = "http://localhost:8510";

// notificationId -> deep-link URL, so the button/click handler (which only
// gets the id back, not the payload) knows where to send the user. Chrome
// notifications are transient UI, not app state - fine to keep this
// in-memory only and let it grow unboundedly for a single long-running
// worker's lifetime (each is a couple hundred bytes; the worker restarts
// periodically anyway under normal MV3 suspension).
const notificationDeepLinks = new Map();

function showSentNotification(payload) {
  const notificationId = `panga-sent-${Date.now()}`;
  const jobLabel = [payload.title, payload.company].filter(Boolean).join(" at ") || "This job";
  const deepLink = `${PANGA_APP_URL}/?job_url=${encodeURIComponent(payload.url)}`;
  notificationDeepLinks.set(notificationId, deepLink);
  chrome.notifications.create(notificationId, {
    type: "basic",
    iconUrl: chrome.runtime.getURL("icons/icon128.png"),
    title: "Sent to Panga",
    message: `${jobLabel} - click to open it in Panga.`,
    buttons: [{ title: "Open in Panga" }],
    priority: 1,
  });
}

function openDeepLinkForNotification(notificationId) {
  const url = notificationDeepLinks.get(notificationId);
  if (!url) return; // not one of ours, or already handled
  chrome.tabs.create({ url });
  notificationDeepLinks.delete(notificationId);
  chrome.notifications.clear(notificationId);
}

chrome.notifications.onButtonClicked.addListener((notificationId) => openDeepLinkForNotification(notificationId));
chrome.notifications.onClicked.addListener((notificationId) => openDeepLinkForNotification(notificationId));

// Toolbar icon badge (2026-08-09, Zahir-approved polish pass): a colored
// dot per tab so the state is visible without opening the popup. No badge
// on a non-job page, amber while a matched job page hasn't finished
// loading its description yet (content.js's cheapReadinessCheck() reports
// this - see its own comment on why it never forces a scroll itself),
// green once it's actually ready to send. Badge text is a single space,
// not a character - chrome.action renders that as a small colored pill
// with no visible glyph, which reads as a plain dot.
const BADGE_COLORS = { ready: "#16a34a", loading: "#d97706" };

function setBadgeForTab(tabId, state) {
  if (state === "ready" || state === "loading") {
    chrome.action.setBadgeText({ tabId, text: " " });
    chrome.action.setBadgeBackgroundColor({ tabId, color: BADGE_COLORS[state] });
  } else {
    chrome.action.setBadgeText({ tabId, text: "" });
  }
}

// Clears a stale badge the moment a tab starts navigating away from
// wherever it was - covers "leaves a LinkedIn job page for something else"
// without needing the "tabs" permission just to read the destination URL.
// If the new page also matches content_scripts.matches, content.js's own
// reportReadiness() re-sets the badge correctly moments later; if not, it
// just stays cleared, which is correct.
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.status === "loading") {
    setBadgeForTab(tabId, "none");
  }
});
