import json
import logging
import socket
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings

from . import history
from .models import FailureCategory

logger = logging.getLogger(__name__)

MAX_REQUEST_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 256 * 1024

PROVIDER_ENVELOPE_RESERVE_BYTES = 16 * 1024

SYSTEM_PROMPT_PATH = (
    Path(__file__).resolve().parent / "prompts" / "qa_analysis_system.md"
)

OBSERVED_KIND = "observed"
HYPOTHESIS_KIND = "hypothesis"

ANALYSIS_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "executive_summary": {"type": "string"},
        "observed_patterns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": [OBSERVED_KIND]},
                    "description": {"type": "string"},
                    "references": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["kind", "description", "references"],
            },
        },
        "cause_hypotheses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": [HYPOTHESIS_KIND]},
                    "description": {"type": "string"},
                    "references": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["kind", "description", "references"],
            },
        },
        "suggestions": {"type": "array", "items": {"type": "string"}},
        "historical_comparison": {"type": "string"},
        "insufficient_evidence": {"type": "boolean"},
    },
    "required": [
        "executive_summary",
        "observed_patterns",
        "cause_hypotheses",
        "suggestions",
        "insufficient_evidence",
    ],
}


class AdapterError(Exception):
    category = FailureCategory.UNEXPECTED


class AdapterConfigError(AdapterError):
    category = FailureCategory.CONFIGURATION


class AdapterRequestError(AdapterError):
    category = FailureCategory.INVALID_REQUEST


class AdapterTimeoutError(AdapterError):
    category = FailureCategory.TIMEOUT


class AdapterConnectionError(AdapterError):
    category = FailureCategory.CONNECTION


