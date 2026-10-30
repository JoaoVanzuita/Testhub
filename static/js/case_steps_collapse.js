/* TestHub case-step collapse: progressive enhancement only.
 *
 * Steps are server-rendered as native <details>, so each one collapses without
 * JavaScript. This script only adds a single "Expandir todos / Recolher todos"
 * control and keeps its label and aria-expanded in sync with the real open
 * state, including when the user toggles an individual step by hand.
 */
(function () {
  "use strict";

  var container = document.querySelector("[data-case-steps]");
  var toggle = document.querySelector("[data-case-steps-toggle]");
  if (!container || !toggle) {
    return;
  }
  // The button is rendered hidden so a no-JS visitor never sees a control that
  // would not work; enabling JS reveals it.
  toggle.hidden = false;

  function detailsList() {
    return Array.prototype.slice.call(
      container.querySelectorAll("[data-case-step]")
    );
  }

  function allOpen() {
    return detailsList().every(function (details) {
      return details.open;
    });
  }

  function sync() {
    var open = allOpen();
    toggle.setAttribute("aria-expanded", String(open));
    toggle.textContent = open ? "Recolher todos" : "Expandir todos";
  }

  toggle.addEventListener("click", function () {
    var open = !allOpen();
    detailsList().forEach(function (details) {
      details.open = open;
    });
    sync();
  });

  // A <details> fires a bubbling "toggle" event whenever its open state
  // changes, including through the native summary click. Keep the control
  // honest when the user expands or collapses a single step by hand.
  container.addEventListener("toggle", function (event) {
    if (
      event.target &&
      typeof event.target.matches === "function" &&
      event.target.matches("[data-case-step]")
    ) {
      sync();
    }
  });

  sync();
})();
