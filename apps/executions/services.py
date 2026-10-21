from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Role
from apps.projects.permissions import is_project_member
from apps.testcases.models import TestCase

from .models import (
    Execution,
    ExecutionAssignment,
    ExecutionCase,
    ExecutionState,
    ExecutionStep,
    ReleaseReason,
    StepOutcome,
)
from .reporting import build_report


class ExecutionError(Exception):
    pass


class NoCasesSelectedError(ExecutionError):
    pass


class InvalidCaseError(ExecutionError):
    pass


class UnusableCaseError(ExecutionError):
    pass


class InvalidOutcomeError(ExecutionError):
    pass


class InvalidStepError(ExecutionError):
    pass


class ImmutableExecutionError(ExecutionError):
    pass


class HandoffError(ExecutionError):
    pass


def _require_operator(actor, execution):
    if not is_project_member(actor, execution.project):
        raise PermissionDenied(
            "O usuário não possui acesso ativo a este projeto."
        )
    assignment = execution.current_assignment
    if assignment is None or assignment.responsible_id != actor.id:
        raise PermissionDenied(
            "Apenas o responsável atual pode alterar esta execução."
        )


def _require_in_progress(execution):
    if execution.state != ExecutionState.IN_PROGRESS:
        raise ImmutableExecutionError(
            "Esta execução já foi concluída e não pode ser alterada."
        )


def get_current_assignment(execution):
    return (
        execution.assignments.filter(released_at__isnull=True)
        .select_related("responsible")
        .first()
    )


def _close_current_assignment(execution, *, reason):
    assignment = get_current_assignment(execution)
    if assignment is None:
        return None
    assignment.released_at = timezone.now()
    assignment.release_reason = reason
    assignment.save(update_fields=["released_at", "release_reason"])
    return assignment


@transaction.atomic
def start_execution(*, actor, project, case_ids):
    if not is_project_member(actor, project):
        raise PermissionDenied(
            "Apenas Testadores atribuídos ao projeto podem iniciar execuções."
        )

    selected_ids = list(dict.fromkeys(case_ids or []))
    if not selected_ids:
        raise NoCasesSelectedError(
            "Selecione ao menos um caso para iniciar a execução."
        )

    cases = list(
        TestCase.objects.select_for_update()
        .filter(pk__in=selected_ids, project=project, is_active=True)
        .order_by("title", "pk")
    )
    if len(cases) != len(selected_ids):
        raise InvalidCaseError(
            "Um ou mais casos selecionados são inativos ou não pertencem a este projeto."
        )

    unusable = [case for case in cases if not case.has_active_steps]
    if unusable:
        titles = "; ".join(case.title for case in unusable)
        raise UnusableCaseError(
            "Estes casos não possuem passos ativos e não podem ser executados: "
            f"{titles}. Adicione ao menos um passo ativo antes de iniciar a execução."
        )

    execution = Execution.objects.create(
        project=project,
        created_by=actor,
        state=ExecutionState.IN_PROGRESS,
        started_at=timezone.now(),
    )

    for case in cases:
        execution_case = ExecutionCase.objects.create(
            execution=execution,
            source_test_case=case,
            title=case.title,
            description=case.description,
            preconditions=case.preconditions,
        )
        steps = case.steps.filter(is_active=True).order_by("position", "pk")
        ExecutionStep.objects.bulk_create(
            [
                ExecutionStep(
                    execution_case=execution_case,
                    source_test_step=step,
                    snapshot_position=step.position,
                    action=step.action,
                    expected_result=step.expected_result,
                )
                for step in steps
            ]
        )

    ExecutionAssignment.objects.create(
        execution=execution,
        responsible=actor,
        assigned_at=timezone.now(),
    )
    return execution


