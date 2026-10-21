from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from . import services
from .models import Role

User = get_user_model()


class UserCreateForm(forms.Form):
    email = forms.EmailField(label="E-mail")
    name = forms.CharField(label="Nome", max_length=150)
    role = forms.ChoiceField(label="Perfil", choices=Role.choices)
    is_active = forms.BooleanField(label="Ativo", required=False, initial=True)
    password1 = forms.CharField(label="Senha", widget=forms.PasswordInput)
    password2 = forms.CharField(
        label="Confirmação da senha", widget=forms.PasswordInput
    )

    def clean_email(self):
        email = self.cleaned_data["email"].strip()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Já existe uma conta com este e-mail.")
        return email

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get("password1")
        password2 = cleaned.get("password2")

        if password1 and password2 and password1 != password2:
            self.add_error("password2", "As senhas não coincidem.")
        elif password1:
            candidate = User(
                email=cleaned.get("email", ""),
                name=cleaned.get("name", ""),
            )
            try:
                validate_password(password1, user=candidate)
            except ValidationError as exc:
                self.add_error("password1", exc)

        return cleaned

    def save(self):
        return services.create_user(
            email=self.cleaned_data["email"],
            name=self.cleaned_data["name"],
            role=self.cleaned_data["role"],
            password=self.cleaned_data["password1"],
            is_active=self.cleaned_data.get("is_active", False),
        )


class UserEditForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["email", "name", "role", "is_active"]

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor

    def clean_email(self):
        email = self.cleaned_data["email"].strip()
        duplicates = User.objects.filter(email__iexact=email)
        if self.instance.pk:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise forms.ValidationError("Já existe uma conta com este e-mail.")
        return email

    def clean_is_active(self):
        is_active = self.cleaned_data["is_active"]
        if (
            not is_active
            and self.instance.pk
            and self.actor is not None
            and self.instance.pk == self.actor.pk
        ):
            raise forms.ValidationError("Você não pode inativar a própria conta.")
        return is_active

    def save(self, commit=True):
        return services.update_user(
            self.instance,
            email=self.cleaned_data["email"],
            name=self.cleaned_data["name"],
            role=self.cleaned_data["role"],
            is_active=self.cleaned_data["is_active"],
        )


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["name"]

    def save(self, commit=True):
        return services.update_profile(
            self.instance,
            name=self.cleaned_data["name"],
        )
