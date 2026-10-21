from django.template.loader import render_to_string
from django.test import Client, TestCase
from django.urls import reverse

from apps.executions import services
from apps.executions.models import CaseStatus, StepOutcome
from apps.executions.templatetags.execution_ui import case_board_columns

from .support import (
    assign,
    make_case,
    make_project,
    make_step,
    make_user,
    start_execution,
)


class CaseBoardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@example.com")
        cls.project = make_project("Projeto Quadro")
        assign(cls.project, cls.owner)

        cls.source_cases = []
        for index in range(12):
            case = make_case(cls.project, title=f"Caso {index + 1:02d}")
            make_step(case, 1, "Passo um", "Resultado um")
            make_step(case, 2, "Passo dois", "Resultado dois")
            cls.source_cases.append(case)

        cls.execution = start_execution(cls.owner, cls.project, cls.source_cases)

        planned = {}
        for index in range(0, 3):
            planned[index] = (StepOutcome.PASSED, StepOutcome.PASSED)
        for index in range(3, 6):
            planned[index] = (StepOutcome.FAILED, None)
        for index in range(6, 9):
            planned[index] = (None, None)
        planned[9] = (StepOutcome.PASSED, None)
        planned[10] = (StepOutcome.PASSED, None)
        planned[11] = (StepOutcome.PASSED, StepOutcome.BLOCKED)

        execution_cases = {
            execution_case.source_test_case_id: execution_case
            for execution_case in cls.execution.execution_cases.all()
        }
        for index, outcomes in planned.items():
            execution_case = execution_cases[cls.source_cases[index].pk]
            steps = list(execution_case.steps.order_by("snapshot_position"))
            for step, outcome in zip(steps, outcomes):
                if outcome is None:
                    continue
                services.update_step_result(
                    execution=cls.execution,
                    step=step,
                    actor=cls.owner,
                    outcome=outcome,
                )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.owner)

    def _detail(self, execution=None):
        response = self.client.get(
            reverse(
                "executions:execution_detail",
                args=[(execution or self.execution).pk],
            )
        )
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_columns_group_cases_by_existing_derived_status(self):
        columns = case_board_columns(self.execution.execution_cases.all())

        self.assertEqual(
            [column["key"] for column in columns],
            [
                CaseStatus.NOT_STARTED.value,
                CaseStatus.PENDING.value,
                CaseStatus.APPROVED.value,
                CaseStatus.REPROVED.value,
            ],
        )
        counts = {column["key"]: column["count"] for column in columns}
        self.assertEqual(counts[CaseStatus.NOT_STARTED.value], 3)
        self.assertEqual(counts[CaseStatus.PENDING.value], 3)
        self.assertEqual(counts[CaseStatus.APPROVED.value], 3)
        self.assertEqual(counts[CaseStatus.REPROVED.value], 3)

    def test_board_renders_every_case_with_an_open_link(self):
        html = self._detail()
        detail_url = reverse(
            "executions:execution_detail", args=[self.execution.pk]
        )

        self.assertIn("data-case-board", html)
        self.assertEqual(html.count("data-board-card="), 12)
        self.assertNotIn("Read-only monitoring board", html)

        for label in ["Não iniciado", "Pendente", "Aprovado", "Reprovado"]:
            self.assertIn(label, html)

        for execution_case in self.execution.execution_cases.all():
            self.assertIn(f'data-board-card="{execution_case.pk}"', html)
            self.assertIn(
                f'href="{detail_url}#execution-case-{execution_case.pk}-title"',
                html,
            )

    def test_blocked_step_stays_pending_with_a_secondary_hint(self):
        html = self._detail()

        blocked_case = self.execution.execution_cases.get(
            source_test_case=self.source_cases[11]
        )
        plain_pending = self.execution.execution_cases.get(
            source_test_case=self.source_cases[9]
        )
        self.assertEqual(blocked_case.status, CaseStatus.PENDING.value)
        self.assertEqual(plain_pending.status, CaseStatus.PENDING.value)

        self.assertNotIn('data-board-column="CASE_BLOCKED"', html)

        blocked_card = self._card(html, blocked_case.pk)
        self.assertIn("Passo bloqueado", blocked_card)
        self.assertNotIn("hidden", blocked_card)

        plain_card = self._card(html, plain_pending.pk)
        self.assertIn("hidden", plain_card)

    def _card(self, html, case_pk):
        start = html.index(f'data-board-card="{case_pk}"')
        return html[start:html.index("</li>", start)]

    def test_completed_execution_keeps_the_board_read_only(self):
        services.complete_execution(execution=self.execution, actor=self.owner)
        html = self._detail()

        self.assertIn("Quadro da execução", html)
        self.assertIn("data-case-board", html)
        self.assertNotIn("data-step-result-form", html)
        self.assertNotIn("data-step-action", html)
        self.assertNotIn("Salvar resultado", html)

    def test_empty_board_partial_is_clear(self):
        html = render_to_string(
            "executions/_case_board.html", {"execution_cases": []}
        )

        self.assertIn("Nenhum caso selecionado nesta execução.", html)
        self.assertNotIn("data-case-board", html)
