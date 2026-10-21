from django.contrib.auth import get_user_model

from apps.accounts.models import Role
from apps.executions import services as execution_services
from apps.executions.models import Report
from apps.projects.models import Project, ProjectMembership
from apps.testcases.models import TestCase as CaseModel, TestStep

User = get_user_model()

PASSWORD = "uma-senha-bem-segura-123"


def make_user(email, role=Role.TESTER, is_active=True):
    return User.objects.create_user(
        email=email,
        name="Usuário de Teste",
        password=PASSWORD,
        role=role,
        is_active=is_active,
    )


def make_admin(email="admin@example.com"):
    return make_user(email, role=Role.ADMINISTRATOR)


def make_project(name="Projeto Alpha", description="Descrição do projeto"):
    return Project.objects.create(name=name, description=description)


def assign(project, user):
    return ProjectMembership.objects.create(project=project, user=user)


def make_case(project, title="Caso de Teste", is_active=True):
    return CaseModel.objects.create(
        project=project,
        title=title,
        description="Descrição do caso",
        preconditions="Nenhuma pré-condição",
        is_active=is_active,
    )


def make_step(case, position=1, action="Ação", expected_result="Esperado", is_active=True):
    return TestStep.objects.create(
        test_case=case,
        position=position,
        action=action,
        expected_result=expected_result,
        is_active=is_active,
    )


def start_execution(actor, project, cases):
    return execution_services.start_execution(
        actor=actor, project=project, case_ids=[case.pk for case in cases]
    )


def complete_execution(execution, actor):
    execution_services.complete_execution(execution=execution, actor=actor)
    return Report.objects.get(execution=execution)
