from django.apps import AppConfig


class TestCasesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.testcases"
    label = "testcases"
    verbose_name = "Casos de teste"
