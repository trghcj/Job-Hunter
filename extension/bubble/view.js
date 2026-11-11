// What the bubble shows, as plain data. No DOM here, so every state is easy to test.
(function () {
  const JH = (globalThis.JH = globalThis.JH || {});

  const ERRORS = {
    waking_failed: "JobHunter didn't answer, try again",
    rate_limited: "Too many checks right now, try again later",
    bad_input: "This page couldn't be read as a job",
    server: "Something went wrong at JobHunter",
  };

  function seenText(seen) {
    if (!seen) return null;
    if (seen.status === "applied") return "You applied to this job";
    if (seen.status === "saved") return "Already saved";
    if (seen.status === "hidden") return "You hid this job";
    if (seen.emailed) return "Already in your daily email";
    if (seen.listed) return "Already in My jobs";
    return null;
  }

  function jobLines(job) {
    if (!job) return { title: null, subtitle: null };
    return { title: job.title, subtitle: [job.company, job.location].filter(Boolean).join(" · ") };
  }

  JH.viewModel = function viewModel(state) {
    const base = { ring: "pending", dot: false, headline: "", seen: null, actions: [],
      showConnect: false, ...jobLines(state.job) };
    switch (state.kind) {
      case "nojob":
        return { ...base, title: null, subtitle: null, headline: "Open a job to check it" };
      case "idle":
        return { ...base, headline: "Click Check to compare this job with your filters",
          actions: [{ id: "check", label: "Check" }] };
      case "checking":
        return { ...base, headline: state.slow
          ? "Waking up JobHunter, this can take up to a minute…" : "Checking…" };
      case "result": {
        const { verdict, reasons, seen } = state.result;
        const match = verdict === "match";
        const status = seen && seen.status;
        return {
          ...base,
          ring: match ? "match" : "nomatch",
          dot: Boolean(seenText(seen)),
          seen: seenText(seen),
          headline: match ? "Matches your filters"
            : `Not a match: ${(reasons && reasons[0]) || "outside your filters"}`,
          actions: [
            { id: "save", label: status === "saved" ? "Saved ✓" : "Save to JobHunter",
              disabled: status === "saved" || status === "applied" },
            { id: "applied", label: status === "applied" ? "Applied ✓" : "Mark applied",
              disabled: status === "applied" },
          ],
        };
      }
      case "error":
        if (state.error === "not_connected") {
          return { ...base, headline: "Connect JobHunter", showConnect: true };
        }
        return { ...base, headline: ERRORS[state.error] || ERRORS.server,
          actions: state.error === "waking_failed" ? [{ id: "retry", label: "Try again" }] : [] };
      default:
        return base;
    }
  };
})();
