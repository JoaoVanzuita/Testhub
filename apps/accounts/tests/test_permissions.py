from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.accounts import permissions
from apps.accounts.models import Role

User = get_user_model()


@permissions.active_user_required
def _active_view(request):
    return HttpResponse("active-ok")


@permissions.administrator_required
def _administrator_view(request):
    return HttpResponse("admin-ok")


@permissions.tester_required
def _tester_view(request):
    return HttpResponse("tester-ok")


class RoleHelperTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.administrator = User.objects.create_user(
            email="admin@example.com", name="Admin", role=Role.ADMINISTRATOR
        )
        cls.tester = User.objects.create_user(
            email="testador@example.com", name="Testador", role=Role.TESTER
        )
        cls.inactive_administrator = User.objects.create_user(
            email="admin-inativo@example.com",
            name="Inativo",
            role=Role.ADMINISTRATOR,
            is_active=False,
        )
        cls.superuser_only = User.objects.create_user(
            email="super@example.com", name="Super", role=Role.TESTER
        )
        cls.superuser_only.is_superuser = True
        cls.superuser_only.save(update_fields=["is_superuser"])

    def test_is_active_user(self):
        self.assertTrue(permissions.is_active_user(self.tester))
        self.assertFalse(permissions.is_active_user(self.inactive_administrator))
        self.assertFalse(permissions.is_active_user(AnonymousUser()))
        self.assertFalse(permissions.is_active_user(None))

    def test_has_role_requires_active_user(self):
        self.assertTrue(permissions.has_role(self.administrator, Role.ADMINISTRATOR))
        self.assertFalse(
            permissions.has_role(self.inactive_administrator, Role.ADMINISTRATOR)
        )

    def test_is_administrator(self):
        self.assertTrue(permissions.is_administrator(self.administrator))
        self.assertFalse(permissions.is_administrator(self.tester))
        self.assertFalse(permissions.is_administrator(self.inactive_administrator))
        self.assertFalse(permissions.is_administrator(AnonymousUser()))

    def test_is_tester(self):
        self.assertTrue(permissions.is_tester(self.tester))
        self.assertFalse(permissions.is_tester(self.administrator))
        self.assertFalse(permissions.is_tester(self.inactive_administrator))
        self.assertFalse(permissions.is_tester(AnonymousUser()))

    def test_superuser_flag_does_not_grant_product_administrator(self):
        self.assertFalse(permissions.is_administrator(self.superuser_only))
        self.assertTrue(permissions.is_tester(self.superuser_only))


class DecoratorTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.administrator = User.objects.create_user(
            email="admin@example.com", name="Admin", role=Role.ADMINISTRATOR
        )
        self.tester = User.objects.create_user(
            email="testador@example.com", name="Testador", role=Role.TESTER
        )
        self.inactive_administrator = User.objects.create_user(
            email="admin-inativo@example.com",
            name="Inativo",
            role=Role.ADMINISTRATOR,
            is_active=False,
        )

    def _request(self, user):
        request = self.factory.get("/area-protegida/")
        request.user = user
        return request

    def assert_redirects_to_login(self, response):
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse("accounts:login")))
        self.assertIn("next=", response.url)

    def test_anonymous_is_redirected_not_forbidden(self):
        for view in (_active_view, _administrator_view, _tester_view):
            self.assert_redirects_to_login(view(self._request(AnonymousUser())))

    def test_inactive_user_is_forbidden(self):
        for view in (_active_view, _administrator_view, _tester_view):
            with self.assertRaises(PermissionDenied):
                view(self._request(self.inactive_administrator))

    def test_administrator_decorator_allows_administrator_only(self):
        self.assertEqual(
            _administrator_view(self._request(self.administrator)).content,
            b"admin-ok",
        )
        with self.assertRaises(PermissionDenied):
            _administrator_view(self._request(self.tester))

    def test_tester_decorator_allows_tester_only(self):
        self.assertEqual(
            _tester_view(self._request(self.tester)).content, b"tester-ok"
        )
        with self.assertRaises(PermissionDenied):
            _tester_view(self._request(self.administrator))

    def test_active_user_decorator_allows_any_active_role(self):
        self.assertEqual(
            _active_view(self._request(self.administrator)).content, b"active-ok"
        )
        self.assertEqual(
            _active_view(self._request(self.tester)).content, b"active-ok"
        )

    def test_decorators_preserve_view_metadata(self):
        self.assertEqual(_administrator_view.__name__, "_administrator_view")
