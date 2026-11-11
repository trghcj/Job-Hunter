// The manifest asks for as little as possible, and every file it names exists.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.join(__dirname, "..");
const manifest = JSON.parse(fs.readFileSync(path.join(root, "manifest.json"), "utf8"));
const SERVER = "https://jobhunter-settings.onrender.com/*";

test("manifest version 3", () => {
  assert.equal(manifest.manifest_version, 3);
  assert.equal(manifest.name, "JobHunter bubble");
});

test("permissions are exactly storage", () => {
  assert.deepEqual(manifest.permissions, ["storage"]);
});

test("no broad host permissions", () => {
  const hosts = manifest.host_permissions;
  assert.ok(hosts.includes(SERVER));
  for (const h of hosts) {
    assert.ok(!["<all_urls>", "*://*/*", "https://*/*", "http://*/*"].includes(h), h);
    assert.ok(h.startsWith("https://"), h);
  }
});

test("content scripts only run on listed hosts", () => {
  const hosts = new Set(manifest.host_permissions);
  for (const entry of manifest.content_scripts || []) {
    for (const m of entry.matches) {
      const base = m.replace(/\/[^/]*\*?$/, "/*").replace(/(https:\/\/[^/]+\/).*/, "$1*");
      assert.ok([...hosts].some((h) => h === base || m.startsWith(h.slice(0, -1))), m);
    }
  }
});

test("every file the manifest names exists", () => {
  const files = [manifest.background.service_worker, manifest.action.default_popup,
    ...Object.values(manifest.icons)];
  for (const entry of manifest.content_scripts || []) files.push(...entry.js, ...(entry.css || []));
  for (const f of files) assert.ok(fs.existsSync(path.join(root, f)), f);
});

test("icons exist at all four sizes", () => {
  assert.deepEqual(Object.keys(manifest.icons).sort((a, b) => a - b), ["16", "32", "48", "128"]);
  for (const f of Object.values(manifest.icons)) {
    const png = fs.readFileSync(path.join(root, f));
    assert.equal(png.subarray(1, 4).toString(), "PNG", f);
  }
});

test("server url only in config.js", () => {
  const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap((d) => {
    if (["node_modules", "test", "tools"].includes(d.name)) return [];
    const p = path.join(dir, d.name);
    return d.isDirectory() ? walk(p) : [p];
  });
  const offenders = walk(root).filter((f) => f.endsWith(".js") && !f.endsWith("config.js"))
    .filter((f) => fs.readFileSync(f, "utf8").includes("onrender.com"));
  assert.deepEqual(offenders, []);
});
