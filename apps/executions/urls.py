from django.urls import path

from . import views

app_name = "executions"

urlpatterns = [
    path(
        "projeto/<int:project_pk>/nova/",
        views.execution_create,
        name="execution_create",
    ),
    path("<int:pk>/", views.execution_detail, name="execution_detail"),
    path(
        "<int:pk>/passos/<int:step_pk>/resultado/",
        views.execution_step_update,
        name="execution_step_update",
    ),
    path("<int:pk>/relatorio/", views.execution_report, name="report_detail"),
    path(
        "projeto/<int:project_pk>/historico/",
        views.history_list,
        name="history_list",
    ),
    path(
        "projeto/<int:project_pk>/historico/comparar/",
        views.history_compare,
        name="history_compare",
    ),
    path("<int:pk>/pausar/", views.execution_pause, name="execution_pause"),
    path("<int:pk>/retomar/", views.execution_resume, name="execution_resume"),
    path("<int:pk>/transferir/", views.execution_handoff, name="execution_handoff"),
    path("<int:pk>/liberar/", views.execution_release, name="execution_release"),
    path("<int:pk>/assumir/", views.execution_assume, name="execution_assume"),
    path("<int:pk>/concluir/", views.execution_complete, name="execution_complete"),
]