class AdapterHttpError(AdapterError):
    category = FailureCategory.HTTP_ERROR

    def __init__(self, message, *, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class AdapterResponseError(AdapterError):
    category = FailureCategory.INVALID_RESPONSE


class AdapterReferenceError(AdapterResponseError):
    category = FailureCategory.INVALID_REFERENCE


def is_provider_configured() -> bool:
    active = bool(getattr(settings, "AI_PROVIDER_ACTIVE", False))
    endpoint = (getattr(settings, "AI_PROVIDER_ENDPOINT", "") or "").strip()
    api_key = (getattr(settings, "AI_PROVIDER_API_KEY", "") or "").strip()
    model = (getattr(settings, "AI_PROVIDER_MODEL", "") or "").strip()
    return bool(active and endpoint and api_key and model)


def _load_system_prompt() -> str:
    try:
        text = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise AdapterConfigError(
            "O arquivo de prompt de QA não foi encontrado ou não pôde ser lido."
        ) from exc
    text = text.strip()
    if not text:
        raise AdapterConfigError("O arquivo de prompt de QA está vazio.")
    return text


def build_request_context(report, *, historical_context=None) -> dict:
    execution = report.execution
    context = {
        "project_context": {
            "reference": "project",
            "description": execution.project.description or "",
        },
        "execution_metrics": {
            "selected_cases": report.selected_cases,
            "approved_cases": report.approved_cases,
            "reproved_cases": report.reproved_cases,
            "pending_cases": report.pending_cases,
            "not_started_cases": report.not_started_cases,
            "passed_steps": report.passed_steps,
            "failed_steps": report.failed_steps,
            "blocked_steps": report.blocked_steps,
            "unevaluated_steps": report.unevaluated_steps,
            "success_rate": str(report.success_rate),
        },
        "cases": history.snapshot_cases(execution),
        "historical_context": _resolve_historical_context(
            report, historical_context
        ),
    }
    return _fit_context_to_budget(context)


def _resolve_historical_context(report, historical_context):
    if historical_context is not None:
        return list(historical_context)
    return history.build_historical_context(report)


def _serialized_size(payload) -> int:
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _fit_context_to_budget(context) -> dict:
    budget = MAX_REQUEST_BYTES - PROVIDER_ENVELOPE_RESERVE_BYTES
    if _serialized_size(context) <= budget:
        return context
    without_history = dict(context, historical_context=[])
    remaining = budget - _serialized_size(without_history)
    context["historical_context"] = history.fit_historical_context(
        context.get("historical_context") or [], remaining
    )
    return context


def _collect_references(context) -> set:
    references = set()

    project_reference = (context.get("project_context") or {}).get("reference")
    if project_reference:
        references.add(project_reference)

    for case in context.get("cases", []):
        reference = case.get("reference")
        if reference:
            references.add(reference)
        for step in case.get("steps", []):
            step_reference = step.get("reference")
            if step_reference:
                references.add(step_reference)

    for cycle in context.get("historical_context", []):
        cycle_reference = cycle.get("reference")
        if cycle_reference:
            references.add(cycle_reference)
        for case in cycle.get("cases", []):
            case_reference = case.get("reference")
            if case_reference:
                references.add(case_reference)
            for step in case.get("steps", []):
                step_reference = step.get("reference")
                if step_reference:
                    references.add(step_reference)

    return references


def _require_str(data, key) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise AdapterResponseError(
            f"A análise do provedor não trouxe o campo textual obrigatório '{key}'."
        )
    return value


def _validate_references(entry, allowed) -> list:
    references = entry.get("references", [])
    if not isinstance(references, list) or not all(
        isinstance(reference, str) for reference in references
    ):
        raise AdapterResponseError("Referências inválidas na análise do provedor.")
    unknown = [reference for reference in references if reference not in allowed]
    if unknown:
        raise AdapterReferenceError(
            "A análise citou referências que não foram enviadas ao provedor."
        )
    return list(references)


def validate_analysis(data, *, context) -> dict:
    if not isinstance(data, dict):
        raise AdapterResponseError("A resposta do provedor não é um objeto JSON.")

    allowed = _collect_references(context)

    executive_summary = _require_str(data, "executive_summary")

    insufficient_evidence = data.get("insufficient_evidence")
    if not isinstance(insufficient_evidence, bool):
        raise AdapterResponseError(
            "O campo 'insufficient_evidence' é obrigatório e booleano."
        )

    raw_patterns = data.get("observed_patterns", [])
    if not isinstance(raw_patterns, list):
        raise AdapterResponseError("'observed_patterns' deve ser uma lista.")
    observed_patterns = []
    for entry in raw_patterns:
        if not isinstance(entry, dict) or entry.get("kind") != OBSERVED_KIND:
            raise AdapterResponseError(
                "Padrões observados devem ser objetos com kind='observed'."
            )
        observed_patterns.append(
            {
                "kind": OBSERVED_KIND,
                "description": _require_str(entry, "description"),
                "references": _validate_references(entry, allowed),
            }
        )

    raw_hypotheses = data.get("cause_hypotheses", [])
    if not isinstance(raw_hypotheses, list):
        raise AdapterResponseError("'cause_hypotheses' deve ser uma lista.")
    cause_hypotheses = []
    for entry in raw_hypotheses:
        if not isinstance(entry, dict) or entry.get("kind") != HYPOTHESIS_KIND:
            raise AdapterResponseError(
                "Hipóteses de causa devem ser objetos com kind='hypothesis'."
            )
        cause_hypotheses.append(
            {
                "kind": HYPOTHESIS_KIND,
                "description": _require_str(entry, "description"),
                "references": _validate_references(entry, allowed),
            }
        )

    raw_suggestions = data.get("suggestions", [])
    if not isinstance(raw_suggestions, list) or not all(
        isinstance(suggestion, str) for suggestion in raw_suggestions
    ):
        raise AdapterResponseError("'suggestions' deve ser uma lista de textos.")

    historical_comparison = data.get("historical_comparison")
    if historical_comparison is not None and not isinstance(
        historical_comparison, (str, dict)
    ):
        raise AdapterResponseError("'historical_comparison' inválido.")

    return {
        "executive_summary": executive_summary,
        "observed_patterns": observed_patterns,
        "cause_hypotheses": cause_hypotheses,
        "suggestions": list(raw_suggestions),
        "historical_comparison": historical_comparison,
        "insufficient_evidence": insufficient_evidence,
    }


def _build_provider_request(context):
    endpoint = (
        (getattr(settings, "AI_PROVIDER_ENDPOINT", "") or "").strip().rstrip("/")
    )
    model = (getattr(settings, "AI_PROVIDER_MODEL", "") or "").strip()
    api_key = (getattr(settings, "AI_PROVIDER_API_KEY", "") or "").strip()

    url = f"{endpoint}/models/{model}:generateContent"
    body = {
        "systemInstruction": {"parts": [{"text": _load_system_prompt()}]},
        "contents": [
            {
                "role": "user",
                "parts": [{"text": json.dumps(context, ensure_ascii=False)}],
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
            "responseFormat": {
                "text": {
                    "mimeType": "APPLICATION_JSON",
                    "schema": ANALYSIS_RESPONSE_SCHEMA,
                }
            },
        },
    }
    try:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AdapterRequestError(
            "Não foi possível montar a solicitação ao provedor de IA."
        ) from exc
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    }
    return url, payload, headers


def _extract_analysis_payload(body) -> dict:
    try:
        envelope = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise AdapterResponseError(
            "A resposta do provedor de IA não é um JSON válido."
        ) from exc
    if not isinstance(envelope, dict):
        raise AdapterResponseError(
            "A resposta do provedor de IA não é um objeto JSON."
        )

    prompt_feedback = envelope.get("promptFeedback")
    if isinstance(prompt_feedback, dict) and prompt_feedback.get("blockReason"):
        raise AdapterResponseError(
            "O provedor de IA bloqueou a solicitação de análise."
        )

    candidates = envelope.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise AdapterResponseError(
            "O provedor de IA não retornou nenhuma análise."
        )

    candidate = candidates[0]
    if not isinstance(candidate, dict):
        raise AdapterResponseError("A resposta do provedor de IA é inválida.")

    finish_reason = candidate.get("finishReason")
    if finish_reason and finish_reason not in ("STOP", "FINISH_REASON_UNSPECIFIED"):
        raise AdapterResponseError(
            "A análise do provedor de IA foi interrompida antes de concluir."
        )

    content = candidate.get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list):
        raise AdapterResponseError(
            "O provedor de IA não retornou o texto da análise."
        )

    text = "".join(
        part["text"]
        for part in parts
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    )
    if not text.strip():
        raise AdapterResponseError("O provedor de IA retornou uma análise vazia.")

    try:
        return json.loads(text)
    except ValueError as exc:
        raise AdapterResponseError(
            "O texto da análise do provedor de IA não é um JSON válido."
        ) from exc


