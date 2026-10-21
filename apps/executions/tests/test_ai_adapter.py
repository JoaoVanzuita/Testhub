import json
import socket
import tempfile
import urllib.error
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Role
from apps.executions import ai_adapter, history, services
from apps.executions.management.commands.process_ai_reports import Command
from apps.executions.models import AIStatus, FailureCategory, Report
from apps.projects.models import Project, ProjectMembership
from apps.testcases.models import TestCase as CaseModel, TestStep

User = get_user_model()

PASSWORD = "uma-senha-bem-segura-123"

PROVIDER = {
    "AI_PROVIDER_ACTIVE": True,
    "AI_PROVIDER_ENDPOINT": "https://provider.test/analyze",
    "AI_PROVIDER_MODEL": "gemini-test-model",
    "AI_PROVIDER_API_KEY": "fake-key-not-a-secret",
    "AI_PROVIDER_TIMEOUT_SECONDS": 5,
}

CONTEXT = {
    "execution_metrics": {"selected_cases": 1, "success_rate": "25.00"},
    "cases": [
        {
            "reference": "case-1",
            "title": "Caso",
            "description": "",
            "preconditions": "",
            "steps": [
                {
                    "reference": "case-1/step-1",
                    "action": "Ação",
                    "expected_result": "Esperado",
                    "outcome": "FAILED",
                    "observation": "",
                }
            ],
        }
    ],
    "historical_context": [],
}


def make_user(email, role=Role.TESTER):
    return User.objects.create_user(
        email=email, name="Usuário de Teste", password=PASSWORD, role=role
    )


def make_project(name="Projeto Alpha", description="Descrição do projeto"):
    return Project.objects.create(name=name, description=description)


def assign(project, user):
    return ProjectMembership.objects.create(project=project, user=user)


def make_case(project, title="Caso de Teste"):
    return CaseModel.objects.create(
        project=project,
        title=title,
        description="Descrição do caso",
        preconditions="Nenhuma pré-condição",
    )


def make_step(case, position, action="Ação", expected_result="Esperado"):
    return TestStep.objects.create(
        test_case=case,
        position=position,
        action=action,
        expected_result=expected_result,
    )


def valid_analysis(reference="case-1/step-1"):
    return {
        "executive_summary": "Resumo executivo da execução.",
        "observed_patterns": [
            {
                "kind": "observed",
                "description": "Falha recorrente no passo 1.",
                "references": [reference],
            }
        ],
        "cause_hypotheses": [
            {
                "kind": "hypothesis",
                "description": "Possível instabilidade de rede.",
                "references": [reference],
            }
        ],
        "suggestions": ["Reproduzir o passo em outro ambiente."],
        "historical_comparison": None,
        "insufficient_evidence": False,
    }


def gemini_envelope(analysis, *, finish_reason="STOP"):
    return {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": json.dumps(analysis, ensure_ascii=False)}]
                },
                "finishReason": finish_reason,
            }
        ]
    }


def transport_returning(analysis, *, status=200, finish_reason="STOP"):
    body = json.dumps(
        gemini_envelope(analysis, finish_reason=finish_reason)
    ).encode("utf-8")

    def _transport(url, data, headers, timeout):
        return status, body

    return _transport


class ProviderConfigurationTests(TestCase):
    def test_not_configured_by_default(self):
        self.assertFalse(ai_adapter.is_provider_configured())

    @override_settings(**PROVIDER)
    def test_configured_with_active_provider(self):
        self.assertTrue(ai_adapter.is_provider_configured())

    @override_settings(
        AI_PROVIDER_ACTIVE=False,
        AI_PROVIDER_ENDPOINT="https://provider.test/analyze",
        AI_PROVIDER_API_KEY="fake-key-not-a-secret",
    )
    def test_endpoint_and_key_without_active_flag_are_not_enough(self):
        self.assertFalse(ai_adapter.is_provider_configured())

    @override_settings(
        AI_PROVIDER_ACTIVE=True,
        AI_PROVIDER_ENDPOINT="https://provider.test/analyze",
        AI_PROVIDER_API_KEY="",
        AI_PROVIDER_MODEL="gemini-test-model",
    )
    def test_active_flag_without_key_is_not_configured(self):
        self.assertFalse(ai_adapter.is_provider_configured())

    @override_settings(
        AI_PROVIDER_ACTIVE=True,
        AI_PROVIDER_ENDPOINT="https://provider.test/analyze",
        AI_PROVIDER_API_KEY="fake-key-not-a-secret",
        AI_PROVIDER_MODEL="",
    )
    def test_active_flag_without_model_is_not_configured(self):
        self.assertFalse(ai_adapter.is_provider_configured())


