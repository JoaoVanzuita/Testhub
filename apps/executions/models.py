from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.projects.models import Project
from apps.testcases.models import TestCase, TestStep


class ExecutionState(models.TextChoices):
    IN_PROGRESS = "IN_PROGRESS", _("Em andamento")
    COMPLETED = "COMPLETED", _("Concluída")


class ReleaseReason(models.TextChoices):
    VOLUNTARY = "VOLUNTARY", _("Liberação voluntária")
    PROJECT_ACCESS_REVOKED = "PROJECT_ACCESS_REVOKED", _("Acesso ao projeto revogado")
    HANDOFF = "HANDOFF", _("Transferência")


class CaseStatus(models.TextChoices):
    NOT_STARTED = "CASE_NOT_STARTED", _("Não iniciado")
    APPROVED = "CASE_APPROVED", _("Aprovado")
    REPROVED = "CASE_REPROVED", _("Reprovado")
    PENDING = "CASE_PENDING", _("Pendente")


class StepOutcome(models.TextChoices):
    PASSED = "PASSED", _("Passou")
    FAILED = "FAILED", _("Falhou")
    BLOCKED = "BLOCKED", _("Bloqueado")


class AIStatus(models.TextChoices):
    PENDING = "PENDING", _("Em processamento")
    PROCESSING = "PROCESSING", _("Em processamento")
    AVAILABLE = "AVAILABLE", _("Disponível")
    UNAVAILABLE = "UNAVAILABLE", _("Indisponível")


class FailureCategory(models.TextChoices):
    CONFIGURATION = "CONFIGURATION", _("Provedor não configurado")
    TIMEOUT = "TIMEOUT", _("Tempo esgotado")
    CONNECTION = "CONNECTION", _("Falha de conexão")
    HTTP_ERROR = "HTTP_ERROR", _("Resposta HTTP inesperada")
    INVALID_REQUEST = "INVALID_REQUEST", _("Solicitação inválida")
    INVALID_RESPONSE = "INVALID_RESPONSE", _("Resposta inválida")
    INVALID_REFERENCE = "INVALID_REFERENCE", _("Referência inválida")
    UNEXPECTED = "UNEXPECTED", _("Erro inesperado")


def derive_case_status(outcomes):
    values = list(outcomes)
    if any(outcome == StepOutcome.FAILED for outcome in values):
        return CaseStatus.REPROVED
    if all(outcome is None for outcome in values):
        return CaseStatus.NOT_STARTED
    if any(outcome in (StepOutcome.BLOCKED, None) for outcome in values):
        return CaseStatus.PENDING
    return CaseStatus.APPROVED


class Execution(models.Model):
    project = models.ForeignKey(
        Project,
        on_delete=models.PROTECT,
        related_name="executions",
        verbose_name=_("projeto"),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_executions",
        verbose_name=_("criado por"),
    )
    state = models.CharField(
        _("estado"),
        max_length=20,
        choices=ExecutionState.choices,
        default=ExecutionState.IN_PROGRESS,
    )
    started_at = models.DateTimeField(_("iniciada em"), default=timezone.now)
    completed_at = models.DateTimeField(_("concluída em"), null=True, blank=True)
    created_at = models.DateTimeField(_("criado em"), auto_now_add=True)
    updated_at = models.DateTimeField(_("atualizado em"), auto_now=True)

    class Meta:
        verbose_name = _("execução")
        verbose_name_plural = _("execuções")
        ordering = ["-started_at", "pk"]
        indexes = [
            models.Index(
                fields=["project", "state", "started_at"],
                name="exec_project_state_started",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(state__in=ExecutionState.values),
                name="exec_state_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(state=ExecutionState.COMPLETED, completed_at__isnull=False)
                    | Q(state=ExecutionState.IN_PROGRESS, completed_at__isnull=True)
                ),
                name="exec_completion_timestamp_consistent",
            ),
        ]

    def __str__(self):
        return f"Execução #{self.pk} — {self.project}"

    @property
    def is_in_progress(self) -> bool:
        return self.state == ExecutionState.IN_PROGRESS

    @property
    def is_completed(self) -> bool:
        return self.state == ExecutionState.COMPLETED

    @property
    def current_assignment(self):
        return (
            self.assignments.filter(released_at__isnull=True)
            .select_related("responsible")
            .first()
        )

    @property
    def current_owner(self):
        assignment = self.current_assignment
        return assignment.responsible if assignment is not None else None


