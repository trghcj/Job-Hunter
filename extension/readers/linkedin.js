// LinkedIn job page (/jobs/view/<id>) or the job open in the search results pane
// (?currentJobId=<id>). LinkedIn's class names are random, so this anchors on the job id,
// the "Company, …" label, the "About the job" heading and the description box; the older
// layout's class names are the fallback.
(function () {
  const JH = (globalThis.JH = globalThis.JH || {});

  const COMPANY = '[aria-label^="Company, "]';
  const OLD = {
    title: ".job-details-jobs-unified-top-card__job-title",
    company: ".job-details-jobs-unified-top-card__company-name",
    location: ".job-details-jobs-unified-top-card__primary-description-container span",
    description: "#job-details",
  };

  function jobId(location) {
    const url = new URL(location.href);
    const view = url.pathname.match(/^\/jobs\/view\/(?:[^/]*-)?(\d+)\/?$/);
    const id = view ? view[1] : url.searchParams.get("currentJobId");
    return /^\d{4,20}$/.test(id || "") ? id : null;
  }

  const closest = (el, test) => {
    while (el && !test(el)) el = el.parentElement;
    return el;
  };
  const idIn = (a) => ((a.getAttribute("href") || "").match(/\/jobs\/view\/(?:[^/?#]*-)?(\d+)/) || [])[1];

  // The paragraphs after the company label, in page order: title, then location line.
  function headerLines(document, marker) {
    const all = [...document.querySelectorAll(`${COMPANY}, p`)];
    return all.slice(all.indexOf(marker) + 1)
      .filter((p) => !marker.contains(p))
      .map((p) => ({ p, text: JH.textOf(p) }))
      .filter(({ text }) => text);
  }

  function readCurrent(document, id) {
    const about = [...document.querySelectorAll("h2")]
      .find((h) => JH.textOf(h) === "About the job");
    const detail = about && closest(about, (el) => el.querySelector(COMPANY));
    if (!detail) return null;
    const marker = detail.querySelector(COMPANY);
    const shown = [...detail.querySelectorAll('a[href*="/jobs/view/"]')].map(idIn).find(Boolean);
    if (shown && shown !== id) return null; // the pane has not switched to this job yet
    const [title, place] = headerLines(document, marker);
    const box = closest(about, (el) => el.querySelector('[data-testid="expandable-text-box"]'));
    const link = marker.querySelector("a");
    return {
      title: title && title.text,
      company: JH.textOf(link || marker),
      location: place && place.text.split(" · ")[0],
      description: box && JH.textOf(box.querySelector('[data-testid="expandable-text-box"]')),
    };
  }

  function readOld(document) {
    const text = (selector) => JH.textOf(document.querySelector(selector));
    return { title: text(OLD.title), company: text(OLD.company), location: text(OLD.location),
      description: text(OLD.description) };
  }

  JH.readLinkedIn = function readLinkedIn(document, location) {
    const id = jobId(location);
    if (!id) return null;
    const found = readCurrent(document, id) || readOld(document);
    return JH.jobInfo({ url: `https://www.linkedin.com/jobs/view/${id}/`, ...found });
  };
})();
