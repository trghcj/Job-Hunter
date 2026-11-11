// Toolbar popup: connection status, automatic checking, sites where the bubble is hidden.
(async function () {
  const $ = (id) => document.getElementById(id);
  const store = chrome.storage.local;

  async function showStatus() {
    const reply = await chrome.runtime.sendMessage({ type: "status" })
      .catch(() => ({ ok: false }));
    const connected = Boolean(reply && reply.ok && reply.data.connected);
    $("status").textContent = connected ? "Connected to your JobHunter account."
      : "Not connected yet.";
    $("status").className = connected ? "ok" : "bad";
    $("connect").hidden = connected;
    $("disconnect").hidden = !connected;
  }

  async function showHidden() {
    const { hiddenSites = [] } = await store.get("hiddenSites");
    const list = $("hidden-list");
    list.replaceChildren();
    for (const host of hiddenSites) {
      const li = document.createElement("li");
      const name = document.createElement("span");
      name.textContent = host;
      const undo = document.createElement("button");
      undo.className = "link";
      undo.type = "button";
      undo.textContent = "Show again";
      undo.addEventListener("click", async () => {
        await store.set({ hiddenSites: hiddenSites.filter((h) => h !== host) });
        showHidden();
      });
      li.append(name, undo);
      list.append(li);
    }
    $("hidden-sites").hidden = hiddenSites.length === 0;
  }

  $("connect").addEventListener("click", () => {
    chrome.tabs?.create
      ? chrome.tabs.create({ url: `${globalThis.JOBHUNTER_URL}/extension/connect` })
      : window.open(`${globalThis.JOBHUNTER_URL}/extension/connect`, "_blank", "noopener");
  });
  $("disconnect").addEventListener("click", async () => {
    await chrome.runtime.sendMessage({ type: "disconnect" });
    showStatus();
  });

  const { autoCheck } = await store.get("autoCheck");
  $("auto").checked = autoCheck !== false;
  $("auto").addEventListener("change", (e) => store.set({ autoCheck: e.target.checked }));

  showStatus();
  showHidden();
})();
