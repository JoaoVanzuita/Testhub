from __future__ import annotations

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from apps.accounts.models import Role
from apps.executions import services as execution_services
from apps.executions.models import (
    AIStatus,
    Execution,
    ExecutionState,
    FailureCategory,
    Report,
    StepOutcome,
)
from apps.projects.models import Project, ProjectMembership
from apps.testcases.models import TestCase, TestStep

User = get_user_model()

ADMIN_EMAIL = "admin@e2e.test"
TEST1_EMAIL = "test1@e2e.test"
TEST2_EMAIL = "test2@e2e.test"
TEST3_EMAIL = "test3@e2e.test"

PASSWORD_ENV = "E2E_USER_PASSWORD"
DEFAULT_PASSWORD = "senha-e2e-forte-123"

PROJECT_NAME = "Loja"
PROJECT_DESCRIPTION = "Projeto usado pelos testes end-to-end (Cypress)."

CASE_LOGIN_TITLE = "Login válido"
CASE_CHECKOUT_TITLE = "Checkout"

LOGIN_STEPS = [
    ("Abrir a tela de login", "A tela de login é exibida"),
    ("Informar credenciais válidas", "O usuário entra no sistema"),
]

CHECKOUT_STEPS = [
    ("Adicionar um produto ao carrinho", "O produto aparece no carrinho"),
    ("Informar os dados de pagamento", "O pagamento é processado"),
    ("Confirmar o pedido", "O pedido é registrado"),
]

SCENARIOS = (
    "base",
    "ai-unavailable",
    "ai-pending",
    "ai-available",
    "ai-insufficient",
)


