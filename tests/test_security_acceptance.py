import json
import urllib.error

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db.models.deletion import ProtectedError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.executions import ai_adapter
from apps.executions import services as execution_services
from apps.executions.models import (
    ExecutionState,
    Report,
    StepOutcome,
)
from apps.projects import services as project_services
from apps.projects.models import Project
from apps.testcases import services as case_services

from .support import (
    assign,
    complete_execution,
    make_admin,
    make_case,
    make_project,
    make_step,
    make_user,
    start_execution,
)

User = get_user_model()

LIVE_SECRET = "sk-live-super-secret-do-not-leak-1234567890"


class RoleBoundaryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_admin()
        cls.tester = make_user("tester@example.com")
        cls.project = make_project()
        assign(cls.project, cls.tester)
        cls.case = make_case(cls.project)
        make_step(cls.case)

    def test_testador_is_denied_every_administrator_route(self):
        client = Client()
        client.force_login(self.tester)

        routes = [
            ("accounts:user_list", []),
            ("accounts:user_create", []),
            ("accounts:user_edit", [self.admin.pk]),
            ("accounts:user_inactivate", [self.admin.pk]),
            ("projects:project_create", []),
            ("projects:project_edit", [self.project.pk]),
            ("projects:project_inactivate", [self.project.pk]),
            ("projects:project_delete", [self.project.pk]),
            ("projects:memberships", [self.project.pk]),
        ]
        for name, args in routes:
            with self.subTest(route=name):
                response = client.post(reverse(name, args=args))
                self.assertEqual(response.status_code, 403)

        self.project.refresh_from_db()
        self.assertTrue(self.project.is_active)

    def test_anonymous_is_redirected_to_login_for_protected_pages(self):
        client = Client()

        routes = [
            ("accounts:user_list", []),
            ("accounts:profile", []),
            ("projects:project_list", []),
            ("testcases:case_list", [self.project.pk]),
            ("executions:execution_detail", [999]),
        ]
        for name, args in routes:
            with self.subTest(route=name):
                response = client.get(reverse(name, args=args))
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("accounts:login"), response.url)

    def test_administrator_manages_projects_but_must_be_assigned_to_operate(self):
        client = Client()
        client.force_login(self.admin)

        self.assertEqual(
            client.get(reverse("projects:project_create")).status_code, 200
        )
        self.assertEqual(
            client.get(
                reverse("testcases:case_list", args=[self.project.pk])
            ).status_code,
            200,
        )
        self.assertEqual(
            client.get(
                reverse("testcases:case_create", args=[self.project.pk])
            ).status_code,
            403,
        )
        self.assertEqual(
            client.get(
                reverse("executions:execution_create", args=[self.project.pk])
            ).status_code,
            403,
        )


class ImmediateMembershipRevocationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tester = make_user("tester@example.com")
        cls.successor = make_user("successor@example.com")
        cls.project = make_project()
        assign(cls.project, cls.tester)
        assign(cls.project, cls.successor)
        cls.case = make_case(cls.project)
        make_step(cls.case)

    def test_revocation_denies_reads_and_writes_immediately(self):
        execution = start_execution(self.tester, self.project, [self.case])
        step = execution.execution_cases.get().steps.get()

        client = Client()
        client.force_login(self.tester)
        self.assertEqual(
            client.get(
                reverse("executions:execution_detail", args=[execution.pk])
            ).status_code,
            200,
        )

        project_services.remove_tester(self.project, self.tester)

        for name, args in [
            ("projects:project_detail", [self.project.pk]),
            ("testcases:case_detail", [self.case.pk]),
            ("executions:execution_detail", [execution.pk]),
            ("executions:report_detail", [execution.pk]),
        ]:
            with self.subTest(route=name):
                self.assertEqual(
                    client.get(reverse(name, args=args)).status_code, 404
                )

        write = client.post(
            reverse(
                "executions:execution_step_update",
                args=[execution.pk, step.pk],
            ),
            {"outcome": StepOutcome.PASSED},
        )
        self.assertEqual(write.status_code, 404)

        with self.assertRaises(PermissionDenied):
            execution_services.update_step_result(
                execution=execution,
                step=step,
                actor=self.tester,
                outcome=StepOutcome.PASSED,
            )
        step.refresh_from_db()
        self.assertIsNone(step.outcome)

    def test_revocation_releases_ownership_and_another_tester_may_assume(self):
        execution = start_execution(self.tester, self.project, [self.case])

        project_services.remove_tester(self.project, self.tester)

        execution.refresh_from_db()
        self.assertEqual(execution.state, ExecutionState.IN_PROGRESS)
        self.assertIsNone(execution.current_assignment)

        execution_services.assume_execution(
            execution=execution, actor=self.successor
        )
        execution.refresh_from_db()
        self.assertEqual(execution.current_owner, self.successor)


class PhysicalProjectDeletionBlockingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_admin()
        cls.tester = make_user("tester@example.com")

    def test_project_referenced_by_a_case_cannot_be_deleted(self):
        project = make_project("Projeto com caso")
        assign(project, self.tester)
        make_case(project)

        with self.assertRaises(project_services.ProtectedProjectError):
            project_services.delete_project(project)
        self.assertTrue(Project.objects.filter(pk=project.pk).exists())

        client = Client()
        client.force_login(self.admin)
        response = client.post(
            reverse("projects:project_delete", args=[project.pk]), follow=True
        )
        self.assertContains(response, "não pode ser excluído")
        self.assertContains(response, 'role="alert"')
        self.assertTrue(Project.objects.filter(pk=project.pk).exists())

    def test_project_referenced_by_an_execution_cannot_be_deleted(self):
        project = make_project("Projeto com execução")
        assign(project, self.tester)
        case = make_case(project)
        make_step(case)
        start_execution(self.tester, project, [case])

        with self.assertRaises(project_services.ProtectedProjectError):
            project_services.delete_project(project)
        self.assertTrue(Project.objects.filter(pk=project.pk).exists())


class OwnerOnlyExecutionEditTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@example.com")
        cls.other = make_user("other@example.com")
        cls.project = make_project()
        assign(cls.project, cls.owner)
        assign(cls.project, cls.other)
        cls.case = make_case(cls.project)
        make_step(cls.case)

    def setUp(self):
        self.execution = start_execution(self.owner, self.project, [self.case])
        self.step = self.execution.execution_cases.get().steps.get()

    def test_non_owner_write_is_denied_by_the_backend(self):
        client = Client()
        client.force_login(self.other)

        response = client.post(
            reverse(
                "executions:execution_step_update",
                args=[self.execution.pk, self.step.pk],
            ),
            {"outcome": StepOutcome.PASSED},
        )

        self.assertEqual(response.status_code, 403)
        self.step.refresh_from_db()
        self.assertIsNone(self.step.outcome)

    def test_non_owner_reads_without_write_controls(self):
        client = Client()
        client.force_login(self.other)

        response = client.get(
            reverse("executions:execution_detail", args=[self.execution.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Salvar resultado")

    def test_owner_write_persists(self):
        client = Client()
        client.force_login(self.owner)

        response = client.post(
            reverse(
                "executions:execution_step_update",
                args=[self.execution.pk, self.step.pk],
            ),
            {"outcome": StepOutcome.FAILED, "observation": "quebrou"},
        )

        self.assertEqual(response.status_code, 302)
        self.step.refresh_from_db()
        self.assertEqual(self.step.outcome, StepOutcome.FAILED)
        self.assertEqual(self.step.observation, "quebrou")


class CompletedRunImmutabilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@example.com")
        cls.project = make_project()
        assign(cls.project, cls.owner)
        cls.case = make_case(cls.project)
        make_step(cls.case)

    def setUp(self):
        self.execution = start_execution(self.owner, self.project, [self.case])
        self.step = self.execution.execution_cases.get().steps.get()
        self.report = complete_execution(self.execution, self.owner)

    def test_completed_run_rejects_result_writes_through_view_and_service(self):
        client = Client()
        client.force_login(self.owner)

        response = client.post(
            reverse(
                "executions:execution_step_update",
                args=[self.execution.pk, self.step.pk],
            ),
            {"outcome": StepOutcome.PASSED},
        )

        self.assertEqual(response.status_code, 302)
        self.step.refresh_from_db()
        self.assertIsNone(self.step.outcome)

        with self.assertRaises(execution_services.ImmutableExecutionError):
            execution_services.update_step_result(
                execution=self.execution,
                step=self.step,
                actor=self.owner,
                outcome=StepOutcome.PASSED,
            )

    def test_completed_run_keeps_exactly_one_report(self):
        with self.assertRaises(execution_services.ImmutableExecutionError):
            execution_services.complete_execution(
                execution=self.execution, actor=self.owner
            )

        self.assertEqual(
            Report.objects.filter(execution=self.execution).count(), 1
        )

    def test_referenced_definitions_are_protected_after_completion(self):
        with self.assertRaises(case_services.ProtectedCaseError):
            case_services.delete_case(self.case, actor=self.owner)
        with self.assertRaises(ProtectedError):
            self.case.steps.get().delete()
        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())


@override_settings(
    AI_PROVIDER_ACTIVE=True,
    AI_PROVIDER_ENDPOINT="https://provider.example.test/v1/analyze",
    AI_PROVIDER_MODEL="gemini-test-model",
    AI_PROVIDER_API_KEY=LIVE_SECRET,
    AI_PROVIDER_TIMEOUT_SECONDS=5,
)
class AISecretNonExposureTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@example.com")
        cls.project = make_project("Projeto Confidencial")
        assign(cls.project, cls.owner)
        cls.case = make_case(cls.project)
        make_step(cls.case)

    def setUp(self):
        self.execution = start_execution(self.owner, self.project, [self.case])
        complete_execution(self.execution, self.owner)

    def test_secret_never_reaches_any_rendered_page(self):
        client = Client()
        client.force_login(self.owner)

        pages = [
            ("accounts:profile", []),
            ("projects:project_list", []),
            ("projects:project_detail", [self.project.pk]),
            ("testcases:case_list", [self.project.pk]),
            ("executions:execution_detail", [self.execution.pk]),
            ("executions:report_detail", [self.execution.pk]),
            ("executions:history_list", [self.project.pk]),
            ("executions:history_compare", [self.project.pk]),
        ]
        for name, args in pages:
            with self.subTest(page=name):
                response = client.get(reverse(name, args=args))
                self.assertEqual(response.status_code, 200)
                content = response.content.decode()
                self.assertNotIn(LIVE_SECRET, content)
                self.assertNotIn("AI_PROVIDER_API_KEY", content)

    def test_secret_absent_from_the_anonymous_login_page(self):
        response = Client().get(reverse("accounts:login"))

        self.assertNotIn(LIVE_SECRET, response.content.decode())

    def test_context_and_adapter_errors_do_not_leak_the_secret(self):
        report = Report.objects.get(execution=self.execution)

        context = ai_adapter.build_request_context(report)
        self.assertNotIn(LIVE_SECRET, json.dumps(context, ensure_ascii=False))

        def failing_transport(url, payload, headers, timeout):
            raise urllib.error.URLError("connection refused")

        with self.assertRaises(ai_adapter.AdapterConnectionError) as caught:
            ai_adapter.analyze(context, transport=failing_transport)

        self.assertNotIn(LIVE_SECRET, str(caught.exception))
