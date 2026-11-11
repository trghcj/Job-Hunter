// Site readers turn the open job page into JobInfo { url, title, company, location, description }.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { parseHTML, DOMParser } = require("linkedom");

globalThis.DOMParser = DOMParser;
globalThis.JH = {};
for (const f of ["common", "jsonld", "linkedin", "naukri", "indeed", "index"]) require(`../readers/${f}.js`);
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

// --- LinkedIn (fixtures cut from real pages; the reader never uses LinkedIn's class names) ---

const LI_JOB = {
  url: "https://www.linkedin.com/jobs/view/4448325376/",
  title: "Application Developer",
  company: "Accenture in India",
  location: "Noida, Uttar Pradesh, India",
};

test("linkedin job page", () => {
  const job = JH.readJob(...page("linkedin_job.html",
    "https://www.linkedin.com/jobs/view/4448325376/?alternateChannel=search&refId=x&trackingId=y"));
  assert.deepEqual({ ...job, description: undefined }, { ...LI_JOB, description: undefined });
  assert.ok(job.description.startsWith("Project Role : Application Developer Project Role"));
  assert.ok(job.description.includes("Minimum 3 Year(s) Of Experience Is Required"));
});

test("linkedin right pane uses currentJobId", () => {
  const job = JH.readJob(...page("linkedin_pane.html",
    "https://www.linkedin.com/jobs/search-results/?currentJobId=4448325376&keywords=jobs"));
  assert.deepEqual({ ...job, description: undefined }, { ...LI_JOB, description: undefined });
});

test("linkedin pane still showing the previous job is not read", () => {
  assert.equal(JH.readJob(...page("linkedin_pane.html",
    "https://www.linkedin.com/jobs/search-results/?currentJobId=4400000001")), null);
});

test("test_linkedin_feed_is_not_a_job", () => {
  assert.equal(JH.readJob(...page("linkedin_feed.html", "https://www.linkedin.com/jobs/")), null);
});

test("linkedin job id before the details have loaded", () => {
  const { document } = parseHTML("<html><body><main></main></body></html>");
  assert.equal(JH.readLinkedIn(document, new URL("https://www.linkedin.com/jobs/view/123/")), null);
});

test("linkedin slug job address and older page layout", () => {
  const { document } = parseHTML(`<html><body>
    <h1 class="job-details-jobs-unified-top-card__job-title">ML Engineer</h1>
    <div class="job-details-jobs-unified-top-card__company-name"><a href="/company/x/">Acme</a></div>
    <div class="job-details-jobs-unified-top-card__primary-description-container">
      <span>Pune, Maharashtra, India</span> · <span>2 days ago</span></div>
    <div id="job-details"><p>Train models.</p></div></body></html>`);
  const job = JH.readLinkedIn(document,
    new URL("https://www.linkedin.com/jobs/view/ml-engineer-at-acme-3900000001"));
  assert.deepEqual(job, { url: "https://www.linkedin.com/jobs/view/3900000001/",
    title: "ML Engineer", company: "Acme", location: "Pune, Maharashtra, India",
    description: "Train models." });
});

// --- Indeed ---

const INDEED_JOB = {
  url: "https://in.indeed.com/viewjob?jk=61ec1518af24d6b6",
  title: "Engineer Associate, Software",
  company: "Giant Eagle",
  location: "Bengaluru, Karnataka",
};

test("indeed job open in the right pane, no id in the address", () => {
  const job = JH.readJob(...page("indeed_job.html", "https://in.indeed.com/"));
  assert.deepEqual({ ...job, description: undefined }, { ...INDEED_JOB, description: undefined });
  assert.ok(job.description.startsWith("Job Summary As an Associate Software Engineer"));
  assert.ok(!job.description.includes("@layer"));
});

test("indeed vjk in the address", () => {
  const job = JH.readJob(...page("indeed_job.html",
    "https://in.indeed.com/jobs?q=python&vjk=61ec1518af24d6b6&from=x"));
  assert.equal(job.url, INDEED_JOB.url);
});