class Command(BaseCommand):
    help = "Create the deterministic E2E baseline dataset for the Cypress suite."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete the domain data before recreating the baseline.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "Allow --reset on a database whose name does not contain "
                "'e2e' (safety guard)."
            ),
        )
        parser.add_argument(
            "--scenario",
            choices=SCENARIOS,
            default="base",
            help="State to apply to the seeded report (AI rendering scenarios).",
        )


    def handle(self, *args, **options):
        db_name = str(connection.settings_dict.get("NAME", ""))
        if options["reset"] and "e2e" not in db_name.lower() and not options["force"]:
            raise CommandError(
                f"Recusando --reset no banco '{db_name}', que não parece ser um "
                "banco E2E. Use --force se realmente for o ambiente de teste."
            )

        password = os.environ.get(PASSWORD_ENV) or DEFAULT_PASSWORD

        if options["reset"]:
            self._reset()
            self.stdout.write(self.style.WARNING("Domínio limpo (--reset)."))

        with transaction.atomic():
            admin = self._upsert_user(
                ADMIN_EMAIL, "Administrador E2E", Role.ADMINISTRATOR, password
            )
            test1 = self._upsert_user(
                TEST1_EMAIL, "Testador Um E2E", Role.TESTER, password
            )
            test2 = self._upsert_user(
                TEST2_EMAIL, "Testador Dois E2E", Role.TESTER, password
            )
            test3 = self._upsert_user(
                TEST3_EMAIL, "Testador Três E2E", Role.TESTER, password
            )

            project = self._upsert_project(PROJECT_NAME, PROJECT_DESCRIPTION)
            self._assign(project, test1)
            self._assign(project, test2)

            login_case = self._sync_case(
                project,
                CASE_LOGIN_TITLE,
                "Verifica o fluxo de autenticação com credenciais válidas.",
                "O usuário não está autenticado.",
                LOGIN_STEPS,
            )
            checkout_case = self._sync_case(
                project,
                CASE_CHECKOUT_TITLE,
                "Verifica a finalização de uma compra.",
                "O usuário está autenticado e há um produto disponível.",
                CHECKOUT_STEPS,
            )

        self._seed_executions(
            project=project,
            actor=test1,
            login_case=login_case,
            checkout_case=checkout_case,
        )
        self._apply_scenario(project, options["scenario"])

        self._report(admin, test1, test2, test3, project, password)


    def _reset(self):
        Report.objects.all().delete()
        Execution.objects.all().delete()
        TestStep.objects.all().delete()
        TestCase.objects.all().delete()
        ProjectMembership.objects.all().delete()
        Project.objects.all().delete()
        User.objects.all().delete()


    def _upsert_user(self, email, name, role, password):
        user = User.objects.filter(email=email).first()
        if user is None:
            user = User(email=email)
        user.name = name
        user.role = role
        user.is_active = True
        user.set_password(password)
        user.save()
        return user

    def _upsert_project(self, name, description):
        project = Project.objects.filter(name__iexact=name).first()
        if project is None:
            project = Project.objects.create(name=name, description=description)
        else:
            project.name = name
            project.description = description
            project.is_active = True
            project.save()
        return project

    def _assign(self, project, user):
        ProjectMembership.objects.get_or_create(
            project=project, user=user, revoked_at__isnull=True
        )


    def _sync_case(self, project, title, description, preconditions, steps):
        case = TestCase.objects.filter(project=project, title=title).first()
        if case is None:
            case = TestCase.objects.create(
                project=project,
                title=title,
                description=description,
                preconditions=preconditions,
            )
        else:
            case.description = description
            case.preconditions = preconditions
            case.is_active = True
            case.save()

        for position, (action, expected) in enumerate(steps, start=1):
            TestStep.objects.update_or_create(
                test_case=case,
                position=position,
                defaults={
                    "action": action,
                    "expected_result": expected,
                    "is_active": True,
                },
            )
        case.steps.filter(position__gt=len(steps)).update(is_active=False)
        return case


    def _seed_executions(self, *, project, actor, login_case, checkout_case):
        if Execution.objects.filter(project=project).exists():
            self.stdout.write(
                "Execuções já existem para o projeto; mantendo-as."
            )
            return

        run_a = execution_services.start_execution(
            actor=actor,
            project=project,
            case_ids=[login_case.pk, checkout_case.pk],
        )
        self._set_outcomes(
            run_a, login_case, [StepOutcome.PASSED, StepOutcome.PASSED]
        )
        self._set_outcomes(
            run_a,
            checkout_case,
            [StepOutcome.PASSED, StepOutcome.FAILED, None],
        )
        execution_services.complete_execution(execution=run_a, actor=actor)

        run_b = execution_services.start_execution(
            actor=actor,
            project=project,
            case_ids=[login_case.pk],
        )
        self._set_outcomes(
            run_b, login_case, [StepOutcome.PASSED, StepOutcome.PASSED]
        )
        execution_services.complete_execution(execution=run_b, actor=actor)

    def _set_outcomes(self, execution, source_case, outcomes):
        execution_case = execution.execution_cases.get(source_test_case=source_case)
        observation = "Falha simulada pela massa de teste E2E."
        for position, outcome in enumerate(outcomes, start=1):
            if outcome is None:
                continue
            step = execution_case.steps.get(snapshot_position=position)
            execution_services.update_step_result(
                execution=execution,
                step=step,
                actor=execution.created_by,
                outcome=outcome,
                observation=observation if outcome != StepOutcome.PASSED else "",
            )


    def _apply_scenario(self, project, scenario):
        if scenario == "base":
            return
        report = (
            Report.objects.filter(execution__project=project)
            .order_by("-execution__started_at", "-pk")
            .first()
        )
        if report is None:
            self.stdout.write(
                self.style.WARNING("Sem relatório para aplicar o cenário; ignorando.")
            )
            return

        if scenario == "ai-unavailable":
            report.ai_status = AIStatus.UNAVAILABLE
            report.analysis = None
            report.failure_category = FailureCategory.CONFIGURATION
        elif scenario == "ai-pending":
            report.ai_status = AIStatus.PENDING
            report.analysis = None
            report.failure_category = None
        elif scenario == "ai-available":
            report.ai_status = AIStatus.AVAILABLE
            report.analysis = _sample_analysis(insufficient=False)
            report.failure_category = None
        elif scenario == "ai-insufficient":
            report.ai_status = AIStatus.AVAILABLE
            report.analysis = _sample_analysis(insufficient=True)
            report.failure_category = None
        report.save(
            update_fields=["ai_status", "analysis", "failure_category", "updated_at"]
        )


    def _report(self, admin, test1, test2, test3, project, password):
        completed = Execution.objects.filter(
            project=project, state=ExecutionState.COMPLETED
        ).count()
        report = Report.objects.filter(execution__project=project).first()
        write = self.stdout.write
        write(self.style.SUCCESS("Massa E2E pronta."))
        write(f"  Projeto: {project.name} (pk={project.pk})")
        write(
            "  Usuários: "
            f"{admin.email} (ADMIN), {test1.email} e {test2.email} (equipe), "
            f"{test3.email} (fora da equipe)"
        )
        write(f"  Execuções concluídas: {completed}")
        if report is not None:
            write(
                "  Relatório: "
                f"selecionados={report.selected_cases}, "
                f"aprovados={report.approved_cases}, "
                f"reprovados={report.reproved_cases}, "
                f"taxa={report.success_rate}%, IA={report.ai_status_label}"
            )
        password_source = PASSWORD_ENV if os.environ.get(PASSWORD_ENV) else "padrão"
        write(f"  Senha dos usuários: {password_source} ({password})")


def _sample_analysis(insufficient: bool) -> dict:
    if insufficient:
        return {
            "executive_summary": "",
            "observed_patterns": [],
            "cause_hypotheses": [],
            "suggestions": [],
            "insufficient_evidence": True,
            "historical_comparison": None,
        }
    return {
        "executive_summary": (
            "O ciclo teve 1 caso aprovado e 1 reprovado; a falha ocorreu no "
            "fluxo de checkout."
        ),
        "observed_patterns": [
            {
                "description": "O passo de pagamento falhou enquanto os passos de "
                "navegação e carrinho passaram.",
                "references": ["Checkout", "Passo 2"],
            }
        ],
        "cause_hypotheses": [
            {
                "description": "Mudança no provedor de pagamento pode ter alterado "
                "o retorno esperado.",
                "references": ["Checkout", "Passo 2"],
            }
        ],
        "suggestions": [
            "Reproduzir o checkout com dados de pagamento válidos e inspecionar a "
            "resposta do provedor.",
        ],
        "insufficient_evidence": False,
        "historical_comparison": {
            "summary": "Taxa de sucesso caiu em relação ao ciclo anterior.",
        },
    }
