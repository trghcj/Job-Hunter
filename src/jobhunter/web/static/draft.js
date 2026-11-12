// Cover letter page: Copy the letter, confirm before Redo, show progress while writing.
// Nothing is sent anywhere by this script; the forms post to JobHunter as usual.
(function () {
  document.querySelectorAll("[data-copy]").forEach(function (button) {
    button.addEventListener("click", function () {
      var box = document.getElementById(button.getAttribute("data-copy"));
      if (!box || !navigator.clipboard) return;
      navigator.clipboard.writeText(box.value).then(function () {
        var label = button.textContent;
        button.textContent = "Copied ✓";
        setTimeout(function () { button.textContent = label; }, 2000);
      });
    });
  });

  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (!window.confirm(form.getAttribute("data-confirm"))) event.preventDefault();
    });
  });

  document.querySelectorAll("form.busy-on-submit").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (event.defaultPrevented) return;
      var button = form.querySelector("button[type=submit]");
      if (button) { button.disabled = true; button.textContent = "Writing…"; }
    });
  });
})();
