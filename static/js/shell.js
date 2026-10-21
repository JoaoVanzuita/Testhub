/* TestHub application shell: mobile navigation drawer.
 *
 * Progressive enhancement only. The navigation links exist in the DOM without
 * JavaScript; this script adds an accessible open/close control for the
 * off-canvas sidebar on small screens (below the 64rem breakpoint), moving
 * focus into the drawer when it opens, keeping Tab/Shift+Tab inside it while
 * it is open, and returning focus to the toggle when it closes. No client-side
 * routing is introduced.
 */
(function () {
  "use strict";

  var toggle = document.querySelector("[data-drawer-toggle]");
  var sidebar = document.getElementById("sidebar");
  if (!toggle || !sidebar) {
    return;
  }

  var scrim = document.querySelector("[data-drawer-scrim]");
  var desktop = window.matchMedia("(min-width: 64rem)");

  function isOpen() {
    return toggle.getAttribute("aria-expanded") === "true";
  }

  function focusables() {
    return Array.prototype.slice.call(
      sidebar.querySelectorAll(
        'a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])'
      )
    );
  }

  function firstFocusable() {
    return focusables()[0] || null;
  }

  function open() {
    sidebar.classList.add("is-open");
    toggle.setAttribute("aria-expanded", "true");
    document.body.classList.add("drawer-open");
    var target = firstFocusable();
    if (target) {
      target.focus();
    }
  }

  function close(returnFocus) {
    sidebar.classList.remove("is-open");
    toggle.setAttribute("aria-expanded", "false");
    document.body.classList.remove("drawer-open");
    if (returnFocus) {
      toggle.focus();
    }
  }

  toggle.addEventListener("click", function () {
    if (isOpen()) {
      close(true);
    } else {
      open();
    }
  });

  if (scrim) {
    scrim.addEventListener("click", function () {
      close(true);
    });
  }

  // Close after navigating from a drawer link.
  sidebar.addEventListener("click", function (event) {
    if (event.target && event.target.closest && event.target.closest("a")) {
      close(false);
    }
  });

  // Escape closes and returns focus; Tab and Shift+Tab stay inside the drawer
  // while it is open. No handler acts when the drawer is closed, so the rest of
  // the page is never trapped.
  document.addEventListener("keydown", function (event) {
    if (!isOpen()) {
      return;
    }
    if (event.key === "Escape") {
      close(true);
      return;
    }
    if (event.key !== "Tab") {
      return;
    }
    var items = focusables();
    if (!items.length) {
      return;
    }
    var first = items[0];
    var last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });

  // Reset the drawer when growing to the desktop layout.
  function handleBreakpoint() {
    if (desktop.matches && isOpen()) {
      close(false);
    }
  }

  if (typeof desktop.addEventListener === "function") {
    desktop.addEventListener("change", handleBreakpoint);
  } else if (typeof desktop.addListener === "function") {
    desktop.addListener(handleBreakpoint);
  }
})();
