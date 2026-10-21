from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Count, Q

from .ai_adapter import is_provider_configured
from .models import (
    AIStatus,
    CaseStatus,
    ExecutionStep,
    Report,
    StepOutcome,
)

ZERO = Decimal("0.00")
PERCENT = Decimal("100")
TWO_PLACES = Decimal("0.01")


def calculate_success_rate(approved_cases, selected_cases) -> Decimal:
    if not selected_cases:
        return ZERO
    rate = (Decimal(approved_cases) / Decimal(selected_cases)) * PERCENT
    return rate.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def calculate_metrics(execution) -> dict:
    case_counts = execution.execution_cases.aggregate(
        selected_cases=Count("id"),
        approved_cases=Count("id", filter=Q(status=CaseStatus.APPROVED)),
        reproved_cases=Count("id", filter=Q(status=CaseStatus.REPROVED)),
        pending_cases=Count("id", filter=Q(status=CaseStatus.PENDING)),
        not_started_cases=Count("id", filter=Q(status=CaseStatus.NOT_STARTED)),
    )
    step_counts = ExecutionStep.objects.filter(
        execution_case__execution=execution
    ).aggregate(
        passed_steps=Count("id", filter=Q(outcome=StepOutcome.PASSED)),
        failed_steps=Count("id", filter=Q(outcome=StepOutcome.FAILED)),
        blocked_steps=Count("id", filter=Q(outcome=StepOutcome.BLOCKED)),
        unevaluated_steps=Count("id", filter=Q(outcome__isnull=True)),
        failure_observations=Count(
            "id",
            filter=Q(
                outcome__in=[StepOutcome.FAILED, StepOutcome.BLOCKED]
            )
            & ~Q(observation=""),
        ),
    )

    metrics = {**case_counts, **step_counts}
    metrics["success_rate"] = calculate_success_rate(
        metrics["approved_cases"], metrics["selected_cases"]
    )
    return metrics


def initial_ai_status() -> str:
    return AIStatus.PENDING if is_provider_configured() else AIStatus.UNAVAILABLE


def build_report(execution) -> Report:
    return Report(
        execution=execution,
        ai_status=initial_ai_status(),
        **calculate_metrics(execution),
    )
