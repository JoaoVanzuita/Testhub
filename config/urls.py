from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", RedirectView.as_view(pattern_name="projects:project_list")),
    path("contas/", include("apps.accounts.urls")),
    path("projetos/", include("apps.projects.urls")),
    path("casos/", include("apps.testcases.urls")),
    path("execucoes/", include("apps.executions.urls")),
]
