// Site readers turn the open job page into JobInfo { url, title, company, location, description }.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { parseHTML, DOMParser } = require("linkedom");

globalThis.DOMParser = DOMParser;
globalThis.JH = {};
for (const f of ["common", "jsonld", "index"]) require(`../readers/${f}.js`);
const JH = globalThis.JH;

const FIXTURES = path.join(__dirname, "fixtures");
function page(name, url) {
  const { document } = parseHTML(fs.readFileSync(path.join(FIXTURES, name), "utf8"));
  return [document, new URL(url)];
}

test("careers greenhouse from JSON-LD", () => {
  const job = JH.readJob(...page("careers_greenhouse.html",
    "https://job-boards.greenhouse.io/groww/jobs/4012345?utm_source=x&gh_src=y#apply"));
  assert.deepEqual(job, {
    url: "https://job-boards.greenhouse.io/groww/jobs/4012345?gh_src=y",
    title: "Backend Engineer",
    company: "Groww",
    location: "Bengaluru, Karnataka, IN",
    description: "Build APIs in Go and Python. 0-2 years of experience.",
  });
});

test("careers workday with @graph, many places, hostile description", () => {
  const job = JH.readJob(...page("careers_workday.html",
    "https://example.wd5.myworkdayjobs.com/en-US/Careers/job/Hyderabad/Software-Engineer-I_R123"));
  assert.equal(job.title, "Software Engineer I");
  assert.equal(job.company, "Example Systems");
  assert.equal(job.location, "Hyderabad, India; Pune, India");
  assert.equal(job.description, "Write Java services Fresh graduates welcome");
  assert.ok(!job.description.includes("alert"));
});

test("remote JSON-LD location", () => {
  const html = `<script type="application/ld+json">${JSON.stringify({
    "@type": "JobPosting", title: "AI Engineer", hiringOrganization: { name: "Remote Co" },
    jobLocationType: "TELECOMMUTE", applicantLocationRequirements: { name: "India" } })}</script>`;
  const { document } = parseHTML(html);
  const job = JH.readJsonLd(document, new URL("https://jobs.lever.co/remoteco/abc"));
  assert.equal(job.location, "Remote (India)");
});

test("JobPosting typed as an array is found", () => {
  const html = `<script type="application/ld+json">${JSON.stringify([
    { "@type": ["JobPosting"], title: "Data Engineer", hiringOrganization: { name: "D" } }])}</script>`;
  const { document } = parseHTML(html);
  assert.equal(JH.readJsonLd(document, new URL("https://jobs.ashbyhq.com/d/1")).title,
    "Data Engineer");
});

test("description trimmed to 20000", () => {
  const html = `<script type="application/ld+json">${JSON.stringify({
    "@type": "JobPosting", title: "T", hiringOrganization: { name: "C" },
    description: "word ".repeat(10000) })}</script>`;
  const { document } = parseHTML(html);
  const job = JH.readJsonLd(document, new URL("https://jobs.lever.co/c/1"));
  assert.equal(job.description.length, 20000);
});

test("title and company trimmed to 300", () => {
  assert.equal(JH.trim("x".repeat(400), 300).length, 300);
  assert.equal(JH.trim("  a   b \n c  ", 300), "a b c");
  assert.equal(JH.trim(null, 300), null);
});

test("page without JobPosting is not a job", () => {
  const { document } = parseHTML("<h1>Our careers</h1>");
  assert.equal(JH.readJob(document, new URL("https://jobs.lever.co/acme")), null);
});

test("JobPosting without title or company is not a job", () => {
  const html = `<script type="application/ld+json">${JSON.stringify({
    "@type": "JobPosting", title: "Only a title" })}</script>`;
  const { document } = parseHTML(html);
  assert.equal(JH.readJob(document, new URL("https://jobs.lever.co/acme/1")), null);
});

test("readJob ignores unsupported hosts", () => {
  assert.equal(JH.readJob(...page("careers_greenhouse.html", "https://example.com/jobs/1")), null);
});
