import re
from html.parser import HTMLParser

from django.template.loader import render_to_string
from django.test import Client, TestCase
from django.urls import reverse

from .support import assign, make_admin, make_case, make_project, make_step, make_user

EMPTY_SUMMARY = "Nenhum resumo de erros deveria ser renderizado quando o formulário é válido."


class _IdAndAriaCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids = []
        self.describedby = []
        self.invalid_count = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        element_id = attributes.get("id")
        if element_id:
            self.ids.append(element_id)
        if attributes.get("aria-describedby"):
            self.describedby.extend(attributes["aria-describedby"].split())
        if attributes.get("aria-invalid") == "true":
            self.invalid_count += 1

    handle_startendtag = handle_starttag


def _collect(html):
    collector = _IdAndAriaCollector()
    collector.feed(html)
    collector.close()
    return collector


SUMMARY_RE = re.compile(
    r'<div class="form-errors"[^>]*\bid="form-errors"[^>]*>(.*?)</div>',
    re.DOTALL,
)
SUMMARY_HREF_RE = re.compile(r'href="#([^"]+)"')


def _summary_block(html):
    match = SUMMARY_RE.search(html)
    if match is None:
        return None
    return match.group(0)


class FormErrorSummaryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_admin()
        cls.owner = make_user("owner@example.com")
        cls.project = make_project("Projeto Formulários")
        assign(cls.project, cls.owner)
        cls.case = make_case(cls.project, "Caso Formulários")
        make_step(cls.case, 1, "Passo um", "Resultado um")

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.owner)
        self.admin_client = Client()
        self.admin_client.force_login(self.admin)


    def _assert_error_contract(self, response, url, expect_invalid=True):
        self.assertEqual(response.status_code, 200, url)
        html = response.content.decode()
        collector = _collect(html)

        duplicates = sorted(
            {value for value in collector.ids if collector.ids.count(value) > 1}
        )
        self.assertEqual(duplicates, [], f"duplicate ids on {url}: {duplicates}")

        block = _summary_block(html)
        self.assertIsNotNone(block, f"missing #form-errors summary on {url}")
        self.assertIn('role="alert"', block, f"summary not assertive on {url}")
        self.assertIn('tabindex="-1"', block, f"summary not focusable on {url}")

        ids = set(collector.ids)
        for target in SUMMARY_HREF_RE.findall(block):
            self.assertIn(target, ids, f"summary link #{target} broken on {url}")

        for target in collector.describedby:
            self.assertIn(
                target, ids, f"aria-describedby #{target} missing on {url}"
            )

        if expect_invalid:
            self.assertGreater(
                collector.invalid_count, 0, f"no aria-invalid control on {url}"
            )


    def test_invalid_login_has_error_summary(self):
        response = Client().post(
            reverse("accounts:login"), {"username": "", "password": ""}
        )
        self._assert_error_contract(response, "login")

    def test_invalid_login_non_field_error_is_listed(self):
        response = Client().post(
            reverse("accounts:login"),
            {"username": "ninguem@example.com", "password": "senha-errada"},
        )
        html = response.content.decode()
        block = _summary_block(html)
        self.assertIsNotNone(block)
        items = re.findall(r"<li>(.*?)</li>", block, re.DOTALL)
        unlinked = [item for item in items if "href=" not in item]
        self.assertGreater(len(unlinked), 0, "non_field_errors not surfaced")

    def test_invalid_user_create_has_error_summary(self):
        response = self.admin_client.post(
            reverse("accounts:user_create"), {"email": "", "name": ""}
        )
        self._assert_error_contract(response, "user_create")

    def test_invalid_profile_has_error_summary(self):
        response = self.client.post(reverse("accounts:profile"), {"name": ""})
        self._assert_error_contract(response, "profile")

    def test_invalid_password_change_has_error_summary(self):
        response = self.client.post(
            reverse("accounts:password_change"),
            {"old_password": "", "new_password1": "", "new_password2": ""},
        )
        self._assert_error_contract(response, "password_change")

    def test_invalid_project_create_has_error_summary(self):
        response = self.admin_client.post(
            reverse("projects:project_create"), {"name": "", "description": ""}
        )
        self._assert_error_contract(response, "project_create")

    def test_invalid_membership_add_rerenders_with_summary(self):
        response = self.admin_client.post(
            reverse("projects:membership_add", args=[self.project.pk]),
            {"user": ""},
        )
        self._assert_error_contract(response, "membership_add")

    def test_invalid_case_create_has_error_summary_and_formset_ids(self):
        payload = {
            "title": "",
            "description": "",
            "preconditions": "",
            "steps-TOTAL_FORMS": "1",
            "steps-INITIAL_FORMS": "0",
            "steps-MIN_NUM_FORMS": "0",
            "steps-MAX_NUM_FORMS": "1000",
            "steps-0-position": "",
            "steps-0-action": "",
            "steps-0-expected_result": "",
            "steps-0-is_active": "on",
        }
        response = self.client.post(
            reverse("testcases:case_create", args=[self.project.pk]), payload
        )
        self._assert_error_contract(response, "case_create")

    def test_case_formset_non_form_error_has_summary(self):
        payload = {
            "title": "Caso sem passos",
            "description": "Descrição",
            "preconditions": "Nenhuma",
            "steps-TOTAL_FORMS": "1",
            "steps-INITIAL_FORMS": "0",
            "steps-MIN_NUM_FORMS": "0",
            "steps-MAX_NUM_FORMS": "1000",
            "steps-0-position": "1",
            "steps-0-action": "Ação",
            "steps-0-expected_result": "Esperado",
            "steps-0-is_active": "on",
            "steps-0-DELETE": "on",
        }
        response = self.client.post(
            reverse("testcases:case_create", args=[self.project.pk]), payload
        )
        html = response.content.decode()
        block = _summary_block(html)
        self.assertIsNotNone(block)
        self.assertIn("ao menos um passo", block)

    def test_invalid_execution_create_has_error_summary(self):
        response = self.client.post(
            reverse("executions:execution_create", args=[self.project.pk]), {}
        )
        self._assert_error_contract(response, "execution_create")

    def test_valid_form_has_no_summary(self):
        response = self.client.get(reverse("accounts:profile"))
        self.assertIsNone(_summary_block(response.content.decode()), EMPTY_SUMMARY)


