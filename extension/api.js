// JobHunter server client, used only by the service worker. Every call resolves to
// { ok: true, data } or { ok: false, error } with error one of:
// not_connected, waking_failed, bad_input, rate_limited, server.

const TOKEN_KEY = "token";
const TOKEN_SHAPE = /^[A-Za-z0-9_-]{30,200}$/;
const RETRY_STATUSES = new Set([502, 503, 504]); // Render answers these while waking up

export function makeClient({ fetch, storage, baseUrl, sleep, timeoutMs = 70000,
  retryDelayMs = 5000 }) {
  async function once(token, method, path, body) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await fetch(`${baseUrl}${path}`, {
        method,
        credentials: "omit",
        cache: "no-store",
        signal: controller.signal,
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } finally {
      clearTimeout(timer);
    }
  }

  async function request(method, path, body) {
    const token = await storage.get(TOKEN_KEY);
    if (!token) return { ok: false, error: "not_connected" };
    let response = null;
    for (let attempt = 1; attempt <= 2; attempt += 1) {
      try {
        response = await once(token, method, path, body);
        if (!RETRY_STATUSES.has(response.status)) break;
      } catch {
        response = null; // network error or timeout: the server is probably asleep
      }
      if (attempt === 1) await sleep(retryDelayMs);
    }
    if (!response || RETRY_STATUSES.has(response.status)) {
      return { ok: false, error: "waking_failed" };
    }
    if (response.status === 401) {
      await storage.remove(TOKEN_KEY);
      return { ok: false, error: "not_connected" };
    }
    if (response.status === 422) return { ok: false, error: "bad_input" };
    if (response.status === 429) return { ok: false, error: "rate_limited" };
    if (!response.ok) return { ok: false, error: "server" };
    return { ok: true, data: await response.json() };
  }

  return {
    check: (job) => request("POST", "/api/v1/check", job),
    save: (job, status) => request("POST", "/api/v1/jobs", { ...job, status }),
    matches: (limit = 5) => request("GET", `/api/v1/matches?limit=${Number(limit)}`),
    me: () => request("GET", "/api/v1/me"),
    async connect(token) {
      if (typeof token !== "string" || !TOKEN_SHAPE.test(token)) {
        return { ok: false, error: "bad_input" };
      }
      await storage.set(TOKEN_KEY, token);
      return { ok: true };
    },
    disconnect: () => storage.remove(TOKEN_KEY),
    isConnected: async () => Boolean(await storage.get(TOKEN_KEY)),
  };
}
