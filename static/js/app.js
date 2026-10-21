/* TestHub base JavaScript: progressive enhancement only.
 * Server-side validation, authorization and persistence remain authoritative. */
(function () {
  "use strict";

  // --- Confirmation modal for destructive actions (data-confirm) ----------
  // Replaces window.confirm with an accessible dialog when the markup is
  // present. Without JavaScript the form still submits through its own POST
  // (the previous behaviour: data-confirm is JS-only).
  var modal = document.querySelector("[data-confirm-modal]");
  var modalMessage = modal ? modal.querySelector("[data-confirm-message]") : null;
  var modalAccept = modal ? modal.querySelector("[data-confirm-accept]") : null;
  // The scrim also carries ``data-confirm-cancel``; target the button so focus
  // can actually move into the dialog when it opens (a bare <div> is not
  // focusable).
  var modalCancel = modal
    ? modal.querySelector("button[data-confirm-cancel]")
    : null;
  var pendingForm = null;
  var pendingSubmitter = null;

  function modalFocusables() {
    if (!modal) {
      return [];
    }
    return Array.prototype.slice.call(
      modal.querySelectorAll(
        'a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])'
      )
    );
  }

  function isModalOpen() {
    return !!modal && !modal.hidden;
  }

  function openConfirm(form, submitter) {
    pendingForm = form;
    pendingSubmitter = submitter || null;
    var message = form.getAttribute("data-confirm") || "";
    if (modalMessage) {
      modalMessage.textContent = message;
    }
    if (modalAccept) {
      var label = submitter && submitter.textContent ? submitter.textContent.trim() : "";
      modalAccept.textContent = label || "Confirmar";
    }
    modal.hidden = false;
    document.body.classList.add("modal-open");
    if (modalCancel) {
      modalCancel.focus();
    }
  }

  function closeConfirm(returnFocus) {
    if (!modal) {
      return;
    }
    modal.hidden = true;
    document.body.classList.remove("modal-open");
    var target = pendingSubmitter;
    pendingForm = null;
    pendingSubmitter = null;
    if (returnFocus && target && typeof target.focus === "function") {
      target.focus();
    }
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    if (form.getAttribute("data-confirmed") === "true") {
      return;
    }
    var message = form.getAttribute("data-confirm");
    if (!message) {
      return;
    }
    if (!modal) {
      // Fallback to the native confirmation when no dialog markup exists.
      if (window.confirm(message)) {
        form.setAttribute("data-confirmed", "true");
      } else {
        event.preventDefault();
      }
      return;
    }
    event.preventDefault();
    openConfirm(form, event.submitter || null);
  });

  if (modal) {
    modal.addEventListener("click", function (event) {
      if (event.target.closest("[data-confirm-cancel]")) {
        event.preventDefault();
        closeConfirm(true);
        return;
      }
      if (event.target.closest("[data-confirm-accept]")) {
        event.preventDefault();
        var form = pendingForm;
        var submitter = pendingSubmitter;
        if (!form) {
          closeConfirm(true);
          return;
        }
        form.setAttribute("data-confirmed", "true");
        closeConfirm(false);
        if (typeof form.requestSubmit === "function") {
          form.requestSubmit(submitter || undefined);
        } else {
          form.submit();
        }
      }
    });

    document.addEventListener("keydown", function (event) {
      if (!isModalOpen()) {
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        closeConfirm(true);
        return;
      }
      if (event.key !== "Tab") {
        return;
      }
      var items = modalFocusables();
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
  }

  // --- Focusable error summary (design system §5.2) ----------------------
  // Move focus to the summary once it is rendered and focus the linked control
  // when a summary link is activated, so the focus always reaches the field.
  var errorSummary = document.getElementById("form-errors");
  if (errorSummary) {
    errorSummary.focus();
    errorSummary.addEventListener("click", function (event) {
      var link =
        event.target && event.target.closest
          ? event.target.closest('a[href^="#"]')
          : null;
      if (!link) {
        return;
      }
      var target = document.getElementById(link.getAttribute("href").slice(1));
      if (!target) {
        return;
      }
      event.preventDefault();
      if (typeof target.focus === "function") {
        target.focus();
      }
      if (typeof target.scrollIntoView === "function") {
        target.scrollIntoView({ block: "center" });
      }
    });
  }

  // Remove the busy state from submit buttons once a navigation is cancelled.
  window.addEventListener("pageshow", function () {
    document.querySelectorAll("[data-busy='true']").forEach(function (element) {
      element.removeAttribute("data-busy");
      element.removeAttribute("aria-busy");
      element.disabled = false;
    });
  });
})();
