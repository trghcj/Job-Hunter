// The page watcher: checks the open job when the URL changes, at most once a second and only
// while the tab is visible; ignores late answers for a job you already left.
const test = require("node:test");
const assert = require("node:assert/strict");

globalThis.JH = {};
require("../bubble/view.js");
require("../content.js");
const JH = globalThis.JH;

const JOB_A = { url: "https://jobs.lever.co/a/1", title: "A", company: "Acme", location: null,
  description: null };
const JOB_B = { ...JOB_A, url: "https://jobs.lever.co/a/2", title: "B" };
const MATCH = { ok: true, data: { verdict: "match", reasons: [], seen: { status: null,
  emailed: false, listed: false } } };

function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

function setup({ job = JOB_A, visible = true, autoCheck = true, reply = MATCH } = {}) {
  const env = { href: "https://jobs.lever.co/a/1", job, visible, sent: [], renders: [],
    timers: [], replies: [] };
  env.watcher = new JH.Watcher({
    readJob: () => env.job,
    send: (message) => {
      env.sent.push(message);
      if (env.replies.length) return env.replies.shift().promise;
      return Promise.resolve(reply);
    },
    render: (view) => env.renders.push(view),
    renderMatches: (list) => env.renders.push({ matches: list }),
    location: () => ({ href: env.href }),
    isVisible: () => env.visible,
    autoCheck: () => autoCheck,
    setTimeout: (fn, ms) => { env.timers.push({ fn, ms }); return env.timers.length; },
    clearTimeout: () => {},
  });
  return env;
}

const last = (env) => env.renders[env.renders.length - 1];

test("test_no_job_sends_nothing", async () => {
  const env = setup({ job: null });
  await env.watcher.tick();
  assert.deepEqual(env.sent, []);
  assert.equal(last(env).headline, "Open a job to check it");
});

test("checks the open job and shows the result", async () => {
  const env = setup();
  await env.watcher.tick();
  assert.deepEqual(env.sent, [{ type: "check", job: JOB_A }]);
  assert.equal(env.renders[0].headline, "Checking…");
  assert.equal(last(env).headline, "Matches your filters");
});

test("same URL is not checked twice", async () => {
  const env = setup();
  await env.watcher.tick();
  await env.watcher.tick();
  assert.equal(env.sent.length, 1);
});

test("test_url_change_rechecks", async () => {
  const env = setup();
  await env.watcher.tick();
  env.href = "https://jobs.lever.co/a/2";
  env.job = JOB_B;
  await env.watcher.tick();
  assert.deepEqual(env.sent.map((m) => m.job.title), ["A", "B"]);
});

test("test_stale_result_ignored", async () => {
  const env = setup();
  const slowA = deferred();
  const fastB = deferred();
  env.replies.push(slowA, fastB);
  const first = env.watcher.tick();
  env.href = "https://jobs.lever.co/a/2";
  env.job = JOB_B;
  const second = env.watcher.tick();
  fastB.resolve({ ok: true, data: { verdict: "no_match", reasons: ["too senior"],
    seen: { status: null, emailed: false, listed: false } } });
  await second;
  slowA.resolve(MATCH);
  await first;
  assert.equal(last(env).headline, "Not a match: too senior");
  assert.equal(last(env).title, "B");
});

test("test_hidden_tab_does_not_poll", async () => {
  const env = setup({ visible: false });
  await env.watcher.tick();
  assert.deepEqual(env.sent, []);
  assert.deepEqual(env.renders, []);
});

test("slow answer shows the waking message after 3 s", async () => {
  const env = setup();
  const pending = deferred();
  env.replies.push(pending);
  const done = env.watcher.tick();
  await Promise.resolve();
  const timer = env.timers.find((t) => t.ms === 3000);
  assert.ok(timer);
  timer.fn();
  assert.equal(last(env).headline, "Waking up JobHunter, this can take up to a minute…");
  pending.resolve(MATCH);
  await done;
  assert.equal(last(env).headline, "Matches your filters");
});

test("test_auto_check_off_waits_for_click", async () => {
  const env = setup({ autoCheck: false });
  await env.watcher.tick();
  assert.deepEqual(env.sent, []);
  assert.equal(last(env).headline, "Click Check to compare this job with your filters");
  await env.watcher.action("check");
  assert.equal(env.sent.length, 1);
});

test("save sends the job and re-checks", async () => {
  const env = setup();
  await env.watcher.tick();
  await env.watcher.action("save");
  assert.deepEqual(env.sent.slice(1).map((m) => m.type), ["save", "check"]);
  assert.equal(env.sent[1].status, "saved");
  await env.watcher.action("applied");
  assert.equal(env.sent[3].status, "applied");
});

test("errors are shown", async () => {
  const env = setup({ reply: { ok: false, error: "not_connected" } });
  await env.watcher.tick();
  assert.equal(last(env).showConnect, true);
});

test("opening the panel loads latest matches", async () => {
  const env = setup({ reply: { ok: true, data: [{ title: "X", company: "Y",
    url: "https://x.example" }] } });
  await env.watcher.opened();
  assert.deepEqual(env.sent, [{ type: "matches", limit: 5 }]);
  assert.deepEqual(last(env).matches, [{ title: "X", company: "Y", url: "https://x.example" }]);
});
