from django.http import Http404

from apps.accounts.permissions import is_active_user, is_administrator, is_tester

from .models import Project, ProjectMembership


def is_project_member(user, project) -> bool:
    if not is_active_user(user):
        return False
    return ProjectMembership.objects.filter(
        project=project, user=user, revoked_at__isnull=True
    ).exists()


def can_access_project(user, project) -> bool:
    if is_administrator(user):
        return True
    if is_tester(user):
        return is_project_member(user, project)
    return False


def get_authorized_projects(user):
    if is_administrator(user):
        return Project.objects.all()
    if is_tester(user):
        return Project.objects.filter(
            is_active=True,
            memberships__user=user,
            memberships__revoked_at__isnull=True,
        ).distinct()
    return Project.objects.none()


def get_authorized_project_or_404(user, pk):
    project = get_authorized_projects(user).filter(pk=pk).first()
    if project is None:
        raise Http404("Projeto não encontrado.")
    return project