test("indeed pane still showing the previous job is not read", () => {
  assert.equal(JH.readJob(...page("indeed_job.html",
    "https://in.indeed.com/jobs?q=python&vjk=0000000000000001")), null);
});

test("indeed viewjob page uses JSON-LD first", () => {
  const html = `<script type="application/ld+json">${JSON.stringify({
    "@type": "JobPosting", title: "Data Analyst", hiringOrganization: { name: "Zeta" },
    jobLocation: { address: { addressLocality: "Hyderabad", addressCountry: "IN" } },
    description: "<p>SQL daily</p>" })}</script>`;
  const { document } = parseHTML(html);
  const job = JH.readJob(document, new URL("https://www.indeed.com/viewjob?jk=abcdef0123456789&tk=1"));
  assert.deepEqual(job, { url: "https://www.indeed.com/viewjob?jk=abcdef0123456789",
    title: "Data Analyst", company: "Zeta", location: "Hyderabad, IN", description: "SQL daily" });
});

test("indeed home with no job open", () => {
  const { document } = parseHTML("<html><body><h1>Jobs for you</h1></body></html>");
  assert.equal(JH.readJob(document, new URL("https://in.indeed.com/")), null);
});

// --- Naukri ---

const NAUKRI_URL = "https://www.naukri.com/job-listings-software-engineer-software-developer-" +
  "netm-corporate-solutions-hyderabad-bengaluru-india-0-to-5-years-111126028176";

test("naukri job page", () => {
  const job = JH.readJob(...page("naukri_job.html", `${NAUKRI_URL}?src=jobsearchDesk&sid=1&xp=1`));
  assert.deepEqual({ ...job, description: undefined }, { url: NAUKRI_URL,
    title: "Software Engineer / Software Developer", company: "Netm Corporate Solutions",
    location: "Bengaluru, Hyderabad, India", description: undefined });
  assert.ok(job.description.startsWith("NetM Corporate Solutions Pvt. Ltd. is hiring"));
});

test("naukri job page without JSON-LD reads the page", () => {
  const [document, location] = page("naukri_job.html", NAUKRI_URL);
  for (const s of document.querySelectorAll('script[type="application/ld+json"]')) s.remove();
  const job = JH.readJob(document, location);
  assert.equal(job.title, "Software Engineer / Software Developer");
  assert.equal(job.company, "NetM Corporate Solutions");
  assert.equal(job.location, "Bengaluru, Hyderabad, India");
  assert.ok(job.description.startsWith("NetM Corporate Solutions Pvt. Ltd. is hiring"));
});

test("test_naukri_search_is_not_a_job", () => {
  assert.equal(JH.readJob(...page("naukri_search.html",
    "https://www.naukri.com/software-developer-jobs?k=software%20developer")), null);
});

test("JSON-LD address given as lists with '-' placeholders", () => {
  const html = `<script type="application/ld+json">${JSON.stringify({
    "@type": "JobPosting", title: "QA", hiringOrganization: { name: "Q" },
    jobLocation: { address: { addressLocality: ["Pune", "Mumbai"], addressRegion: ["Pune", "-"],
      addressCountry: "-" } } })}</script>`;
  const { document } = parseHTML(html);
  assert.equal(JH.readJsonLd(document, new URL("https://jobs.lever.co/q/1")).location, "Pune, Mumbai");
});

// --- Fixtures ---

test("fixtures contain no personal data", () => {
  const banned = [/@gmail\.com/i, /miniprofile/i, /trackingId=/i, /refId=/i, /licdn\.com/i,
    /<img/i, /alumni/i, /connections? work/i, /reach out to/i, /[?&](tk|mobtk)=/i];
  for (const name of fs.readdirSync(FIXTURES).filter((f) => f.endsWith(".html"))) {
    const html = fs.readFileSync(path.join(FIXTURES, name), "utf8");
    for (const re of banned) assert.ok(!re.test(html), `${name} matches ${re}`);
  }
});