def _default_transport(url, payload, headers, timeout):
    request = urllib.request.Request(
        url, data=payload, headers=headers, method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read(MAX_RESPONSE_BYTES + 1)


def analyze(context, *, transport=None):
    active = bool(getattr(settings, "AI_PROVIDER_ACTIVE", False))
    endpoint = (getattr(settings, "AI_PROVIDER_ENDPOINT", "") or "").strip()
    api_key = (getattr(settings, "AI_PROVIDER_API_KEY", "") or "").strip()
    model = (getattr(settings, "AI_PROVIDER_MODEL", "") or "").strip()
    if not active or not endpoint or not api_key or not model:
        raise AdapterConfigError(
            "Nenhum provedor de IA aprovado e ativado. Defina AI_PROVIDER_ACTIVE, "
            "AI_PROVIDER_ENDPOINT, AI_PROVIDER_API_KEY e AI_PROVIDER_MODEL "
            "conforme docs/ai-provider-decision.md."
        )
    if urlparse(endpoint).scheme != "https":
        raise AdapterConfigError("O endpoint do provedor de IA deve usar HTTPS.")

    timeout = float(getattr(settings, "AI_PROVIDER_TIMEOUT_SECONDS", 15) or 15)

    url, payload, headers = _build_provider_request(context)
    if len(payload) > MAX_REQUEST_BYTES:
        raise AdapterRequestError(
            "A solicitação de análise excede o tamanho máximo permitido."
        )

    dispatch = transport or _default_transport

    try:
        status, body = dispatch(url, payload, headers, timeout)
    except AdapterError:
        raise
    except (socket.timeout, TimeoutError) as exc:
        raise AdapterTimeoutError(
            "Tempo esgotado ao consultar o provedor de IA."
        ) from exc
    except urllib.error.HTTPError as exc:
        raise AdapterHttpError(
            "O provedor de IA retornou um status HTTP inesperado.",
            status_code=exc.code,
        ) from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (socket.timeout, TimeoutError)):
            raise AdapterTimeoutError(
                "Tempo esgotado ao consultar o provedor de IA."
            ) from exc
        raise AdapterConnectionError(
            "Não foi possível conectar ao provedor de IA."
        ) from exc
    except OSError as exc:
        raise AdapterConnectionError(
            "Não foi possível conectar ao provedor de IA."
        ) from exc

    if not (200 <= int(status) < 300):
        raise AdapterHttpError(
            "O provedor de IA retornou um status HTTP inesperado.",
            status_code=status,
        )

    if len(body) > MAX_RESPONSE_BYTES:
        raise AdapterResponseError(
            "A resposta do provedor de IA excede o tamanho máximo permitido."
        )

    data = _extract_analysis_payload(body)
    return validate_analysis(data, context=context)
