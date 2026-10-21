import copy
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .models import Execution, ExecutionState, Report, StepOutcome

DEFAULT_HISTORY_LIMIT = 5


@dataclass(frozen=True)
class CaseDifference:
    pk: int
    title: str


@dataclass(frozen=True)
class ExecutionSummary:
    execution: Execution
    report: Report
    completed_at: datetime
    selected_cases: int
    approved_cases: int
    reproved_cases: int
    pending_cases: int
    not_started_cases: int
    passed_steps: int
    failed_steps: int
    blocked_steps: int
    unevaluated_steps: int
    failure_observations: int
    success_rate: Decimal
    case_ids: frozenset
    case_titles: dict


@dataclass(frozen=True)
class ExecutionComparison:
    base: ExecutionSummary
    against: ExecutionSummary
    same_case_set: bool
    common_count: int
    only_in_base: tuple
    only_in_against: tuple


def get_completed_executions(project):
    return (
        Execution.objects.filter(
            project=project, state=ExecutionState.COMPLETED
        )
        .select_related("created_by")
        .order_by("-completed_at", "-pk")
    )


def _report_metrics(report):
    if report is None:
        return {
            "selected_cases": 0,
            "approved_cases": 0,
            "reproved_cases": 0,
            "pending_cases": 0,
            "not_started_cases": 0,
            "passed_steps": 0,
            "failed_steps": 0,
            "blocked_steps": 0,
            "unevaluated_steps": 0,
            "failure_observations": 0,
            "success_rate": Decimal("0.00"),
        }
    return {
        "selected_cases": report.selected_cases,
        "approved_cases": report.approved_cases,
        "reproved_cases": report.reproved_cases,
        "pending_cases": report.pending_cases,
        "not_started_cases": report.not_started_cases,
        "passed_steps": report.passed_steps,
        "failed_steps": report.failed_steps,
        "blocked_steps": report.blocked_steps,
        "unevaluated_steps": report.unevaluated_steps,
        "failure_observations": report.failure_observations,
        "success_rate": report.success_rate,
    }


def _summary_for(execution, report):
    case_rows = list(
        execution.execution_cases.values_list("source_test_case_id", "title")
    )
    case_ids = frozenset(pk for pk, _ in case_rows)
    case_titles = {pk: title for pk, title in case_rows}
    return ExecutionSummary(
        execution=execution,
        report=report,
        completed_at=execution.completed_at,
        case_ids=case_ids,
        case_titles=case_titles,
        **_report_metrics(report),
    )


def completed_execution_summaries(project):
    executions = list(get_completed_executions(project))
    reports = {
        report.execution_id: report
        for report in Report.objects.filter(execution__in=executions)
    }
    return [
        _summary_for(execution, reports.get(execution.pk))
        for execution in executions
    ]


def compare_executions(base, against):
    if base.execution.pk == against.execution.pk:
        raise ValueError("Escolha duas execuções diferentes para comparar.")
    if base.execution.project_id != against.execution.project_id:
        raise ValueError("Somente execuções do mesmo projeto podem ser comparadas.")

    only_in_base_ids = base.case_ids - against.case_ids
    only_in_against_ids = against.case_ids - base.case_ids

    return ExecutionComparison(
        base=base,
        against=against,
        same_case_set=not only_in_base_ids and not only_in_against_ids,
        common_count=len(base.case_ids & against.case_ids),
        only_in_base=tuple(
            CaseDifference(pk, base.case_titles.get(pk, ""))
            for pk in sorted(only_in_base_ids)
        ),
        only_in_against=tuple(
            CaseDifference(pk, against.case_titles.get(pk, ""))
            for pk in sorted(only_in_against_ids)
        ),
    )


def snapshot_cases(execution, *, prefix=""):
    cases = []
    for case_index, case in enumerate(
        execution.execution_cases.prefetch_related("steps").order_by("pk"),
        start=1,
    ):
        case_reference = f"{prefix}case-{case_index}"
        steps = []
        for step_index, step in enumerate(
            case.steps.order_by("snapshot_position", "pk"), start=1
        ):
            steps.append(
                {
                    "reference": f"{case_reference}/step-{step_index}",
                    "action": step.action,
                    "expected_result": step.expected_result,
                    "outcome": step.outcome or "NOT_EVALUATED",
                    "observation": step.observation or "",
                }
            )
        cases.append(
            {
                "reference": case_reference,
                "title": case.title,
                "description": case.description,
                "preconditions": case.preconditions,
                "steps": steps,
            }
        )
    return cases


def build_historical_context(report, *, limit=DEFAULT_HISTORY_LIMIT):
    execution = report.execution
    prior_executions = list(
        Execution.objects.filter(
            project_id=execution.project_id,
            state=ExecutionState.COMPLETED,
            completed_at__lt=execution.completed_at,
        )
        .exclude(pk=execution.pk)
        .order_by("-completed_at", "-pk")[:limit]
    )
    if not prior_executions:
        return []

    reports = {
        prior_report.execution_id: prior_report
        for prior_report in Report.objects.filter(execution__in=prior_executions)
    }

    context = []
    for index, previous in enumerate(prior_executions, start=1):
        completed_at = previous.completed_at
        metrics = _report_metrics(reports.get(previous.pk))
        metrics["success_rate"] = str(metrics["success_rate"])
        context.append(
            {
                "reference": f"history-{index}",
                "completed_at": completed_at.isoformat() if completed_at else None,
                "metrics": metrics,
                "cases": snapshot_cases(previous, prefix=f"history-{index}/"),
            }
        )
    return context


def fit_historical_context(entries, budget_bytes):
    fitted = copy.deepcopy(list(entries))
    if budget_bytes is None or _serialized_size(fitted) <= budget_bytes:
        return fitted
    _omit_passed_step_text(fitted)
    while fitted and _serialized_size(fitted) > budget_bytes:
        fitted.pop()
    return fitted


def _omit_passed_step_text(entries):
    for entry in entries:
        for case in entry.get("cases", []):
            for step in case.get("steps", []):
                if step.get("outcome") == StepOutcome.PASSED:
                    step["action"] = ""
                    step["expected_result"] = ""
                    step["observation"] = ""


def _serialized_size(payload) -> int:
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
