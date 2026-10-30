from django.test import Client, TestCase
from django.urls import reverse

from .support import (
    assign,
    complete_execution,
    make_case,
    make_project,
    make_step,
    make_user,
    start_execution,
)


class CaseRunnerModalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@example.com")
        cls.project = make_project("Projeto Modal")
        assign(cls.project, cls.owner)
        cls.case = make_case(cls.project, "Caso Modal")
        make_step(cls.case, 1, "Passo um", "Resultado um")
        make_step(cls.case, 2, "Passo dois", "Resultado dois")

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.owner)
        self.execution = start_execution(self.owner, self.project, [self.case])

    def _html(self, execution):
        response = self.client.get(
            reverse("executions:execution_detail", args=[execution.pk])
        )
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_live_runner_renders_each_case_in_a_closed_dialog(self):
        html = self._html(self.execution)

        execution_case = self.execution.execution_cases.get()
        self.assertIn('class="case-modal"', html)
        self.assertIn(f'data-case-modal data-pk="{execution_case.pk}"', html)
        self.assertIn(f'id="case-modal-{execution_case.pk}-title"', html)
        # The dialog is closed by default: the case is not listed below the board.
        self.assertNotIn(" open>", html)

    def test_board_cards_open_the_modal_on_the_live_runner(self):
        html = self._html(self.execution)

        execution_case = self.execution.execution_cases.get()
        self.assertIn(f'data-case-open="{execution_case.pk}"', html)
        self.assertIn('aria-haspopup="dialog"', html)
        self.assertIn(">Abrir</button>", html)

    def test_step_result_forms_stay_inside_the_modal(self):
        html = self._html(self.execution)

        self.assertIn("data-step-result-form", html)
        self.assertIn("Salvar resultado", html)
        self.assertIn("data-step-feedback", html)

    def test_completed_runner_renders_cases_in_closed_dialogs(self):
        complete_execution(self.execution, self.owner)
        html = self._html(self.execution)

        execution_case = self.execution.execution_cases.get()
        self.assertIn('class="case-modal"', html)
        self.assertIn(f'data-case-modal data-pk="{execution_case.pk}"', html)
        self.assertIn(f'data-case-open="{execution_case.pk}"', html)
        self.assertNotIn("data-step-result-form", html)
        self.assertNotIn("Salvar resultado", html)
