// Picks the reader for the current site. Returns JobInfo, or null when no job is open.
(function () {
  const JH = (globalThis.JH = globalThis.JH || {});

  const CAREERS = new Set(["boards.greenhouse.io", "job-boards.greenhouse.io", "jobs.lever.co",
    "jobs.ashbyhq.com", "jobs.smartrecruiters.com"]);

  function readerFor(host) {
    if (host === "www.linkedin.com") return JH.readLinkedIn;
    if (host === "www.naukri.com") return JH.readNaukri;
    if (host === "in.indeed.com" || host === "www.indeed.com") return JH.readIndeed;
    if (CAREERS.has(host) || host.endsWith(".myworkdayjobs.com")) return JH.readJsonLd;
    return null;
  }

  JH.readJob = function readJob(document, location) {
    const reader = readerFor(location.hostname.toLowerCase());
    if (!reader) return null;
    try {
      return reader(document, location);
    } catch {
      return null; // a site changed its page: show "Open a job", never break the page
    }
  };
})();
