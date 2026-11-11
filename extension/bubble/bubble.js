// The floating bubble: a 48 px button and a 320 px panel inside a closed shadow root, so
// the job site's styles cannot touch it and it cannot touch the site. All page-derived text
// is set with textContent, never as HTML.
(function () {
  const JH = (globalThis.JH = globalThis.JH || {});

  const CSS = `
    :host { all: initial; }
    .jh-wrap { position: fixed; z-index: 2147483646; font: 14px/1.45 system-ui, -apple-system,
      "Segoe UI", Roboto, Arial, sans-serif; color: #1F1B16; }
    .jh-button { width: 48px; height: 48px; border-radius: 50%; border: 3px solid #E09F3E;
      background: #FAF7F2; box-shadow: 0 4px 14px rgba(31, 27, 22, 0.25); cursor: grab;
      padding: 0; display: grid; place-items: center; position: relative; }
    .jh-logo { width: 30px; height: 30px; pointer-events: none; display: block; }
    .jh-button.jh-match { border-color: #4F8A4B; box-shadow: 0 0 0 3px rgba(79, 138, 75, 0.3),
      0 4px 14px rgba(31, 27, 22, 0.25); }
    .jh-button.jh-nomatch { border-color: #9A9086; }
    .jh-button.jh-pending { border-style: dashed; }
    .jh-dot { position: absolute; top: -2px; right: -2px; width: 12px; height: 12px;
      border-radius: 50%; background: #E09F3E; border: 2px solid #FAF7F2; }
    .jh-dot[hidden] { display: none; }
    .jh-panel { position: absolute; bottom: 58px; right: 0; width: 320px; max-width: 86vw;
      background: #FAF7F2; border: 1px solid #E6DFD5; border-radius: 12px; padding: 14px 16px;
      box-shadow: 0 10px 30px rgba(31, 27, 22, 0.25); }
    .jh-panel[hidden] { display: none; }
    .jh-head { display: flex; justify-content: space-between; align-items: center;
      margin-bottom: 8px; font-weight: 700; }
    .jh-close { background: none; border: 0; font-size: 18px; cursor: pointer; color: #6B6258; }
    .jh-title { font-weight: 700; margin: 0; }
    .jh-sub, .jh-seen, .jh-hint { color: #6B6258; margin: 2px 0 0; font-size: 13px; }
    .jh-headline { margin: 10px 0 0; font-weight: 600; }
    .jh-headline.jh-match { color: #3E7139; }
    .jh-actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
    .jh-actions button, .jh-connect { background: #E09F3E; color: #1F1B16; border: 0;
      border-radius: 8px; padding: 7px 12px; font: inherit; font-weight: 700; cursor: pointer;
      text-decoration: none; display: inline-block; }
    .jh-actions button + button { background: transparent; border: 1px solid #E6DFD5; }
    .jh-actions button:disabled { opacity: 0.6; cursor: default; }
    .jh-section { border-top: 1px solid #E6DFD5; margin-top: 12px; padding-top: 10px; }
    .jh-section h3 { font-size: 13px; margin: 0 0 6px; color: #6B6258; }
    .jh-matches { list-style: none; margin: 0; padding: 0; }
    .jh-matches li { margin: 4px 0; }
    .jh-matches a, .jh-link { color: #8F5A12; }
    .jh-footer { display: flex; justify-content: space-between; margin-top: 10px; font-size: 13px; }
    .jh-footer button { background: none; border: 0; color: #6B6258; text-decoration: underline;
      cursor: pointer; font: inherit; padding: 0; }
  `;

  const SAFE_LINK = /^https:\/\//i;
  const SVG_NS = "http://www.w3.org/2000/svg";
  // The JobHunter logo (same shapes as the web app's logo.svg), drawn inline so the job
  // site's image rules never block it and no extension file is exposed to the page.
  const LOGO = [
    ["path", { d: "M16 5h32a12 12 0 0 1 12 12v18a12 12 0 0 1-12 12H25L12 58V46.4A12 12 0 0 1 4 35V17A12 12 0 0 1 16 5z", fill: "#E09F3E" }],
    ["circle", { cx: "29", cy: "22", r: "9", fill: "#FAF7F2", stroke: "#1F1B16", "stroke-width": "5" }],
    ["path", { d: "M35.4 28.4L40 33v4a5 5 0 0 1-9 3", fill: "none", stroke: "#1F1B16", "stroke-width": "5", "stroke-linecap": "round", "stroke-linejoin": "round" }],
  ];

  function logo(doc) {
    const svg = doc.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 64 64");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("class", "jh-logo");
    for (const [tag, attrs] of LOGO) {
      const shape = doc.createElementNS(SVG_NS, tag);
      for (const [k, v] of Object.entries(attrs)) shape.setAttribute(k, v);
      svg.append(shape);
    }
    return svg;
  }

  function el(doc, tag, cls, text) {
    const node = doc.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  class Bubble {
    // storage: { get(key), set(key, value) } (chrome.storage.local in the extension)
    constructor(document, window, storage, hostname, serverUrl = "") {
      this.document = document;
      this.window = window;
      this.storage = storage;
      this.hostname = hostname;
      this.serverUrl = serverUrl;
      this.onAction = () => {};
      this.onOpen = () => {};
      this.onHide = () => {};

      this.host = el(document, "div");
      this.host.id = "jobhunter-bubble-host";
      this.root = this.host.attachShadow({ mode: "closed" });
      const style = el(document, "style", null, CSS);
      this.wrap = el(document, "div", "jh-wrap");
      this.button = el(document, "button", "jh-button jh-pending");
      this.button.type = "button";
      this.button.setAttribute("aria-label", "JobHunter");
      this.dot = el(document, "span", "jh-dot");
      this.dot.hidden = true;
      this.button.append(logo(document), this.dot);
      this.panel = el(document, "div", "jh-panel");
      this.panel.hidden = true;
      this.panel.setAttribute("role", "dialog");
      this.panel.setAttribute("aria-label", "JobHunter");
      this.wrap.append(this.panel, this.button);
      this.root.append(style, this.wrap);
      this.place({ right: 20, bottom: 20 });
      document.body.append(this.host);
      this.button.addEventListener("click", () => this.toggle());
      this.enableDrag();
      this.restorePosition();
    }

    place({ right, bottom }) {
      this.position = { right, bottom };
      this.wrap.style.right = `${right}px`;
      this.wrap.style.bottom = `${bottom}px`;
    }

    async restorePosition() {
      const saved = (await this.storage.get("positions")) || {};
      if (saved[this.hostname]) this.place(saved[this.hostname]);
    }

    enableDrag() {
      let start = null;
      let moved = false;
      this.button.addEventListener("pointerdown", (e) => {
        start = { x: e.clientX, y: e.clientY, ...this.position };
        moved = false;
        if (this.button.setPointerCapture) this.button.setPointerCapture(e.pointerId);
      });
      this.button.addEventListener("pointermove", (e) => {
        if (!start) return;
        const dx = e.clientX - start.x;
        const dy = e.clientY - start.y;
        if (Math.abs(dx) + Math.abs(dy) > 4) moved = true;
        const w = this.window.innerWidth || 1000;
        const h = this.window.innerHeight || 800;
        this.place({ right: Math.min(Math.max(start.right - dx, 0), w - 56),
          bottom: Math.min(Math.max(start.bottom - dy, 0), h - 56) });
      });
      this.button.addEventListener("pointerup", async () => {
        if (!start) return;
        start = null;
        if (!moved) return;
        this.justDragged = true;
        const saved = (await this.storage.get("positions")) || {};
        saved[this.hostname] = this.position;
        await this.storage.set("positions", saved);
      });
    }

    toggle() {
      if (this.justDragged) {
        this.justDragged = false;
        return;
      }
      this.panel.hidden = !this.panel.hidden;
      if (!this.panel.hidden) this.onOpen();
    }

    render(view) {
      this.button.className = `jh-button jh-${view.ring}`;
      this.button.setAttribute("aria-label", `JobHunter: ${view.headline}`);
      this.dot.hidden = !view.dot;
      const d = this.document;
      const head = el(d, "div", "jh-head");
      const close = el(d, "button", "jh-close", "×");
      close.type = "button";
      close.setAttribute("aria-label", "Close");
      close.addEventListener("click", () => { this.panel.hidden = true; });
      head.append(el(d, "span", null, "JobHunter"), close);
      const body = [head];
      if (view.title) body.push(el(d, "p", "jh-title", view.title));
      if (view.subtitle) body.push(el(d, "p", "jh-sub", view.subtitle));
      body.push(el(d, "p", `jh-headline jh-${view.ring}`, view.headline));
      if (view.seen) body.push(el(d, "p", "jh-seen", view.seen));
      if (view.showConnect && this.serverUrl) {
        const link = el(d, "a", "jh-connect", "Connect JobHunter");
        link.href = `${this.serverUrl}/extension/connect`;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        body.push(link);
      }
      if (view.actions.length) {
        const actions = el(d, "div", "jh-actions");
        for (const action of view.actions) {
          const b = el(d, "button", null, action.label);
          b.type = "button";
          b.disabled = Boolean(action.disabled);
          b.addEventListener("click", () => this.onAction(action.id));
          actions.append(b);
        }
        body.push(actions);
      }
      this.matchesSection = el(d, "div", "jh-section");
      this.matchesSection.hidden = true;
      body.push(this.matchesSection, this.footer());
      this.panel.replaceChildren(...body);
    }

    footer() {
      const d = this.document;
      const footer = el(d, "div", "jh-footer");
      if (this.serverUrl) {
        const open = el(d, "a", "jh-link", "Open My jobs");
        open.href = `${this.serverUrl}/`;
        open.target = "_blank";
        open.rel = "noopener noreferrer";
        footer.append(open);
      }
      const hide = el(d, "button", null, `Hide on ${this.hostname}`);
      hide.type = "button";
      hide.addEventListener("click", () => this.onHide());
      footer.append(hide);
      return footer;
    }

    renderMatches(matches) {
      const d = this.document;
      const section = this.matchesSection || el(d, "div", "jh-section");
      if (!this.matchesSection) {
        this.matchesSection = section;
        this.panel.append(section);
      }
      const list = el(d, "ul", "jh-matches");
      for (const m of matches || []) {
        if (!SAFE_LINK.test(String(m.url || ""))) continue;
        const a = el(d, "a", null, [m.title, m.company].filter(Boolean).join(" · "));
        a.setAttribute("href", m.url);
        a.setAttribute("target", "_blank");
        a.setAttribute("rel", "noopener noreferrer");
        const li = el(d, "li");
        li.append(a);
        list.append(li);
      }
      section.replaceChildren(el(d, "h3", null, "Your latest matches"),
        list.children.length ? list : el(d, "p", "jh-hint", "No matches yet."));
      section.hidden = false;
    }

    remove() {
      this.host.remove();
    }
  }

  JH.Bubble = Bubble;
})();
