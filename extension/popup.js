const statusEl = document.getElementById("status");
const jobEl = document.getElementById("job");
const sendBtn = document.getElementById("sendBtn");
const resultEl = document.getElementById("result");

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

async function init() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  currentTab = tab;
  if (!tab || !tab.url || !isSupportedUrl(tab.url)) {
    statusEl.textContent = "Open a LinkedIn or Dice job posting to send it to Panga.";
    return;
  }
  statusEl.textContent = "Reading this job posting...";
  chrome.tabs.sendMessage(tab.id, { type: "extractJob" }, (response) => {
    if (chrome.runtime.lastError || !response) {
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
    sendBtn.style.display = "block";
  });
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
