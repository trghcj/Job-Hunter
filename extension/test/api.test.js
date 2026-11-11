// The service worker's API client: bearer token, retries once while Render wakes up,
// forgets a revoked token.
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

let makeClient;
test.before(async () => {
  ({ makeClient } = await import(pathToFileURL(path.join(__dirname, "..", "api.js")).href));
});

const BASE = "https://jobhunter.example";
const TOKEN = "x".repeat(43);
const JOB = { url: "https://www.linkedin.com/jobs/view/1/", title: "Backend Engineer",
  company: "Acme", location: "Pune", description: null };

function memoryStorage(initial = {}) {
  const data = { ...initial };
  return {
    data,
    async get(key) { return data[key]; },
    async set(key, value) { data[key] = value; },
    async remove(key) { delete data[key]; },
  };
}

function reply(status, body = {}) {
  return { status, ok: status >= 200 && status < 300, json: async () => body };
}

function client({ fetch, storage = memoryStorage({ token: TOKEN }), sleeps = [] } = {}) {
  return makeClient({ fetch, storage, baseUrl: BASE, timeoutMs: 50, retryDelayMs: 5,
    sleep: async (ms) => { sleeps.push(ms); } });
}

test("check sends bearer and json", async () => {
  const calls = [];
  const c = client({ fetch: async (url, init) => { calls.push([url, init]);
    return reply(200, { verdict: "match", reasons: [], seen: {} }); } });
  const result = await c.check(JOB);
  assert.deepEqual(result, { ok: true, data: { verdict: "match", reasons: [], seen: {} } });
  const [url, init] = calls[0];
  assert.equal(url, `${BASE}/api/v1/check`);
  assert.equal(init.method, "POST");
  assert.equal(init.headers.Authorization, `Bearer ${TOKEN}`);
  assert.equal(init.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(init.body), JOB);
  assert.equal(init.credentials, "omit");
});

test("not connected without token: no request made", async () => {
  let called = false;
  const c = client({ fetch: async () => { called = true; }, storage: memoryStorage() });
  assert.deepEqual(await c.check(JOB), { ok: false, error: "not_connected" });
  assert.equal(called, false);
});

test("401 forgets token", async () => {
  const storage = memoryStorage({ token: TOKEN });
  const c = client({ fetch: async () => reply(401), storage });
  assert.deepEqual(await c.me(), { ok: false, error: "not_connected" });
  assert.equal(storage.data.token, undefined);
  assert.equal(await c.isConnected(), false);
});

test("slow server retries once then gives up", async () => {
  let calls = 0;
  const sleeps = [];
  const c = client({ fetch: (url, init) => { calls += 1;
    return new Promise((_, reject) => init.signal.addEventListener("abort",
      () => reject(new Error("aborted")))); }, sleeps });
  assert.deepEqual(await c.check(JOB), { ok: false, error: "waking_failed" });
  assert.equal(calls, 2);
  assert.deepEqual(sleeps, [5]);
});

test("retry succeeds after a network error", async () => {
  let calls = 0;
  const c = client({ fetch: async () => { calls += 1;
    if (calls === 1) throw new TypeError("Failed to fetch");
    return reply(200, [{ title: "A" }]); } });
  assert.deepEqual(await c.matches(), { ok: true, data: [{ title: "A" }] });
  assert.equal(calls, 2);
});

test("502 while waking is retried", async () => {
  const codes = [502, 200];
  const c = client({ fetch: async () => reply(codes.shift(), { email: "a@b.c" }) });
  assert.deepEqual(await c.me(), { ok: true, data: { email: "a@b.c" } });
});

test("429 is rate limited, 422 is bad input, 500 is server", async () => {
  for (const [status, error] of [[429, "rate_limited"], [422, "bad_input"], [500, "server"]]) {
    const c = client({ fetch: async () => reply(status) });
    assert.deepEqual(await c.check(JOB), { ok: false, error }, String(status));
  }
});

test("save posts status, matches passes the limit", async () => {
  const calls = [];
  const c = client({ fetch: async (url, init) => { calls.push([url, init]);
    return reply(200, {}); } });
  await c.save(JOB, "applied");
  await c.matches(3);
  assert.equal(calls[0][0], `${BASE}/api/v1/jobs`);
  assert.equal(JSON.parse(calls[0][1].body).status, "applied");
  assert.equal(calls[1][0], `${BASE}/api/v1/matches?limit=3`);
  assert.equal(calls[1][1].method, "GET");
  assert.equal(calls[1][1].body, undefined);
});

test("connect stores token", async () => {
  const storage = memoryStorage();
  const c = client({ fetch: async () => reply(200), storage });
  assert.deepEqual(await c.connect(TOKEN), { ok: true });
  assert.equal(storage.data.token, TOKEN);
  assert.equal(await c.isConnected(), true);
  await c.disconnect();
  assert.equal(await c.isConnected(), false);
});

test("connect rejects empty or odd tokens", async () => {
  const storage = memoryStorage();
  const c = client({ fetch: async () => reply(200), storage });
  for (const bad of ["", "short", null, 42, "has spaces in it but is long enough!!"]) {
    assert.deepEqual(await c.connect(bad), { ok: false, error: "bad_input" }, String(bad));
  }
  assert.equal(storage.data.token, undefined);
});
