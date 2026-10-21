from unittest import mock

from django.contrib.auth import get_user_model
from django.db import IntegrityError, models, transaction
from django.db.models.deletion import ProtectedError
from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import Role
from apps.projects.models import Project, ProjectMembership
from apps.testcases.models import TestCase as CaseModel, TestStep

User = get_user_model()

PASSWORD = "uma-senha-bem-segura-123"
STEPS_PREFIX = "steps"


def make_user(email, role=Role.TESTER, **overrides):
    data = {
        "email": email,
        "name": "Usuário de Teste",
        "password": PASSWORD,
        "role": role,
    }
    data.update(overrides)
    return User.objects.create_user(**data)


def make_project(name="Projeto Alpha", description="Descrição do projeto"):
    return Project.objects.create(name=name, description=description)


def assign(project, user):
    return ProjectMembership.objects.create(project=project, user=user)


def make_case(project, title="Caso de Teste", **overrides):
    data = {
        "project": project,
        "title": title,
        "description": "Descrição do caso",
        "preconditions": "Nenhuma pré-condição",
    }
    data.update(overrides)
    return CaseModel.objects.create(**data)


def make_step(case, position=1, action="Ação", expected_result="Esperado", **overrides):
    data = {
        "test_case": case,
        "position": position,
        "action": action,
        "expected_result": expected_result,
    }
    data.update(overrides)
    return TestStep.objects.create(**data)


def case_payload(title="Caso Válido", description="Descrição", preconditions="Nenhuma"):
    return {
        "title": title,
        "description": description,
        "preconditions": preconditions,
    }


def step_payload(steps, prefix=STEPS_PREFIX):
    data = {
        f"{prefix}-TOTAL_FORMS": str(len(steps)),
        f"{prefix}-INITIAL_FORMS": str(sum(1 for s in steps if s.get("pk"))),
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }
    for index, step in enumerate(steps):
        key = f"{prefix}-{index}"
        data[f"{key}-position"] = str(step["position"])
        data[f"{key}-action"] = step.get("action", "")
        data[f"{key}-expected_result"] = step.get("expected_result", "")
        if step.get("pk"):
            data[f"{key}-id"] = str(step["pk"])
        if step.get("is_active", True):
            data[f"{key}-is_active"] = "on"
        if step.get("delete"):
            data[f"{key}-DELETE"] = "on"
    return data


class CaseModelIntegrityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.project = make_project()
        cls.case = make_case(cls.project)

    def test_step_position_is_unique_per_case(self):
        make_step(self.case, position=1)

        with self.assertRaises(IntegrityError), transaction.atomic():
            make_step(self.case, position=1, action="Duplicado")

    def test_step_position_must_be_greater_than_zero(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_step(self.case, position=0)

    def test_case_keeps_cases_protected_against_project_deletion(self):
        self.assertIs(
            CaseModel._meta.get_field("project").remote_field.on_delete, models.PROTECT
        )

        with self.assertRaises(ProtectedError):
            self.project.delete()

    def test_steps_belong_to_the_case_aggregate(self):
        self.assertIs(
            TestStep._meta.get_field("test_case").remote_field.on_delete,
            models.CASCADE,
        )
        step = make_step(self.case, position=2)

        self.case.delete()

        self.assertFalse(TestStep.objects.filter(pk=step.pk).exists())


class CaseCreationValidationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("admin@example.com", role=Role.ADMINISTRATOR)
        cls.tester = make_user("tester@example.com")
        cls.project = make_project()
        assign(cls.project, cls.tester)

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.tester)

    def post_create(self, payload):
        return self.client.post(
            reverse("testcases:case_create", args=[self.project.pk]), payload
        )

    def test_create_case_with_one_ordered_step(self):
        payload = case_payload()
        payload.update(
            step_payload(
                [
                    {
                        "position": 1,
                        "action": "Executar login",
                        "expected_result": "Usuário autenticado",
                    }
                ]
            )
        )

        response = self.post_create(payload)

        case = CaseModel.objects.get(title="Caso Válido")
        self.assertRedirects(
            response, reverse("testcases:case_detail", args=[case.pk])
        )
        self.assertEqual(case.project, self.project)
        self.assertEqual(case.description, "Descrição")
        self.assertEqual(case.preconditions, "Nenhuma")
        self.assertEqual(
            list(case.steps.order_by("position").values_list("position", flat=True)),
            [1],
        )

    def test_case_without_steps_is_rejected(self):
        response = self.post_create(case_payload())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ao menos um passo")
        self.assertFalse(CaseModel.objects.exists())

    def test_required_case_fields_are_enforced(self):
        payload = case_payload(title="", description="", preconditions="")
        payload.update(
            step_payload(
                [{"position": 1, "action": "Fazer", "expected_result": "Feito"}]
            )
        )

        response = self.post_create(payload)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(CaseModel.objects.exists())
        form = response.context["form"]
        self.assertIn("title", form.errors)
        self.assertIn("description", form.errors)
        self.assertIn("preconditions", form.errors)

    def test_required_step_fields_are_enforced(self):
        payload = case_payload()
        payload.update(
            step_payload([{"position": 1, "action": "", "expected_result": ""}])
        )

        response = self.post_create(payload)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(CaseModel.objects.exists())
        formset = response.context["formset"]
        step_form = formset.forms[0]
        self.assertIn("action", step_form.errors)
        self.assertIn("expected_result", step_form.errors)

    def test_duplicate_step_positions_are_rejected(self):
        payload = case_payload()
        payload.update(
            step_payload(
                [
                    {"position": 1, "action": "A", "expected_result": "a"},
                    {"position": 1, "action": "B", "expected_result": "b"},
                ]
            )
        )

        response = self.post_create(payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "duplicad")
        self.assertFalse(CaseModel.objects.exists())

    def test_non_positive_position_is_rejected(self):
        payload = case_payload()
        payload.update(
            step_payload([{"position": 0, "action": "A", "expected_result": "a"}])
        )

        response = self.post_create(payload)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(CaseModel.objects.exists())


class CaseEditTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tester = make_user("tester@example.com")
        cls.project = make_project()
        assign(cls.project, cls.tester)

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.tester)

    def post_edit(self, case, payload):
        return self.client.post(
            reverse("testcases:case_edit", args=[case.pk]), payload
        )

    def test_edit_reorders_steps_and_updates_content(self):
        case = make_case(self.project, title="Original")
        first = make_step(case, 1, "Passo A", "A pronto")
        second = make_step(case, 2, "Passo B", "B pronto")
        payload = case_payload(
            title="Atualizado", description="Nova descrição", preconditions="Outra"
        )
        payload["is_active"] = "on"
        payload.update(
            step_payload(
                [
                    {
                        "pk": first.pk,
                        "position": 2,
                        "action": "Passo A",
                        "expected_result": "A pronto",
                    },
                    {
                        "pk": second.pk,
                        "position": 1,
                        "action": "Passo B alterado",
                        "expected_result": "B pronto",
                    },
                ]
            )
        )

        response = self.post_edit(case, payload)

        self.assertRedirects(
            response, reverse("testcases:case_detail", args=[case.pk])
        )
        case.refresh_from_db()
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(case.title, "Atualizado")
        self.assertEqual(case.description, "Nova descrição")
        self.assertEqual(second.position, 1)
        self.assertEqual(first.position, 2)
        self.assertEqual(second.action, "Passo B alterado")
        self.assertEqual(
            list(case.steps.order_by("position").values_list("pk", flat=True)),
            [second.pk, first.pk],
        )

    def test_edit_normalizes_positions_to_sequential(self):
        case = make_case(self.project)
        first = make_step(case, 1, "Passo A", "A pronto")
        second = make_step(case, 2, "Passo B", "B pronto")
        payload = case_payload()
        payload["is_active"] = "on"
        payload.update(
            step_payload(
                [
                    {
                        "pk": first.pk,
                        "position": 10,
                        "action": "Passo A",
                        "expected_result": "A pronto",
                    },
                    {
                        "pk": second.pk,
                        "position": 20,
                        "action": "Passo B",
                        "expected_result": "B pronto",
                    },
                ]
            )
        )

        self.post_edit(case, payload)

        self.assertEqual(
            list(case.steps.order_by("position").values_list("position", flat=True)),
            [1, 2],
        )

    def test_case_can_be_inactivated_logically(self):
        case = make_case(self.project)
        make_step(case, 1)

        response = self.client.post(
            reverse("testcases:case_inactivate", args=[case.pk])
        )

        self.assertRedirects(
            response, reverse("testcases:case_list", args=[self.project.pk])
        )
        case.refresh_from_db()
        self.assertFalse(case.is_active)
        self.assertTrue(CaseModel.objects.filter(pk=case.pk).exists())


class CaseAuthorizationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("admin@example.com", role=Role.ADMINISTRATOR)
        cls.assigned = make_user("assigned@example.com")
        cls.unassigned = make_user("unassigned@example.com")
        cls.project = make_project()
        assign(cls.project, cls.assigned)
        cls.case = make_case(cls.project)
        make_step(cls.case, 1)

    def test_unassigned_tester_cannot_reach_cases(self):
        client = Client()
        client.force_login(self.unassigned)

        self.assertEqual(
            client.get(
                reverse("testcases:case_list", args=[self.project.pk])
            ).status_code,
            404,
        )
        self.assertEqual(
            client.get(reverse("testcases:case_detail", args=[self.case.pk])).status_code,
            404,
        )
        self.assertEqual(
            client.get(
                reverse("testcases:case_create", args=[self.project.pk])
            ).status_code,
            404,
        )
        self.assertEqual(
            client.get(reverse("testcases:case_edit", args=[self.case.pk])).status_code,
            404,
        )

    def test_administrator_can_read_but_not_edit_cases(self):
        client = Client()
        client.force_login(self.admin)

        self.assertEqual(
            client.get(
                reverse("testcases:case_list", args=[self.project.pk])
            ).status_code,
            200,
        )
        self.assertEqual(
            client.get(reverse("testcases:case_detail", args=[self.case.pk])).status_code,
            200,
        )
        self.assertEqual(
            client.get(
                reverse("testcases:case_create", args=[self.project.pk])
            ).status_code,
            403,
        )
        self.assertEqual(
            client.get(reverse("testcases:case_edit", args=[self.case.pk])).status_code,
            403,
        )

    def test_assigned_tester_can_manage_cases(self):
        client = Client()
        client.force_login(self.assigned)

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
            200,
        )
        self.assertEqual(
            client.get(reverse("testcases:case_edit", args=[self.case.pk])).status_code,
            200,
        )

    def test_anonymous_is_redirected(self):
        client = Client()

        response = client.get(
            reverse("testcases:case_list", args=[self.project.pk])
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_revoked_membership_denies_cases_immediately(self):
        client = Client()
        client.force_login(self.assigned)
        case = make_case(self.project, title="Caso Revogável")

        membership = ProjectMembership.objects.get(
            project=self.project, user=self.assigned, revoked_at__isnull=True
        )
        membership.revoked_at = membership.assigned_at
        membership.save(update_fields=["revoked_at"])

        self.assertEqual(
            client.get(reverse("testcases:case_detail", args=[case.pk])).status_code,
            404,
        )
        self.assertEqual(
            client.post(
                reverse("testcases:case_inactivate", args=[case.pk])
            ).status_code,
            404,
        )


class CaseProtectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tester = make_user("tester@example.com")
        cls.project = make_project()
        assign(cls.project, cls.tester)

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.tester)

    def test_unreferenced_case_can_be_deleted(self):
        case = make_case(self.project, title="Descartável")
        make_step(case, 1)

        response = self.client.post(reverse("testcases:case_delete", args=[case.pk]))

        self.assertRedirects(
            response, reverse("testcases:case_list", args=[self.project.pk])
        )
        self.assertFalse(CaseModel.objects.filter(pk=case.pk).exists())

    def test_protected_case_is_not_deleted_and_reports_inactivation(self):
        case = make_case(self.project, title="Referenciado")
        make_step(case, 1)
        error = ProtectedError("protegido", {case})

        with mock.patch.object(CaseModel, "delete", side_effect=error):
            response = self.client.post(
                reverse("testcases:case_delete", args=[case.pk]), follow=True
            )

        self.assertContains(response, "inativ", status_code=200)
        self.assertTrue(CaseModel.objects.filter(pk=case.pk).exists())

    def test_removing_referenced_step_inactivates_instead_of_deleting(self):
        case = make_case(self.project)
        kept = make_step(case, 1, "Manter", "ok")
        referenced = make_step(case, 2, "Referenciado", "ok")
        payload = case_payload()
        payload["is_active"] = "on"
        payload.update(
            step_payload(
                [
                    {
                        "pk": kept.pk,
                        "position": 1,
                        "action": "Manter",
                        "expected_result": "ok",
                    }
                ]
            )
        )
        error = ProtectedError("protegido", {referenced})

        with mock.patch.object(TestStep, "delete", side_effect=error):
            response = self.client.post(
                reverse("testcases:case_edit", args=[case.pk]), payload
            )

        self.assertRedirects(
            response, reverse("testcases:case_detail", args=[case.pk])
        )
        referenced.refresh_from_db()
        self.assertFalse(referenced.is_active)
        self.assertTrue(TestStep.objects.filter(pk=referenced.pk).exists())
        kept.refresh_from_db()
        self.assertEqual(kept.position, 1)

    def test_step_deletion_service_is_protected(self):
        case = make_case(self.project)
        step = make_step(case, 1)
        error = ProtectedError("protegido", {step})

        with mock.patch.object(TestStep, "delete", side_effect=error):
            from apps.testcases import services

            services.save_steps(
                case,
                [
                    {
                        "pk": None,
                        "position": 1,
                        "action": "Novo",
                        "expected_result": "ok",
                        "is_active": True,
                    }
                ],
            )

        step.refresh_from_db()
        self.assertFalse(step.is_active)