class _Message:
    def __init__(self, text, tags):
        self.text = text
        self.tags = tags

    def __str__(self):
        return self.text


class FlashMessageLiveRegionTests(TestCase):
    def _render(self, *messages):
        return render_to_string(
            "components/messages.html", {"messages": list(messages)}
        )

    def test_wrapper_is_not_a_live_region(self):
        html = self._render(_Message("Erro.", "error"))
        self.assertIn('<ul class="messages">', html)
        self.assertNotIn("aria-live", html)

    def test_error_announces_assertively(self):
        html = self._render(_Message("Falha.", "error"))
        self.assertIn('role="alert"', html)

    def test_success_announces_politely(self):
        html = self._render(_Message("Feito.", "success"))
        self.assertIn('role="status"', html)
        self.assertNotIn('role="alert"', html)

    def test_order_and_messages_preserved(self):
        html = self._render(
            _Message("Primeira", "success"), _Message("Segunda", "error")
        )
        self.assertLess(html.index("Primeira"), html.index("Segunda"))


class ConfirmModalContractTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_admin()
        cls.owner = make_user("owner@example.com")
        cls.project = make_project("Projeto Modal")
        assign(cls.project, cls.owner)

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.owner)

    def test_modal_markup_present_for_authenticated_users(self):
        html = self.client.get(reverse("projects:project_list")).content.decode()
        for hook in (
            "data-confirm-modal",
            "data-confirm-message",
            "data-confirm-accept",
            "data-confirm-cancel",
        ):
            self.assertIn(hook, html)
        self.assertIn('role="dialog"', html)
        self.assertIn("aria-modal=\"true\"", html)

    def test_modal_absent_when_anonymous(self):
        html = Client().get(reverse("accounts:login")).content.decode()
        self.assertNotIn("data-confirm-modal", html)

    def test_destructive_form_keeps_post_action_and_confirmation(self):
        admin = Client()
        admin.force_login(self.admin)
        html = admin.get(
            reverse("projects:memberships", args=[self.project.pk])
        ).content.decode()
        self.assertIn("data-confirm=", html)
        self.assertIn("csrfmiddlewaretoken", html)

    def test_membership_remove_still_posts_successfully(self):
        member = make_user("membro@example.com")
        assign(self.project, member)
        admin = Client()
        admin.force_login(self.admin)
        response = admin.post(
            reverse(
                "projects:membership_remove",
                args=[self.project.pk, member.pk],
            ),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Atribuição removida com sucesso.")
