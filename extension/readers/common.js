// Shared helpers for the site readers. Classic script: everything hangs off globalThis.JH.
// Readers only look at the page that is already open; they never click or fetch anything.
(function () {
  const JH = (globalThis.JH = globalThis.JH || {});

  JH.LIMITS = { title: 300, company: 300, location: 300, description: 20000 };

  // Collapse whitespace and cut to `max` characters; null for empty input.
  JH.trim = function trim(text, max) {
    if (text === null || text === undefined) return null;
    const clean = String(text).replace(/\s+/g, " ").trim();
    return clean ? clean.slice(0, max) : null;
  };

  const SKIP = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "SVG"]);
  const BLOCK = new Set(["P", "DIV", "LI", "UL", "OL", "BR", "H1", "H2", "H3", "H4", "H5",
    "H6", "TR", "TD", "TH", "TABLE", "SECTION", "ARTICLE", "HEADER", "FOOTER", "DD", "DT",
    "BLOCKQUOTE", "PRE"]);

  function collectText(node, out) {
    for (const child of node.childNodes || []) {
      if (child.nodeType === 3) {
        out.push(child.data);
      } else if (child.nodeType === 1) {
        const tag = String(child.tagName).toUpperCase();
        if (SKIP.has(tag)) continue;
        const block = BLOCK.has(tag);
        if (block) out.push(" ");
        collectText(child, out);
        if (block) out.push(" ");
      }
    }
    return out;
  }

  // Visible text of an element: inline text joined as written, blocks separated by a space.
  JH.textOf = function textOf(node) {
    return node ? collectText(node, []).join("").replace(/\s+/g, " ").trim() : "";
  };

  // HTML (possibly entity-escaped once more, as some JSON-LD is) to plain text. Uses an
  // inert parsed document: nothing in it loads or runs.
  JH.htmlToText = function htmlToText(html) {
    if (!html) return "";
    let text = String(html);
    for (let i = 0; i < 2 && /<\/?[a-z!][^>]*>|&[a-z#0-9]+;/i.test(text); i += 1) {
      const doc = new globalThis.DOMParser().parseFromString(
        `<!doctype html><html><body>${text}</body></html>`, "text/html");
      text = JH.textOf(doc.body);
    }
    return text.replace(/\s+/g, " ").trim();
  };

  const TRACKING = /^(utm_|gclid$|fbclid$|trk|trackingId$|refId$|src$|xid$|uid$|alertId$)/i;

  // The page address without the #fragment and without tracking parameters.
  JH.cleanUrl = function cleanUrl(location) {
    const url = new URL(location.href);
    url.hash = "";
    for (const key of [...url.searchParams.keys()]) {
      if (TRACKING.test(key)) url.searchParams.delete(key);
    }
    return url.toString();
  };

  // A JobInfo, or null when the essentials (title and company) are missing.
  JH.jobInfo = function jobInfo({ url, title, company, location, description }) {
    const L = JH.LIMITS;
    const info = {
      url,
      title: JH.trim(title, L.title),
      company: JH.trim(company, L.company),
      location: JH.trim(location, L.location),
      description: JH.trim(description, L.description),
    };
    return info.url && info.title && info.company ? info : null;
  };
})();
