from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone

from apps.accounts.models import Role

from .models import Project, ProjectMembership


class ProtectedProjectError(Exception):
    pass


class MembershipError(Exception):
    pass


def create_project(*, name, description):
    with transaction.atomic():
        return Project.objects.create(name=name, description=description)


def update_project(project, *, name, description, is_active):
    with transaction.atomic():
        project.name = name
        project.description = description
        project.is_active = is_active
        project.save()
    return project


def inactivate_project(project):
    project.is_active = False
    project.save(update_fields=["is_active", "updated_at"])
    return project


def delete_project(project):
    try:
        with transaction.atomic():
            project.delete()
    except ProtectedError as exc:
        raise ProtectedProjectError(
            "Este projeto possui casos ou execuções associadas e não pode ser "
            "excluído; utilize a inativação para preservar o histórico."
        ) from exc


def assign_tester(project, user):
    if user is None or not user.is_active or user.role != Role.TESTER:
        raise MembershipError(
            "Somente Testadores ativos podem ser atribuídos a um projeto."
        )

    with transaction.atomic():
        existing = (
            ProjectMembership.objects.select_for_update()
            .filter(project=project, user=user, revoked_at__isnull=True)
            .first()
        )
        if existing is not None:
            return existing
        return ProjectMembership.objects.create(project=project, user=user)


def remove_tester(project, user):
    from apps.executions.services import release_for_project_revocation

    with transaction.atomic():
        release_for_project_revocation(project=project, user=user)
        return ProjectMembership.objects.filter(
            project=project, user=user, revoked_at__isnull=True
        ).update(revoked_at=timezone.now())
