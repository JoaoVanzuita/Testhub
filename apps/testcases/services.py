from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import F
from django.db.models.deletion import ProtectedError

from apps.projects.permissions import is_project_member

from .models import TestCase, TestStep

POSITION_OFFSET = 100000


class CaseError(Exception):
    pass


class StepValidationError(CaseError):
    pass


class ProtectedCaseError(CaseError):
    pass


def _require_editor(actor, project):
    if not is_project_member(actor, project):
        raise PermissionDenied(
            "Apenas Testadores atribuídos ao projeto podem gerenciar casos de teste."
        )


def _validated_submitted_steps(steps):
    submitted = [step for step in steps if not step.get("delete")]
    if not submitted:
        raise StepValidationError("Informe ao menos um passo para o caso.")

    positions = [step["position"] for step in submitted]
    if any(position is None or position < 1 for position in positions):
        raise StepValidationError("A ordem dos passos deve ser um número positivo.")
    if len(set(positions)) != len(positions):
        raise StepValidationError("As ordens dos passos devem ser únicas.")

    return sorted(submitted, key=lambda step: step["position"])


@transaction.atomic
def save_steps(case, steps):
    TestCase.objects.select_for_update().get(pk=case.pk)

    ordered = _validated_submitted_steps(steps)
    existing = {step.pk: step for step in case.steps.all()}

    active = []
    parked = []
    for submitted in ordered:
        is_active = submitted.get("is_active", True)
        pk = submitted.get("pk")
        if pk and pk in existing:
            step = existing.pop(pk)
            step.action = submitted["action"]
            step.expected_result = submitted["expected_result"]
            step.is_active = is_active
        else:
            step = TestStep(
                test_case=case,
                action=submitted["action"],
                expected_result=submitted["expected_result"],
                is_active=is_active,
            )
        (active if is_active else parked).append(step)

    if not active:
        raise StepValidationError("O caso deve possuir ao menos um passo ativo.")

    for step in list(existing.values()):
        try:
            step.delete()
        except ProtectedError:
            step.is_active = False
            parked.append(step)

    TestStep.objects.filter(test_case=case).update(
        position=F("position") + POSITION_OFFSET
    )
    for index, step in enumerate(active, start=1):
        step.position = index
        step.save()
    for index, step in enumerate(parked, start=len(active) + 1):
        step.position = index
        step.save()

    return case


@transaction.atomic
def create_case(*, actor, project, title, description, preconditions, steps):
    _require_editor(actor, project)
    case = TestCase.objects.create(
        project=project,
        title=title,
        description=description,
        preconditions=preconditions,
    )
    save_steps(case, steps)
    return case


@transaction.atomic
def update_case(
    case, *, actor, title, description, preconditions, is_active, steps
):
    _require_editor(actor, case.project)
    case.title = title
    case.description = description
    case.preconditions = preconditions
    case.is_active = is_active
    case.save()
    save_steps(case, steps)
    return case


@transaction.atomic
def inactivate_case(case, *, actor):
    _require_editor(actor, case.project)
    case.is_active = False
    case.save(update_fields=["is_active", "updated_at"])
    return case


@transaction.atomic
def delete_case(case, *, actor):
    _require_editor(actor, case.project)
    try:
        case.delete()
    except ProtectedError as exc:
        raise ProtectedCaseError(
            "Este caso possui execuções associadas e não pode ser excluído; "
            "utilize a inativação para preservar o histórico."
        ) from exc
