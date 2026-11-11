// Reads a schema.org JobPosting from the page's JSON-LD (careers sites, and Naukri/Indeed
// when they include it).
(function () {
  const JH = (globalThis.JH = globalThis.JH || {});

  function isJobPosting(node) {
    const type = node && node["@type"];
    return Array.isArray(type) ? type.includes("JobPosting") : type === "JobPosting";
  }

  function* nodes(value) {
    if (Array.isArray(value)) {
      for (const item of value) yield* nodes(item);
    } else if (value && typeof value === "object") {
      yield value;
      if (value["@graph"]) yield* nodes(value["@graph"]);
    }
  }

  function findPosting(document) {
    for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
      let data;
      try {
        data = JSON.parse(script.textContent);
      } catch {
        continue; // broken JSON-LD on the page: skip it
      }
      for (const node of nodes(data)) if (isJobPosting(node)) return node;
    }
    return null;
  }

  const nameOf = (v) => (typeof v === "string" ? v : v && (v.name || v["@id"])) || "";

  function placeText(place) {
    const a = (place && place.address) || {};
    if (typeof a === "string") return a;
    // Some sites (Naukri) give lists of places and "-" for an empty value.
    const parts = [a.addressLocality, a.addressRegion, a.addressCountry]
      .flatMap((v) => [].concat(v || [])).map((v) => nameOf(v).trim())
      .filter((v) => v && v !== "-");
    return parts.filter((p, i) => parts.indexOf(p) === i).join(", ");
  }

  function locationOf(posting) {
    const places = [].concat(posting.jobLocation || []).map(placeText).filter(Boolean);
    const remote = String(posting.jobLocationType || "").toUpperCase() === "TELECOMMUTE";
    if (!remote) return places.join("; ");
    if (places.length) return `${places.join("; ")} (Remote)`;
    const where = [].concat(posting.applicantLocationRequirements || []).map(nameOf)
      .filter(Boolean).join(", ");
    return where ? `Remote (${where})` : "Remote";
  }

  JH.readJsonLd = function readJsonLd(document, location) {
    const posting = findPosting(document);
    if (!posting) return null;
    return JH.jobInfo({
      url: JH.cleanUrl(location),
      title: JH.htmlToText(posting.title),
      company: nameOf(posting.hiringOrganization),
      location: locationOf(posting),
      description: JH.htmlToText(posting.description),
    });
  };
})();
