from django.test import Client, TestCase
from django.urls import reverse

from apps.executions import services
from apps.executions.models import StepOutcome

from .support import (
    assign,
    complete_execution,
    make_case,
    make_project,
    make_step,
    make_user,
    start_execution,
)

LONG_TOKEN = "evidencia-" + "x" * 220


class CompletedExecutionReadonlyPresentationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@example.com")
        cls.project = make_project("Projeto Concluído")
        assign(cls.project, cls.owner)

        cls.long_action = "Executar o fluxo completo " + "y" * 200

        cls.cases = {}
        for key, title in [
            ("approved", "Caso Aprovado"),
            ("reproved", "Caso Reprovado"),
            ("pending", "Caso Pendente"),
            ("not_started", "Caso Não Iniciado"),
        ]:
            case = make_case(cls.project, title)
            action = cls.long_action if key == "reproved" else f"Primeiro passo — {title}"
            make_step(case, 1, action, "Resultado esperado")
            make_step(case, 2, f"Segundo passo — {title}", "Outro resultado")
            cls.cases[key] = case

        cls.execution = start_execution(
            cls.owner, cls.project, list(cls.cases.values())
        )

        cls.failed_observation = "Falha ao salvar " + LONG_TOKEN
        cls.blocked_observation = "Ambiente indisponível durante o teste."

        cls._record("approved", [StepOutcome.PASSED, StepOutcome.PASSED])
        cls._record(
            "reproved",
            [StepOutcome.FAILED, None],
            observation=cls.failed_observation,
        )
        cls._record(
            "pending",
            [StepOutcome.BLOCKED, StepOutcome.PASSED],
            observation=cls.blocked_observation,
        )

        complete_execution(cls.execution, cls.owner)

    @classmethod
    def _record(cls, key, outcomes, observation=""):
        execution_case = cls.execution.execution_cases.get(
            source_test_case=cls.cases[key]
        )
        steps = list(execution_case.steps.order_by("snapshot_position"))
        observation_applied = False
        for step, outcome in zip(steps, outcomes):
            if outcome is None:
                continue
            note = ""
            if observation and not observation_applied:
                note = observation
                observation_applied = True
            services.update_step_result(
                execution=cls.execution,
                step=step,
                actor=cls.owner,
                outcome=outcome,
                observation=note,
            )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.owner)

    def _html(self):
        response = self.client.get(
            reverse("executions:execution_detail", args=[self.execution.pk])
        )
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_finished_run_offers_no_result_controls(self):
        html = self._html()

        self.assertIn("Concluída", html)
        self.assertIn("readonly-notice", html)
        self.assertNotIn("data-step-result-form", html)
        self.assertNotIn("data-step-action", html)
        self.assertNotIn("Salvar resultado", html)
        self.assertNotIn("<textarea", html)
        self.assertIn("data-case-board", html)

    def test_outcomes_are_labelled_and_styled_by_text(self):
        html = self._html()

        for label in ["Passou", "Falhou", "Bloqueado", "Não avaliado"]:
            self.assertIn(label, html)
        for modifier in [
            "status--ok",
            "status--danger",
            "status--warning",
            "status--off",
        ]:
            self.assertIn(modifier, html)
        for outcome in ["PASSED", "FAILED", "BLOCKED"]:
            self.assertIn(f'data-outcome="{outcome}"', html)

    def test_steps_keep_the_snapshot_order(self):
        html = self._html()

        self.assertLess(html.index("1.</span>"), html.index("2.</span>"))

    def test_observation_renders_with_its_step(self):
        html = self._html()

        self.assertIn("execution-step__observation", html)
        self.assertIn(self.failed_observation, html)
        self.assertIn(self.blocked_observation, html)

    def test_report_history_and_project_links_are_present(self):
        html = self._html()

        self.assertIn(
            reverse("executions:report_detail", args=[self.execution.pk]), html
        )
        self.assertIn(
            reverse("executions:history_list", args=[self.project.pk]), html
        )
        self.assertIn(
            reverse("projects:project_detail", args=[self.project.pk]), html
        )

    def test_responsible_history_is_preserved(self):
        html = self._html()

        self.assertIn("Histórico de responsáveis pela condução", html)
        self.assertIn(self.owner.name, html)

    def test_summary_reports_progress_and_case_counts(self):
        html = self._html()

        self.assertIn("5 de 8 passos avaliados", html)
        for label in [
            "casos",
            "aprovados",
            "reprovados",
            "pendentes",
            "não iniciados",
        ]:
            self.assertIn(label, html)

    def test_long_snapshot_content_is_kept_in_the_markup(self):
        html = self._html()

        self.assertIn(self.long_action, html)
        self.assertIn(LONG_TOKEN, html)