class ExecutionAssignment(models.Model):
    execution = models.ForeignKey(
        Execution,
        on_delete=models.CASCADE,
        related_name="assignments",
        verbose_name=_("execução"),
    )
    responsible = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="execution_assignments",
        verbose_name=_("responsável"),
    )
    assigned_at = models.DateTimeField(_("atribuído em"), default=timezone.now)
    released_at = models.DateTimeField(_("liberado em"), null=True, blank=True)
    release_reason = models.CharField(
        _("motivo da liberação"),
        max_length=32,
        choices=ReleaseReason.choices,
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = _("atribuição de execução")
        verbose_name_plural = _("atribuições de execução")
        ordering = ["assigned_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["execution"],
                condition=Q(released_at__isnull=True),
                name="exec_one_current_assignment",
            ),
            models.CheckConstraint(
                condition=(
                    Q(released_at__isnull=True, release_reason__isnull=True)
                    | Q(released_at__isnull=False)
                ),
                name="exec_assignment_release_consistent",
            ),
            models.CheckConstraint(
                condition=(
                    Q(release_reason__isnull=True)
                    | Q(release_reason__in=ReleaseReason.values)
                ),
                name="exec_assignment_reason_valid",
            ),
        ]

    def __str__(self):
        return f"{self.responsible} em {self.execution}"

    @property
    def is_current(self) -> bool:
        return self.released_at is None


class ExecutionCase(models.Model):
    execution = models.ForeignKey(
        Execution,
        on_delete=models.CASCADE,
        related_name="execution_cases",
        verbose_name=_("execução"),
    )
    source_test_case = models.ForeignKey(
        TestCase,
        on_delete=models.PROTECT,
        related_name="execution_cases",
        verbose_name=_("caso de origem"),
    )
    title = models.CharField(_("título"), max_length=200)
    description = models.TextField(_("descrição"))
    preconditions = models.TextField(_("pré-condições"))
    status = models.CharField(
        _("situação"),
        max_length=20,
        choices=CaseStatus.choices,
        default=CaseStatus.NOT_STARTED,
    )
    created_at = models.DateTimeField(_("criado em"), auto_now_add=True)
    updated_at = models.DateTimeField(_("atualizado em"), auto_now=True)

    class Meta:
        verbose_name = _("caso em execução")
        verbose_name_plural = _("casos em execução")
        ordering = ["pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["execution", "source_test_case"],
                name="exec_case_unique_source",
            ),
            models.CheckConstraint(
                condition=Q(status__in=CaseStatus.values),
                name="exec_case_status_valid",
            ),
        ]

    def __str__(self):
        return self.title

    @property
    def total_steps(self) -> int:
        return len(self.steps.all())

    @property
    def assessed_steps(self) -> int:
        return sum(1 for step in self.steps.all() if step.outcome is not None)

    @property
    def progress_label(self) -> str:
        return f"{self.assessed_steps} de {self.total_steps} passos avaliados"

    def refresh_status(self, *, save=True):
        status = derive_case_status(
            self.steps.values_list("outcome", flat=True)
        )
        if save and status != self.status:
            self.status = status
            self.save(update_fields=["status", "updated_at"])
        return status


class ExecutionStep(models.Model):
    execution_case = models.ForeignKey(
        ExecutionCase,
        on_delete=models.CASCADE,
        related_name="steps",
        verbose_name=_("caso em execução"),
    )
    source_test_step = models.ForeignKey(
        TestStep,
        on_delete=models.PROTECT,
        related_name="execution_steps",
        verbose_name=_("passo de origem"),
    )
    snapshot_position = models.PositiveIntegerField(_("ordem"))
    action = models.TextField(_("ação"))
    expected_result = models.TextField(_("resultado esperado"))
    outcome = models.CharField(
        _("resultado"),
        max_length=10,
        choices=StepOutcome.choices,
        null=True,
        blank=True,
    )
    observation = models.TextField(_("observação"), blank=True, default="")
    created_at = models.DateTimeField(_("criado em"), auto_now_add=True)
    updated_at = models.DateTimeField(_("atualizado em"), auto_now=True)

    class Meta:
        verbose_name = _("passo em execução")
        verbose_name_plural = _("passos em execução")
        ordering = ["snapshot_position", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["execution_case", "snapshot_position"],
                name="exec_step_case_position_uniq",
            ),
            models.CheckConstraint(
                condition=Q(snapshot_position__gt=0),
                name="exec_step_position_positive",
            ),
            models.CheckConstraint(
                condition=(
                    Q(outcome__isnull=True) | Q(outcome__in=StepOutcome.values)
                ),
                name="exec_step_outcome_valid",
            ),
        ]

    def __str__(self):
        return f"{self.snapshot_position}. {self.action}"


