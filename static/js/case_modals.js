/* TestHub case-modal open/close — shared by the live runner and the completed
 * (read-only) ledger. Progressive enhancement only: each case is server-rendered
 * inside a closed native <dialog> and the board cards (`data-case-open`) reveal
 * it as a modal; the header close control (`data-case-modal-close`) dismisses
 * it. Without JavaScript the cards are inert and the cases stay collapsed.
 */
(function () {
  "use strict";

  var modals = Array.prototype.slice.call(
    document.querySelectorAll("[data-case-modal]")
  );

  function openModal(pk) {
    for (var i = 0; i < modals.length; i += 1) {
      if (modals[i].getAttribute("data-pk") === String(pk)) {
        if (typeof modals[i].showModal === "function") {
          modals[i].showModal();
        }
        return;
      }
    }
  }

  document.querySelectorAll("[data-case-open]").forEach(function (trigger) {
    trigger.addEventListener("click", function (event) {
      event.preventDefault();
      openModal(trigger.getAttribute("data-case-open"));
    });
  });

  document.querySelectorAll("[data-case-modal-close]").forEach(function (button) {
    button.addEventListener("click", function () {
      var dialog = button.closest("[data-case-modal]");
      if (dialog && dialog.open) {
        dialog.close();
      }
    });
  });
})();
