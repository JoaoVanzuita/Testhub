import threading

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from apps.accounts.models import Role
from apps.executions import services
from apps.executions.models import (
    CaseStatus,
    Execution,
    ExecutionAssignment,
    ExecutionState,
    ReleaseReason,
    StepOutcome,
)
from apps.projects import services as project_services
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


def make_case(project, title="Caso de Teste", is_active=True):
    return CaseModel.objects.create(
        project=project,
        title=title,
        description="Descrição do caso",
        preconditions="Nenhuma pré-condição",
        is_active=is_active,
    )


def make_step(case, position, action="Ação", expected_result="Esperado", is_active=True):
    return TestStep.objects.create(
        test_case=case,
        position=position,
        action=action,
        expected_result=expected_result,
        is_active=is_active,
    )


class ExecutionStartTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@example.com")
        cls.other = make_user("other@example.com")
        cls.admin = make_user("admin@example.com", role=Role.ADMINISTRATOR)
        cls.project = make_project()
        cls.other_project = make_project("Projeto Beta")
        assign(cls.project, cls.owner)
        assign(cls.project, cls.other)
        assign(cls.other_project, cls.owner)
        cls.case = make_case(cls.project)
        make_step(cls.case, 1, "Login", "Usuário autenticado")
        make_step(cls.case, 2, "Abrir painel", "Painel visível")

    def test_start_snapshots_definition_and_records_owner(self):
        execution = services.start_execution(
            actor=self.owner, project=self.project, case_ids=[self.case.pk]
        )

        self.assertEqual(execution.state, ExecutionState.IN_PROGRESS)
        self.assertEqual(execution.created_by, self.owner)
        self.assertIsNone(execution.completed_at)
        execution_case = execution.execution_cases.get()
        self.assertEqual(execution_case.title, self.case.title)
        self.assertEqual(
            list(
                execution_case.steps.values_list("snapshot_position", flat=True)
            ),
            [1, 2],
        )
        self.assertEqual(execution.current_owner, self.owner)

    def test_start_requires_at_least_one_case(self):
        with self.assertRaises(services.NoCasesSelectedError):
            services.start_execution(
                actor=self.owner, project=self.project, case_ids=[]
            )
        self.assertFalse(Execution.objects.exists())

    def test_start_rejects_foreign_or_inactive_cases(self):
        foreign = make_case(self.other_project, title="Caso Beta")
        with self.assertRaises(services.InvalidCaseError):
            services.start_execution(
                actor=self.owner, project=self.project, case_ids=[foreign.pk]
            )

        inactive = make_case(self.project, title="Caso Inativo", is_active=False)
        with self.assertRaises(services.InvalidCaseError):
            services.start_execution(
                actor=self.owner, project=self.project, case_ids=[inactive.pk]
            )
        self.assertFalse(Execution.objects.exists())

    def test_start_rejects_case_without_active_steps(self):
        unusable = make_case(self.project, title="Caso Sem Passos")
        make_step(unusable, 1, "Passo inativo", "x", is_active=False)

        with self.assertRaises(services.UnusableCaseError):
            services.start_execution(
                actor=self.owner, project=self.project, case_ids=[unusable.pk]
            )
        self.assertFalse(Execution.objects.exists())

    def test_selection_form_omits_cases_without_active_steps(self):
        unusable = make_case(self.project, title="Caso Sem Passos")
        make_step(unusable, 1, "Passo inativo", "x", is_active=False)
        client = Client()
        client.force_login(self.owner)

        response = client.get(
            reverse("executions:execution_create", args=[self.project.pk])
        )

        self.assertEqual(response.status_code, 200)
        offered = response.context["form"].fields["cases"].queryset
        self.assertIn(self.case, offered)
        self.assertNotIn(unusable, offered)

    def test_start_requires_project_membership(self):
        stranger = make_user("stranger@example.com")
        with self.assertRaises(PermissionDenied):
            services.start_execution(
                actor=stranger, project=self.project, case_ids=[self.case.pk]
            )

    def test_start_view_creates_execution(self):
        client = Client()
        client.force_login(self.owner)

        response = client.post(
            reverse("executions:execution_create", args=[self.project.pk]),
            {"cases": [str(self.case.pk)]},
        )

        execution = Execution.objects.get()
        self.assertRedirects(
            response, reverse("executions:execution_detail", args=[execution.pk])
        )

    def test_start_view_without_cases_is_rejected(self):
        client = Client()
        client.force_login(self.owner)

        response = client.post(
            reverse("executions:execution_create", args=[self.project.pk]),
            {"cases": []},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ao menos um caso")
        self.assertFalse(Execution.objects.exists())

    def test_administrator_without_membership_cannot_start(self):
        client = Client()
        client.force_login(self.admin)

        response = client.get(
            reverse("executions:execution_create", args=[self.project.pk])
        )

        self.assertEqual(response.status_code, 403)


class OwnerMutationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@example.com")
        cls.reader = make_user("reader@example.com")
        cls.project = make_project()
        assign(cls.project, cls.owner)
        assign(cls.project, cls.reader)
        cls.case = make_case(cls.project)
        make_step(cls.case, 1, "Login", "Usuário autenticado")

    def setUp(self):
        self.execution = services.start_execution(
            actor=self.owner, project=self.project, case_ids=[self.case.pk]
        )
        self.step = self.execution.execution_cases.get().steps.get()

    def step_url(self):
        return reverse(
            "executions:execution_step_update",
            args=[self.execution.pk, self.step.pk],
        )

    def test_owner_records_result_with_persistence_feedback(self):
        client = Client()
        client.force_login(self.owner)

        response = client.post(
            self.step_url(),
            {"outcome": StepOutcome.PASSED, "observation": "ok"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["persisted"])
        self.step.refresh_from_db()
        self.assertEqual(self.step.outcome, StepOutcome.PASSED)
        self.assertEqual(self.step.observation, "ok")

    def test_non_owner_cannot_change_results(self):
        client = Client()
        client.force_login(self.reader)

        response = client.post(
            self.step_url(),
            {"outcome": StepOutcome.PASSED, "observation": "no"},
        )

        self.assertEqual(response.status_code, 403)
        self.step.refresh_from_db()
        self.assertIsNone(self.step.outcome)

    def test_non_owner_can_read_the_execution(self):
        client = Client()
        client.force_login(self.reader)

        response = client.get(
            reverse("executions:execution_detail", args=[self.execution.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Salvar resultado")

    def test_failed_persistence_is_not_reported_as_saved(self):
        client = Client()
        client.force_login(self.owner)

        response = client.post(
            self.step_url(),
            {"outcome": "INVALID"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["persisted"])
        self.step.refresh_from_db()
        self.assertIsNone(self.step.outcome)


class PauseResumeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@example.com")
        cls.project = make_project()
        assign(cls.project, cls.owner)
        cls.case = make_case(cls.project)
        make_step(cls.case, 1, "Login", "Usuário autenticado")

    def setUp(self):
        self.execution = services.start_execution(
            actor=self.owner, project=self.project, case_ids=[self.case.pk]
        )
        self.step = self.execution.execution_cases.get().steps.get()

    def test_pause_and_resume_preserve_results(self):
        client = Client()
        client.force_login(self.owner)

        client.post(
            reverse(
                "executions:execution_step_update",
                args=[self.execution.pk, self.step.pk],
            ),
            {"outcome": StepOutcome.PASSED},
        )
        pause = client.post(
            reverse("executions:execution_pause", args=[self.execution.pk])
        )
        resume = client.post(
            reverse("executions:execution_resume", args=[self.execution.pk])
        )

        self.assertRedirects(
            pause,
            reverse("executions:execution_detail", args=[self.execution.pk]),
        )
        self.assertRedirects(
            resume,
            reverse("executions:execution_detail", args=[self.execution.pk]),
        )
        self.execution.refresh_from_db()
        self.step.refresh_from_db()
        self.assertEqual(self.execution.state, ExecutionState.IN_PROGRESS)
        self.assertEqual(self.step.outcome, StepOutcome.PASSED)


class HandoffTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@example.com")
        cls.successor = make_user("successor@example.com")
        cls.stranger = make_user("stranger@example.com")
        cls.project = make_project()
        assign(cls.project, cls.owner)
        assign(cls.project, cls.successor)
        cls.case = make_case(cls.project)
        make_step(cls.case, 1, "Login", "Usuário autenticado")

    def setUp(self):
        self.execution = services.start_execution(
            actor=self.owner, project=self.project, case_ids=[self.case.pk]
        )
        self.step = self.execution.execution_cases.get().steps.get()

    def test_authorized_handoff_moves_current_ownership(self):
        client = Client()
        client.force_login(self.owner)

        response = client.post(
            reverse("executions:execution_handoff", args=[self.execution.pk]),
            {"successor": str(self.successor.pk)},
        )

        self.assertRedirects(
            response,
            reverse("executions:execution_detail", args=[self.execution.pk]),
        )
        self.execution.refresh_from_db()
        self.assertEqual(self.execution.current_owner, self.successor)
        released = self.execution.assignments.get(
            responsible=self.owner, released_at__isnull=False
        )
        self.assertEqual(released.release_reason, ReleaseReason.HANDOFF)

    def test_handoff_rejects_unassigned_successor(self):
        client = Client()
        client.force_login(self.owner)

        response = client.post(
            reverse("executions:execution_handoff", args=[self.execution.pk]),
            {"successor": str(self.stranger.pk)},
        )

        self.assertEqual(response.status_code, 302)
        self.execution.refresh_from_db()
        self.assertEqual(self.execution.current_owner, self.owner)

    def test_previous_owner_can_no_longer_write_after_handoff(self):
        services.handoff_execution(
            execution=self.execution, actor=self.owner, successor=self.successor
        )
        with self.assertRaises(PermissionDenied):
            services.update_step_result(
                execution=self.execution,
                step=self.step,
                actor=self.owner,
                outcome=StepOutcome.PASSED,
            )

    def test_release_then_assume_preserves_results(self):
        services.update_step_result(
            execution=self.execution,
            step=self.step,
            actor=self.owner,
            outcome=StepOutcome.FAILED,
            observation="quebrou",
        )
        services.release_assignment(execution=self.execution, actor=self.owner)

        self.execution.refresh_from_db()
        self.assertIsNone(self.execution.current_assignment)

        services.assume_execution(execution=self.execution, actor=self.successor)

        self.execution.refresh_from_db()
        self.assertEqual(self.execution.current_owner, self.successor)
        self.step.refresh_from_db()
        self.assertEqual(self.step.outcome, StepOutcome.FAILED)
        self.assertEqual(self.step.observation, "quebrou")


class ProjectRevocationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@example.com")
        cls.successor = make_user("successor@example.com")
        cls.project = make_project()
        assign(cls.project, cls.owner)
        assign(cls.project, cls.successor)
        cls.case = make_case(cls.project)
        make_step(cls.case, 1, "Login", "Usuário autenticado")

    def setUp(self):
        self.execution = services.start_execution(
            actor=self.owner, project=self.project, case_ids=[self.case.pk]
        )
        self.step = self.execution.execution_cases.get().steps.get()

    def test_revocation_releases_the_current_assignment(self):
        project_services.remove_tester(self.project, self.owner)

        self.execution.refresh_from_db()
        self.assertIsNone(self.execution.current_assignment)
        released = self.execution.assignments.get(
            responsible=self.owner, released_at__isnull=False
        )
        self.assertEqual(
            released.release_reason, ReleaseReason.PROJECT_ACCESS_REVOKED
        )

    def test_revoked_owner_cannot_write_or_read(self):
        project_services.remove_tester(self.project, self.owner)

        with self.assertRaises(PermissionDenied):
            services.update_step_result(
                execution=self.execution,
                step=self.step,
                actor=self.owner,
                outcome=StepOutcome.PASSED,
            )

        client = Client()
        client.force_login(self.owner)
        response = client.get(
            reverse("executions:execution_detail", args=[self.execution.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_another_tester_can_assume_after_revocation(self):
        project_services.remove_tester(self.project, self.owner)

        services.assume_execution(execution=self.execution, actor=self.successor)

        self.execution.refresh_from_db()
        self.assertEqual(self.execution.current_owner, self.successor)


class CompletionImmutabilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@example.com")
        cls.other = make_user("other@example.com")
        cls.project = make_project()
        assign(cls.project, cls.owner)
        assign(cls.project, cls.other)
        cls.case = make_case(cls.project)
        make_step(cls.case, 1, "Login", "Usuário autenticado")
        make_step(cls.case, 2, "Abrir painel", "Painel visível")

    def setUp(self):
        self.execution = services.start_execution(
            actor=self.owner, project=self.project, case_ids=[self.case.pk]
        )
        self.execution_case = self.execution.execution_cases.get()
        self.steps = list(self.execution_case.steps.order_by("snapshot_position"))

    def test_completion_sets_state_and_derives_pending_status(self):
        services.update_step_result(
            execution=self.execution,
            step=self.steps[0],
            actor=self.owner,
            outcome=StepOutcome.PASSED,
        )

        services.complete_execution(execution=self.execution, actor=self.owner)

        self.execution.refresh_from_db()
        self.execution_case.refresh_from_db()
        self.assertEqual(self.execution.state, ExecutionState.COMPLETED)
        self.assertIsNotNone(self.execution.completed_at)
        self.assertEqual(self.execution_case.status, CaseStatus.PENDING)
        self.assertIsNone(self.execution.current_assignment)

    def test_completed_execution_rejects_result_writes(self):
        services.complete_execution(execution=self.execution, actor=self.owner)

        with self.assertRaises(services.ImmutableExecutionError):
            services.update_step_result(
                execution=self.execution,
                step=self.steps[0],
                actor=self.owner,
                outcome=StepOutcome.PASSED,
            )

        self.steps[0].refresh_from_db()
        self.assertIsNone(self.steps[0].outcome)

    def test_completed_execution_rejects_assignment_changes(self):
        services.complete_execution(execution=self.execution, actor=self.owner)

        with self.assertRaises(services.ImmutableExecutionError):
            services.release_assignment(execution=self.execution, actor=self.owner)
        with self.assertRaises(services.ImmutableExecutionError):
            services.complete_execution(execution=self.execution, actor=self.owner)

    def test_completed_execution_is_read_only_in_the_view(self):
        services.complete_execution(execution=self.execution, actor=self.owner)
        client = Client()
        client.force_login(self.owner)

        response = client.get(
            reverse("executions:execution_detail", args=[self.execution.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Concluída")
        self.assertNotContains(response, "Salvar resultado")

        write = client.post(
            reverse(
                "executions:execution_step_update",
                args=[self.execution.pk, self.steps[0].pk],
            ),
            {"outcome": StepOutcome.PASSED},
        )
        self.assertEqual(write.status_code, 302)
        self.steps[0].refresh_from_db()
        self.assertIsNone(self.steps[0].outcome)


class ConcurrentWriteVsCompletionTests(TransactionTestCase):
    def setUp(self):
        self.owner = make_user("owner@example.com")
        self.project = make_project()
        assign(self.project, self.owner)
        self.case = make_case(self.project)
        make_step(self.case, 1, "Login", "Usuário autenticado")
        self.execution = services.start_execution(
            actor=self.owner, project=self.project, case_ids=[self.case.pk]
        )
        self.step = self.execution.execution_cases.get().steps.get()

    def test_concurrent_write_and_completion_keep_results_consistent(self):
        barrier = threading.Barrier(2)
        results = {}

        def write():
            try:
                barrier.wait()
                services.update_step_result(
                    execution=self.execution,
                    step=self.step,
                    actor=self.owner,
                    outcome=StepOutcome.PASSED,
                    observation="ok",
                )
                results["write"] = "ok"
            except Exception as exc:  # noqa: BLE001 - record the observed type
                results["write"] = type(exc).__name__
            finally:
                connection.close()

        def complete():
            try:
                barrier.wait()
                services.complete_execution(
                    execution=self.execution, actor=self.owner
                )
                results["complete"] = "ok"
            except Exception as exc:  # noqa: BLE001
                results["complete"] = type(exc).__name__
            finally:
                connection.close()

        threads = [threading.Thread(target=write), threading.Thread(target=complete)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.execution.refresh_from_db()
        self.step.refresh_from_db()
        self.assertEqual(results.get("complete"), "ok")
        self.assertEqual(self.execution.state, ExecutionState.COMPLETED)

        if results.get("write") == "ok":
            self.assertEqual(self.step.outcome, StepOutcome.PASSED)
        else:
            self.assertEqual(results.get("write"), "ImmutableExecutionError")
            self.assertIsNone(self.step.outcome)

        with self.assertRaises(services.ImmutableExecutionError):
            services.update_step_result(
                execution=self.execution,
                step=self.step,
                actor=self.owner,
                outcome=StepOutcome.FAILED,
            )
