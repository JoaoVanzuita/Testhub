from django.test import Client, TestCase
from django.urls import reverse

from .support import assign, make_case, make_project, make_step, make_user


class CaseEditorStepCollapseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tester = make_user("tester@example.com")
        cls.project = make_project("Projeto Editor")
        assign(cls.project, cls.tester)
        cls.case = make_case(cls.project, "Caso editável")
        make_step(cls.case, 1, "Abrir a tela", "Tela aberta")
        make_step(cls.case, 2, "Clicar em salvar", "Registro salvo")

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.tester)

    def _html(self, name, *args):
        response = self.client.get(reverse(name, args=list(args)))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_edit_page_offers_collapse_controls(self):
        html = self._html("testcases:case_edit", self.case.pk)

        # One global control, plus a per-step toggle for every rendered form
        # (2 saved steps + 1 empty extra form) and its template copy.
        self.assertEqual(html.count("data-steps-collapse-toggle"), 1)
        self.assertEqual(html.count("data-step-toggle"), 4)
        self.assertIn("Recolher todos", html)

    def test_collapse_controls_are_always_rendered(self):
        html = self._html("testcases:case_edit", self.case.pk)

        # The toggles follow the editor's other JS-only controls (move/remove/
        # add): they are always visible and, without JavaScript, simply inert.
        self.assertIn("data-steps-collapse-toggle", html)
        self.assertIn("data-step-toggle", html)
        self.assertNotIn("data-step-toggle hidden", html)
        self.assertNotIn("data-steps-collapse-toggle hidden", html)

    def test_each_step_body_has_a_stable_id_matching_its_toggle(self):
        html = self._html("testcases:case_edit", self.case.pk)

        for index in range(3):
            self.assertIn(f'id="step-body-steps-{index}"', html)
            self.assertIn(f'aria-controls="step-body-steps-{index}"', html)

    def test_create_page_also_offers_collapse_controls(self):
        html = self._html("testcases:case_create", self.project.pk)

        self.assertIn("data-steps-collapse-toggle", html)
        self.assertIn("data-step-toggle", html)

    def test_existing_move_and_remove_controls_are_preserved(self):
        html = self._html("testcases:case_edit", self.case.pk)

        for control in ["Mover para cima", "Mover para baixo", "Remover passo"]:
            self.assertIn(control, html)