class AdapterValidationTests(TestCase):
    @override_settings(**PROVIDER)
    def test_valid_analysis_is_normalized(self):
        result = ai_adapter.analyze(
            CONTEXT, transport=transport_returning(valid_analysis())
        )

        self.assertEqual(result["executive_summary"], "Resumo executivo da execução.")
        self.assertEqual(result["observed_patterns"][0]["kind"], "observed")
        self.assertEqual(result["cause_hypotheses"][0]["kind"], "hypothesis")
        self.assertEqual(result["suggestions"], ["Reproduzir o passo em outro ambiente."])
        self.assertFalse(result["insufficient_evidence"])

    @override_settings(**PROVIDER)
    def test_insufficient_evidence_is_accepted(self):
        payload = valid_analysis()
        payload["insufficient_evidence"] = True
        payload["observed_patterns"] = []
        payload["cause_hypotheses"] = []
        payload["suggestions"] = []

        result = ai_adapter.analyze(CONTEXT, transport=transport_returning(payload))

        self.assertTrue(result["insufficient_evidence"])

    @override_settings(**PROVIDER)
    def test_timeout_is_classified(self):
        def transport(url, data, headers, timeout):
            raise socket.timeout("timed out")

        with self.assertRaises(ai_adapter.AdapterTimeoutError) as ctx:
            ai_adapter.analyze(CONTEXT, transport=transport)

        self.assertEqual(ctx.exception.category, FailureCategory.TIMEOUT)

    @override_settings(**PROVIDER)
    def test_connection_failure_is_classified(self):
        def transport(url, data, headers, timeout):
            raise urllib.error.URLError("connection refused")

        with self.assertRaises(ai_adapter.AdapterConnectionError) as ctx:
            ai_adapter.analyze(CONTEXT, transport=transport)

        self.assertEqual(ctx.exception.category, FailureCategory.CONNECTION)

    @override_settings(**PROVIDER)
    def test_non_2xx_response_is_classified(self):
        with self.assertRaises(ai_adapter.AdapterHttpError) as ctx:
            ai_adapter.analyze(
                CONTEXT, transport=transport_returning({}, status=500)
            )

        self.assertEqual(ctx.exception.category, FailureCategory.HTTP_ERROR)
        self.assertEqual(ctx.exception.status_code, 500)

    @override_settings(**PROVIDER)
    def test_invalid_json_is_rejected(self):
        def transport(url, data, headers, timeout):
            return 200, b"isto-nao-e-json"

        with self.assertRaises(ai_adapter.AdapterResponseError):
            ai_adapter.analyze(CONTEXT, transport=transport)

    @override_settings(**PROVIDER)
    def test_missing_required_field_is_rejected(self):
        payload = valid_analysis()
        del payload["executive_summary"]

        with self.assertRaises(ai_adapter.AdapterResponseError):
            ai_adapter.analyze(CONTEXT, transport=transport_returning(payload))

    @override_settings(**PROVIDER)
    def test_hypothesis_must_be_labelled(self):
        payload = valid_analysis()
        payload["cause_hypotheses"][0]["kind"] = "fact"

        with self.assertRaises(ai_adapter.AdapterResponseError):
            ai_adapter.analyze(CONTEXT, transport=transport_returning(payload))

    @override_settings(**PROVIDER)
    def test_unexpected_references_are_rejected(self):
        payload = valid_analysis(reference="case-9/step-9")

        with self.assertRaises(ai_adapter.AdapterReferenceError) as ctx:
            ai_adapter.analyze(CONTEXT, transport=transport_returning(payload))

        self.assertEqual(ctx.exception.category, FailureCategory.INVALID_REFERENCE)

    @override_settings(**PROVIDER)
    def test_project_and_history_references_are_accepted(self):
        context = {
            **CONTEXT,
            "project_context": {
                "reference": "project",
                "description": "Descrição do projeto.",
            },
            "historical_context": [
                {
                    "reference": "history-1",
                    "metrics": {},
                    "cases": [
                        {
                            "reference": "history-1/case-1",
                            "steps": [
                                {"reference": "history-1/case-1/step-1"}
                            ],
                        }
                    ],
                }
            ],
        }
        payload = valid_analysis(reference="history-1/case-1/step-1")
        payload["cause_hypotheses"][0]["references"] = ["project", "history-1"]

        result = ai_adapter.analyze(
            context, transport=transport_returning(payload)
        )

        self.assertEqual(
            result["cause_hypotheses"][0]["references"], ["project", "history-1"]
        )

    @override_settings(**PROVIDER)
    def test_history_reference_absent_from_the_request_is_rejected(self):
        payload = valid_analysis(reference="history-1/case-1/step-1")

        with self.assertRaises(ai_adapter.AdapterReferenceError):
            ai_adapter.analyze(CONTEXT, transport=transport_returning(payload))

    @override_settings(
        AI_PROVIDER_ACTIVE=True,
        AI_PROVIDER_ENDPOINT="http://provider.test/analyze",
        AI_PROVIDER_API_KEY="fake-key",
        AI_PROVIDER_MODEL="gemini-test-model",
    )
    def test_plain_http_endpoint_is_rejected(self):
        with self.assertRaises(ai_adapter.AdapterConfigError):
            ai_adapter.analyze(
                CONTEXT, transport=transport_returning(valid_analysis())
            )

    @override_settings(
        AI_PROVIDER_ACTIVE=False,
        AI_PROVIDER_ENDPOINT="https://provider.test/analyze",
        AI_PROVIDER_API_KEY="fake-key",
    )
    def test_inactive_provider_makes_no_network_call(self):
        calls = []

        def transport(url, data, headers, timeout):
            calls.append((url, data))
            return 200, b"{}"

        with self.assertRaises(ai_adapter.AdapterConfigError):
            ai_adapter.analyze(CONTEXT, transport=transport)

        self.assertEqual(calls, [])

    def test_unconfigured_provider_makes_no_network_call(self):
        calls = []

        def transport(url, data, headers, timeout):
            calls.append((url, data))
            return 200, b"{}"

        with self.assertRaises(ai_adapter.AdapterConfigError):
            ai_adapter.analyze(CONTEXT, transport=transport)

        self.assertEqual(calls, [])

    @override_settings(**PROVIDER)
    def test_oversized_request_is_rejected(self):
        huge = dict(CONTEXT)
        huge["cases"] = [{"reference": "case-1", "blob": "x" * (260 * 1024)}]

        with self.assertRaises(ai_adapter.AdapterRequestError):
            ai_adapter.analyze(
                huge, transport=transport_returning(valid_analysis())
            )


