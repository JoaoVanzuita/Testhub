from django.urls import path

from . import views

app_name = "testcases"

urlpatterns = [
    path(
        "projeto/<int:project_pk>/",
        views.case_list,
        name="case_list",
    ),
    path(
        "projeto/<int:project_pk>/novo/",
        views.case_create,
        name="case_create",
    ),
    path("<int:pk>/", views.case_detail, name="case_detail"),
    path("<int:pk>/editar/", views.case_edit, name="case_edit"),
    path("<int:pk>/inativar/", views.case_inactivate, name="case_inactivate"),
    path("<int:pk>/excluir/", views.case_delete, name="case_delete"),
]
