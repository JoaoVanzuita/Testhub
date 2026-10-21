from html.parser import HTMLParser

from django.test import Client, TestCase
from django.urls import reverse

from apps.executions import services
from apps.executions.models import Execution

from .support import (
    assign,
    make_admin,
    make_case,
    make_project,
    make_step,
    make_user,
    start_execution,
)

LABELABLE_TAGS = {"input", "select", "textarea"}
IGNORED_INPUT_TYPES = {"hidden", "submit", "button", "reset", "image"}


class _AccessibilityCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.html_lang = None
        self.has_viewport = False
        self.main_ids = []
        self.skip_hrefs = []
        self.label_targets = set()
        self.controls = []
        self.tables = 0
        self.captions = 0
        self.h1_count = 0
        self.aria_live_count = 0
        self._label_depth = 0
        self._table_depth = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if "aria-live" in attributes:
            self.aria_live_count += 1
        if tag == "html":
            self.html_lang = attributes.get("lang")
        elif tag == "meta" and attributes.get("name") == "viewport":
            self.has_viewport = True
        elif tag == "main" and attributes.get("id"):
            self.main_ids.append(attributes["id"])
        elif tag == "a":
            href = attributes.get("href") or ""
            if href.startswith("#"):
                self.skip_hrefs.append(href)
        elif tag == "label":
            self._label_depth += 1
            if attributes.get("for"):
                self.label_targets.add(attributes["for"])
        elif tag in LABELABLE_TAGS:
            self.controls.append((tag, attributes, self._label_depth > 0))
        elif tag == "h1":
            self.h1_count += 1
        elif tag == "table":
            self._table_depth += 1
            self.tables += 1
        elif tag == "caption" and self._table_depth:
            self.captions += 1

    def handle_endtag(self, tag):
        if tag == "label":
            self._label_depth = max(0, self._label_depth - 1)
        elif tag == "table":
            self._table_depth = max(0, self._table_depth - 1)


def collect_html(html):
    collector = _AccessibilityCollector()
    collector.feed(html)
    collector.close()
    return collector


class RenderedPageAccessibilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_admin()
        cls.owner = make_user("owner@example.com")
        cls.project = make_project("Projeto Acessível")
        assign(cls.project, cls.owner)
        cls.case = make_case(cls.project, "Caso Acessível")
        make_step(cls.case, 1, "Passo um", "Resultado um")
        make_step(cls.case, 2, "Passo dois", "Resultado dois")

        for _ in range(2):
            execution = start_execution(cls.owner, cls.project, [cls.case])
            services.complete_execution(execution=execution, actor=cls.owner)
        cls.completed = list(
            Execution.objects.filter(project=cls.project).order_by("pk")
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.owner)
        self.live = start_execution(self.owner, self.project, [self.case])


    def _assert_accessible(self, response):
        self.assertEqual(response.status_code, 200)
        collector = collect_html(response.content.decode())

        self.assertEqual(collector.html_lang, "pt-br", "missing document language")
        self.assertTrue(collector.has_viewport, "missing responsive viewport meta")
        self.assertIn("#conteudo", collector.skip_hrefs, "missing skip link")
        self.assertIn("conteudo", collector.main_ids, "missing main landmark")
        self.assertEqual(collector.h1_count, 1, "expected exactly one <h1>")
        self.assertEqual(
            collector.captions,
            collector.tables,
            "every data table must carry a <caption>",
        )

        for tag, attributes, wrapped in collector.controls:
            if tag == "input" and attributes.get("type") in IGNORED_INPUT_TYPES:
                continue
            control_id = attributes.get("id")
            labelled = (
                wrapped
                or (control_id and control_id in collector.label_targets)
                or attributes.get("aria-label")
                or attributes.get("aria-labelledby")
            )
            self.assertTrue(
                labelled, f"form control without a label: {tag} {attributes}"
            )
        return collector

    def _get(self, name, *args):
        return self.client.get(reverse(name, args=list(args)))


    def test_login_page(self):
        self._assert_accessible(Client().get(reverse("accounts:login")))

    def test_project_pages(self):
        self._assert_accessible(self._get("projects:project_list"))
        self._assert_accessible(
            self._get("projects:project_detail", self.project.pk)
        )

    def test_administrator_pages(self):
        admin_client = Client()
        admin_client.force_login(self.admin)

        self._assert_accessible(admin_client.get(reverse("projects:project_create")))
        self._assert_accessible(
            admin_client.get(
                reverse("projects:memberships", args=[self.project.pk])
            )
        )
        self._assert_accessible(admin_client.get(reverse("accounts:user_list")))
        self._assert_accessible(admin_client.get(reverse("accounts:user_create")))
        self._assert_accessible(
            admin_client.get(reverse("projects:project_edit", args=[self.project.pk]))
        )

    def test_case_pages(self):
        self._assert_accessible(self._get("testcases:case_list", self.project.pk))
        self._assert_accessible(self._get("testcases:case_detail", self.case.pk))
        self._assert_accessible(self._get("testcases:case_create", self.project.pk))
        self._assert_accessible(self._get("testcases:case_edit", self.case.pk))

    def test_execution_and_report_pages(self):
        self._assert_accessible(
            self._get("executions:execution_create", self.project.pk)
        )
        self._assert_accessible(
            self._get("executions:execution_detail", self.live.pk)
        )
        self._assert_accessible(
            self._get("executions:execution_detail", self.completed[0].pk)
        )
        self._assert_accessible(
            self._get("executions:report_detail", self.completed[0].pk)
        )

    def test_history_pages(self):
        self._assert_accessible(self._get("executions:history_list", self.project.pk))
        self._assert_accessible(
            self._get("executions:history_compare", self.project.pk)
        )

    def test_profile_and_password_pages(self):
        self._assert_accessible(self._get("accounts:profile"))
        self._assert_accessible(self._get("accounts:password_change"))


    def test_live_result_entry_announces_feedback(self):
        response = self._get("executions:execution_detail", self.live.pk)
        collector = self._assert_accessible(response)
        html = response.content.decode()

        self.assertGreaterEqual(collector.aria_live_count, 1)
        self.assertIn("data-step-feedback", html)
        self.assertIn('aria-live="polite"', html)
        self.assertIn("Salvar resultado", html)

    def test_result_states_are_distinguished_by_text_not_colour(self):
        html = self._get(
            "executions:report_detail", self.completed[0].pk
        ).content.decode()

        self.assertIn("Indisponível", html)
        self.assertIn("status--off", html)

    def test_step_editor_exposes_move_controls_with_text(self):
        html = self._get("testcases:case_edit", self.case.pk).content.decode()

        for control in ["Mover para cima", "Mover para baixo", "Remover passo"]:
            self.assertIn(control, html)
