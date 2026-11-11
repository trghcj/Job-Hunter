// Content script: watches the open job page and drives the bubble. It only reads the page;
// it never clicks, scrolls, navigates or requests anything from the job site.
(function () {
  const JH = (globalThis.JH = globalThis.JH || {});
  const SLOW_MS = 3000;
  const TICK_MS = 1000; // never more often than once a second

  class Watcher {
    constructor({ readJob, send, render, renderMatches, location, isVisible, autoCheck,
      setTimeout, clearTimeout }) {
      Object.assign(this, { readJob, send, render, renderMatches, location, isVisible,
        autoCheck, setTimeout, clearTimeout });
      this.lastHref = null;
      this.job = null;
      this.requestId = 0;
    }

    // Called every second: re-read the page only when its address changed.
    async tick() {
      if (!this.isVisible()) return;
      const href = this.location().href;
      if (href === this.lastHref) return;
      this.lastHref = href;
      await this.refresh(false);
    }

    async refresh(force) {
      const id = ++this.requestId;
      const job = this.readJob();
      this.job = job;
      if (!job) {
        this.render(JH.viewModel({ kind: "nojob" }));
        return;
      }
      if (!force && !this.autoCheck()) {
        this.render(JH.viewModel({ kind: "idle", job }));
        return;
      }
      await this.check(id, job);
    }

    async check(id, job) {
      this.render(JH.viewModel({ kind: "checking", job, slow: false }));
      const timer = this.setTimeout(() => {
        if (id === this.requestId) this.render(JH.viewModel({ kind: "checking", job, slow: true }));
      }, SLOW_MS);
      const reply = await this.send({ type: "check", job });
      this.clearTimeout(timer);
      if (id !== this.requestId) return; // you already moved to another job
      this.render(JH.viewModel(reply && reply.ok
        ? { kind: "result", job, result: reply.data }
        : { kind: "error", job, error: (reply && reply.error) || "server" }));
    }

    async action(id) {
      if (id === "check" || id === "retry") return this.refresh(true);
      if ((id === "save" || id === "applied") && this.job) {
        const status = id === "save" ? "saved" : "applied";
        const reply = await this.send({ type: "save", job: this.job, status });
        if (reply && !reply.ok) {
          this.render(JH.viewModel({ kind: "error", job: this.job, error: reply.error }));
          return undefined;
        }
        return this.refresh(true);
      }
      return undefined;
    }

    async opened() {
      const reply = await this.send({ type: "matches", limit: 5 });
      if (reply && reply.ok) this.renderMatches(reply.data);
    }
  }

  JH.Watcher = Watcher;

  // ---- start-up in the browser (skipped in tests, where `chrome` does not exist)
  if (typeof chrome === "undefined" || !chrome.runtime || !chrome.runtime.id) return;

  const storage = {
    get: async (key) => (await chrome.storage.local.get(key))[key],
    set: (key, value) => chrome.storage.local.set({ [key]: value }),
  };

  async function start() {
    const host = location.hostname;
    const hidden = (await storage.get("hiddenSites")) || [];
    if (hidden.includes(host)) return;
    let autoCheck = (await storage.get("autoCheck")) !== false;
    chrome.storage.onChanged.addListener((changes) => {
      if (changes.autoCheck) autoCheck = changes.autoCheck.newValue !== false;
    });

    const bubble = new JH.Bubble(document, window, storage, host, globalThis.JOBHUNTER_URL);
    const watcher = new Watcher({
      readJob: () => JH.readJob(document, location),
      send: (message) => chrome.runtime.sendMessage(message)
        .catch(() => ({ ok: false, error: "server" })),
      render: (view) => bubble.render(view),
      renderMatches: (list) => bubble.renderMatches(list),
      location: () => location,
      isVisible: () => document.visibilityState === "visible",
      autoCheck: () => autoCheck,
      setTimeout: (fn, ms) => setTimeout(fn, ms),
      clearTimeout: (t) => clearTimeout(t),
    });
    bubble.onAction = (id) => watcher.action(id);
    bubble.onOpen = () => watcher.opened();
    bubble.onHide = async () => {
      const list = (await storage.get("hiddenSites")) || [];
      if (!list.includes(host)) await storage.set("hiddenSites", [...list, host]);
      bubble.remove();
      clearInterval(timer);
    };
    const timer = setInterval(() => watcher.tick(), TICK_MS);
    document.addEventListener("visibilitychange", () => watcher.tick());
    watcher.tick();
  }

  start();
})();
