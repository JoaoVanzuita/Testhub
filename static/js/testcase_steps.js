/* TestHub ordered-step editor: progressive enhancement only.
 *
 * The server normalizes and validates step order; this script only improves the
 * editing experience by reordering rows, renumbering the "position" controls,
 * cloning the empty formset form when a new step is added and keeping the
 * move/remove affordances honest (boundary buttons disabled, focus preserved
 * after dynamic operations, one polite announcement per change).
 *
 * The formset contract is untouched: `data-step-*`, the generated field names
 * and ids, `__prefix__` and the management form keep working exactly as before.
 */
(function () {
  "use strict";

  function stepRows(container) {
    return Array.prototype.slice.call(
      container.querySelectorAll("[data-step-row]")
    );
  }

  function isDeleted(row) {
    var box = row.querySelector('input[name$="-DELETE"]');
    return Boolean(box && box.checked);
  }

  function activeRows(container) {
    return stepRows(container).filter(function (row) {
      return !isDeleted(row) && !row.hidden;
    });
  }

  function fieldValue(row, suffix) {
    var input = row.querySelector('[name$="-' + suffix + '"]');
    return input ? String(input.value).trim() : "";
  }

  // A freshly added (or server-rendered extra) step with no content yet. Its
  // position is cleared on submit so Django's `empty_permitted` ignores it and
  // an untouched row never blocks the form.
  function isNewEmptyRow(row) {
    if (fieldValue(row, "id")) {
      return false;
    }
    return !fieldValue(row, "action") && !fieldValue(row, "expected_result");
  }

  function countText(total) {
    return total === 1 ? "1 passo" : total + " passos";
  }

  function stepNumber(row) {
    var index = row.querySelector("[data-step-index]");
    return index ? index.textContent.trim() : "";
  }

  function setStatus(formset, message) {
    var status = formset.querySelector("[data-steps-status]");
    if (status) {
      status.textContent = message;
    }
  }

  function updatePositions(container) {
    var rows = activeRows(container);
    rows.forEach(function (row, index) {
      var position = row.querySelector('input[name$="-position"]');
      if (position) {
        position.value = String(index + 1);
      }
      var label = row.querySelector("[data-step-index]");
      if (label) {
        label.textContent = String(index + 1);
      }
    });
  }

  // Disable the boundary moves so the affordance matches what the control can
  // actually do; the script still guards the reorder itself.
  function updateMoveControls(container) {
    var rows = activeRows(container);
    rows.forEach(function (row, index) {
      var up = row.querySelector('[data-step-move="up"]');
      var down = row.querySelector('[data-step-move="down"]');
      if (up) {
        up.disabled = index === 0;
      }
      if (down) {
        down.disabled = index === rows.length - 1;
      }
    });
  }

  function refresh(container) {
    updatePositions(container);
    updateMoveControls(container);
  }

  function focusFirstField(row) {
    if (!row) {
      return;
    }
    var field =
      row.querySelector('textarea[name$="-action"]') ||
      row.querySelector("textarea, input:not([type='hidden'])");
    if (field) {
      field.focus();
    }
  }

  // Nearest visible sibling, so a row already marked as removed (kept in the
  // DOM for the server) never absorbs a move.
  function siblingRow(row, direction) {
    var node =
      direction === "up" ? row.previousElementSibling : row.nextElementSibling;
    while (node && node.hidden) {
      node =
        direction === "up"
          ? node.previousElementSibling
          : node.nextElementSibling;
    }
    return node;
  }

  function moveRow(row, direction, formset, container, trigger) {
    if (!row) {
      return;
    }
    var target = siblingRow(row, direction);
    var moved = false;
    if (target) {
      if (direction === "up") {
        container.insertBefore(row, target);
      } else {
        container.insertBefore(target, row);
      }
      moved = true;
    }
    refresh(container);
    if (!moved) {
      return;
    }
    setStatus(
      formset,
      "Passo movido para a posição " +
        stepNumber(row) +
        " de " +
        activeRows(container).length +
        "."
    );
    // Moving into the first/last slot disables the button the user just used.
    // Keep focus inside the row instead of dropping it on <body>.
    if (trigger && trigger.disabled) {
      var fallback =
        row.querySelector("[data-step-move]:not([disabled])") ||
        row.querySelector("[data-step-remove]");
      if (fallback) {
        fallback.focus();
      }
    }
  }

  function addRow(formset, container) {
    var template = formset.querySelector("[data-step-empty-form]");
    var total = formset.querySelector('input[name$="-TOTAL_FORMS"]');
    if (!template || !total) {
      return;
    }
    var index = parseInt(total.value, 10) || 0;
    var wrapper = document.createElement("div");
    wrapper.innerHTML = template.innerHTML.replace(/__prefix__/g, String(index));
    var row = wrapper.querySelector("[data-step-row]");
    if (!row) {
      return;
    }
    // Keep the untouched trailing slot last so the visible numbering stays
    // honest while the new step is being written.
    var rows = activeRows(container);
    var slot = null;
    for (var i = rows.length - 1; i >= 0; i -= 1) {
      if (isNewEmptyRow(rows[i])) {
        slot = rows[i];
        break;
      }
    }
    if (slot) {
      container.insertBefore(row, slot);
    } else {
      container.appendChild(row);
    }
    total.value = String(index + 1);
    refresh(container);
    updatePreview(row);
    setStatus(
      formset,
      "Passo adicionado na posição " +
        stepNumber(row) +
        " de " +
        activeRows(container).length +
        "."
    );
    focusFirstField(row);
  }

  function removeRow(row, formset, container) {
    if (!row) {
      return;
    }
    var total = formset.querySelector('input[name$="-TOTAL_FORMS"]');
    var deleteBox = row.querySelector('input[name$="-DELETE"]');
    var before = activeRows(container);
    var index = before.indexOf(row);
    var keepFocus = row.contains(document.activeElement);

    if (deleteBox) {
      // Keep the row in the POST payload but mark it as deleted so the server
      // can decide between physical removal and logical inactivation.
      deleteBox.checked = true;
      row.hidden = true;
    } else {
      row.remove();
      if (total) {
        total.value = String(stepRows(container).length);
      }
    }
    refresh(container);
    setStatus(
      formset,
      "Passo removido. Restam " + countText(activeRows(container).length) + "."
    );

    if (!keepFocus) {
      return;
    }
    var after = activeRows(container);
    var target = after[index] || after[index - 1] || null;
    var focusTarget = target
      ? target.querySelector("[data-step-remove]")
      : formset.querySelector("[data-step-add]");
    if (focusTarget) {
      focusTarget.focus();
    }
  }

  // Mirror the action textarea into the collapsed-step preview. The preview is
  // `aria-hidden`, so it never duplicates the labelled field for AT.
  function updatePreview(row) {
    if (!row) {
      return;
    }
    var preview = row.querySelector("[data-step-preview]");
    var action = row.querySelector('textarea[name$="-action"]');
    if (preview && action) {
      var text = String(action.value).trim();
      preview.textContent = text || "Sem ação";
    }
  }

  document.querySelectorAll("[data-steps-formset]").forEach(function (formset) {
    var container = formset.querySelector("[data-steps-container]");
    if (!container) {
      return;
    }

    var globalToggle = formset.querySelector("[data-steps-collapse-toggle]");

    // --- Collapse / expand (JS-enhanced) --------------------------------
    function setCollapsed(row, collapsed) {
      row.classList.toggle("is-collapsed", collapsed);
      var toggle = row.querySelector("[data-step-toggle]");
      if (toggle) {
        toggle.setAttribute("aria-expanded", String(!collapsed));
        toggle.textContent = collapsed ? "Expandir" : "Recolher";
      }
    }

    function allExpanded() {
      return activeRows(container).every(function (row) {
        return !row.classList.contains("is-collapsed");
      });
    }

    function syncGlobalToggle() {
      if (!globalToggle) {
        return;
      }
      var expanded = allExpanded();
      globalToggle.setAttribute("aria-expanded", String(expanded));
      globalToggle.textContent = expanded ? "Recolher todos" : "Expandir todos";
    }

    function collapseAll(collapsed) {
      activeRows(container).forEach(function (row) {
        setCollapsed(row, collapsed);
      });
      syncGlobalToggle();
    }

    function initCollapse() {
      stepRows(container).forEach(function (row) {
        updatePreview(row);
      });
      // Default state: every step starts collapsed. When the server re-renders
      // after a failed submit, an error summary is present and the rows stay
      // expanded so the offending fields remain visible.
      var collapsedByDefault = !formset.querySelector("#form-errors");
      activeRows(container).forEach(function (row) {
        setCollapsed(row, collapsedByDefault);
      });
      syncGlobalToggle();
    }

    formset.addEventListener("click", function (event) {
      var target = event.target;
      if (!target || typeof target.closest !== "function") {
        return;
      }
      var action = target.closest(
        "[data-step-move], [data-step-remove], [data-step-add], [data-step-toggle], [data-steps-collapse-toggle]"
      );
      if (!action) {
        return;
      }
      var row = action.closest("[data-step-row]");

      if (action.hasAttribute("data-steps-collapse-toggle")) {
        event.preventDefault();
        // Collapse all when everything is expanded, otherwise expand all.
        collapseAll(allExpanded());
      } else if (action.hasAttribute("data-step-toggle")) {
        event.preventDefault();
        setCollapsed(row, !row.classList.contains("is-collapsed"));
        syncGlobalToggle();
      } else if (action.hasAttribute("data-step-add")) {
        event.preventDefault();
        addRow(formset, container);
        syncGlobalToggle();
      } else if (action.hasAttribute("data-step-remove")) {
        event.preventDefault();
        removeRow(row, formset, container);
        syncGlobalToggle();
      } else if (action.hasAttribute("data-step-move")) {
        event.preventDefault();
        moveRow(
          row,
          action.getAttribute("data-step-move"),
          formset,
          container,
          action
        );
      }
    });

    // Keep the collapsed-step preview in sync with the action textarea.
    formset.addEventListener("input", function (event) {
      var target = event.target;
      if (
        target &&
        typeof target.matches === "function" &&
        target.matches('textarea[name$="-action"]')
      ) {
        updatePreview(target.closest("[data-step-row]"));
      }
    });

    // An untouched extra row must not carry a position, otherwise setting one
    // would mark it as changed and Django would require its fields.
    formset.addEventListener("submit", function () {
      activeRows(container).forEach(function (row) {
        if (!isNewEmptyRow(row)) {
          return;
        }
        var position = row.querySelector('input[name$="-position"]');
        if (position) {
          position.value = "";
        }
      });
    });

    refresh(container);
    initCollapse();
  });
})();
