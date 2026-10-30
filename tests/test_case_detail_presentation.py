from django.test import Client, TestCase
from django.urls import reverse

from .support import assign, make_case, make_project, make_step, make_user


class CaseDetailStepCollapseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tester = make_user("tester@example.com")
        cls.project = make_project("Projeto Colapsável")
        assign(cls.project, cls.tester)
        cls.case = make_case(cls.project, "Caso com passos")
        make_step(cls.case, 1, "Abrir a tela", "Tela aberta")
        make_step(
            cls.case,
            2,
            "Clicar em salvar",
            "Registro salvo",
            is_active=False,
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.tester)

    def _html(self):
        response = self.client.get(
            reverse("testcases:case_detail", args=[self.case.pk])
        )
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_steps_render_as_collapsible_details(self):
        html = self._html()

        self.assertEqual(html.count("<details"), 2)
        self.assertEqual(html.count("<summary"), 2)
        self.assertIn("data-case-step", html)

    def test_expected_results_are_kept_in_the_markup(self):
        html = self._html()

        self.assertIn("Tela aberta", html)
        self.assertIn("Registro salvo", html)

    def test_toggle_control_starts_hidden_for_progressive_enhancement(self):
        html = self._html()

        self.assertIn("data-case-steps-toggle", html)
        self.assertIn('aria-expanded="false"', html)
        self.assertIn("Expandir todos", html)

    def test_inactive_step_keeps_the_inactive_badge(self):
        html = self._html()

        self.assertIn("Inativo", html)

    def test_steps_keep_order(self):
        html = self._html()

        self.assertLess(html.index("Abrir a tela"), html.index("Clicar em salvar"))
