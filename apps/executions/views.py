from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render

from apps.accounts.permissions import active_user_required
from apps.projects.permissions import get_authorized_project_or_404

from . import history, services
from .forms import (
    ExecutionStartForm,
    HandoffForm,
    HistoryComparisonForm,
    StepResultForm,
)
from .models import ExecutionStep, Report
from .permissions import (
    can_assume,
    can_operate,
    get_readable_execution_or_404,
    require_execution_creator,
)


def _wants_json(request) -> bool:
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


def _redirect_to_detail(execution):
    return redirect("executions:execution_detail", pk=execution.pk)


@active_user_required
def execution_create(request, project_pk):
    project = get_authorized_project_or_404(request.user, project_pk)
    require_execution_creator(request.user, project)

    if request.method == "POST":
        form = ExecutionStartForm(request.POST, project=project)
        if form.is_valid():
            try:
                execution = services.start_execution(
                    actor=request.user,
                    project=project,
                    case_ids=[case.pk for case in form.cleaned_data["cases"]],
                )
            except services.ExecutionError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, "Execução iniciada com sucesso.")
                return _redirect_to_detail(execution)
    else:
        form = ExecutionStartForm(project=project)

    return render(
        request,
        "executions/execution_form.html",
        {"form": form, "project": project},
    )


@active_user_required
def execution_detail(request, pk):
    execution = get_readable_execution_or_404(request.user, pk)
    execution_cases = execution.execution_cases.prefetch_related("steps")
    context = {
        "execution": execution,
        "execution_cases": execution_cases,
        "current_owner": execution.current_owner,
        "can_operate": can_operate(execution, request.user),
        "can_assume": can_assume(execution, request.user),
        "handoff_form": HandoffForm(
            project=execution.project, exclude_user=request.user
        ),
        "report": Report.objects.filter(execution=execution).first(),
    }
    template = (
        "executions/execution_readonly.html"
        if execution.is_completed
        else "executions/execution_detail.html"
    )
    return render(request, template, context)


@active_user_required
def execution_step_update(request, pk, step_pk):
    execution = get_readable_execution_or_404(request.user, pk)
    if request.method != "POST":
        return _redirect_to_detail(execution)

    step = (
        ExecutionStep.objects.select_related("execution_case")
        .filter(pk=step_pk, execution_case__execution=execution)
        .first()
    )
    if step is None:
        if _wants_json(request):
            return JsonResponse(
                {"persisted": False, "error": "Passo não encontrado."},
                status=404,
            )
        messages.error(request, "Passo não encontrado.")
        return _redirect_to_detail(execution)

    form = StepResultForm(request.POST)
    if not form.is_valid():
        error = " ".join(
            message
            for errors in form.errors.values()
            for message in errors
        )
        if _wants_json(request):
            return JsonResponse({"persisted": False, "error": error}, status=400)
        messages.error(request, error or "Não foi possível registrar o resultado.")
        return _redirect_to_detail(execution)

    try:
        updated = services.update_step_result(
            execution=execution,
            step=step,
            actor=request.user,
            outcome=form.cleaned_data["outcome"],
            observation=form.cleaned_data["observation"],
        )
    except services.ExecutionError as exc:
        if _wants_json(request):
            return JsonResponse({"persisted": False, "error": str(exc)}, status=400)
        messages.error(request, str(exc))
        return _redirect_to_detail(execution)

    if _wants_json(request):
        return JsonResponse(
            {
                "persisted": True,
                "step_id": updated.pk,
                "outcome": updated.outcome,
                "outcome_label": updated.get_outcome_display(),
                "case_status": updated.execution_case.status,
                "case_status_label": updated.execution_case.get_status_display(),
                "observation": updated.observation,
            }
        )
    messages.success(request, "Resultado do passo salvo.")
    return _redirect_to_detail(execution)


@active_user_required
def execution_pause(request, pk):
    execution = get_readable_execution_or_404(request.user, pk)
    if request.method != "POST":
        return _redirect_to_detail(execution)
    try:
        services.pause_execution(execution=execution, actor=request.user)
    except services.ExecutionError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            "Execução pausada. Os apontamentos permanecem salvos; retome quando quiser.",
        )
    return _redirect_to_detail(execution)


@active_user_required
def execution_resume(request, pk):
    execution = get_readable_execution_or_404(request.user, pk)
    if request.method != "POST":
        return _redirect_to_detail(execution)
    try:
        services.resume_execution(execution=execution, actor=request.user)
    except services.ExecutionError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Execução retomada.")
    return _redirect_to_detail(execution)