class ProviderEnvelopeTests(TestCase):
    @override_settings(**PROVIDER)
    def test_request_envelope_uses_generate_content(self):
        captured = {}

        def transport(url, data, headers, timeout):
            captured["url"] = url
            captured["data"] = data
            captured["headers"] = headers
            return 200, json.dumps(
                gemini_envelope(valid_analysis())
            ).encode("utf-8")

        ai_adapter.analyze(CONTEXT, transport=transport)

        self.assertEqual(
            captured["url"],
            "https://provider.test/analyze/models/gemini-test-model:generateContent",
        )
        self.assertEqual(
            captured["headers"]["x-goog-api-key"], "fake-key-not-a-secret"
        )
        self.assertNotIn("Authorization", captured["headers"])

        body = json.loads(captured["data"].decode("utf-8"))
        self.assertEqual(
            body["systemInstruction"]["parts"][0]["text"],
            ai_adapter._load_system_prompt(),
        )
        self.assertIn(
            "case-1/step-1", body["contents"][0]["parts"][0]["text"]
        )

        generation_config = body["generationConfig"]
        self.assertEqual(generation_config["responseMimeType"], "application/json")
        self.assertNotIn("responseSchema", generation_config)
        response_format = generation_config["responseFormat"]["text"]
        self.assertEqual(response_format["mimeType"], "APPLICATION_JSON")
        schema = response_format["schema"]
        self.assertIn("executive_summary", schema["properties"])
        self.assertEqual(
            schema["properties"]["observed_patterns"]["items"]["properties"][
                "kind"
            ]["enum"],
            ["observed"],
        )

    @override_settings(**PROVIDER)
    def test_rate_limit_is_classified_as_an_http_error(self):
        with self.assertRaises(ai_adapter.AdapterHttpError) as ctx:
            ai_adapter.analyze(
                CONTEXT,
                transport=transport_returning(valid_analysis(), status=429),
            )

        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(ctx.exception.category, FailureCategory.HTTP_ERROR)

    @override_settings(**PROVIDER)
    def test_blocked_prompt_without_candidate_is_an_invalid_response(self):
        def transport(url, data, headers, timeout):
            body = json.dumps(
                {"promptFeedback": {"blockReason": "SAFETY"}}
            ).encode("utf-8")
            return 200, body

        with self.assertRaises(ai_adapter.AdapterResponseError) as ctx:
            ai_adapter.analyze(CONTEXT, transport=transport)

        self.assertEqual(ctx.exception.category, FailureCategory.INVALID_RESPONSE)

    @override_settings(**PROVIDER)
    def test_safety_finish_reason_is_an_invalid_response(self):
        with self.assertRaises(ai_adapter.AdapterResponseError):
            ai_adapter.analyze(
                CONTEXT,
                transport=transport_returning(
                    valid_analysis(), finish_reason="SAFETY"
                ),
            )

    @override_settings(**PROVIDER)
    def test_empty_candidates_is_an_invalid_response(self):
        def transport(url, data, headers, timeout):
            return 200, json.dumps({"candidates": []}).encode("utf-8")

        with self.assertRaises(ai_adapter.AdapterResponseError):
            ai_adapter.analyze(CONTEXT, transport=transport)


