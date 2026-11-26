from django.test import TestCase, override_settings

from .jwt import COOKIE_NAME
from .models import User

CSRF = {"HTTP_X_REQUESTED_WITH": "swiftpass"}


@override_settings(DEBUG=True)
class SessionCookieTests(TestCase):
    def register(self):
        return self.client.post(
            "/v1/auth/register",
            {"fullName": "Test User", "identifier": "cookie@example.com", "password": "S3cure-pass-phrase"},
            content_type="application/json",
        )

    def test_login_sets_httponly_samesite_cookie(self):
        res = self.register()
        self.assertEqual(res.status_code, 201)
        cookie = res.cookies[COOKIE_NAME]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")

    def test_cookie_authenticates_reads(self):
        self.register()
        self.assertEqual(self.client.get("/v1/auth/me").status_code, 200)

    def test_cookie_post_without_csrf_header_is_rejected(self):
        self.register()
        self.assertEqual(self.client.post("/v1/auth/agent-token", content_type="application/json").status_code, 401)
        self.assertEqual(self.client.post("/v1/auth/agent-token", content_type="application/json", **CSRF).status_code, 200)

    def test_agent_token_is_a_working_bearer(self):
        self.register()
        token = self.client.post("/v1/auth/agent-token", content_type="application/json", **CSRF).json()["token"]
        fresh = self.client_class()
        self.assertEqual(fresh.get("/v1/auth/me", HTTP_AUTHORIZATION=f"Bearer {token}").status_code, 200)

    def test_bearer_still_works_without_cookie(self):
        token = self.register().json()["token"]
        fresh = self.client_class()
        self.assertEqual(fresh.get("/v1/auth/me", HTTP_AUTHORIZATION=f"Bearer {token}").status_code, 200)

    def test_logout_clears_cookie_and_revokes(self):
        token = self.register().json()["token"]
        res = self.client.post("/v1/auth/logout", content_type="application/json", **CSRF)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.cookies[COOKIE_NAME].value, "")
        self.assertEqual(self.client_class().get("/v1/auth/me", HTTP_AUTHORIZATION=f"Bearer {token}").status_code, 401)

    def test_stale_cookie_is_a_guest_on_cart_routes(self):
        self.register()
        User.objects.update(token_version=99)  # revoke
        res = self.client.post("/v1/carts", {}, content_type="application/json", HTTP_X_GUEST_KEY="g" * 24, **CSRF)
        self.assertEqual(res.status_code, 201)
