from django.core.exceptions import PermissionDenied
from django.http import Http404

from apps.projects.permissions import get_authorized_projects, is_project_member

from .models import Execution, ExecutionState


def get_readable_execution_or_404(user, pk):
    execution = (
        Execution.objects.select_related("project", "created_by")
        .filter(project__in=get_authorized_projects(user), pk=pk)
        .first()
    )
    if execution is None:
        raise Http404("Execução não encontrada.")
    return execution


def is_current_owner(execution, user) -> bool:
    if not is_project_member(user, execution.project):
        return False
    assignment = execution.current_assignment
    return assignment is not None and assignment.responsible_id == user.id


def can_operate(execution, user) -> bool:
    return (
        execution.state == ExecutionState.IN_PROGRESS
        and is_current_owner(execution, user)
    )


def can_assume(execution, user) -> bool:
    return (
        execution.state == ExecutionState.IN_PROGRESS
        and is_project_member(user, execution.project)
        and execution.current_assignment is None
    )


def require_execution_creator(user, project):
    if not is_project_member(user, project):
        raise PermissionDenied(
            "Apenas Testadores atribuídos ao projeto podem iniciar execuções."
        )
