from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _


class Role(models.TextChoices):
    ADMINISTRATOR = "ADMINISTRATOR", _("Administrador")
    TESTER = "TESTER", _("Testador")


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _normalized_email(self, email: str) -> str:
        return self.normalize_email((email or "").strip()).lower()

    def _create_user(self, email, name, password, **extra_fields):
        if not email or not str(email).strip():
            raise ValueError(_("O email é obrigatório."))
        if not name or not str(name).strip():
            raise ValueError(_("O nome é obrigatório."))

        user = self.model(
            email=self._normalized_email(email),
            name=str(name).strip(),
            **extra_fields,
        )
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, name, password=None, **extra_fields):
        extra_fields.setdefault("role", Role.TESTER)
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, name, password, **extra_fields)

    def create_superuser(self, email, name, password=None, **extra_fields):
        extra_fields.setdefault("role", Role.ADMINISTRATOR)
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError(_("Um superusuário precisa de is_staff=True."))
        if extra_fields.get("is_superuser") is not True:
            raise ValueError(_("Um superusuário precisa de is_superuser=True."))

        return self._create_user(email, name, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(_("email"), unique=True)
    name = models.CharField(_("nome"), max_length=150)
    role = models.CharField(
        _("perfil"),
        max_length=20,
        choices=Role.choices,
        default=Role.TESTER,
    )
    is_active = models.BooleanField(_("ativo"), default=True)
    is_staff = models.BooleanField(_("membro da equipe"), default=False)
    created_at = models.DateTimeField(_("criado em"), auto_now_add=True)
    updated_at = models.DateTimeField(_("atualizado em"), auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["name"]

    class Meta:
        verbose_name = _("usuário")
        verbose_name_plural = _("usuários")
        ordering = ["name", "email"]
        constraints = [
            models.CheckConstraint(
                condition=Q(role__in=Role.values),
                name="accounts_user_role_valid",
            ),
        ]

    def __str__(self):
        return self.name or self.email

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        fields_to_include = set()

        if self.email:
            normalized_email = self.__class__.objects.normalize_email(self.email).lower()
            if normalized_email != self.email:
                self.email = normalized_email
                fields_to_include.add("email")
        if self.name:
            stripped_name = self.name.strip()
            if stripped_name != self.name:
                self.name = stripped_name
                fields_to_include.add("name")

        if update_fields is not None and fields_to_include:
            kwargs["update_fields"] = set(update_fields) | fields_to_include

        super().save(*args, **kwargs)

    def get_full_name(self):
        return self.name

    def get_short_name(self):
        return self.name.split(" ", 1)[0] if self.name else ""

    @property
    def is_administrator(self) -> bool:
        return self.is_active and self.role == Role.ADMINISTRATOR

    @property
    def is_tester(self) -> bool:
        return self.is_active and self.role == Role.TESTER
