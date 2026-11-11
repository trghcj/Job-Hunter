// The connect page hands its one-time token to the extension exactly once.
const test = require("node:test");
const assert = require("node:assert/strict");
const { parseHTML } = require("linkedom");

globalThis.JH = {};
require("../connect.js");
const JH = globalThis.JH;

const TOKEN = "a".repeat(43);

function page(withToken = true) {
  const tokenDiv = withToken ? `<div id="jh-connect" data-token="${TOKEN}" hidden></div>` : "";
  return parseHTML(`<!doctype html><html><body>${tokenDiv}
    <p id="jh-connect-status">Connecting…</p></body></html>`).document;
}

test("sends token once and clears it", async () => {
  const document = page();
  const sent = [];
  await JH.handoff(document, async (m) => { sent.push(m); return { ok: true }; });
  await JH.handoff(document, async (m) => { sent.push(m); return { ok: true }; });
  assert.deepEqual(sent, [{ type: "connect", token: TOKEN }]);
  assert.equal(document.getElementById("jh-connect").getAttribute("data-token"), null);
  assert.equal(document.getElementById("jh-connect-status").textContent,
    "Connected ✓ You can close this tab.");
});

test("no element → nothing sent", async () => {
  const sent = [];
  await JH.handoff(page(false), async (m) => { sent.push(m); return { ok: true }; });
  assert.deepEqual(sent, []);
});

test("shows failure text", async () => {
  const document = page();
  await JH.handoff(document, async () => ({ ok: false, error: "bad_input" }));
  assert.equal(document.getElementById("jh-connect-status").textContent,
    "Couldn't connect, reload this page.");
});

test("a broken extension message shows failure text", async () => {
  const document = page();
  await JH.handoff(document, async () => { throw new Error("no receiver"); });
  assert.equal(document.getElementById("jh-connect-status").textContent,
    "Couldn't connect, reload this page.");
});
