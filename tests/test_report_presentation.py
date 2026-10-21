import re

from django.test import Client, TestCase
from django.urls import reverse

from apps.executions import services
from apps.executions.models import AIStatus, Report, StepOutcome

from .support import (
    assign,
    complete_execution,
    make_case,
    make_project,
    make_step,
    make_user,
    start_execution,
)

HYPOTHESIS_TEXT = "Suspeita de instabilidade de rede no ambiente de teste."
OBSERVED_TEXT = "Falha recorrente no passo de autenticação."
SUMMARY_TEXT = "Resumo executivo do provedor simulado."


def _section(html, marker):
    match = re.search(
        r"<section\b[^>]*\bdata-" + marker + r"\b.*?</section>",
        html,
        re.DOTALL,
    )
    return match.group(0) if match else ""


class ReportPresentationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@example.com")
        cls.project = make_project()
        assign(cls.project, cls.owner)
        cls.cases = {}
        for key, title in [
            ("approved", "Caso Aprovado"),
            ("reproved", "Caso Reprovado"),
            ("blocked", "Caso Bloqueado"),
            ("not_started", "Caso Não Iniciado"),
        ]:
            case = make_case(cls.project, title)
            make_step(case, 1, "Passo único", "Resultado esperado")
            cls.cases[key] = case

    def setUp(self):
        self.execution = start_execution(
            self.owner, self.project, list(self.cases.values())
        )
        self._set("approved", StepOutcome.PASSED)
        self._set("reproved", StepOutcome.FAILED, observation="Erro ao salvar.")
        self._set("blocked", StepOutcome.BLOCKED)
        self.report = complete_execution(self.execution, self.owner)

        self.client = Client()
        self.client.force_login(self.owner)

    def _set(self, key, outcome, observation=""):
        execution_case = self.execution.execution_cases.get(
            source_test_case=self.cases[key]
        )
        services.update_step_result(
            execution=self.execution,
            step=execution_case.steps.get(),
            actor=self.owner,
            outcome=outcome,
            observation=observation,
        )

    def _report_html(self):
        response = self.client.get(
            reverse("executions:report_detail", args=[self.execution.pk])
        )
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def _make_available(self, *, insufficient_evidence=False):
        analysis = {
            "executive_summary": SUMMARY_TEXT,
            "observed_patterns": [
                {
                    "kind": "observed",
                    "description": OBSERVED_TEXT,
                    "references": ["case-1/step-1"],
                }
            ],
            "cause_hypotheses": [
                {
                    "kind": "hypothesis",
                    "description": HYPOTHESIS_TEXT,
                    "references": ["case-1/step-1"],
                }
            ],
            "suggestions": ["Reproduzir em outro ambiente."],
            "historical_comparison": None,
            "insufficient_evidence": insufficient_evidence,
        }
        Report.objects.filter(pk=self.report.pk).update(
            ai_status=AIStatus.AVAILABLE,
            analysis=analysis,
            failure_category=None,
        )

    def test_recorded_metrics_are_available_and_show_the_two_decimal_rate(self):
        html = self._report_html()

        metrics = _section(html, "recorded-metrics")
        self.assertNotEqual(metrics, "")
        self.assertIn("Métricas registradas", metrics)
        self.assertIn("25.00%", metrics)
        self.assertIn("Casos selecionados", metrics)
        self.assertIn("Com observação de falha/bloqueio", metrics)

    def test_recorded_metrics_and_ai_analysis_are_separate_sections(self):
        self._make_available()

        html = self._report_html()
        metrics = _section(html, "recorded-metrics")
        analysis = _section(html, "ai-analysis")

        self.assertNotEqual(metrics, "")
        self.assertNotEqual(analysis, "")
        self.assertNotIn(HYPOTHESIS_TEXT, metrics)
        self.assertNotIn(SUMMARY_TEXT, metrics)
        self.assertNotIn("Métricas registradas", analysis)

    def test_unavailable_analysis_explains_that_metrics_remain_valid(self):
        html = self._report_html()

        analysis = _section(html, "ai-analysis")
        self.assertIn("Indisponível", analysis)
        self.assertIn("data-ai-unavailable", analysis)

    def test_processing_state_renders_as_em_processamento(self):
        Report.objects.filter(pk=self.report.pk).update(
            ai_status=AIStatus.PROCESSING, analysis=None
        )

        html = self._report_html()

        self.assertIn("Em processamento", html)

    def test_available_analysis_labels_hypotheses_as_interpretation(self):
        self._make_available()

        html = self._report_html()
        analysis = _section(html, "ai-analysis")

        self.assertIn("Disponível", analysis)
        self.assertIn(SUMMARY_TEXT, analysis)
        self.assertIn("data-observed-patterns", analysis)
        self.assertIn(OBSERVED_TEXT, analysis)
        self.assertIn("data-cause-hypotheses", analysis)
        self.assertIn("Hipótese / interpretação", analysis)
        self.assertIn(HYPOTHESIS_TEXT, analysis)

    def test_observed_patterns_are_not_labelled_as_hypotheses(self):
        self._make_available()

        analysis = _section(self._report_html(), "ai-analysis")
        observed_block = analysis.split("data-observed-patterns", 1)[1]
        observed_block = observed_block.split("</ul>", 1)[0]

        self.assertIn(OBSERVED_TEXT, observed_block)
        self.assertNotIn("Hipótese / interpretação", observed_block)

    def test_insufficient_evidence_is_marked_inconclusive(self):
        self._make_available(insufficient_evidence=True)

        analysis = _section(self._report_html(), "ai-analysis")

        self.assertIn("data-insufficient-evidence", analysis)
        self.assertIn("Análise inconclusiva", analysis)

    def test_ai_failure_never_changes_the_recorded_metrics(self):
        metrics_before = (
            self.report.selected_cases,
            self.report.approved_cases,
            str(self.report.success_rate),
        )
        Report.objects.filter(pk=self.report.pk).update(
            ai_status=AIStatus.UNAVAILABLE,
            analysis=None,
            failure_category="TIMEOUT",
        )

        html = self._report_html()
        self.report.refresh_from_db()

        self.assertIn("25.00%", _section(html, "recorded-metrics"))
        self.assertEqual(
            (
                self.report.selected_cases,
                self.report.approved_cases,
                str(self.report.success_rate),
            ),
            metrics_before,
        )
