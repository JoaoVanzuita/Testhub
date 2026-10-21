from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import Role
from apps.executions import reporting, services
from apps.executions.models import (
    AIStatus,
    Report,
    StepOutcome,
)
from apps.projects.models import Project, ProjectMembership
from apps.testcases.models import TestCase as CaseModel, TestStep

User = get_user_model()

PASSWORD = "uma-senha-bem-segura-123"


def make_user(email, role=Role.TESTER):
    return User.objects.create_user(
        email=email, name="Usuário de Teste", password=PASSWORD, role=role
    )


def make_project(name="Projeto Alpha"):
    return Project.objects.create(name=name, description="Descrição do projeto")


def assign(project, user):
    return ProjectMembership.objects.create(project=project, user=user)


def make_case(project, title="Caso de Teste"):
    return CaseModel.objects.create(
        project=project,
        title=title,
        description="Descrição do caso",
        preconditions="Nenhuma pré-condição",
    )


def make_step(case, position, action="Ação", expected_result="Esperado"):
    return TestStep.objects.create(
        test_case=case,
        position=position,
        action=action,
        expected_result=expected_result,
    )


class SuccessRateFormulaTests(TestCase):
    def test_formula_and_rounding(self):
        self.assertEqual(
            reporting.calculate_success_rate(1, 4), Decimal("25.00")
        )
        self.assertEqual(
            reporting.calculate_success_rate(1, 3), Decimal("33.33")
        )
        self.assertEqual(
            reporting.calculate_success_rate(2, 3), Decimal("66.67")
        )
        self.assertEqual(
            reporting.calculate_success_rate(0, 5), Decimal("0.00")
        )
        self.assertEqual(
            reporting.calculate_success_rate(5, 5), Decimal("100.00")
        )

    def test_empty_denominator_is_zero(self):
        self.assertEqual(reporting.calculate_success_rate(0, 0), Decimal("0.00"))


class ReportMetricsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@example.com")
        cls.project = make_project()
        assign(cls.project, cls.owner)
        cls.cases = {}
        for key, title in [
            ("approved", "Caso Aprovado"),
            ("reproved", "Caso Reprovado"),
            ("blocked", "Caso Bloqueado"),
            ("not_started", "Caso Não Iniciado"),
        ]:
            case = make_case(cls.project, title)
            make_step(case, 1, "Passo único", "Resultado esperado")
            cls.cases[key] = case

    def setUp(self):
        self.execution = services.start_execution(
            actor=self.owner,
            project=self.project,
            case_ids=[case.pk for case in self.cases.values()],
        )

    def _set(self, key, outcome):
        execution_case = self.execution.execution_cases.get(
            source_test_case=self.cases[key]
        )
        step = execution_case.steps.get()
        services.update_step_result(
            execution=self.execution, step=step, actor=self.owner, outcome=outcome
        )

    def _complete(self):
        services.complete_execution(execution=self.execution, actor=self.owner)
        return Report.objects.get(execution=self.execution)

    def test_counts_and_success_rate_for_four_case_run(self):
        self._set("approved", StepOutcome.PASSED)
        self._set("reproved", StepOutcome.FAILED)
        self._set("blocked", StepOutcome.BLOCKED)

        report = self._complete()

        self.assertEqual(report.selected_cases, 4)
        self.assertEqual(report.approved_cases, 1)
        self.assertEqual(report.reproved_cases, 1)
        self.assertEqual(report.pending_cases, 1)
        self.assertEqual(report.not_started_cases, 1)
        self.assertEqual(report.success_rate, Decimal("25.00"))

        self.assertEqual(report.passed_steps, 1)
        self.assertEqual(report.failed_steps, 1)
        self.assertEqual(report.blocked_steps, 1)
        self.assertEqual(report.unevaluated_steps, 1)

        self.assertEqual(
            report.approved_cases
            + report.reproved_cases
            + report.pending_cases
            + report.not_started_cases,
            report.selected_cases,
        )

    def test_completion_creates_exactly_one_report(self):
        self._complete()

        with self.assertRaises(services.ImmutableExecutionError):
            services.complete_execution(
                execution=self.execution, actor=self.owner
            )

        self.assertEqual(Report.objects.filter(execution=self.execution).count(), 1)

    def test_metrics_follow_the_persisted_statuses(self):
        self._set("approved", StepOutcome.PASSED)
        self._set("approved", StepOutcome.PASSED)
        self._set("reproved", StepOutcome.FAILED)
        self._set("blocked", StepOutcome.BLOCKED)
        report = self._complete()

        self.assertEqual(report.selected_cases, 4)
        self.assertEqual(report.approved_cases, 1)
        self.assertEqual(report.success_rate, Decimal("25.00"))

    def test_report_is_unavailable_without_an_approved_provider(self):
        report = self._complete()

        self.assertEqual(report.ai_status, AIStatus.UNAVAILABLE)
        self.assertIsNone(report.analysis)
        self.assertFalse(report.has_analysis)
        self.assertEqual(report.ai_status_label, "Indisponível")

    def test_failure_observations_are_counted(self):
        for key, outcome, observation in [
            ("reproved", StepOutcome.FAILED, "  Falha ao salvar o registro.  "),
            ("blocked", StepOutcome.BLOCKED, "Ambiente indisponível."),
        ]:
            execution_case = self.execution.execution_cases.get(
                source_test_case=self.cases[key]
            )
            services.update_step_result(
                execution=self.execution,
                step=execution_case.steps.get(),
                actor=self.owner,
                outcome=outcome,
                observation=observation,
            )

        approved_case = self.execution.execution_cases.get(
            source_test_case=self.cases["approved"]
        )
        services.update_step_result(
            execution=self.execution,
            step=approved_case.steps.get(),
            actor=self.owner,
            outcome=StepOutcome.PASSED,
            observation="Observação em passo aprovado.",
        )

        report = self._complete()

        self.assertEqual(report.failure_observations, 2)

    def test_failure_observations_ignore_blank_observations(self):
        self._set("reproved", StepOutcome.FAILED)
        self._set("blocked", StepOutcome.BLOCKED)

        report = self._complete()

        self.assertEqual(report.failure_observations, 0)


class ReportConstraintTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@example.com")
        cls.project = make_project()
        assign(cls.project, cls.owner)
        cls.case = make_case(cls.project)
        make_step(cls.case, 1)

    def setUp(self):
        self.execution = services.start_execution(
            actor=self.owner, project=self.project, case_ids=[self.case.pk]
        )

    def test_inconsistent_case_counts_are_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Report.objects.create(
                execution=self.execution,
                selected_cases=2,
                approved_cases=1,
                ai_status=AIStatus.UNAVAILABLE,
            )

    def test_analysis_requires_available_status(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Report.objects.create(
                execution=self.execution,
                ai_status=AIStatus.UNAVAILABLE,
                analysis={"executive_summary": "não deveria existir"},
            )

    def test_success_rate_out_of_range_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Report.objects.create(
                execution=self.execution,
                success_rate=Decimal("150.00"),
                ai_status=AIStatus.UNAVAILABLE,
            )

    def test_one_to_one_execution_is_enforced(self):
        Report.objects.create(execution=self.execution, ai_status=AIStatus.UNAVAILABLE)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Report.objects.create(
                execution=self.execution, ai_status=AIStatus.UNAVAILABLE
            )


class ReportViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@example.com")
        cls.project = make_project()
        assign(cls.project, cls.owner)
        cls.case = make_case(cls.project)
        make_step(cls.case, 1)

    def setUp(self):
        self.execution = services.start_execution(
            actor=self.owner, project=self.project, case_ids=[self.case.pk]
        )

    def test_report_view_requires_a_completed_report(self):
        client = Client()
        client.force_login(self.owner)

        response = client.get(
            reverse("executions:report_detail", args=[self.execution.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_report_view_shows_recorded_metrics(self):
        services.complete_execution(execution=self.execution, actor=self.owner)
        client = Client()
        client.force_login(self.owner)

        response = client.get(
            reverse("executions:report_detail", args=[self.execution.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Métricas registradas")
        self.assertContains(response, "0.00%")
        self.assertContains(response, "Indisponível")
        self.assertContains(response, "Com observação de falha/bloqueio")

    def _available_report(self, *, insufficient_evidence=False):
        services.complete_execution(execution=self.execution, actor=self.owner)
        analysis = {
            "executive_summary": "Resumo do provedor simulado.",
            "observed_patterns": [
                {
                    "kind": "observed",
                    "description": "Falha recorrente no login.",
                    "references": ["case-1/step-1"],
                }
            ],
            "cause_hypotheses": [
                {
                    "kind": "hypothesis",
                    "description": "Suspeita de instabilidade de rede.",
                    "references": ["case-1/step-1"],
                }
            ],
            "suggestions": ["Reproduzir em outro ambiente."],
            "historical_comparison": None,
            "insufficient_evidence": insufficient_evidence,
        }
        Report.objects.filter(execution=self.execution).update(
            ai_status=AIStatus.AVAILABLE, analysis=analysis, failure_category=None
        )

    def test_report_renders_available_analysis_with_hypothesis_label(self):
        self._available_report()
        client = Client()
        client.force_login(self.owner)

        response = client.get(
            reverse("executions:report_detail", args=[self.execution.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Disponível")
        self.assertContains(response, "Resumo do provedor simulado.")
        self.assertContains(response, "Falha recorrente no login.")
        self.assertContains(response, "Hipótese / interpretação")
        self.assertContains(response, "Suspeita de instabilidade de rede.")

    def test_report_marks_insufficient_evidence_as_inconclusive(self):
        self._available_report(insufficient_evidence=True)
        client = Client()
        client.force_login(self.owner)

        response = client.get(
            reverse("executions:report_detail", args=[self.execution.pk])
        )

        self.assertContains(response, "Análise inconclusiva")

    def test_completion_message_reports_the_ai_status(self):
        client = Client()
        client.force_login(self.owner)

        response = client.post(
            reverse("executions:execution_complete", args=[self.execution.pk]),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Análise de IA: Indisponível")

    def test_readonly_page_shows_the_ai_status(self):
        services.complete_execution(execution=self.execution, actor=self.owner)
        client = Client()
        client.force_login(self.owner)

        response = client.get(
            reverse("executions:execution_detail", args=[self.execution.pk])
        )

        self.assertContains(response, "data-ai-status-notice")
        self.assertContains(response, "Indisponível")

    def test_report_view_denies_unassigned_user(self):
        services.complete_execution(execution=self.execution, actor=self.owner)
        stranger = make_user("stranger@example.com")
        client = Client()
        client.force_login(stranger)

        response = client.get(
            reverse("executions:report_detail", args=[self.execution.pk])
        )

        self.assertEqual(response.status_code, 404)
