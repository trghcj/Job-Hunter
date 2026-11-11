// Indeed: a /viewjob page, or the job open in the right pane of a search or the home page.
// The job key comes from the address (vjk= / jk=) or, when the address has none, from the
// pane's own company and apply links. JSON-LD first, then the pane's data-testid elements.
(function () {
  const JH = (globalThis.JH = globalThis.JH || {});

  const KEY = /^[0-9a-f]{16}$/i;
  const PANE_LINKS = '[data-testid="company-info-metadata"] a[href*="fromjk="], ' +
    'a[data-testid="viewjob-apply"][href*="jk="]';

  function keyFromAddress(location) {
    const params = new URL(location.href).searchParams;
    return params.get("vjk") || params.get("jk");
  }

  function keyFromPane(document) {
    const link = document.querySelector(PANE_LINKS);
    if (!link) return null;
    const params = new URL(link.getAttribute("href"), "https://in.indeed.com/").searchParams;
    return params.get("fromjk") || params.get("jk");
  }

  function readPane(document) {
    const el = (selector) => document.querySelector(selector);
    const meta = el('[data-testid="company-info-metadata"]');
    const row = meta && meta.firstElementChild;
    const place = row && row.children.length > 1 ? row.lastElementChild : null;
    const title = JH.textOf(el('[data-testid="vj-job-title"]') ||
      el('[data-testid="jobsearch-JobInfoHeader-title"]'));
    return {
      title: title.replace(/ - job post$/i, ""),
      company: JH.textOf((meta && meta.querySelector("a")) ||
        el('[data-testid="inlineHeader-companyName"]')),
      location: JH.textOf(place || el('[data-testid="inlineHeader-companyLocation"]')),
      description: JH.textOf(el(".simple-job-description-html") || el("#jobDescriptionText")),
    };
  }

  JH.readIndeed = function readIndeed(document, location) {
    const fromAddress = keyFromAddress(location);
    const fromPane = keyFromPane(document);
    if (fromAddress && fromPane && fromAddress !== fromPane) return null; // pane not updated yet
    const key = fromAddress || fromPane;
    if (!KEY.test(key || "")) return null;
    const url = `https://${location.hostname}/viewjob?jk=${key.toLowerCase()}`;
    const fromJsonLd = JH.readJsonLd(document, location);
    if (fromJsonLd) return { ...fromJsonLd, url };
    return JH.jobInfo({ url, ...readPane(document) });
  };
})();
