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
    return { ok: true };
  } catch (e) {
    return { ok: false, error: "Couldn't reach Panga - make sure the app is open." };
  }
}
