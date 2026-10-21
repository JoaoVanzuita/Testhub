from django import forms
from django.contrib.auth import get_user_model

from apps.accounts.models import Role

from . import services
from .models import Project, ProjectMembership

User = get_user_model()


class _ProjectNameMixin:
    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        duplicates = Project.objects.filter(name__iexact=name)
        if self.instance.pk:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise forms.ValidationError(
                "Já existe um projeto com este nome; informe um nome distinto."
            )
        return name


class ProjectForm(_ProjectNameMixin, forms.ModelForm):
    class Meta:
        model = Project
        fields = ["name", "description"]
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}

    def save(self, commit=True):
        return services.create_project(
            name=self.cleaned_data["name"],
            description=self.cleaned_data["description"],
        )


class ProjectEditForm(_ProjectNameMixin, forms.ModelForm):
    class Meta:
        model = Project
        fields = ["name", "description", "is_active"]
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}

    def save(self, commit=True):
        return services.update_project(
            self.instance,
            name=self.cleaned_data["name"],
            description=self.cleaned_data["description"],
            is_active=self.cleaned_data["is_active"],
        )


class MembershipForm(forms.Form):
    user = forms.ModelChoiceField(
        label="Testador",
        queryset=User.objects.none(),
        empty_label="Selecione um testador",
    )

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.project = project
        assigned_ids = ProjectMembership.objects.filter(
            project=project, revoked_at__isnull=True
        ).values_list("user_id", flat=True)
        self.fields["user"].queryset = (
            User.objects.filter(role=Role.TESTER, is_active=True)
            .exclude(pk__in=assigned_ids)
            .order_by("name")
        )