@active_user_required
def execution_handoff(request, pk):
    execution = get_readable_execution_or_404(request.user, pk)
    if request.method != "POST":
        return _redirect_to_detail(execution)

    form = HandoffForm(
        request.POST, project=execution.project, exclude_user=request.user
    )
    if not form.is_valid():
        messages.error(request, "Escolha um Testador válido para assumir.")
        return _redirect_to_detail(execution)

    try:
        services.handoff_execution(
            execution=execution,
            actor=request.user,
            successor=form.cleaned_data["successor"],
        )
    except services.ExecutionError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Execução transferida com sucesso.")
    return _redirect_to_detail(execution)


@active_user_required
def execution_release(request, pk):
    execution = get_readable_execution_or_404(request.user, pk)
    if request.method != "POST":
        return _redirect_to_detail(execution)
    try:
        services.release_assignment(execution=execution, actor=request.user)
    except services.ExecutionError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            "Execução liberada. Outro Testador autorizado pode assumi-la.",
        )
    return _redirect_to_detail(execution)


@active_user_required
def execution_assume(request, pk):
    execution = get_readable_execution_or_404(request.user, pk)
    if request.method != "POST":
        return _redirect_to_detail(execution)
    try:
        services.assume_execution(execution=execution, actor=request.user)
    except services.ExecutionError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Você assumiu a condução desta execução.")
    return _redirect_to_detail(execution)


@active_user_required
def execution_complete(request, pk):
    execution = get_readable_execution_or_404(request.user, pk)
    if request.method != "POST":
        return _redirect_to_detail(execution)
    try:
        services.complete_execution(execution=execution, actor=request.user)
    except services.ExecutionError as exc:
        messages.error(request, str(exc))
    else:
        report = Report.objects.filter(execution=execution).first()
        ai_label = report.ai_status_label if report else "Indisponível"
        messages.success(
            request,
            "Execução concluída. Os resultados ficaram registrados e imutáveis. "
            f"Análise de IA: {ai_label}.",
        )
    return _redirect_to_detail(execution)


@active_user_required
def execution_report(request, pk):
    execution = get_readable_execution_or_404(request.user, pk)
    report = Report.objects.filter(execution=execution).first()
    if report is None:
        raise Http404("Relatório ainda não disponível para esta execução.")
    return render(
        request,
        "executions/report_detail.html",
        {"execution": execution, "report": report},
    )


@active_user_required
def history_list(request, project_pk):
    project = get_authorized_project_or_404(request.user, project_pk)
    summaries = history.completed_execution_summaries(project)
    return render(
        request,
        "executions/history_list.html",
        {
            "project": project,
            "summaries": summaries,
            "completed_count": len(summaries),
            "has_baseline": len(summaries) == 1,
            "comparison_form": HistoryComparisonForm(
                summaries=summaries,
                initial={
                    "base": str(summaries[1].execution.pk),
                    "against": str(summaries[0].execution.pk),
                },
            )
            if len(summaries) >= 2
            else None,
        },
    )


@active_user_required
def history_compare(request, project_pk):
    project = get_authorized_project_or_404(request.user, project_pk)
    summaries = history.completed_execution_summaries(project)
    by_pk = {summary.execution.pk: summary for summary in summaries}

    if len(summaries) < 2:
        return render(
            request,
            "executions/history_compare.html",
            {
                "project": project,
                "baseline": summaries[0] if summaries else None,
                "completed_count": len(summaries),
                "comparison": None,
                "form": None,
            },
        )

    def resolve(value):
        try:
            key = int(value)
        except (TypeError, ValueError):
            raise Http404("Execução inválida para comparação.")
        summary = by_pk.get(key)
        if summary is None:
            raise Http404("Execução não encontrada neste projeto.")
        return summary

    base_value = request.GET.get("base")
    against_value = request.GET.get("against")

    error = None
    if base_value or against_value:
        base = resolve(base_value)
        against = resolve(against_value)
        if base.execution.pk == against.execution.pk:
            error = "Escolha duas execuções diferentes para comparar."
            comparison = None
        else:
            comparison = history.compare_executions(base, against)
    else:
        base, against = summaries[1], summaries[0]
        comparison = history.compare_executions(base, against)

    if comparison is not None:
        initial = {
            "base": str(comparison.base.execution.pk),
            "against": str(comparison.against.execution.pk),
        }
    else:
        initial = {
            "base": str(base.execution.pk),
            "against": str(against.execution.pk),
        }

    form = HistoryComparisonForm(summaries=summaries, initial=initial)
    return render(
        request,
        "executions/history_compare.html",
        {
            "project": project,
            "comparison": comparison,
            "form": form,
            "error": error,
            "completed_count": len(summaries),
            "baseline": None,
        },
    )
