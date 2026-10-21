from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import Role

User = get_user_model()

PASSWORD = "uma-senha-bem-segura-123"
NEW_PASSWORD = "outra-senha-igual-segura-456"


def make_user(email, role=Role.TESTER, password=PASSWORD, **overrides):
    data = {"email": email, "name": "Usuário de Teste", "password": password, "role": role}
    data.update(overrides)
    return User.objects.create_user(**data)


class AccountManagementAuthorizationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("admin@example.com", role=Role.ADMINISTRATOR)
        cls.tester = make_user("tester@example.com")

    def setUp(self):
        self.client = Client()

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(reverse("accounts:user_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_tester_cannot_list_or_create_users(self):
        self.client.force_login(self.tester)

        self.assertEqual(self.client.get(reverse("accounts:user_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("accounts:user_create")).status_code, 403)

    def test_tester_cannot_edit_or_inactivate_users(self):
        self.client.force_login(self.tester)
        victim = make_user("victim@example.com")

        self.assertEqual(
            self.client.get(reverse("accounts:user_edit", args=[victim.pk])).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                reverse("accounts:user_inactivate", args=[victim.pk])
            ).status_code,
            403,
        )
        victim.refresh_from_db()
        self.assertTrue(victim.is_active)


class AdministratorUserManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("admin@example.com", role=Role.ADMINISTRATOR)

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.admin)

    def _create_payload(self, email, role=Role.TESTER, **overrides):
        payload = {
            "email": email,
            "name": "Novo Usuário",
            "role": role,
            "is_active": "on",
            "password1": PASSWORD,
            "password2": PASSWORD,
        }
        payload.update(overrides)
        return payload

    def test_administrator_creates_tester_who_can_authenticate(self):
        response = self.client.post(
            reverse("accounts:user_create"),
            self._create_payload("novo.testador@example.com"),
        )

        self.assertRedirects(response, reverse("accounts:user_list"))
        user = User.objects.get(email="novo.testador@example.com")
        self.assertEqual(user.role, Role.TESTER)
        self.assertTrue(user.is_active)
        self.assertTrue(user.check_password(PASSWORD))

        fresh = Client()
        self.assertTrue(
            fresh.login(email="novo.testador@example.com", password=PASSWORD)
        )

    def test_administrator_can_grant_administrator_profile(self):
        response = self.client.post(
            reverse("accounts:user_create"),
            self._create_payload("novo.admin@example.com", role=Role.ADMINISTRATOR),
        )

        self.assertRedirects(response, reverse("accounts:user_list"))
        self.assertTrue(User.objects.get(email="novo.admin@example.com").is_administrator)

    def test_duplicate_email_is_rejected_case_insensitively(self):
        make_user("duplicado@example.com")

        response = self.client.post(
            reverse("accounts:user_create"),
            self._create_payload("DUPLICADO@example.com"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("email", response.context["form"].errors)
        self.assertEqual(
            User.objects.filter(email__iexact="duplicado@example.com").count(), 1
        )

    def test_password_confirmation_mismatch_is_rejected(self):
        response = self.client.post(
            reverse("accounts:user_create"),
            self._create_payload(
                "mismatch@example.com",
                password2="confirmacao-diferente-123",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="mismatch@example.com").exists())

    def test_weak_password_is_rejected(self):
        response = self.client.post(
            reverse("accounts:user_create"),
            self._create_payload(
                "fraco@example.com", password1="12345678", password2="12345678"
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="fraco@example.com").exists())

    def test_administrator_edits_user_fields_and_role(self):
        tester = make_user("editar@example.com")

        response = self.client.post(
            reverse("accounts:user_edit", args=[tester.pk]),
            {
                "email": tester.email,
                "name": "Nome Editado",
                "role": Role.ADMINISTRATOR,
                "is_active": "on",
            },
        )

        self.assertRedirects(response, reverse("accounts:user_list"))
        tester.refresh_from_db()
        self.assertEqual(tester.name, "Nome Editado")
        self.assertEqual(tester.role, Role.ADMINISTRATOR)

    def test_administrator_inactivates_user_and_login_is_denied(self):
        tester = make_user("inativar@example.com")

        response = self.client.post(
            reverse("accounts:user_inactivate", args=[tester.pk])
        )
        self.assertRedirects(response, reverse("accounts:user_list"))

        tester.refresh_from_db()
        self.assertFalse(tester.is_active)

        fresh = Client()
        self.assertFalse(fresh.login(email="inativar@example.com", password=PASSWORD))
        self.assertNotIn("_auth_user_id", fresh.session)

    def test_administrator_cannot_inactivate_own_account(self):
        response = self.client.post(
            reverse("accounts:user_inactivate", args=[self.admin.pk]), follow=True
        )

        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)
        self.assertContains(response, "não pode inativar a própria conta")

    def test_administrator_cannot_inactivate_self_through_edit_form(self):
        response = self.client.post(
            reverse("accounts:user_edit", args=[self.admin.pk]),
            {
                "email": self.admin.email,
                "name": self.admin.name,
                "role": self.admin.role,
                "is_active": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("is_active", response.context["form"].errors)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)


class InactiveLoginTests(TestCase):
    def test_inactive_account_cannot_log_in_through_the_view(self):
        make_user("inativo@example.com", is_active=False)
        client = Client()

        response = client.post(
            reverse("accounts:login"),
            {"username": "inativo@example.com", "password": PASSWORD},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", client.session)


class OwnProfileTests(TestCase):
    def setUp(self):
        self.tester = make_user("perfil@example.com")
        self.client = Client()
        self.client.force_login(self.tester)

    def test_user_updates_own_profile(self):
        response = self.client.post(
            reverse("accounts:profile"),
            {"name": "Nome Próprio", "email": self.tester.email},
        )

        self.assertRedirects(response, reverse("accounts:profile"))
        self.tester.refresh_from_db()
        self.assertEqual(self.tester.name, "Nome Próprio")

    def test_profile_edit_cannot_escalate_role_or_active_state(self):
        response = self.client.post(
            reverse("accounts:profile"),
            {
                "name": "Tentativa de Escalação",
                "email": self.tester.email,
                "role": Role.ADMINISTRATOR,
            },
        )

        self.assertRedirects(response, reverse("accounts:profile"))
        self.tester.refresh_from_db()
        self.assertEqual(self.tester.role, Role.TESTER)
        self.assertTrue(self.tester.is_active)

    def test_profile_cannot_change_the_login_email(self):
        response = self.client.post(
            reverse("accounts:profile"),
            {"name": "Nome Atualizado", "email": "trocado@example.com"},
        )

        self.assertRedirects(response, reverse("accounts:profile"))
        self.tester.refresh_from_db()
        self.assertEqual(self.tester.name, "Nome Atualizado")
        self.assertEqual(self.tester.email, "perfil@example.com")


class PasswordChangeTests(TestCase):
    def setUp(self):
        self.tester = make_user("senha@example.com")
        self.client = Client()
        self.client.force_login(self.tester)

    def _payload(self, old=PASSWORD, new1=NEW_PASSWORD, new2=NEW_PASSWORD):
        return {
            "old_password": old,
            "new_password1": new1,
            "new_password2": new2,
        }

    def test_wrong_current_password_is_rejected(self):
        response = self.client.post(
            reverse("accounts:password_change"),
            self._payload(old="senha-incorreta"),
        )

        self.assertEqual(response.status_code, 200)
        self.tester.refresh_from_db()
        self.assertTrue(self.tester.check_password(PASSWORD))

    def test_mismatched_confirmation_is_rejected(self):
        response = self.client.post(
            reverse("accounts:password_change"),
            self._payload(new2="confirmacao-diferente-789"),
        )

        self.assertEqual(response.status_code, 200)
        self.tester.refresh_from_db()
        self.assertTrue(self.tester.check_password(PASSWORD))

    def test_successful_change_persists_and_keeps_session(self):
        response = self.client.post(
            reverse("accounts:password_change"),
            self._payload(),
        )

        self.assertRedirects(response, reverse("accounts:profile"))
        self.tester.refresh_from_db()
        self.assertTrue(self.tester.check_password(NEW_PASSWORD))

        self.assertEqual(
            self.client.get(reverse("accounts:profile")).status_code, 200
        )
