// What the bubble shows for each state (pure), and that page text is never run as HTML.
const test = require("node:test");
const assert = require("node:assert/strict");
const { parseHTML } = require("linkedom");

globalThis.JH = {};
require("../bubble/view.js");
require("../bubble/bubble.js");
const JH = globalThis.JH;

const JOB = { url: "https://jobs.lever.co/a/1", title: "Backend Engineer", company: "Acme",
  location: "Pune", description: null };
const NOT_SEEN = { status: null, emailed: false, listed: false };

function result(verdict, reasons = [], seen = NOT_SEEN) {
  return { kind: "result", job: JOB, result: { verdict, reasons, seen } };
}

test("no job", () => {
  const v = JH.viewModel({ kind: "nojob" });
  assert.equal(v.ring, "pending");
  assert.equal(v.headline, "Open a job to check it");
  assert.deepEqual(v.actions, []);
});

test("checking, then slow", () => {
  assert.equal(JH.viewModel({ kind: "checking", job: JOB, slow: false }).headline, "Checking…");
  assert.equal(JH.viewModel({ kind: "checking", job: JOB, slow: true }).headline,
    "Waking up JobHunter, this can take up to a minute…");
});

test("match is green with save actions", () => {
  const v = JH.viewModel(result("match"));
  assert.equal(v.ring, "match");
  assert.equal(v.headline, "Matches your filters");
  assert.equal(v.title, "Backend Engineer");
  assert.equal(v.subtitle, "Acme · Pune");
  assert.deepEqual(v.actions.map((a) => a.id), ["save", "applied"]);
  assert.equal(v.actions[0].label, "Save to JobHunter");
  assert.equal(v.dot, false);
});

test("no match shows the reason", () => {
  const v = JH.viewModel(result("no_match", ["needs 5+ years"]));
  assert.equal(v.ring, "nomatch");
  assert.equal(v.headline, "Not a match: needs 5+ years");
});

test("seen shows a dot and the status", () => {
  const saved = JH.viewModel(result("match", [], { status: "saved", emailed: false, listed: true }));
  assert.equal(saved.dot, true);
  assert.equal(saved.seen, "Already saved");
  assert.equal(saved.actions.find((a) => a.id === "save").label, "Saved ✓");
  assert.equal(saved.actions.find((a) => a.id === "save").disabled, true);
  const mailed = JH.viewModel(result("match", [], { status: null, emailed: true, listed: true }));
  assert.equal(mailed.dot, true);
  assert.equal(mailed.seen, "Already in your daily email");
  const listed = JH.viewModel(result("match", [], { status: null, emailed: false, listed: true }));
  assert.equal(listed.seen, "Already in My jobs");
  const applied = JH.viewModel(result("match", [], { status: "applied", emailed: false,
    listed: false }));
  assert.equal(applied.seen, "You applied to this job");
});

test("not connected", () => {
  const v = JH.viewModel({ kind: "error", error: "not_connected", job: JOB });
  assert.equal(v.showConnect, true);
  assert.equal(v.headline, "Connect JobHunter");
});

test("server did not answer", () => {
  const v = JH.viewModel({ kind: "error", error: "waking_failed", job: JOB });
  assert.equal(v.headline, "JobHunter didn't answer, try again");
  assert.deepEqual(v.actions.map((a) => a.id), ["retry"]);
});

test("other errors", () => {
  assert.equal(JH.viewModel({ kind: "error", error: "rate_limited" }).headline,
    "Too many checks right now, try again later");
  assert.equal(JH.viewModel({ kind: "error", error: "bad_input" }).headline,
    "This page couldn't be read as a job");
  assert.equal(JH.viewModel({ kind: "error", error: "server" }).headline,
    "Something went wrong at JobHunter");
});

function newBubble() {
  const { document, window } = parseHTML("<!doctype html><html><body><p>site</p></body></html>");
  const store = {};
  const bubble = new JH.Bubble(document, window, {
    get: async (k) => store[k], set: async (k, v) => { store[k] = v; } }, "www.linkedin.com");
  return { document, bubble, store };
}

test("bubble lives in a shadow root", () => {
  const { document, bubble } = newBubble();
  const host = document.getElementById("jobhunter-bubble-host");
  assert.ok(host && bubble.root && bubble.root !== document);
});

test("test_panel_shows_html_as_text", () => {
  const { bubble } = newBubble();
  const job = { ...JOB, title: "<img src=x onerror=alert(1)><b>Pwn</b>" };
  bubble.render(JH.viewModel({ kind: "result", job,
    result: { verdict: "match", reasons: [], seen: NOT_SEEN } }));
  const title = bubble.root.querySelector(".jh-title");
  assert.equal(title.textContent, "<img src=x onerror=alert(1)><b>Pwn</b>");
  assert.equal(bubble.root.querySelector("img[src=x]"), null);
  assert.equal(bubble.root.querySelector("b"), null);
});

test("ring class and dot follow the view model", () => {
  const { bubble } = newBubble();
  bubble.render(JH.viewModel(result("match", [], { status: "saved", emailed: false,
    listed: false })));
  const button = bubble.root.querySelector(".jh-button");
  assert.ok(button.className.includes("jh-match"));
  assert.ok(bubble.root.querySelector(".jh-dot:not([hidden])"));
  bubble.render(JH.viewModel(result("no_match", ["x"])));
  assert.ok(bubble.root.querySelector(".jh-button").className.includes("jh-nomatch"));
  assert.ok(bubble.root.querySelector(".jh-dot[hidden]"));
});

test("action buttons call back with their id", () => {
  const { bubble } = newBubble();
  const clicked = [];
  bubble.onAction = (id) => clicked.push(id);
  bubble.render(JH.viewModel(result("match")));
  for (const b of bubble.root.querySelectorAll(".jh-actions button")) {
    b.dispatchEvent(new bubble.window.Event("click"));
  }
  assert.deepEqual(clicked, ["save", "applied"]);
});

test("matches list renders links that open in a new tab", () => {
  const { bubble } = newBubble();
  bubble.renderMatches([{ title: "AI <Engineer>", company: "Uplers", url: "https://x.example/1" },
    { title: "Bad", company: "B", url: "javascript:alert(1)" }]);
  const links = bubble.root.querySelectorAll(".jh-matches a");
  assert.equal(links.length, 1);
  assert.equal(links[0].getAttribute("href"), "https://x.example/1");
  assert.equal(links[0].getAttribute("target"), "_blank");
  assert.equal(links[0].getAttribute("rel"), "noopener noreferrer");
  assert.equal(links[0].textContent, "AI <Engineer> · Uplers");
});

test("logo is drawn inline, no image file from the extension", () => {
  const { bubble } = newBubble();
  assert.ok(bubble.root.querySelector("svg.jh-logo path"));
  assert.equal(bubble.root.querySelector("img"), null);
});
