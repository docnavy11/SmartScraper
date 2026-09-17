/* Theme toggle, log follow, bulk-select. Everything else is server rendered. */
(function () {
  "use strict";

  function currentTheme() {
    var explicit = document.documentElement.getAttribute("data-theme");
    if (explicit) return explicit;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches
      ? "light"
      : "dark";
  }

  var toggle = document.getElementById("theme-toggle");
  if (toggle) {
    toggle.addEventListener("click", function () {
      var next = currentTheme() === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      try {
        localStorage.setItem("ss-theme", next);
      } catch (e) {
        /* private window: the theme still applies for this page */
      }
      toggle.setAttribute(
        "aria-label",
        next === "dark" ? "Switch to light theme" : "Switch to dark theme"
      );
    });
  }

  /* Keep the live log pinned to the newest line while Follow is on. */
  document.body.addEventListener("htmx:sseMessage", function (evt) {
    var box = evt.target.closest(".logbox") || document.getElementById("log-stream");
    if (box && box.dataset.follow !== "off") box.scrollTop = box.scrollHeight;
  });

  var follow = document.getElementById("log-follow");
  if (follow) {
    follow.addEventListener("click", function () {
      var box = document.getElementById("log-stream");
      if (!box) return;
      var on = box.dataset.follow !== "off";
      box.dataset.follow = on ? "off" : "on";
      follow.setAttribute("aria-pressed", String(!on));
      follow.querySelector("span").textContent = on ? "Paused" : "Follow";
    });
  }

  /* Bulk selection: the header checkbox drives the rows, and the count in the
     bulk bar tracks whatever is ticked. */
  document.querySelectorAll("[data-select-all]").forEach(function (master) {
    var scope = document.getElementById(master.getAttribute("data-select-all"));
    if (!scope) return;
    var boxes = function () {
      return Array.prototype.slice.call(scope.querySelectorAll('input[type="checkbox"][name="run_id"]'));
    };
    var count = document.getElementById("bulk-count");
    var sync = function () {
      var n = boxes().filter(function (b) {
        return b.checked;
      }).length;
      if (count) count.textContent = n + " selected";
    };
    master.addEventListener("change", function () {
      boxes().forEach(function (b) {
        b.checked = master.checked;
      });
      sync();
    });
    scope.addEventListener("change", sync);
    sync();
  });
})();
