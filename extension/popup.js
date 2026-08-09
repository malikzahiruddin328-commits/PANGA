const statusEl = document.getElementById("status");
const jobEl = document.getElementById("job");
const previewEl = document.getElementById("preview");
const sendBtn = document.getElementById("sendBtn");
const resultEl = document.getElementById("result");

const PREVIEW_SNIPPET_LENGTH = 150;

// Builds confidence about what's actually about to be sent (Zahir-approved
// polish pass, 2026-08-09) - a black-box "Send to Panga" button asked the
// user to trust extraction worked without showing them anything. Uses
// textContent, not innerHTML, so nothing in the extracted JD text (which
// is arbitrary text from a third-party page) can be interpreted as markup.
function renderPreview(description) {
  previewEl.textContent = "";
  const countDiv = document.createElement("div");
  countDiv.className = "count";
  countDiv.textContent = `${description.length.toLocaleString()} characters detected`;

  const snippetDiv = document.createElement("div");
  snippetDiv.className = "snippet";
  const truncated = description.length > PREVIEW_SNIPPET_LENGTH;
  const snippet = description.slice(0, PREVIEW_SNIPPET_LENGTH).trim();
  snippetDiv.textContent = `"${snippet}${truncated ? "…" : ""}"`;

  previewEl.appendChild(countDiv);
  previewEl.appendChild(snippetDiv);
  previewEl.style.display = "block";
}

let currentTab = null;
let extracted = null;

function isSupportedUrl(url) {
  return (
    /^https:\/\/www\.linkedin\.com\/jobs\/view\//.test(url) ||
    /^https:\/\/www\.dice\.com\/job-detail\//.test(url)
  );
}

function sourceForUrl(url) {
  return url.includes("linkedin.com") ? "linkedin" : "dice";
}

function sendExtractJobMessage(tabId) {
  return new Promise((resolve) => {
    chrome.tabs.sendMessage(tabId, { type: "extractJob" }, (response) => {
      // chrome.runtime.lastError (typically "Could not establish connection.
      // Receiving end does not exist.") means no content script answered in
      // this tab at all - resolve null rather than reject, so the caller can
      // try the fallback below instead of treating it as a hard failure.
      resolve(chrome.runtime.lastError || !response ? null : response);
    });
  });
}

// Real bug found live 2026-08-09 (Zahir, a real LinkedIn posting): the
// popup showed "Couldn't read this page" - not either of content.js's own
// extraction-failure messages, meaning no content script was ever there to
// answer. content_scripts.matches was already correct for the URL; the
// likely cause is LinkedIn being a single-page app - clicking into a job
// from search results/a feed updates the URL via the History API, which
// does NOT trigger Chrome's declarative content-script injection the way a
// real page load does (a documented Chrome extension platform gap, not a
// bug in content.js's own DOM-reading logic). Falls back to injecting
// content.js on demand, right when the user actually clicks the extension
// icon - fits this extension's existing "act on an explicit click, no
// background surveillance" design better than a persistent
// chrome.webNavigation listener watching every tab for SPA route changes.
async function ensureContentScriptAndExtract(tabId) {
  const firstTry = await sendExtractJobMessage(tabId);
  if (firstTry) return firstTry;

  try {
    await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
  } catch (e) {
    return null; // e.g. a chrome:// page or another non-injectable tab
  }
  return await sendExtractJobMessage(tabId);
}

async function init() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  currentTab = tab;
  if (!tab || !tab.url || !isSupportedUrl(tab.url)) {
    statusEl.textContent = "Open a LinkedIn or Dice job posting to send it to Panga.";
    return;
  }
  statusEl.textContent = "Reading this job posting...";
  const response = await ensureContentScriptAndExtract(tab.id);
  if (!response) {
    statusEl.textContent = "Couldn't read this page - try reloading it.";
    return;
  }
  if (!response.ok) {
    statusEl.textContent = response.error;
    return;
  }
  extracted = response.data;
  statusEl.textContent = "Ready to send:";
  jobEl.textContent =
    [extracted.title, extracted.company].filter(Boolean).join(" — ") ||
    "(title/company not detected, description was)";
  renderPreview(extracted.description);
  sendBtn.style.display = "block";
}

sendBtn.addEventListener("click", () => {
  sendBtn.disabled = true;
  sendBtn.textContent = "Sending...";
  const payload = { ...extracted, url: currentTab.url, source: sourceForUrl(currentTab.url) };
  chrome.runtime.sendMessage({ type: "sendToPanga", payload }, (response) => {
    if (response && response.ok) {
      resultEl.textContent =
        "Sent - open this job's paste-JD box in Panga to see it filled in.";
      sendBtn.textContent = "Sent";
    } else {
      resultEl.textContent = (response && response.error) || "Failed to send.";
      sendBtn.disabled = false;
      sendBtn.textContent = "Send to Panga";
    }
  });
});

init();
