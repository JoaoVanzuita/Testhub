from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.permissions import active_user_required, administrator_required

from . import services
from .forms import MembershipForm, ProjectEditForm, ProjectForm
from .models import ProjectMembership
from .permissions import get_authorized_project_or_404, get_authorized_projects

User = get_user_model()


@active_user_required
def project_list(request):
    projects = get_authorized_projects(request.user)
    return render(request, "projects/project_list.html", {"projects": projects})


@administrator_required
def project_create(request):
    if request.method == "POST":
        form = ProjectForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Projeto criado com sucesso.")
            return redirect("projects:project_list")
    else:
        form = ProjectForm()
    return render(
        request, "projects/project_form.html", {"form": form, "mode": "create"}
    )


@administrator_required
def project_edit(request, pk):
    project = get_authorized_project_or_404(request.user, pk)
    if request.method == "POST":
        form = ProjectEditForm(request.POST, instance=project)
        if form.is_valid():
            form.save()
            messages.success(request, "Projeto atualizado com sucesso.")
            return redirect("projects:project_list")
    else:
        form = ProjectEditForm(instance=project)
    return render(
        request,
        "projects/project_form.html",
        {"form": form, "mode": "edit", "object": project},
    )


@administrator_required
def project_inactivate(request, pk):
    if request.method != "POST":
        return redirect("projects:project_list")
    project = get_authorized_project_or_404(request.user, pk)
    services.inactivate_project(project)
    messages.success(request, "Projeto inativado com sucesso.")
    return redirect("projects:project_list")


@administrator_required
def project_delete(request, pk):
    if request.method != "POST":
        return redirect("projects:project_list")
    project = get_authorized_project_or_404(request.user, pk)
    try:
        services.delete_project(project)
    except services.ProtectedProjectError as exc:
        messages.warning(request, str(exc))
    else:
        messages.success(request, "Projeto excluído com sucesso.")
    return redirect("projects:project_list")


@active_user_required
def project_detail(request, pk):
    project = get_authorized_project_or_404(request.user, pk)
    memberships = project.memberships.filter(revoked_at__isnull=True).select_related(
        "user"
    )
    executions = project.executions.select_related("created_by").order_by(
        "-started_at", "pk"
    )[:20]
    return render(
        request,
        "projects/project_detail.html",
        {
            "project": project,
            "memberships": memberships,
            "executions": executions,
        },
    )


@administrator_required
def memberships(request, pk):
    project = get_authorized_project_or_404(request.user, pk)
    active_memberships = project.memberships.filter(
        revoked_at__isnull=True
    ).select_related("user")
    form = MembershipForm(project=project)
    return render(
        request,
        "projects/memberships.html",
        {"project": project, "form": form, "memberships": active_memberships},
    )


@administrator_required
def membership_add(request, pk):
    if request.method != "POST":
        return redirect("projects:memberships", pk=pk)
    project = get_authorized_project_or_404(request.user, pk)
    form = MembershipForm(request.POST, project=project)
    if form.is_valid():
        services.assign_tester(project, form.cleaned_data["user"])
        messages.success(request, "Testador atribuído com sucesso.")
        return redirect("projects:memberships", pk=project.pk)
    active_memberships = project.memberships.filter(
        revoked_at__isnull=True
    ).select_related("user")
    return render(
        request,
        "projects/memberships.html",
        {"project": project, "form": form, "memberships": active_memberships},
    )


@administrator_required
def membership_remove(request, pk, user_id):
    if request.method != "POST":
        return redirect("projects:memberships", pk=pk)
    project = get_authorized_project_or_404(request.user, pk)
    user = get_object_or_404(User, pk=user_id)
    services.remove_tester(project, user)
    messages.success(request, "Atribuição removida com sucesso.")
    return redirect("projects:memberships", pk=project.pk)