class Report(models.Model):
    execution = models.OneToOneField(
        Execution,
        on_delete=models.PROTECT,
        related_name="report",
        verbose_name=_("execução"),
    )
    created_at = models.DateTimeField(_("criado em"), auto_now_add=True)
    updated_at = models.DateTimeField(_("atualizado em"), auto_now=True)

    selected_cases = models.PositiveIntegerField(
        _("casos selecionados"), default=0
    )
    approved_cases = models.PositiveIntegerField(_("casos aprovados"), default=0)
    reproved_cases = models.PositiveIntegerField(_("casos reprovados"), default=0)
    pending_cases = models.PositiveIntegerField(_("casos pendentes"), default=0)
    not_started_cases = models.PositiveIntegerField(
        _("casos não iniciados"), default=0
    )
    passed_steps = models.PositiveIntegerField(_("passos com Passou"), default=0)
    failed_steps = models.PositiveIntegerField(_("passos com Falhou"), default=0)
    blocked_steps = models.PositiveIntegerField(_("passos com Bloqueado"), default=0)
    unevaluated_steps = models.PositiveIntegerField(
        _("passos não avaliados"), default=0
    )
    failure_observations = models.PositiveIntegerField(
        _("observações de falha/bloqueio"), default=0
    )
    success_rate = models.DecimalField(
        _("taxa de sucesso"),
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    ai_status = models.CharField(
        _("estado da análise"),
        max_length=20,
        choices=AIStatus.choices,
        default=AIStatus.UNAVAILABLE,
    )
    analysis = models.JSONField(_("análise"), null=True, blank=True)
    failure_category = models.CharField(
        _("categoria da falha"),
        max_length=32,
        choices=FailureCategory.choices,
        null=True,
        blank=True,
    )

    attempts = models.PositiveIntegerField(_("tentativas"), default=0)
    lease_token = models.CharField(
        _("token do lease"), max_length=64, null=True, blank=True
    )
    lease_expires_at = models.DateTimeField(
        _("lease expira em"), null=True, blank=True
    )

    class Meta:
        verbose_name = _("relatório")
        verbose_name_plural = _("relatórios")
        ordering = ["-created_at", "pk"]
        indexes = [
            models.Index(
                fields=["ai_status", "lease_expires_at"],
                name="report_ai_status_lease",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(success_rate__gte=0) & Q(success_rate__lte=100),
                name="report_success_rate_range",
            ),
            models.CheckConstraint(
                condition=Q(
                    selected_cases=(
                        F("approved_cases")
                        + F("reproved_cases")
                        + F("pending_cases")
                        + F("not_started_cases")
                    )
                ),
                name="report_case_counts_consistent",
            ),
            models.CheckConstraint(
                condition=(
                    Q(ai_status=AIStatus.AVAILABLE, analysis__isnull=False)
                    | Q(
                        ai_status__in=[
                            AIStatus.PENDING,
                            AIStatus.PROCESSING,
                            AIStatus.UNAVAILABLE,
                        ],
                        analysis__isnull=True,
                    )
                ),
                name="report_analysis_only_when_available",
            ),
        ]

    def __str__(self):
        return f"Relatório da execução #{self.execution_id}"

    @property
    def ai_status_label(self) -> str:
        if self.ai_status in (AIStatus.PENDING, AIStatus.PROCESSING):
            return "Em processamento"
        if self.ai_status == AIStatus.AVAILABLE:
            return "Disponível"
        return "Indisponível"

    @property
    def is_ai_processing(self) -> bool:
        return self.ai_status in (AIStatus.PENDING, AIStatus.PROCESSING)

    @property
    def has_analysis(self) -> bool:
        return self.ai_status == AIStatus.AVAILABLE and bool(self.analysis)
