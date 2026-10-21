from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q, Value
from django.db.models.functions import Length, Lower, Trim
from django.db.models.lookups import GreaterThan
from django.utils.translation import gettext_lazy as _


class Project(models.Model):
    name = models.CharField(_("nome"), max_length=200)
    description = models.TextField(
        _("descrição"),
        help_text=_(
            "Uma descrição detalhada ajuda a tornar as análises de IA mais "
            "coerentes."
        ),
    )
    is_active = models.BooleanField(_("ativo"), default=True)
    created_at = models.DateTimeField(_("criado em"), auto_now_add=True)
    updated_at = models.DateTimeField(_("atualizado em"), auto_now=True)

    class Meta:
        verbose_name = _("projeto")
        verbose_name_plural = _("projetos")
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                Lower("name"), name="projects_project_name_ci_unique"
            ),
            models.CheckConstraint(
                condition=GreaterThan(Length(Trim("description")), Value(0)),
                name="projects_project_description_required",
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if not (self.description or "").strip():
            raise ValidationError(
                {"description": _("Informe uma descrição do projeto.")}
            )

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        changed_fields = set()
        for field in ("name", "description"):
            value = getattr(self, field)
            if value:
                stripped = value.strip()
                if stripped != value:
                    setattr(self, field, stripped)
                    changed_fields.add(field)
        if update_fields is not None and changed_fields:
            kwargs["update_fields"] = set(update_fields) | changed_fields
        super().save(*args, **kwargs)


class ProjectMembership(models.Model):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="memberships",
        verbose_name=_("projeto"),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="project_memberships",
        verbose_name=_("usuário"),
    )
    assigned_at = models.DateTimeField(_("atribuído em"), auto_now_add=True)
    revoked_at = models.DateTimeField(_("revogado em"), null=True, blank=True)

    class Meta:
        verbose_name = _("atribuição de projeto")
        verbose_name_plural = _("atribuições de projeto")
        ordering = ["project_id", "user_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "user"],
                condition=Q(revoked_at__isnull=True),
                name="projects_membership_active_unique",
            ),
        ]

    def __str__(self):
        return f"{self.user} em {self.project}"

    @property
    def is_active(self):
        return self.revoked_at is None
