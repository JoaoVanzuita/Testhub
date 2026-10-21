import re

from django.test import Client, TestCase
from django.urls import reverse

from apps.executions import history, services
from apps.executions.models import AIStatus, Report, StepOutcome
from apps.executions.templatetags.report_ui import (
    comparison_metric_rows,
    comparison_step_rows,
)

from .support import (
    assign,
    complete_execution,
    make_case,
    make_project,
    make_step,
    make_user,
    start_execution,
)

OBSERVED_TEXT = "Falha recorrente no passo de autenticação."


def _section(html, marker):
    match = re.search(
        r"<section\b[^>]*\bdata-" + marker + r"\b.*?</section>",
        html,
        re.DOTALL,
    )
    return match.group(0) if match else ""


class ReportStatCardTests(TestCase):
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

    def _make_available(self):
        analysis = {
            "executive_summary": "Resumo executivo do provedor simulado.",
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
                    "description": "Suspeita de instabilidade.",
                    "references": ["case-1/step-1"],
                }
            ],
            "suggestions": ["Reproduzir em outro ambiente."],
            "historical_comparison": None,
            "insufficient_evidence": False,
        }
        Report.objects.filter(pk=self.report.pk).update(
            ai_status=AIStatus.AVAILABLE, analysis=analysis, failure_category=None
        )

    def test_recorded_metrics_render_as_stat_cards(self):
        metrics = _section(self._report_html(), "recorded-metrics")

        self.assertIn("stat-grid", metrics)
        self.assertIn("stat-card", metrics)
        self.assertIn("data-success-rate", metrics)
        self.assertIn("25.00%", metrics)
        self.assertIn("Casos selecionados", metrics)
        self.assertIn("Com observação de falha/bloqueio", metrics)

    def test_contract_sections_do_not_nest_further_sections(self):
        self._make_available()

        html = self._report_html()

        self.assertEqual(_section(html, "recorded-metrics").count("<section"), 1)
        self.assertEqual(_section(html, "ai-analysis").count("<section"), 1)

    def test_ai_accent_is_reserved_for_the_ai_section(self):
        self._make_available()

        html = self._report_html()
        metrics = _section(html, "recorded-metrics")
        analysis = _section(html, "ai-analysis")

        self.assertIn("ai-insight", analysis)
        self.assertIn("badge--ai", analysis)
        self.assertIn("Interpretação de IA", analysis)
        self.assertNotIn("ai-insight", metrics)
        self.assertNotIn("badge--ai", metrics)

    def test_unavailable_analysis_has_no_interpretation_badge(self):
        analysis = _section(self._report_html(), "ai-analysis")

        self.assertIn("ai-insight", analysis)
        self.assertNotIn("Interpretação de IA", analysis)
        self.assertIn("Indisponível", analysis)

    def test_all_three_ai_states_still_render(self):
        self.assertIn("Indisponível", self._report_html())

        Report.objects.filter(pk=self.report.pk).update(
            ai_status=AIStatus.PROCESSING, analysis=None
        )
        self.assertIn("Em processamento", self._report_html())

        self._make_available()
        self.assertIn("Disponível", self._report_html())


class ComparisonRowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@example.com")
        cls.project = make_project()
        assign(cls.project, cls.owner)
        cls.case_a = make_case(cls.project, "Caso A")
        cls.case_b = make_case(cls.project, "Caso B")
        make_step(cls.case_a, 1)
        make_step(cls.case_b, 1)

    def _complete(self, outcomes):
        execution = start_execution(
            self.owner, self.project, list(outcomes.keys())
        )
        for case, outcome in outcomes.items():
            execution_case = execution.execution_cases.get(source_test_case=case)
            services.update_step_result(
                execution=execution,
                step=execution_case.steps.get(),
                actor=self.owner,
                outcome=outcome,
            )
        complete_execution(execution, self.owner)
        return execution

    def _comparison(self, older, newer):
        by_pk = {
            summary.execution.pk: summary
            for summary in history.completed_execution_summaries(self.project)
        }
        return history.compare_executions(by_pk[older.pk], by_pk[newer.pk])

    def test_metric_rows_flag_only_the_recorded_differences(self):
        older = self._complete({self.case_a: StepOutcome.PASSED})
        newer = self._complete(
            {self.case_a: StepOutcome.PASSED, self.case_b: StepOutcome.FAILED}
        )
        comparison = self._comparison(older, newer)

        rows = {row["label"]: row for row in comparison_metric_rows(comparison)}

        self.assertEqual(rows["Casos selecionados"]["base"], 1)
        self.assertEqual(rows["Casos selecionados"]["against"], 2)
        self.assertTrue(rows["Casos selecionados"]["differs"])
        self.assertEqual(rows["Taxa de sucesso"]["base"], "100.00%")
        self.assertEqual(rows["Taxa de sucesso"]["against"], "50.00%")
        self.assertTrue(rows["Taxa de sucesso"]["differs"])
        self.assertFalse(rows["Concluída em"]["differs"])

    def test_step_rows_cover_the_recorded_results(self):
        older = self._complete({self.case_a: StepOutcome.FAILED})
        newer = self._complete({self.case_a: StepOutcome.PASSED})
        comparison = self._comparison(older, newer)

        rows = {row["label"]: row for row in comparison_step_rows(comparison)}

        self.assertEqual(
            set(rows),
            {
                "Passou",
                "Falhou",
                "Bloqueado",
                "Não avaliados",
                "Com observação de falha/bloqueio",
            },
        )
        self.assertTrue(rows["Passou"]["differs"])
        self.assertTrue(rows["Falhou"]["differs"])
        self.assertFalse(rows["Bloqueado"]["differs"])

    def test_identical_runs_produce_no_difference_marks(self):
        older = self._complete({self.case_a: StepOutcome.PASSED})
        newer = self._complete({self.case_a: StepOutcome.PASSED})
        comparison = self._comparison(older, newer)

        for row in comparison_metric_rows(comparison):
            self.assertFalse(row["differs"], row["label"])
        for row in comparison_step_rows(comparison):
            self.assertFalse(row["differs"], row["label"])


class ComparisonPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@example.com")
        cls.project = make_project()
        assign(cls.project, cls.owner)
        cls.case_a = make_case(cls.project, "Caso A")
        cls.case_b = make_case(cls.project, "Caso B")
        make_step(cls.case_a, 1)
        make_step(cls.case_b, 1)

    def _complete(self, cases, outcome=StepOutcome.PASSED):
        execution = start_execution(self.owner, self.project, cases)
        for execution_case in execution.execution_cases.all():
            services.update_step_result(
                execution=execution,
                step=execution_case.steps.get(),
                actor=self.owner,
                outcome=outcome,
            )
        complete_execution(execution, self.owner)
        return execution

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.owner)

    def _page(self):
        response = self.client.get(
            reverse("executions:history_compare", args=[self.project.pk])
        )
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_differing_runs_are_marked_without_hiding_data(self):
        self._complete([self.case_a])
        self._complete([self.case_a, self.case_b])

        html = self._page()

        self.assertIn('data-differs="true"', html)
        self.assertIn("compare-row--diff", html)
        self.assertIn("Comparação #", html)
        self.assertIn("Resultados dos passos por execução", html)
        self.assertIn("Com observação de falha/bloqueio", html)
        self.assertIn("conjuntos de casos comparados são diferentes", html)

    def test_identical_runs_render_no_difference_marks(self):
        self._complete([self.case_a])
        self._complete([self.case_a])

        html = self._page()

        self.assertNotIn('data-differs="true"', html)
        self.assertIn("conjuntos de casos comparados são iguais", html)
