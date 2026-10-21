from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.projects.models import Project


class TestCase(models.Model):
    project = models.ForeignKey(
        Project,
        on_delete=models.PROTECT,
        related_name="test_cases",
        verbose_name=_("projeto"),
    )
    title = models.CharField(_("título"), max_length=200)
    description = models.TextField(_("descrição"))
    preconditions = models.TextField(_("pré-condições"))
    is_active = models.BooleanField(_("ativo"), default=True)
    created_at = models.DateTimeField(_("criado em"), auto_now_add=True)
    updated_at = models.DateTimeField(_("atualizado em"), auto_now=True)

    class Meta:
        verbose_name = _("caso de teste")
        verbose_name_plural = _("casos de teste")
        ordering = ["-is_active", "title", "pk"]
        indexes = [
            models.Index(
                fields=["project", "is_active", "title"],
                name="tc_case_project_active",
            ),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if self.title:
            stripped = self.title.strip()
            if stripped != self.title:
                self.title = stripped
                update_fields = kwargs.get("update_fields")
                if update_fields is not None:
                    kwargs["update_fields"] = set(update_fields) | {"title"}
        super().save(*args, **kwargs)

    @property
    def has_active_steps(self) -> bool:
        return self.steps.filter(is_active=True).exists()


class TestStep(models.Model):
    test_case = models.ForeignKey(
        TestCase,
        on_delete=models.CASCADE,
        related_name="steps",
        verbose_name=_("caso de teste"),
    )
    position = models.PositiveIntegerField(_("ordem"))
    action = models.TextField(_("ação"))
    expected_result = models.TextField(_("resultado esperado"))
    is_active = models.BooleanField(_("ativo"), default=True)
    created_at = models.DateTimeField(_("criado em"), auto_now_add=True)
    updated_at = models.DateTimeField(_("atualizado em"), auto_now=True)

    class Meta:
        verbose_name = _("passo")
        verbose_name_plural = _("passos")
        ordering = ["position", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["test_case", "position"],
                name="tc_step_case_position_uniq",
            ),
            models.CheckConstraint(
                condition=Q(position__gt=0),
                name="tc_step_position_positive",
            ),
        ]

    def __str__(self):
        return f"{self.position}. {self.action}"
