// Naukri job page (/job-listings-…-<id>). JSON-LD first, then the page's own elements.
// Search results, company pages and the home page are not a job.
(function () {
  const JH = (globalThis.JH = globalThis.JH || {});

  const JOB_PATH = /^\/(?:jd\/)?job-listings-[^/]*-\d{6,}\/?$/;
  const PAGE = {
    title: 'h1[class*="jd-header-title"]',
    company: '[class*="jd-header-comp-name"] a, [class*="jd-header-comp-name"]',
    location: '[class*="jhc__location"]',
    description: '[class*="dang-inner-html"]',
  };

  JH.readNaukri = function readNaukri(document, location) {
    const address = new URL(location.href);
    if (!JOB_PATH.test(address.pathname)) return null;
    const url = `https://www.naukri.com${address.pathname}`;
    const fromJsonLd = JH.readJsonLd(document, location);
    if (fromJsonLd) return { ...fromJsonLd, url };
    const text = (selector) => JH.textOf(document.querySelector(selector));
    return JH.jobInfo({ url, title: text(PAGE.title), company: text(PAGE.company),
      location: text(PAGE.location), description: text(PAGE.description) });
  };
})();
