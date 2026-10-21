from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models.deletion import ProtectedError

from .models import Role, User


class ProtectedUserError(Exception):
    pass


def create_user(*, email, name, role=Role.TESTER, password, is_active=True):
    normalized_email = (email or "").strip()
    if User.objects.filter(email__iexact=normalized_email).exists():
        raise ValidationError({"email": "Já existe uma conta com este e-mail."})

    with transaction.atomic():
        return User.objects.create_user(
            email=normalized_email,
            name=name,
            password=password,
            role=role,
            is_active=is_active,
        )


def update_user(user, *, email, name, role, is_active):
    normalized_email = (email or "").strip()
    if (
        User.objects.filter(email__iexact=normalized_email)
        .exclude(pk=user.pk)
        .exists()
    ):
        raise ValidationError({"email": "Já existe uma conta com este e-mail."})

    with transaction.atomic():
        user.email = normalized_email
        user.name = name
        user.role = role
        user.is_active = is_active
        user.save()
    return user


def inactivate_user(user):
    user.is_active = False
    user.save(update_fields=["is_active", "updated_at"])
    return user


def update_profile(user, *, name):
    with transaction.atomic():
        user.name = name
        user.save()
    return user


def delete_user(user):
    try:
        with transaction.atomic():
            user.delete()
    except ProtectedError as exc:  # pragma: no cover - defensive guard
        raise ProtectedUserError(
            "Esta conta possui referências históricas e não pode ser excluída; "
            "utilize a inativação."
        ) from exc
