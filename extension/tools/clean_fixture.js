// Cuts a page saved from a job site (test/fixtures/raw/, never committed) down to the job
// area, and removes personal and tracking data, for use as a committed test fixture.
//   node tools/clean_fixture.js <linkedin|indeed|naukri> <raw.html> <out.html>
// Always read the output before committing it.
const fs = require("node:fs");
const { parseHTML } = require("linkedom");

const DROP_TAGS = "script,style,svg,img,picture,iframe,noscript,link,meta,button,input,template,video,source,form";
const KEEP_ATTRS = new Set(["href", "data-testid", "aria-label", "role", "data-jk", "id"]);
// Class names the readers use (Naukri adds a build hash after them: styles_jd-header-title__x).
const KEEP_CLASSES = /^(simple-job-description-html|jcs-JobTitle|styles_(jd-header-title|jd-header-comp-name|jhc__location|JDC__dang-inner-html)__\w+)$/;
const KEEP_PARAMS = new Set(["currentJobId", "jk", "vjk", "fromjk"]);
// Blocks about the signed-in person (their network, school, resume match) or upsells.
const PERSONAL = /people you can reach out|school alumni|connections? work|your profile and|resume|match score|match details|information helpful|top applicant|hiring insights|premium|set alert for similar|meet the hiring team/i;

function cleanHref(href) {
  try {
    const url = new URL(href, "https://example.invalid/");
    for (const key of [...url.searchParams.keys()]) {
      if (!KEEP_PARAMS.has(key)) url.searchParams.delete(key);
    }
    url.hash = "";
    return url.origin === "https://example.invalid" ? "#" : url.toString();
  } catch {
    return "#";
  }
}

function scrub(root) {
  for (const el of root.querySelectorAll(DROP_TAGS)) el.remove();
  for (const el of [root, ...root.querySelectorAll("*")]) {
    for (const { name, value } of [...el.attributes]) {
      if (name === "class") {
        const kept = value.split(/\s+/).filter((c) => KEEP_CLASSES.test(c));
        if (kept.length) el.setAttribute("class", kept.join(" "));
        else el.removeAttribute("class");
      } else if (!KEEP_ATTRS.has(name)) {
        el.removeAttribute(name);
      } else if (name === "href") {
        el.setAttribute("href", cleanHref(value));
      }
    }
  }
}

// Removes the largest block around each personal marker that still leaves `keep` intact.
function dropPersonal(root, keep) {
  const ownText = (el) => [...el.childNodes].filter((n) => n.nodeType === 3)
    .map((n) => n.data).join(" ");
  const hits = [...root.querySelectorAll("h1,h2,h3,h4,p,span,div")].filter((el) =>
    PERSONAL.test(el.children.length === 0 ? el.textContent : ownText(el)));
  for (const hit of hits) {
    if (!root.contains(hit)) continue;
    let block = hit;
    while (block.parentElement && block.parentElement !== root &&
      !keep.some((k) => block.parentElement.contains(k))) block = block.parentElement;
    if (!keep.some((k) => block.contains(k))) block.remove();
  }
}

const climb = (el, ok) => { while (el && !ok(el)) el = el.parentElement; return el; };

function linkedinRoot(document) {
  const about = [...document.querySelectorAll("h2")]
    .find((h) => h.textContent.trim() === "About the job");
  if (!about) {
    // A jobs page with no job open: keep one list of job cards.
    const list = [...document.querySelectorAll("h2")]
      .find((h) => /jobs that match your profile/i.test(h.textContent));
    const cards = 'a[href*="currentJobId="]';
    const root = climb(list, (el) => el.querySelectorAll(cards).length > 1);
    return { root, keep: [list, ...root.querySelectorAll(cards)] };
  }
  const marker = '[aria-label^="Company, "]';
  const root = climb(about, (el) => el.querySelector(marker));
  const jobTitle = document.title.split(" | ")[0].trim();
  const header = climb(root.querySelector(marker), (el) => el.textContent.includes(jobTitle) &&
    el.textContent.replace(/\s+/g, " ").length > jobTitle.length + 30);
  const description = about.parentElement.nextElementSibling;
  return { root, keep: [about, header, description].filter(Boolean) };
}

function indeedRoot(document) {
  const root = document.querySelector("#jobsearch-ViewjobPaneWrapper") ||
    document.querySelector('[data-testid="viewjob-main-content"]');
  const keep = ['[data-testid="desktop-job-header"]', '[data-testid="vj-job-description-heading"]',
    ".simple-job-description-html"].map((s) => root.querySelector(s)).filter(Boolean);
  return { root, keep };
}

function naukriRoot(document) {
  const jsonld = [...document.querySelectorAll('script[type="application/ld+json"]')]
    .filter((s) => /JobPosting/.test(s.textContent));
  const title = document.querySelector('h1[class*="jd-header-title"]');
  if (title) {
    const description = document.querySelector('[class*="dang-inner-html"]');
    const root = climb(title, (el) => el.contains(description));
    return { root, keep: [title, description], jsonld };
  }
  // A search results page: keep the list of job cards.
  const cards = 'a[href*="/job-listings-"]';
  const root = climb(document.querySelector(cards), (el) => el.querySelectorAll(cards).length > 2);
  return { root, keep: [...root.querySelectorAll(cards)], jsonld };
}

function main([site, input, output]) {
  const { document } = parseHTML(fs.readFileSync(input, "utf8"));
  const pick = { linkedin: linkedinRoot, indeed: indeedRoot, naukri: naukriRoot }[site];
  if (!pick) throw new Error("site must be linkedin, indeed or naukri");
  const title = document.title.replace(/[<>&]/g, "");
  const { root, keep, jsonld = [] } = pick(document);
  const scripts = jsonld.map((s) => `<script type="application/ld+json">${
    s.textContent.trim().replace(/</g, "\\u003c")}</script>`).join("\n");
  dropPersonal(root, keep);
  scrub(root);
  const html = `<!doctype html>\n<html><head><meta charset="utf-8"><title>${title}</title>\n${
    scripts}</head><body>\n${root.outerHTML}\n</body></html>\n`;
  fs.writeFileSync(output, html);
  console.log(`${output}: ${html.length} bytes`);
}

main(process.argv.slice(2));
