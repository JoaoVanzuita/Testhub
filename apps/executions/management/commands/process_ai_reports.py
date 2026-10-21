import logging
import time
import uuid
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.executions import ai_adapter
from apps.executions.models import AIStatus, FailureCategory, Report

logger = logging.getLogger(__name__)

DEFAULT_SLEEP_SECONDS = 5.0
DEFAULT_LEASE_SECONDS = 60.0


class Command(BaseCommand):
    help = "Processa a análise de IA dos relatórios de execuções concluídas."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Processa no máximo um relatório e encerra (uso em testes).",
        )
        parser.add_argument(
            "--sleep",
            type=float,
            default=DEFAULT_SLEEP_SECONDS,
            help="Intervalo entre verificações, em segundos.",
        )
        parser.add_argument(
            "--lease-seconds",
            type=float,
            default=DEFAULT_LEASE_SECONDS,
            help="Duração do lease de processamento, em segundos.",
        )

    def handle(self, *args, **options):
        lease_seconds = options["lease_seconds"]

        if options["once"]:
            processed = self.process_one(lease_seconds=lease_seconds)
            if processed is None:
                self.stdout.write("Nenhum relatório pendente.")
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Relatório #{processed.pk} processado ({processed.ai_status})."
                    )
                )
            return

        sleep_seconds = max(options["sleep"], 0.1)
        self.stdout.write(self.style.SUCCESS("Worker de IA iniciado."))
        while True:
            processed = self.process_one(lease_seconds=lease_seconds)
            if processed is None:
                time.sleep(sleep_seconds)


    def claim(self, *, lease_seconds):
        now = timezone.now()
        with transaction.atomic():
            report = (
                Report.objects.select_for_update(skip_locked=True)
                .filter(ai_status=AIStatus.PENDING)
                .order_by("created_at", "pk")
                .first()
            )
            if report is None:
                report = (
                    Report.objects.select_for_update(skip_locked=True)
                    .filter(
                        ai_status=AIStatus.PROCESSING,
                        lease_expires_at__lt=now,
                    )
                    .order_by("lease_expires_at", "pk")
                    .first()
                )
            if report is None:
                return None

            token = uuid.uuid4().hex
            report.ai_status = AIStatus.PROCESSING
            report.lease_token = token
            report.lease_expires_at = now + timedelta(seconds=lease_seconds)
            report.attempts = report.attempts + 1
            report.failure_category = None
            report.save(
                update_fields=[
                    "ai_status",
                    "lease_token",
                    "lease_expires_at",
                    "attempts",
                    "failure_category",
                    "updated_at",
                ]
            )
            return report.pk, token


    def process_one(self, *, lease_seconds):
        claimed = self.claim(lease_seconds=lease_seconds)
        if claimed is None:
            return None
        report_pk, token = claimed

        report = Report.objects.get(pk=report_pk)

        if not ai_adapter.is_provider_configured():
            self._finish(
                report_pk, token, failure_category=FailureCategory.CONFIGURATION
            )
            report.refresh_from_db()
            return report

        try:
            context = ai_adapter.build_request_context(report)
            analysis = ai_adapter.analyze(context)
        except ai_adapter.AdapterError as exc:
            logger.warning(
                "AI analysis unavailable for report %s (category=%s).",
                report_pk,
                exc.category,
            )
            self._finish(report_pk, token, failure_category=exc.category)
        except Exception:  # noqa: BLE001 - worker must survive any provider error
            logger.exception("Unexpected AI worker error for report %s.", report_pk)
            self._finish(
                report_pk, token, failure_category=FailureCategory.UNEXPECTED
            )
        else:
            self._finish(report_pk, token, analysis=analysis)

        report.refresh_from_db()
        return report

    def _finish(self, report_pk, token, *, analysis=None, failure_category=None):
        with transaction.atomic():
            report = (
                Report.objects.select_for_update().filter(pk=report_pk).first()
            )
            if (
                report is None
                or report.lease_token != token
                or report.ai_status != AIStatus.PROCESSING
            ):
                logger.info(
                    "Skipping stale AI result for report %s (lease changed).",
                    report_pk,
                )
                return

            if analysis is not None:
                report.ai_status = AIStatus.AVAILABLE
                report.analysis = analysis
                report.failure_category = None
            else:
                report.ai_status = AIStatus.UNAVAILABLE
                report.analysis = None
                report.failure_category = (
                    failure_category or FailureCategory.UNEXPECTED
                )
            report.lease_token = None
            report.lease_expires_at = None
            report.save(
                update_fields=[
                    "ai_status",
                    "analysis",
                    "failure_category",
                    "lease_token",
                    "lease_expires_at",
                    "updated_at",
                ]
            )

