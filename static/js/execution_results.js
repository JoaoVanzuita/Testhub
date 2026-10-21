/* TestHub guided step-result runner: progressive enhancement only.
 *
 * The server persists each apontamento and returns success only after the
 * transaction commits (FR-024). This script posts the chosen outcome
 * asynchronously and updates the visible step result, the case status, the case
 * progress, the execution-level summary, the read-only monitoring board and the
 * "next pending step" hint only from the backend response, so a result is never
 * shown as saved when
 * persistence failed. Without JavaScript each quick action is a normal submit
 * button and the server answers with POST/redirect/GET.
 */
(function () {
  "use strict";

  var OUTCOME_STATUS = {
    PASSED: "status--ok",
    FAILED: "status--danger",
    BLOCKED: "status--warning",
  };

  var CASE_STATUS = {
    CASE_APPROVED: "status--ok",
    CASE_REPROVED: "status--danger",
    CASE_PENDING: "status--warning",
    CASE_NOT_STARTED: "status--off",
  };

  function statusClass(map, value, fallback) {
    return "status " + (map[value] || fallback);
  }

  function cases() {
    return Array.prototype.slice.call(
      document.querySelectorAll("[data-execution-case]")
    );
  }

  function rowsOf(section) {
    return Array.prototype.slice.call(
      section.querySelectorAll("[data-step-row]")
    );
  }

  function outcomeOf(row) {
    var el = row.querySelector("[data-step-outcome]");
    return el ? el.getAttribute("data-outcome") || "" : "";
  }

  function setFeedback(form, text, state) {
    var feedback = form.querySelector("[data-step-feedback]");
    if (!feedback) {
      return;
    }
    feedback.textContent = text || "";
    if (state) {
      feedback.setAttribute("data-state", state);
    } else {
      feedback.removeAttribute("data-state");
    }
  }

  function recountCase(section) {
    var rows = rowsOf(section);
    var total = rows.length;
    var assessed = 0;
    rows.forEach(function (row) {
      if (outcomeOf(row)) {
        assessed += 1;
      }
    });

    var bar = section.querySelector("[data-case-progress]");
    var label = section.querySelector("[data-case-progress-label]");
    if (bar) {
      bar.setAttribute("max", String(total));
      bar.value = assessed;
    }
    if (label) {
      label.textContent = assessed + " de " + total + " passos avaliados";
    }
    return { total: total, assessed: assessed };
  }

  function firstPendingRow() {
    var found = null;
    cases().forEach(function (section) {
      if (found) {
        return;
      }
      rowsOf(section).forEach(function (row) {
        if (!found && !outcomeOf(row)) {
          found = row;
        }
      });
    });
    return found;
  }

  function updateNextPending() {
    var next = firstPendingRow();
    var badge = document.querySelector("[data-next-pending-badge]");

    document.querySelectorAll("[data-step-row]").forEach(function (row) {
      var isNext = row === next;
      row.classList.toggle("execution-step--next", isNext);
      if (isNext) {
        row.setAttribute("data-next-pending", "");
      } else {
        row.removeAttribute("data-next-pending");
      }
    });

    if (badge) {
      if (next) {
        var action = next.querySelector(".execution-step__action");
        if (action) {
          action.appendChild(badge);
        }
      } else {
        badge.remove();
      }
    }

    var summary = document.querySelector("[data-next-pending-summary]");
    if (summary) {
      if (!next) {
        summary.textContent =
          "Todos os passos avaliados. Você pode concluir a execução.";
        return;
      }
      var section = next.closest("[data-execution-case]");
      var title = section ? section.querySelector("h2") : null;
      var position = next.querySelector(".execution-step__position");
      var parts = [];
      if (title) {
        parts.push(title.textContent.trim());
      }
      if (position) {
        parts.push("passo " + position.textContent.trim().replace(/\.$/, ""));
      }
      summary.textContent =
        "Próximo passo pendente: " + (parts.join(" · ") || "destacado abaixo") + ".";
    }
  }

  function updateBoard() {
    // The board is read-only monitoring: every change is re-derived from the
    // persisted case markup (never from an optimistic assumption), so a card
    // only moves to another column after the backend accepted the result.
    var board = document.querySelector("[data-case-board]");
    if (!board) {
      return;
    }

    var sections = {};
    cases().forEach(function (section) {
      var pk = section.getAttribute("data-pk");
      if (pk) {
        sections[pk] = section;
      }
    });

    board.querySelectorAll("[data-board-card]").forEach(function (card) {
      var section = sections[card.getAttribute("data-board-card")];
      if (!section) {
        return;
      }

      var status = section.getAttribute("data-status") || "";
      card.setAttribute("data-status", status);

      var sourceStatus = section.querySelector("[data-case-status]");
      var cardStatus = card.querySelector("[data-board-card-status]");
      if (sourceStatus && cardStatus) {
        cardStatus.textContent = sourceStatus.textContent.trim();
        cardStatus.className = sourceStatus.className;
      }

      var sourceProgress = section.querySelector("[data-case-progress-label]");
      var cardMeta = card.querySelector("[data-board-card-meta]");
      if (sourceProgress && cardMeta) {
        cardMeta.textContent = sourceProgress.textContent.trim();
      }

      var cardFlag = card.querySelector("[data-board-card-flag]");
      if (cardFlag) {
        var blocked = section.querySelectorAll(
          '[data-step-row][data-outcome="BLOCKED"]'
        ).length;
        cardFlag.hidden = blocked === 0;
      }

      var column = board.querySelector('[data-board-column="' + status + '"]');
      var list = column ? column.querySelector("[data-board-list]") : null;
      if (list && card.parentElement !== list) {
        var empty = list.querySelector("[data-board-empty]");
        if (empty) {
          list.insertBefore(card, empty);
        } else {
          list.appendChild(card);
        }
      }
    });

    board.querySelectorAll("[data-board-column]").forEach(function (column) {
      var list = column.querySelector("[data-board-list]");
      var count = column.querySelector("[data-board-count]");
      if (!list || !count) {
        return;
      }
      var total = list.querySelectorAll("[data-board-card]").length;
      count.textContent = String(total);
      var empty = list.querySelector("[data-board-empty]");
      if (empty) {
        empty.hidden = total > 0;
      }
    });
  }

  function updateSummary() {
    var allCases = cases();
    var totalSteps = 0;
    var assessedSteps = 0;
    var counts = {
      CASE_APPROVED: 0,
      CASE_REPROVED: 0,
      CASE_PENDING: 0,
      CASE_NOT_STARTED: 0,
    };

    allCases.forEach(function (section) {
      var stepInfo = recountCase(section);
      totalSteps += stepInfo.total;
      assessedSteps += stepInfo.assessed;
      var status = section.getAttribute("data-status");
      if (status in counts) {
        counts[status] += 1;
      }
    });

    var percent = totalSteps
      ? Math.round((assessedSteps * 100) / totalSteps)
      : 0;
    var progress = document.querySelector("[data-execution-progress]");
    var bar = document.querySelector("[data-execution-progress-bar]");
    var label = document.querySelector("[data-execution-progress-label]");

    if (progress) {
      progress.setAttribute(
        "aria-label",
        assessedSteps + " de " + totalSteps + " passos avaliados"
      );
    }
    if (bar) {
      bar.style.setProperty("--value", percent + "%");
    }
    if (label) {
      label.textContent = assessedSteps + " de " + totalSteps + " passos avaliados";
    }

    setText("[data-summary-cases]", String(allCases.length));
    setText("[data-summary-approved]", String(counts.CASE_APPROVED));
    setText("[data-summary-reproved]", String(counts.CASE_REPROVED));
    setText("[data-summary-pending]", String(counts.CASE_PENDING));
    setText("[data-summary-not-started]", String(counts.CASE_NOT_STARTED));

    updateNextPending();
    updateBoard();
  }

  function setText(selector, value) {
    var el = document.querySelector(selector);
    if (el) {
      el.textContent = value;
    }
  }

  function updateFromResponse(form, data) {
    var row = form.closest("[data-step-row]");
    if (row) {
      row.setAttribute("data-outcome", data.outcome || "");
      var outcome = row.querySelector("[data-step-outcome]");
      if (outcome) {
        outcome.textContent = data.outcome_label;
        outcome.setAttribute("data-outcome", data.outcome || "");
        outcome.className = statusClass(OUTCOME_STATUS, data.outcome, "status--off");
      }

      row.querySelectorAll("[data-step-action]").forEach(function (button) {
        button.classList.toggle(
          "is-active",
          button.getAttribute("data-step-action") === data.outcome
        );
      });

      var saveButton = row.querySelector("[data-step-save]");
      if (saveButton) {
        saveButton.value = data.outcome || "";
        saveButton.disabled = false;
        saveButton.removeAttribute("aria-disabled");
        saveButton.removeAttribute("title");
      }

      var observation = row.querySelector('textarea[name="observation"]');
      if (observation && typeof data.observation === "string") {
        observation.value = data.observation;
      }
    }

    var section = form.closest("[data-execution-case]");
    if (section) {
      section.setAttribute("data-status", data.case_status);
      var caseStatus = section.querySelector("[data-case-status]");
      if (caseStatus) {
        caseStatus.textContent = data.case_status_label;
        caseStatus.className = statusClass(
          CASE_STATUS,
          data.case_status,
          "status--off"
        );
      }
    }

    updateSummary();
  }

  function handleSubmit(event) {
    event.preventDefault();
    var form = event.currentTarget;
    var submitter = event.submitter || null;
    var submitButton = submitter || form.querySelector('button[type="submit"]');
    var body = new FormData(form);

    // A submit button's value is not part of FormData unless it is the
    // submitter, so the chosen quick action must be added explicitly.
    if (submitter && submitter.name) {
      body.append(submitter.name, submitter.value);
    } else {
      var active = form.querySelector("[data-step-action].is-active");
      if (active && active.name) {
        body.append(active.name, active.value);
      }
    }

    if (submitButton) {
      submitButton.disabled = true;
      submitButton.setAttribute("aria-busy", "true");
      // app.js re-enables [data-busy='true'] buttons on pageshow, which is the
      // only cleanup path when the user navigates away mid-request and returns.
      submitButton.setAttribute("data-busy", "true");
    }
    setFeedback(form, "Salvando resultado…", "pending");

    window
      .fetch(form.action, {
        method: "POST",
        body: body,
        credentials: "same-origin",
        headers: { "X-Requested-With": "XMLHttpRequest" },
      })
      .then(function (response) {
        return response
          .json()
          .catch(function () {
            return {};
          })
          .then(function (data) {
            if (!response.ok || !data.persisted) {
              throw new Error(
                data.error || "Não foi possível salvar o resultado."
              );
            }
            return data;
          });
      })
      .then(function (data) {
        updateFromResponse(form, data);
        setFeedback(form, "Resultado salvo.", "success");
      })
      .catch(function (error) {
        setFeedback(form, error.message, "error");
      })
      .finally(function () {
        if (submitButton && submitButton.isConnected) {
          submitButton.disabled = false;
          submitButton.removeAttribute("aria-busy");
          submitButton.removeAttribute("data-busy");
        }
      });
  }

  document.querySelectorAll("[data-step-result-form]").forEach(function (form) {
    form.addEventListener("submit", handleSubmit);
  });

  // Sync the summary and the next-pending hint from the persisted markup.
  updateSummary();
})();
