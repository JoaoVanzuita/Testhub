from django.urls import path

from . import views

app_name = "projects"

urlpatterns = [
    path("", views.project_list, name="project_list"),
    path("novo/", views.project_create, name="project_create"),
    path("<int:pk>/", views.project_detail, name="project_detail"),
    path("<int:pk>/editar/", views.project_edit, name="project_edit"),
    path("<int:pk>/inativar/", views.project_inactivate, name="project_inactivate"),
    path("<int:pk>/excluir/", views.project_delete, name="project_delete"),
    path("<int:pk>/membros/", views.memberships, name="memberships"),
    path(
        "<int:pk>/membros/adicionar/",
        views.membership_add,
        name="membership_add",
    ),
    path(
        "<int:pk>/membros/<int:user_id>/remover/",
        views.membership_remove,
        name="membership_remove",
    ),
]
