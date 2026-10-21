from django import forms
from django.contrib.auth import get_user_model

from apps.accounts.models import Role
from apps.testcases.models import TestCase

from .models import StepOutcome

User = get_user_model()


class HistoryComparisonForm(forms.Form):
    base = forms.ChoiceField(label="Execução de referência")
    against = forms.ChoiceField(label="Execução comparada")

    def __init__(self, *args, summaries=None, **kwargs):
        super().__init__(*args, **kwargs)
        choices = []
        for summary in summaries or []:
            execution = summary.execution
            completed = (
                execution.completed_at.strftime("%d/%m/%Y %H:%M")
                if execution.completed_at
                else "—"
            )
            choices.append(
                (
                    str(execution.pk),
                    f"#{execution.pk} — concluída em {completed} — "
                    f"taxa {summary.success_rate:.2f}%",
                )
            )
        self.fields["base"].choices = choices
        self.fields["against"].choices = choices

    def clean(self):
        cleaned = super().clean()
        base = cleaned.get("base")
        against = cleaned.get("against")
        if base and against and base == against:
            raise forms.ValidationError(
                "Escolha duas execuções diferentes para comparar."
            )
        return cleaned


class ExecutionStartForm(forms.Form):
    cases = forms.ModelMultipleChoiceField(
        queryset=TestCase.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        label="Casos de teste",
        error_messages={
            "required": "Selecione ao menos um caso para iniciar a execução.",
        },
    )

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        if project is not None:
            self.fields["cases"].queryset = (
                project.test_cases.filter(is_active=True, steps__is_active=True)
                .distinct()
                .order_by("title", "pk")
            )

    def clean_cases(self):
        cases = self.cleaned_data["cases"]
        if not cases:
            raise forms.ValidationError(
                "Selecione ao menos um caso para iniciar a execução."
            )
        return cases


class StepResultForm(forms.Form):
    outcome = forms.ChoiceField(
        choices=StepOutcome.choices,
        label="Resultado",
        error_messages={
            "required": "Informe um resultado para o passo.",
            "invalid_choice": "Resultado inválido. Use Passou, Falhou ou Bloqueado.",
        },
    )
    observation = forms.CharField(
        required=False,
        label="Observação",
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Opcional para qualquer resultado; descreva o comportamento "
        "inesperado ou o impedimento em Falhou e Bloqueado.",
    )


class HandoffForm(forms.Form):
    successor = forms.ModelChoiceField(
        queryset=User.objects.none(),
        label="Novo responsável",
        error_messages={
            "required": "Escolha o Testador que assume a execução.",
            "invalid_choice": "Escolha um Testador atribuído a este projeto.",
        },
    )

    def __init__(self, *args, project=None, exclude_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = User.objects.none()
        if project is not None:
            queryset = (
                User.objects.filter(
                    role=Role.TESTER,
                    is_active=True,
                    project_memberships__project=project,
                    project_memberships__revoked_at__isnull=True,
                )
                .distinct()
                .order_by("name", "email")
            )
            if exclude_user is not None and exclude_user.pk:
                queryset = queryset.exclude(pk=exclude_user.pk)
        self.fields["successor"].queryset = queryset
