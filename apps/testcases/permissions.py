from django.core.exceptions import PermissionDenied
from django.http import Http404

from apps.projects.permissions import (
    get_authorized_projects,
    is_project_member,
)

from .models import TestCase


def can_edit_cases(user, project) -> bool:
    return is_project_member(user, project)


def require_case_editor(user, project):
    if not can_edit_cases(user, project):
        raise PermissionDenied(
            "Apenas Testadores atribuídos ao projeto podem gerenciar casos de teste."
        )


def get_readable_case_or_404(user, pk):
    case = (
        TestCase.objects.select_related("project")
        .filter(project__in=get_authorized_projects(user), pk=pk)
        .first()
    )
    if case is None:
        raise Http404("Caso de teste não encontrado.")
    return case
