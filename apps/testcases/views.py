from django.contrib import messages
from django.shortcuts import redirect, render

from apps.accounts.permissions import active_user_required
from apps.projects.permissions import get_authorized_project_or_404

from . import services
from .forms import TestCaseEditForm, TestCaseForm, TestStepFormSet, extract_steps
from .models import TestCase
from .permissions import (
    can_edit_cases,
    get_readable_case_or_404,
    require_case_editor,
)

STEPS_PREFIX = "steps"


@active_user_required
def case_list(request, project_pk):
    project = get_authorized_project_or_404(request.user, project_pk)
    cases = project.test_cases.prefetch_related("steps")
    return render(
        request,
        "testcases/case_list.html",
        {
            "project": project,
            "cases": cases,
            "can_edit": can_edit_cases(request.user, project),
        },
    )


@active_user_required
def case_create(request, project_pk):
    project = get_authorized_project_or_404(request.user, project_pk)
    require_case_editor(request.user, project)

    if request.method == "POST":
        form = TestCaseForm(request.POST)
        formset = TestStepFormSet(
            request.POST, instance=TestCase(project=project), prefix=STEPS_PREFIX
        )
        if form.is_valid() and formset.is_valid():
            try:
                case = services.create_case(
                    actor=request.user,
                    project=project,
                    title=form.cleaned_data["title"],
                    description=form.cleaned_data["description"],
                    preconditions=form.cleaned_data["preconditions"],
                    steps=extract_steps(formset),
                )
            except services.StepValidationError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, "Caso de teste criado com sucesso.")
                return redirect("testcases:case_detail", pk=case.pk)
    else:
        form = TestCaseForm()
        formset = TestStepFormSet(
            instance=TestCase(project=project), prefix=STEPS_PREFIX
        )
    return render(
        request,
        "testcases/case_form.html",
        {"form": form, "formset": formset, "project": project, "mode": "create"},
    )


@active_user_required
def case_detail(request, pk):
    case = get_readable_case_or_404(request.user, pk)
    steps = case.steps.all()
    return render(
        request,
        "testcases/case_detail.html",
        {
            "case": case,
            "project": case.project,
            "steps": steps,
            "can_edit": can_edit_cases(request.user, case.project),
        },
    )


@active_user_required
def case_edit(request, pk):
    case = get_readable_case_or_404(request.user, pk)
    require_case_editor(request.user, case.project)

    if request.method == "POST":
        form = TestCaseEditForm(request.POST, instance=case)
        formset = TestStepFormSet(request.POST, instance=case, prefix=STEPS_PREFIX)
        if form.is_valid() and formset.is_valid():
            try:
                services.update_case(
                    case,
                    actor=request.user,
                    title=form.cleaned_data["title"],
                    description=form.cleaned_data["description"],
                    preconditions=form.cleaned_data["preconditions"],
                    is_active=form.cleaned_data["is_active"],
                    steps=extract_steps(formset),
                )
            except services.StepValidationError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, "Caso de teste atualizado com sucesso.")
                return redirect("testcases:case_detail", pk=case.pk)
    else:
        form = TestCaseEditForm(instance=case)
        formset = TestStepFormSet(instance=case, prefix=STEPS_PREFIX)
    return render(
        request,
        "testcases/case_form.html",
        {
            "form": form,
            "formset": formset,
            "project": case.project,
            "case": case,
            "mode": "edit",
        },
    )


@active_user_required
def case_inactivate(request, pk):
    case = get_readable_case_or_404(request.user, pk)
    if request.method != "POST":
        return redirect("testcases:case_detail", pk=case.pk)
    services.inactivate_case(case, actor=request.user)
    messages.success(request, "Caso de teste inativado com sucesso.")
    return redirect("testcases:case_list", project_pk=case.project_id)


@active_user_required
def case_delete(request, pk):
    case = get_readable_case_or_404(request.user, pk)
    if request.method != "POST":
        return redirect("testcases:case_detail", pk=case.pk)
    try:
        services.delete_case(case, actor=request.user)
    except services.ProtectedCaseError as exc:
        messages.warning(request, str(exc))
        return redirect("testcases:case_detail", pk=case.pk)
    messages.success(request, "Caso de teste excluído com sucesso.")
    return redirect("testcases:case_list", project_pk=case.project_id)
