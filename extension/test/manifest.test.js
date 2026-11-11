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

test("network access only to the JobHunter server", () => {
  assert.deepEqual(manifest.host_permissions, [SERVER]);
});

const JOB_SITES = new Set([
  "https://www.linkedin.com/jobs/*", "https://www.naukri.com/*", "https://in.indeed.com/*",
  "https://www.indeed.com/*", "https://boards.greenhouse.io/*",
  "https://job-boards.greenhouse.io/*", "https://jobs.lever.co/*", "https://jobs.ashbyhq.com/*",
  "https://*.myworkdayjobs.com/*", "https://jobs.smartrecruiters.com/*",
]);

test("content scripts only run on job sites and the connect page", () => {
  for (const entry of manifest.content_scripts) {
    for (const m of entry.matches) {
      assert.ok(JOB_SITES.has(m) || m === "https://jobhunter-settings.onrender.com/extension/connect*", m);
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
