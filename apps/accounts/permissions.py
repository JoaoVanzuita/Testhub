from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied

from .models import Role

ADMINISTRATOR = Role.ADMINISTRATOR
TESTER = Role.TESTER


def is_active_user(user) -> bool:
    return bool(user and user.is_authenticated and user.is_active)


def has_role(user, role: str) -> bool:
    return is_active_user(user) and getattr(user, "role", None) == role


def is_administrator(user) -> bool:
    return has_role(user, ADMINISTRATOR)


def is_tester(user) -> bool:
    return has_role(user, TESTER)


def _redirect_to_login(request):
    return redirect_to_login(request.get_full_path())


def active_user_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return _redirect_to_login(request)
        if not request.user.is_active:
            raise PermissionDenied("Usuário inativo.")
        return view_func(request, *args, **kwargs)

    return _wrapped


def administrator_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return _redirect_to_login(request)
        if not is_administrator(request.user):
            raise PermissionDenied("Ação exclusiva de Administradores.")
        return view_func(request, *args, **kwargs)

    return _wrapped


def tester_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return _redirect_to_login(request)
        if not is_tester(request.user):
            raise PermissionDenied("Ação exclusiva de Testadores.")
        return view_func(request, *args, **kwargs)

    return _wrapped
