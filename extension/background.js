// Service worker: the only part of the extension that holds the token and talks to the
// JobHunter server. Content scripts and the popup ask it by message.
import "./config.js";
import { makeClient } from "./api.js";

const storage = {
  get: async (key) => (await chrome.storage.local.get(key))[key],
  set: (key, value) => chrome.storage.local.set({ [key]: value }),
  remove: (key) => chrome.storage.local.remove(key),
};

const client = makeClient({
  fetch: (...args) => fetch(...args),
  storage,
  baseUrl: globalThis.JOBHUNTER_URL,
  sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
});

const CONNECT_PAGE = `${globalThis.JOBHUNTER_URL}/extension/connect`;

async function handle(message, sender) {
  switch (message && message.type) {
    case "check": return client.check(message.job);
    case "save": return client.save(message.job, message.status);
    case "matches": return client.matches(message.limit);
    case "me": return client.me();
    case "status": return { ok: true, data: { connected: await client.isConnected() } };
    case "disconnect": await client.disconnect(); return { ok: true };
    case "connect":
      // Only the JobHunter connect page may hand over a token.
      if (!sender.url || !sender.url.startsWith(CONNECT_PAGE)) {
        return { ok: false, error: "bad_input" };
      }
      return client.connect(message.token);
    default: return { ok: false, error: "bad_input" };
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  handle(message, sender).then(sendResponse,
    () => sendResponse({ ok: false, error: "server" }));
  return true; // answer asynchronously
});