class SystemPromptTests(TestCase):
    def test_prompt_file_is_loaded(self):
        prompt = ai_adapter._load_system_prompt()

        self.assertTrue(prompt)
        self.assertEqual(
            prompt,
            ai_adapter.SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip(),
        )
        self.assertIn("referências", prompt.lower())

    def test_missing_prompt_file_is_a_configuration_failure(self):
        missing = Path(tempfile.gettempdir()) / "nao-existe-qa-prompt.md"
        with patch.object(ai_adapter, "SYSTEM_PROMPT_PATH", missing):
            with self.assertRaises(ai_adapter.AdapterConfigError):
                ai_adapter._load_system_prompt()

    def test_empty_prompt_file_is_a_configuration_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty.md"
            empty.write_text("   \n", encoding="utf-8")
            with patch.object(ai_adapter, "SYSTEM_PROMPT_PATH", empty):
                with self.assertRaises(ai_adapter.AdapterConfigError):
                    ai_adapter._load_system_prompt()


class BuildContextTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("pessoa@example.com")
        cls.project = make_project(
            "Projeto Sigiloso",
            description="Plataforma de vendas com login, carrinho e checkout.",
        )
        assign(cls.project, cls.owner)
        cls.case = make_case(cls.project, "Caso de login")
        make_step(cls.case, 1, "Autenticar", "Usuário autenticado")

    def _completed_report(self):
        execution = services.start_execution(
            actor=self.owner, project=self.project, case_ids=[self.case.pk]
        )
        services.complete_execution(execution=execution, actor=self.owner)
        return Report.objects.get(execution=execution)

    def test_context_includes_description_without_project_name(self):
        report = self._completed_report()

        context = ai_adapter.build_request_context(report)
        serialized = json.dumps(context, ensure_ascii=False)

        self.assertEqual(context["project_context"]["reference"], "project")
        self.assertEqual(
            context["project_context"]["description"], self.project.description
        )
        self.assertNotIn(self.owner.email, serialized)
        self.assertNotIn(self.owner.name, serialized)
        self.assertNotIn(self.project.name, serialized)
        self.assertEqual(context["cases"][0]["reference"], "case-1")
        self.assertEqual(
            context["cases"][0]["steps"][0]["reference"], "case-1/step-1"
        )
        self.assertIn("execution_metrics", context)
        self.assertEqual(context["execution_metrics"]["selected_cases"], 1)
        self.assertEqual(context["historical_context"], [])


class HistoricalContextTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("historian@example.com")
        cls.project = make_project("Projeto Corrente")
        cls.other_project = make_project("Projeto Distante")
        assign(cls.project, cls.owner)
        assign(cls.other_project, cls.owner)
        cls.case = make_case(cls.project, "Caso Corrente")
        make_step(cls.case, 1, "Executar corrente", "Resultado corrente")
        cls.other_case = make_case(cls.other_project, "Caso Distante")
        make_step(cls.other_case, 1, "Executar distante", "Resultado distante")

    def _completed_report(self, project, case):
        execution = services.start_execution(
            actor=self.owner, project=project, case_ids=[case.pk]
        )
        services.complete_execution(execution=execution, actor=self.owner)
        return Report.objects.get(execution=execution)

    def test_absent_history_yields_an_empty_context(self):
        report = self._completed_report(self.project, self.case)

        context = ai_adapter.build_request_context(report)

        self.assertEqual(context["historical_context"], [])

    def test_prior_runs_are_included_as_detailed_snapshots(self):
        self._completed_report(self.project, self.case)
        current = self._completed_report(self.project, self.case)

        context = ai_adapter.build_request_context(current)

        entries = context["historical_context"]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["reference"], "history-1")
        self.assertEqual(entry["metrics"]["selected_cases"], 1)
        self.assertEqual(entry["metrics"]["success_rate"], "0.00")

        cases = entry["cases"]
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["reference"], "history-1/case-1")
        steps = cases[0]["steps"]
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["reference"], "history-1/case-1/step-1")
        self.assertEqual(steps[0]["outcome"], "NOT_EVALUATED")

    def test_history_omits_identity_and_project_name(self):
        self._completed_report(self.project, self.case)
        current = self._completed_report(self.project, self.case)

        context = ai_adapter.build_request_context(current)
        serialized = json.dumps(context, ensure_ascii=False)

        self.assertNotIn(self.owner.email, serialized)
        self.assertNotIn(self.owner.name, serialized)
        self.assertNotIn(self.project.name, serialized)
        history_payload = json.dumps(
            context["historical_context"], ensure_ascii=False
        )
        self.assertIn(self.case.title, history_payload)

    def test_history_detail_degrades_to_fit_the_request_budget(self):
        def _cycle(index, *, text):
            return {
                "reference": f"history-{index}",
                "cases": [
                    {
                        "reference": f"history-{index}/case-1",
                        "steps": [
                            {
                                "reference": f"history-{index}/case-1/step-1",
                                "outcome": "PASSED",
                                "action": text,
                                "expected_result": text,
                                "observation": "",
                            },
                            {
                                "reference": f"history-{index}/case-1/step-2",
                                "outcome": "FAILED",
                                "action": text,
                                "expected_result": text,
                                "observation": text,
                            },
                        ],
                    }
                ],
            }

        big = "x" * 2000
        entries = [_cycle(i, text=big) for i in range(1, 6)]
        full_size = history._serialized_size(entries)

        stripped = history.fit_historical_context(entries, full_size - 1)
        self.assertEqual(len(stripped), len(entries))
        passed_step = stripped[0]["cases"][0]["steps"][0]
        self.assertEqual(passed_step["outcome"], "PASSED")
        self.assertEqual(passed_step["action"], "")
        failed_step = stripped[0]["cases"][0]["steps"][1]
        self.assertEqual(failed_step["outcome"], "FAILED")
        self.assertEqual(failed_step["observation"], big)

        one_cycle = history._serialized_size(
            history.fit_historical_context(entries[:1], None)
        )
        pair = history.fit_historical_context(entries, one_cycle * 2)
        self.assertTrue(1 <= len(pair) < len(entries))
        self.assertEqual(
            [entry["reference"] for entry in pair],
            [f"history-{i}" for i in range(1, len(pair) + 1)],
        )

        self.assertEqual(history.fit_historical_context(entries, None), entries)

    def test_history_never_crosses_projects(self):
        self._completed_report(self.other_project, self.other_case)
        current = self._completed_report(self.project, self.case)

        context = ai_adapter.build_request_context(current)
        serialized = json.dumps(context, ensure_ascii=False)

        self.assertEqual(context["historical_context"], [])
        self.assertNotIn(self.other_project.name, serialized)
        self.assertNotIn(self.other_case.title, serialized)


class CompletionProviderStatusTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@example.com")
        cls.project = make_project()
        assign(cls.project, cls.owner)
        cls.case = make_case(cls.project)
        make_step(cls.case, 1)

    def _complete(self):
        execution = services.start_execution(
            actor=self.owner, project=self.project, case_ids=[self.case.pk]
        )
        services.complete_execution(execution=execution, actor=self.owner)
        return Report.objects.get(execution=execution)

    def test_unavailable_without_provider(self):
        report = self._complete()

        self.assertEqual(report.ai_status, AIStatus.UNAVAILABLE)
        self.assertEqual(report.ai_status_label, "Indisponível")

    @override_settings(**PROVIDER)
    def test_pending_with_approved_provider(self):
        report = self._complete()

        self.assertEqual(report.ai_status, AIStatus.PENDING)
        self.assertTrue(report.is_ai_processing)
        self.assertEqual(report.ai_status_label, "Em processamento")
        self.assertIsNone(report.analysis)


class WorkerCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@example.com")
        cls.project = make_project()
        assign(cls.project, cls.owner)
        cls.case = make_case(cls.project)
        make_step(cls.case, 1)

    def _pending_report(self):
        execution = services.start_execution(
            actor=self.owner, project=self.project, case_ids=[self.case.pk]
        )
        services.complete_execution(execution=execution, actor=self.owner)
        return Report.objects.get(execution=execution)

    @override_settings(**PROVIDER)
    def test_worker_stores_valid_analysis(self):
        report = self._pending_report()
        metrics_before = (
            report.selected_cases,
            report.approved_cases,
            str(report.success_rate),
        )

        with patch(
            "apps.executions.ai_adapter.analyze",
            return_value=valid_analysis(),
        ) as analyze:
            call_command("process_ai_reports", once=True)

        report.refresh_from_db()
        self.assertEqual(report.ai_status, AIStatus.AVAILABLE)
        self.assertTrue(report.has_analysis)
        self.assertEqual(report.ai_status_label, "Disponível")
        self.assertIsNone(report.lease_token)
        self.assertIsNone(report.lease_expires_at)
        analyze.assert_called_once()
        self.assertEqual(
            (
                report.selected_cases,
                report.approved_cases,
                str(report.success_rate),
            ),
            metrics_before,
        )

    @override_settings(**PROVIDER)
    def test_adapter_failure_keeps_metrics_and_marks_unavailable(self):
        report = self._pending_report()
        metrics_before = (
            report.selected_cases,
            report.approved_cases,
            str(report.success_rate),
        )

        with patch(
            "apps.executions.ai_adapter.analyze",
            side_effect=ai_adapter.AdapterTimeoutError("timeout"),
        ):
            call_command("process_ai_reports", once=True)

        report.refresh_from_db()
        self.assertEqual(report.ai_status, AIStatus.UNAVAILABLE)
        self.assertEqual(report.failure_category, FailureCategory.TIMEOUT)
        self.assertIsNone(report.analysis)
        self.assertEqual(
            (
                report.selected_cases,
                report.approved_cases,
                str(report.success_rate),
            ),
            metrics_before,
        )

    @override_settings(**PROVIDER)
    def test_repeated_processing_never_creates_a_second_report(self):
        report = self._pending_report()

        with patch(
            "apps.executions.ai_adapter.analyze",
            return_value=valid_analysis(),
        ):
            call_command("process_ai_reports", once=True)
            call_command("process_ai_reports", once=True)

        report.refresh_from_db()
        self.assertEqual(Report.objects.filter(execution=report.execution_id).count(), 1)
        self.assertEqual(report.ai_status, AIStatus.AVAILABLE)

    @override_settings(**PROVIDER)
    def test_expired_lease_is_reclaimed(self):
        report = self._pending_report()
        Report.objects.filter(pk=report.pk).update(
            ai_status=AIStatus.PROCESSING,
            lease_token="stale-token",
            lease_expires_at=timezone.now() - timedelta(seconds=5),
        )

        with patch(
            "apps.executions.ai_adapter.analyze",
            return_value=valid_analysis(),
        ):
            call_command("process_ai_reports", once=True)

        report.refresh_from_db()
        self.assertEqual(report.ai_status, AIStatus.AVAILABLE)
        self.assertEqual(report.attempts, 1)

    def test_unconfigured_provider_leaves_no_pending_work(self):
        report = self._pending_report()

        call_command("process_ai_reports", once=True)

        report.refresh_from_db()
        self.assertEqual(report.ai_status, AIStatus.UNAVAILABLE)

    @override_settings(**PROVIDER)
    def test_stale_worker_cannot_overwrite_a_reclaimed_report(self):
        report = self._pending_report()
        command = Command()
        command.stdout = StringIO()

        report_pk, token = command.claim(lease_seconds=60)
        Report.objects.filter(pk=report_pk).update(lease_token="other-token")

        command._finish(report_pk, token, analysis=valid_analysis())

        report.refresh_from_db()
        self.assertEqual(report.lease_token, "other-token")
        self.assertIsNone(report.analysis)
        self.assertEqual(report.ai_status, AIStatus.PROCESSING)
