from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import Role
from apps.projects import services
from apps.projects.forms import MembershipForm, ProjectForm
from apps.projects.models import Project, ProjectMembership

User = get_user_model()

PASSWORD = "uma-senha-bem-segura-123"


def make_user(email, role=Role.TESTER, **overrides):
    data = {"email": email, "name": "Usuário de Teste", "password": PASSWORD, "role": role}
    data.update(overrides)
    return User.objects.create_user(**data)


def make_project(name="Projeto Alpha", description="Descrição do projeto"):
    return Project.objects.create(name=name, description=description)


class ProjectAuthorizationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("admin@example.com", role=Role.ADMINISTRATOR)
        cls.tester = make_user("tester@example.com")

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.tester)

    def test_tester_cannot_create_or_edit_projects(self):
        project = make_project()

        self.assertEqual(self.client.get(reverse("projects:project_create")).status_code, 403)
        self.assertEqual(
            self.client.get(reverse("projects:project_edit", args=[project.pk])).status_code,
            403,
        )

    def test_tester_cannot_manage_memberships(self):
        project = make_project()
        other = make_user("other@example.com")

        self.assertEqual(
            self.client.post(
                reverse("projects:membership_add", args=[project.pk]),
                {"user": other.pk},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                reverse("projects:membership_remove", args=[project.pk, other.pk])
            ).status_code,
            403,
        )

    def test_anonymous_is_redirected(self):
        self.client.logout()
        response = self.client.get(reverse("projects:project_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)


class ProjectManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("admin@example.com", role=Role.ADMINISTRATOR)

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.admin)

    def test_administrator_creates_project_without_assignments(self):
        response = self.client.post(
            reverse("projects:project_create"),
            {"name": "Projeto Sem Equipe", "description": "Descrição livre"},
        )

        self.assertRedirects(response, reverse("projects:project_list"))
        project = Project.objects.get(name="Projeto Sem Equipe")
        self.assertEqual(project.memberships.count(), 0)
        self.assertTrue(project.is_active)
        self.assertIn(project, Project.objects.all())

    def test_duplicate_project_name_is_rejected_case_insensitively(self):
        make_project(name="Projeto Duplicado")

        response = self.client.post(
            reverse("projects:project_create"),
            {"name": "projeto duplicado", "description": "Descrição válida"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("name", response.context["form"].errors)
        self.assertEqual(
            Project.objects.filter(name__iexact="Projeto Duplicado").count(), 1
        )

    def test_administrator_edits_and_inactivates_project(self):
        project = make_project(name="Projeto Editável")

        response = self.client.post(
            reverse("projects:project_edit", args=[project.pk]),
            {"name": "Projeto Editado", "description": "Nova descrição", "is_active": "on"},
        )
        self.assertRedirects(response, reverse("projects:project_list"))
        project.refresh_from_db()
        self.assertEqual(project.name, "Projeto Editado")

        self.client.post(reverse("projects:project_inactivate", args=[project.pk]))
        project.refresh_from_db()
        self.assertFalse(project.is_active)

    def test_physical_deletion_is_blocked_while_referenced(self):
        project = make_project(name="Projeto Referenciado")

        error = ProtectedError("protegido", {project})
        with mock.patch.object(Project, "delete", side_effect=error):
            response = self.client.post(
                reverse("projects:project_delete", args=[project.pk]),
                follow=True,
            )

        self.assertTrue(Project.objects.filter(pk=project.pk).exists())
        self.assertContains(response, "inativ", status_code=200)

    def test_project_without_references_can_be_deleted(self):
        project = make_project(name="Projeto Descartável")

        response = self.client.post(
            reverse("projects:project_delete", args=[project.pk])
        )

        self.assertRedirects(response, reverse("projects:project_list"))
        self.assertFalse(Project.objects.filter(pk=project.pk).exists())


class MembershipWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("admin@example.com", role=Role.ADMINISTRATOR)
        cls.tester = make_user("tester@example.com")

    def setUp(self):
        self.admin_client = Client()
        self.admin_client.force_login(self.admin)
        self.tester_client = Client()
        self.tester_client.force_login(self.tester)

    def test_created_project_is_invisible_to_unassigned_tester(self):
        project = make_project(name="Projeto Restrito")

        admin_list = self.admin_client.get(reverse("projects:project_list"))
        self.assertIn(project, admin_list.context["projects"])

        tester_list = self.tester_client.get(reverse("projects:project_list"))
        self.assertNotIn(project, tester_list.context["projects"])
        self.assertEqual(
            self.tester_client.get(
                reverse("projects:project_detail", args=[project.pk])
            ).status_code,
            404,
        )

    def test_assignment_grants_access(self):
        project = make_project(name="Projeto Atribuído")

        response = self.admin_client.post(
            reverse("projects:membership_add", args=[project.pk]),
            {"user": self.tester.pk},
        )
        self.assertRedirects(
            response, reverse("projects:memberships", args=[project.pk])
        )

        self.assertTrue(
            ProjectMembership.objects.filter(
                project=project, user=self.tester, revoked_at__isnull=True
            ).exists()
        )
        self.assertIn(
            project,
            self.tester_client.get(reverse("projects:project_list")).context["projects"],
        )
        self.assertEqual(
            self.tester_client.get(
                reverse("projects:project_detail", args=[project.pk])
            ).status_code,
            200,
        )

    def test_removing_assignment_revokes_access_immediately(self):
        project = make_project(name="Projeto Revogável")
        self.admin_client.post(
            reverse("projects:membership_add", args=[project.pk]),
            {"user": self.tester.pk},
        )

        response = self.admin_client.post(
            reverse("projects:membership_remove", args=[project.pk, self.tester.pk])
        )
        self.assertRedirects(
            response, reverse("projects:memberships", args=[project.pk])
        )

        self.assertFalse(
            ProjectMembership.objects.filter(
                project=project, user=self.tester, revoked_at__isnull=True
            ).exists()
        )
        tester_list = self.tester_client.get(reverse("projects:project_list"))
        self.assertNotIn(project, tester_list.context["projects"])
        self.assertEqual(
            self.tester_client.get(
                reverse("projects:project_detail", args=[project.pk])
            ).status_code,
            404,
        )

    def test_only_active_testers_can_be_assigned(self):
        project = make_project(name="Projeto Sem Inativos")
        inactive = make_user("inativo@example.com", is_active=False)
        administrator = make_user("outro.admin@example.com", role=Role.ADMINISTRATOR)

        form = MembershipForm(project=project)
        offered = set(form.fields["user"].queryset.values_list("pk", flat=True))
        self.assertNotIn(inactive.pk, offered)
        self.assertNotIn(administrator.pk, offered)
        self.assertIn(self.tester.pk, offered)

        with self.assertRaises(services.MembershipError):
            services.assign_tester(project, inactive)
        with self.assertRaises(services.MembershipError):
            services.assign_tester(project, administrator)


class ProjectDescriptionRequirementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("admin@example.com", role=Role.ADMINISTRATOR)

    def test_form_field_is_required_with_ai_help_text(self):
        field = ProjectForm().fields["description"]
        self.assertTrue(field.required)
        self.assertIn("análises de IA", str(field.help_text))

    def test_form_rejects_blank_description(self):
        form = ProjectForm(data={"name": "Projeto Sem Descrição", "description": ""})
        self.assertFalse(form.is_valid())
        self.assertIn("description", form.errors)

    def test_create_page_displays_ai_help_text(self):
        client = Client()
        client.force_login(self.admin)
        response = client.get(reverse("projects:project_create"))
        self.assertContains(
            response, "ajuda a tornar as análises de IA mais coerentes"
        )

    def test_clean_rejects_whitespace_only_description(self):
        project = Project(name="Projeto Espaços", description="   ")
        with self.assertRaises(ValidationError) as ctx:
            project.clean()
        self.assertIn("description", ctx.exception.message_dict)

    def test_save_strips_surrounding_whitespace(self):
        project = Project.objects.create(
            name="Projeto Com Espaços", description="  Descrição com bordas  "
        )
        project.refresh_from_db()
        self.assertEqual(project.description, "Descrição com bordas")

    def test_database_rejects_blank_description(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Project.objects.create(name="Projeto Vazio", description="   ")
