from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy

from . import services
from .forms import ProfileForm, UserCreateForm, UserEditForm
from .models import User
from .permissions import active_user_required, administrator_required


class AccountLoginView(auth_views.LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True


class AccountLogoutView(auth_views.LogoutView):
    pass


class AccountPasswordChangeView(auth_views.PasswordChangeView):
    template_name = "accounts/password_form.html"
    success_url = reverse_lazy("accounts:profile")

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Senha alterada com sucesso.")
        return response


@administrator_required
def user_list(request):
    users = User.objects.all()
    return render(request, "accounts/user_list.html", {"users": users})


@administrator_required
def user_create(request):
    if request.method == "POST":
        form = UserCreateForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Usuário cadastrado com sucesso.")
            return redirect("accounts:user_list")
    else:
        form = UserCreateForm()
    return render(
        request, "accounts/user_form.html", {"form": form, "mode": "create"}
    )


@administrator_required
def user_edit(request, pk):
    user = get_object_or_404(User, pk=pk)
    if request.method == "POST":
        form = UserEditForm(request.POST, instance=user, actor=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Usuário atualizado com sucesso.")
            return redirect("accounts:user_list")
    else:
        form = UserEditForm(instance=user, actor=request.user)
    return render(
        request,
        "accounts/user_form.html",
        {"form": form, "mode": "edit", "object": user},
    )


@administrator_required
def user_inactivate(request, pk):
    if request.method != "POST":
        return redirect("accounts:user_list")
    user = get_object_or_404(User, pk=pk)
    if user.pk == request.user.pk:
        messages.error(request, "Você não pode inativar a própria conta.")
        return redirect("accounts:user_list")
    services.inactivate_user(user)
    messages.success(request, "Usuário inativado com sucesso.")
    return redirect("accounts:user_list")


@active_user_required
def profile(request):
    if request.method == "POST":
        form = ProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Perfil atualizado com sucesso.")
            return redirect("accounts:profile")
    else:
        form = ProfileForm(instance=request.user)
    return render(request, "accounts/profile.html", {"form": form})
