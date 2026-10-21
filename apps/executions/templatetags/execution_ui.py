from django import template

from apps.executions.models import CaseStatus, StepOutcome

register = template.Library()

BOARD_COLUMNS = (
    (CaseStatus.NOT_STARTED, "status--off"),
    (CaseStatus.PENDING, "status--warning"),
    (CaseStatus.APPROVED, "status--ok"),
    (CaseStatus.REPROVED, "status--danger"),
)


@register.simple_tag
def execution_summary(execution_cases):
    counts = {
        CaseStatus.NOT_STARTED: 0,
        CaseStatus.PENDING: 0,
        CaseStatus.APPROVED: 0,
        CaseStatus.REPROVED: 0,
    }
    total_steps = 0
    assessed_steps = 0
    next_pending_pk = None

    for case in execution_cases:
        if case.status in counts:
            counts[case.status] += 1
        for step in case.steps.all():
            total_steps += 1
            if step.outcome is not None:
                assessed_steps += 1
            elif next_pending_pk is None:
                next_pending_pk = step.pk

    return {
        "total_cases": len(execution_cases),
        "not_started": counts[CaseStatus.NOT_STARTED],
        "pending": counts[CaseStatus.PENDING],
        "approved": counts[CaseStatus.APPROVED],
        "reproved": counts[CaseStatus.REPROVED],
        "total_steps": total_steps,
        "assessed_steps": assessed_steps,
        "percent": round(assessed_steps * 100 / total_steps) if total_steps else 0,
        "next_pending_pk": next_pending_pk,
    }


@register.simple_tag
def case_board_columns(execution_cases):
    buckets = {status: [] for status, _ in BOARD_COLUMNS}
    for case in execution_cases:
        if case.status in buckets:
            buckets[case.status].append(case)

    return [
        {
            "key": status.value,
            "label": status.label,
            "status_class": status_class,
            "cases": buckets[status],
            "count": len(buckets[status]),
        }
        for status, status_class in BOARD_COLUMNS
    ]


@register.simple_tag
def case_has_blocked(execution_case):
    return any(
        step.outcome == StepOutcome.BLOCKED for step in execution_case.steps.all()
    )


CASE_STATUS_CLASS = {
    CaseStatus.APPROVED: "status--ok",
    CaseStatus.PENDING: "status--warning",
    CaseStatus.REPROVED: "status--danger",
    CaseStatus.NOT_STARTED: "status--off",
}

STEP_OUTCOME_CLASS = {
    StepOutcome.PASSED: "status--ok",
    StepOutcome.FAILED: "status--danger",
    StepOutcome.BLOCKED: "status--warning",
}


@register.filter
def case_status_class(status):
    return CASE_STATUS_CLASS.get(status, "status--off")


@register.filter
def outcome_status_class(outcome):
    return STEP_OUTCOME_CLASS.get(outcome, "status--off")
