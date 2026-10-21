from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.accounts.models import Role

User = get_user_model()


def make_user(**overrides):
    data = {
        "email": "testador@example.com",
        "name": "Testador Padrão",
        "password": "uma-senha-bem-segura-123",
    }
    data.update(overrides)
    return User.objects.create_user(**data)


class UserModelConfigurationTests(TestCase):
    def test_auth_user_model_points_to_accounts_user(self):
        self.assertEqual(User._meta.label, "accounts.User")
        self.assertEqual(User.USERNAME_FIELD, "email")
        self.assertIn("name", User.REQUIRED_FIELDS)

    def test_role_exposes_exactly_the_two_product_values(self):
        self.assertEqual(set(Role.values), {"ADMINISTRATOR", "TESTER"})


class EmailNormalizationTests(TestCase):
    def test_email_is_lowercased_and_trimmed_before_persistence(self):
        user = make_user(email="  Ana.SILVA@Example.COM  ")

        self.assertEqual(user.email, "ana.silva@example.com")

        user.refresh_from_db()
        self.assertEqual(user.email, "ana.silva@example.com")

    def test_email_normalization_applies_beyond_the_manager(self):
        user = User(email="  MARIA.Santos@Example.COM ", name="Maria")
        user.set_password("uma-senha-bem-segura-123")
        user.save()

        user.refresh_from_db()
        self.assertEqual(user.email, "maria.santos@example.com")

    def test_partial_save_with_listed_fields_persists_normalization(self):
        user = make_user(email="parcial@example.com")

        user.name = "  Nome Ajustado  "
        user.email = " AJUSTADO@Example.COM "
        user.save(update_fields=["name", "email"])

        user.refresh_from_db()
        self.assertEqual(user.name, "Nome Ajustado")
        self.assertEqual(user.email, "ajustado@example.com")

    def test_partial_save_normalizes_changed_email_even_if_not_listed(self):
        user = make_user(email="partes@example.com")

        user.name = "Nome"
        user.email = "PARTES.ATUALIZADO@Example.COM"
        user.save(update_fields=["name"])

        user.refresh_from_db()
        self.assertEqual(user.email, "partes.atualizado@example.com")

    def test_duplicate_email_is_rejected_case_insensitively(self):
        make_user(email="tester@example.com")

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                User.objects.create(
                    email="TESTER@EXAMPLE.COM",
                    name="Duplicado",
                )

    def test_blank_email_is_rejected_by_the_manager(self):
        with self.assertRaises(ValueError):
            User.objects.create_user(
                email="   ",
                name="Sem Email",
                password="uma-senha-bem-segura-123",
            )


class RoleFieldTests(TestCase):
    def test_default_role_is_tester(self):
        user = make_user(email="padrao@example.com")
        self.assertEqual(user.role, Role.TESTER)
        self.assertTrue(user.is_tester)
        self.assertFalse(user.is_administrator)

    def test_administrator_product_role_keeps_staff_flags_distinct(self):
        admin = make_user(email="admin@example.com", role=Role.ADMINISTRATOR)

        self.assertEqual(admin.role, Role.ADMINISTRATOR)
        self.assertTrue(admin.is_administrator)
        self.assertFalse(admin.is_tester)
        self.assertFalse(admin.is_staff)
        self.assertFalse(admin.is_superuser)

    def test_invalid_role_is_rejected_by_the_database(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                User.objects.create(
                    email="invalido@example.com",
                    name="Perfil Inválido",
                    role="SUPERVISOR",
                )

    def test_create_superuser_sets_administrator_role_and_flags(self):
        superuser = User.objects.create_superuser(
            email="Root@Example.com",
            name="Root",
            password="uma-senha-bem-segura-123",
        )

        self.assertEqual(superuser.role, Role.ADMINISTRATOR)
        self.assertTrue(superuser.is_staff)
        self.assertTrue(superuser.is_superuser)
        self.assertEqual(superuser.email, "root@example.com")

    def test_inactive_user_is_not_reported_as_administrator_or_tester(self):
        inactive_admin = make_user(
            email="admin-inativo@example.com",
            role=Role.ADMINISTRATOR,
            is_active=False,
        )
        inactive_tester = make_user(
            email="testador-inativo@example.com",
            is_active=False,
        )

        self.assertFalse(inactive_admin.is_administrator)
        self.assertFalse(inactive_admin.is_tester)
        self.assertFalse(inactive_tester.is_tester)
        self.assertFalse(inactive_tester.is_administrator)


class PasswordHashingTests(TestCase):
    def test_password_is_hashed_and_verifiable(self):
        user = make_user(
            email="hash@example.com", password="uma-senha-bem-segura-123"
        )

        self.assertNotEqual(user.password, "uma-senha-bem-segura-123")
        self.assertTrue(user.password.startswith("pbkdf2_"))
        self.assertTrue(user.check_password("uma-senha-bem-segura-123"))
        self.assertFalse(user.check_password("senha-errada"))
        self.assertTrue(user.has_usable_password())


class AuthenticationTests(TestCase):
    def test_active_user_can_authenticate(self):
        user = make_user(email="ativo@example.com")

        self.assertEqual(
            authenticate(email="ativo@example.com", password="uma-senha-bem-segura-123"),
            user,
        )

    def test_inactive_user_cannot_authenticate(self):
        make_user(email="inativo@example.com", is_active=False)

        self.assertIsNone(
            authenticate(
                email="inativo@example.com", password="uma-senha-bem-segura-123"
            )
        )

    def test_session_backend_rejects_inactive_user(self):
        user = make_user(email="inativo-sessao@example.com", is_active=False)

        self.assertIsNone(ModelBackend().get_user(user.pk))

    def test_wrong_password_is_rejected(self):
        make_user(email="senha@example.com")

        self.assertIsNone(
            authenticate(email="senha@example.com", password="senha-errada")
        )
