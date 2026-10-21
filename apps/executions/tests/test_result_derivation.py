from django.contrib.auth import get_user_model
from django.db import IntegrityError, models, transaction
from django.db.models.deletion import ProtectedError
from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import Role
from apps.executions import services
from apps.executions.models import (
    CaseStatus,
    ExecutionCase,
    ExecutionStep,
    StepOutcome,
    derive_case_status,
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


def make_case(project, title="Caso de Teste", is_active=True):
    return CaseModel.objects.create(
        project=project,
        title=title,
        description="Descrição do caso",
        preconditions="Nenhuma pré-condição",
        is_active=is_active,
    )


def make_step(case, position, action="Ação", expected_result="Esperado"):
    return TestStep.objects.create(
        test_case=case,
        position=position,
        action=action,
        expected_result=expected_result,
    )


class DerivationFunctionTests(TestCase):
    def test_all_passed_is_approved(self):
        self.assertEqual(
            derive_case_status([StepOutcome.PASSED, StepOutcome.PASSED]),
            CaseStatus.APPROVED,
        )

    def test_any_failed_is_reproved_even_with_unassessed_steps(self):
        self.assertEqual(
            derive_case_status([StepOutcome.FAILED, None]),
            CaseStatus.REPROVED,
        )
        self.assertEqual(
            derive_case_status([None, StepOutcome.FAILED, StepOutcome.PASSED]),
            CaseStatus.REPROVED,
        )

    def test_blocked_without_failure_is_pending(self):
        self.assertEqual(
            derive_case_status([StepOutcome.PASSED, StepOutcome.BLOCKED]),
            CaseStatus.PENDING,
        )

    def test_unassessed_without_failure_or_blocked_is_pending(self):
        self.assertEqual(
            derive_case_status([StepOutcome.PASSED, None]),
            CaseStatus.PENDING,
        )

    def test_no_assessed_step_is_not_started(self):
        self.assertEqual(
            derive_case_status([None, None]),
            CaseStatus.NOT_STARTED,
        )

    def test_case_without_steps_is_not_started(self):
        self.assertEqual(derive_case_status([]), CaseStatus.NOT_STARTED)


class DerivationPersistenceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@example.com")
        cls.project = make_project()
        assign(cls.project, cls.owner)
        cls.case = make_case(cls.project)
        make_step(cls.case, 1, "Passo A", "A pronto")
        make_step(cls.case, 2, "Passo B", "B pronto")

    def setUp(self):
        self.execution = services.start_execution(
            actor=self.owner, project=self.project, case_ids=[self.case.pk]
        )
        self.execution_case = self.execution.execution_cases.get()
        self.steps = list(self.execution_case.steps.order_by("snapshot_position"))

    def _write(self, step, outcome, observation=""):
        return services.update_step_result(
            execution=self.execution,
            step=step,
            actor=self.owner,
            outcome=outcome,
            observation=observation,
        )

    def test_all_steps_passed_approves_case(self):
        self._write(self.steps[0], StepOutcome.PASSED)
        self._write(self.steps[1], StepOutcome.PASSED)

        self.execution_case.refresh_from_db()
        self.assertEqual(self.execution_case.status, CaseStatus.APPROVED)

    def test_any_failed_step_reproves_case(self):
        self._write(self.steps[0], StepOutcome.FAILED, "quebrou")

        self.execution_case.refresh_from_db()
        self.assertEqual(self.execution_case.status, CaseStatus.REPROVED)

    def test_blocked_step_marks_case_pending(self):
        self._write(self.steps[0], StepOutcome.BLOCKED, "bloqueado")

        self.execution_case.refresh_from_db()
        self.assertEqual(self.execution_case.status, CaseStatus.PENDING)

    def test_case_starts_not_started(self):
        self.assertEqual(self.execution_case.status, CaseStatus.NOT_STARTED)

    def test_observation_is_optional_for_every_outcome(self):
        step = self._write(self.steps[0], StepOutcome.PASSED)

        self.assertEqual(step.observation, "")

    def test_invalid_outcome_is_rejected(self):
        with self.assertRaises(services.InvalidOutcomeError):
            self._write(self.steps[0], "INVALID")

        self.steps[0].refresh_from_db()
        self.assertIsNone(self.steps[0].outcome)


class SnapshotIntegrityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@example.com")
        cls.project = make_project()
        assign(cls.project, cls.owner)
        cls.case = make_case(cls.project)
        cls.step = make_step(cls.case, 1)

    def setUp(self):
        self.execution = services.start_execution(
            actor=self.owner, project=self.project, case_ids=[self.case.pk]
        )
        self.execution_case = self.execution.execution_cases.get()
        self.execution_step = self.execution_case.steps.get()

    def test_case_cannot_be_selected_twice(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ExecutionCase.objects.create(
                execution=self.execution,
                source_test_case=self.case,
                title=self.case.title,
                description=self.case.description,
                preconditions=self.case.preconditions,
            )

    def test_source_case_is_protected_from_deletion(self):
        self.assertIs(
            ExecutionCase._meta.get_field("source_test_case").remote_field.on_delete,
            models.PROTECT,
        )

        with self.assertRaises(ProtectedError):
            self.case.delete()

    def test_source_step_is_protected_from_deletion(self):
        self.assertIs(
            ExecutionStep._meta.get_field("source_test_step").remote_field.on_delete,
            models.PROTECT,
        )

        with self.assertRaises(ProtectedError):
            self.step.delete()

    def test_creator_is_protected_from_deletion(self):
        self.assertIs(
            self.execution._meta.get_field("created_by").remote_field.on_delete,
            models.PROTECT,
        )

    def test_snapshot_position_must_be_unique_and_positive(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ExecutionStep.objects.create(
                execution_case=self.execution_case,
                source_test_step=self.step,
                snapshot_position=1,
                action="Duplicado",
                expected_result="x",
            )

        other = make_step(self.case, 2)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ExecutionStep.objects.create(
                execution_case=self.execution_case,
                source_test_step=other,
                snapshot_position=0,
                action="Zero",
                expected_result="x",
            )

    def test_outcome_must_be_null_or_allowed(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ExecutionStep.objects.create(
                execution_case=self.execution_case,
                source_test_step=self.step,
                snapshot_position=2,
                action="Ação",
                expected_result="x",
                outcome="INVALID",
            )

    def test_editing_the_source_case_does_not_change_the_snapshot(self):
        original_title = self.execution_case.title
        self.case.title = "Título alterado"
        self.case.description = "Nova descrição"
        self.case.save()

        self.execution_case.refresh_from_db()
        self.assertEqual(self.execution_case.title, original_title)
        self.assertEqual(self.execution_case.description, "Descrição do caso")


class CaseProgressTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@example.com")
        cls.project = make_project()
        assign(cls.project, cls.owner)
        cls.case = make_case(cls.project)
        make_step(cls.case, 1, "Passo A", "A pronto")
        make_step(cls.case, 2, "Passo B", "B pronto")

    def setUp(self):
        self.execution = services.start_execution(
            actor=self.owner, project=self.project, case_ids=[self.case.pk]
        )
        self.execution_case = self.execution.execution_cases.get()
        self.steps = list(self.execution_case.steps.order_by("snapshot_position"))

    def test_progress_starts_at_zero(self):
        self.assertEqual(self.execution_case.total_steps, 2)
        self.assertEqual(self.execution_case.assessed_steps, 0)
        self.assertEqual(
            self.execution_case.progress_label, "0 de 2 passos avaliados"
        )

    def test_progress_follows_recorded_outcomes(self):
        services.update_step_result(
            execution=self.execution,
            step=self.steps[0],
            actor=self.owner,
            outcome=StepOutcome.PASSED,
        )

        self.execution_case.refresh_from_db()
        self.assertEqual(self.execution_case.assessed_steps, 1)
        self.assertEqual(
            self.execution_case.progress_label, "1 de 2 passos avaliados"
        )

    def test_detail_template_shows_progress_indicator(self):
        client = Client()
        client.force_login(self.owner)

        response = client.get(
            reverse("executions:execution_detail", args=[self.execution.pk])
        )

        self.assertContains(response, "0 de 2 passos avaliados")
        self.assertContains(response, "data-case-progress")

    def test_readonly_template_shows_progress_indicator(self):
        services.update_step_result(
            execution=self.execution,
            step=self.steps[0],
            actor=self.owner,
            outcome=StepOutcome.PASSED,
        )
        services.complete_execution(execution=self.execution, actor=self.owner)
        client = Client()
        client.force_login(self.owner)

        response = client.get(
            reverse("executions:execution_detail", args=[self.execution.pk])
        )

        self.assertContains(response, "1 de 2 passos avaliados")