@transaction.atomic
def update_step_result(*, execution, step, actor, outcome, observation=""):
    execution = Execution.objects.select_for_update().get(pk=execution.pk)
    _require_in_progress(execution)
    _require_operator(actor, execution)

    if outcome not in StepOutcome.values:
        raise InvalidOutcomeError(
            "Resultado inválido. Use Passou, Falhou ou Bloqueado."
        )

    if step.execution_case.execution_id != execution.pk:
        raise InvalidStepError("O passo não pertence a esta execução.")

    step.outcome = outcome
    step.observation = (observation or "").strip()
    step.save(update_fields=["outcome", "observation", "updated_at"])
    step.execution_case.refresh_status()
    return step


@transaction.atomic
def pause_execution(*, execution, actor):
    execution = Execution.objects.select_for_update().get(pk=execution.pk)
    _require_in_progress(execution)
    _require_operator(actor, execution)
    return execution


@transaction.atomic
def resume_execution(*, execution, actor):
    execution = Execution.objects.select_for_update().get(pk=execution.pk)
    _require_in_progress(execution)
    _require_operator(actor, execution)
    return execution


@transaction.atomic
def release_assignment(*, execution, actor, reason=ReleaseReason.VOLUNTARY):
    execution = Execution.objects.select_for_update().get(pk=execution.pk)
    _require_in_progress(execution)
    _require_operator(actor, execution)
    _close_current_assignment(execution, reason=reason)
    return execution


@transaction.atomic
def handoff_execution(*, execution, actor, successor):
    execution = Execution.objects.select_for_update().get(pk=execution.pk)
    _require_in_progress(execution)
    _require_operator(actor, execution)

    if successor is None:
        raise HandoffError("Escolha um Testador para assumir a execução.")
    if (
        not successor.is_active
        or successor.role != Role.TESTER
    ):
        raise HandoffError("Somente Testadores ativos podem assumir a execução.")
    if successor.pk == actor.pk:
        raise HandoffError("Escolha outro Testador para assumir a execução.")
    if not is_project_member(successor, execution.project):
        raise HandoffError(
            "O Testador escolhido não está atribuído a este projeto."
        )

    _close_current_assignment(execution, reason=ReleaseReason.HANDOFF)
    return ExecutionAssignment.objects.create(
        execution=execution,
        responsible=successor,
        assigned_at=timezone.now(),
    )


@transaction.atomic
def assume_execution(*, execution, actor):
    execution = Execution.objects.select_for_update().get(pk=execution.pk)
    _require_in_progress(execution)

    if not is_project_member(actor, execution.project):
        raise PermissionDenied(
            "Apenas Testadores atribuídos ao projeto podem assumir a execução."
        )
    if actor.role != Role.TESTER:
        raise PermissionDenied("Apenas Testadores podem assumir a execução.")

    current = get_current_assignment(execution)
    if current is not None:
        if current.responsible_id == actor.id:
            return execution
        raise HandoffError("A execução já possui um responsável atual.")

    ExecutionAssignment.objects.create(
        execution=execution,
        responsible=actor,
        assigned_at=timezone.now(),
    )
    return execution


@transaction.atomic
def release_for_project_revocation(*, project, user):
    execution_ids = list(
        Execution.objects.filter(
            project=project,
            state=ExecutionState.IN_PROGRESS,
            assignments__responsible=user,
            assignments__released_at__isnull=True,
        )
        .values_list("pk", flat=True)
        .distinct()
    )
    executions = (
        Execution.objects.select_for_update()
        .filter(pk__in=execution_ids)
        .order_by("pk")
    )
    released = []
    for execution in executions:
        released.append(
            _close_current_assignment(
                execution, reason=ReleaseReason.PROJECT_ACCESS_REVOKED
            )
        )
    return released


@transaction.atomic
def complete_execution(*, execution, actor):
    execution = Execution.objects.select_for_update().get(pk=execution.pk)
    _require_in_progress(execution)
    _require_operator(actor, execution)

    for execution_case in execution.execution_cases.all():
        execution_case.refresh_status(save=True)

    execution.state = ExecutionState.COMPLETED
    execution.completed_at = timezone.now()
    execution.save(update_fields=["state", "completed_at", "updated_at"])

    report = build_report(execution)
    report.save()

    _close_current_assignment(execution, reason=None)
    return execution
