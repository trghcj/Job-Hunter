// Runs only on JobHunter's /extension/connect page: passes the one-time token shown there to
// the service worker, then removes it from the page.
(function () {
  const JH = (globalThis.JH = globalThis.JH || {});

  JH.handoff = async function handoff(document, send) {
    const holder = document.getElementById("jh-connect");
    const token = holder && holder.getAttribute("data-token");
    if (!token) return;
    holder.removeAttribute("data-token");
    let reply;
    try {
      reply = await send({ type: "connect", token });
    } catch {
      reply = { ok: false };
    }
    const status = document.getElementById("jh-connect-status");
    if (status) {
      status.textContent = reply && reply.ok ? "Connected ✓ You can close this tab."
        : "Couldn't connect, reload this page.";
    }
  };

  if (typeof chrome === "undefined" || !chrome.runtime || !chrome.runtime.id) return;
  JH.handoff(document, (message) => chrome.runtime.sendMessage(message));
})();
