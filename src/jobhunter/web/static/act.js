// One-click email links: opening /a/<token> changes nothing; this sends the confirm form.
// Link scanners only fetch the page and do not run scripts, so they never act.
(function () {
  var form = document.getElementById("act-form");
  if (form) form.submit();
})();
