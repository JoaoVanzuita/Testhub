from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("entrar/", views.AccountLoginView.as_view(), name="login"),
    path("sair/", views.AccountLogoutView.as_view(), name="logout"),
    path("usuarios/", views.user_list, name="user_list"),
    path("usuarios/novo/", views.user_create, name="user_create"),
    path("usuarios/<int:pk>/editar/", views.user_edit, name="user_edit"),
    path(
        "usuarios/<int:pk>/inativar/",
        views.user_inactivate,
        name="user_inactivate",
    ),
    path("perfil/", views.profile, name="profile"),
    path(
        "perfil/senha/",
        views.AccountPasswordChangeView.as_view(),
        name="password_change",
    ),
]
