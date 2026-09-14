"""
MavunoGuard AI — test suite.

Auth tests mock the Supabase client so they run without real credentials.
Infrastructure / static-asset tests use the FastAPI TestClient directly.
"""
import unittest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers to build fake Supabase response objects
# ---------------------------------------------------------------------------

def _make_user(uid="test-uid-123", email="farmer@example.com", username="testfarmer"):
    user = MagicMock()
    user.id = uid
    user.email = email
    user.user_metadata = {"username": username}
    return user


def _make_session(access_token="fake-jwt-token"):
    session = MagicMock()
    session.access_token = access_token
    return session


def _make_auth_response(with_session=True, email="farmer@example.com", username="testfarmer"):
    res = MagicMock()
    res.user = _make_user(email=email, username=username)
    res.session = _make_session() if with_session else None
    return res


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestStaticAssets(unittest.TestCase):
    """Static files and infrastructure endpoints — no mocking needed."""

    @classmethod
    def setUpClass(cls):
        from server.app import app
        cls.client = TestClient(app)

    def test_index(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)

    def test_service_worker(self):
        r = self.client.get("/sw.js")
        self.assertEqual(r.status_code, 200)

    def test_manifest(self):
        r = self.client.get("/static/manifest.webmanifest")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["name"], "MavunoGuard AI")

    def test_icons(self):
        for icon in ["icon-192.png", "icon-512.png", "icon-maskable-512.png"]:
            r = self.client.get(f"/static/icons/{icon}")
            self.assertEqual(r.status_code, 200)

    def test_health(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])

    def test_config(self):
        r = self.client.get("/api/config")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("weather", data)
        self.assertIn("ai", data)


class TestAuthEndpoints(unittest.TestCase):
    """Auth endpoints — Supabase client is mocked."""

    def setUp(self):
        from server.app import app
        self.client = TestClient(app)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def test_register_success(self):
        """Successful registration returns token + user dict."""
        with patch("server.routers.auth.supabase") as mock_sb:
            mock_sb.auth.sign_up.return_value = _make_auth_response()
            r = self.client.post("/api/auth/register", json={
                "username": "testfarmer",
                "email": "farmer@example.com",
                "password": "securepassword123",
            })
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("token", data)
        self.assertEqual(data["token"], "fake-jwt-token")
        self.assertEqual(data["user"]["username"], "testfarmer")
        self.assertEqual(data["user"]["email"], "farmer@example.com")

    def test_register_email_confirmation_pending(self):
        """When Supabase requires email confirmation, token is None."""
        with patch("server.routers.auth.supabase") as mock_sb:
            mock_sb.auth.sign_up.return_value = _make_auth_response(with_session=False)
            r = self.client.post("/api/auth/register", json={
                "username": "testfarmer",
                "email": "farmer@example.com",
                "password": "securepassword123",
            })
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIsNone(data["token"])
        self.assertIn("message", data)

    def test_register_missing_email_rejected(self):
        """Email is required for Supabase Auth."""
        with patch("server.routers.auth.supabase") as mock_sb:
            r = self.client.post("/api/auth/register", json={
                "username": "testfarmer",
                "password": "securepassword123",
                # no email
            })
        self.assertEqual(r.status_code, 422)  # Pydantic validation error

    def test_register_short_password_rejected(self):
        with patch("server.routers.auth.supabase") as mock_sb:
            r = self.client.post("/api/auth/register", json={
                "username": "testfarmer",
                "email": "farmer@example.com",
                "password": "abc",  # < 6 chars
            })
        self.assertEqual(r.status_code, 422)

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    def test_login_success(self):
        with patch("server.routers.auth.supabase") as mock_sb:
            mock_sb.auth.sign_in_with_password.return_value = _make_auth_response()
            r = self.client.post("/api/auth/login", json={
                "username": "farmer@example.com",
                "password": "securepassword123",
            })
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("token", data)
        self.assertEqual(data["token"], "fake-jwt-token")

    def test_login_bad_credentials(self):
        with patch("server.routers.auth.supabase") as mock_sb:
            mock_sb.auth.sign_in_with_password.side_effect = Exception("Invalid login credentials")
            r = self.client.post("/api/auth/login", json={
                "username": "nobody",
                "password": "wrongpassword",
            })
        self.assertEqual(r.status_code, 401)

    # ------------------------------------------------------------------
    # /me
    # ------------------------------------------------------------------

    def test_me_valid_token(self):
        with patch("server.routers.auth.supabase") as mock_sb:
            mock_sb.auth.get_user.return_value = _make_auth_response()
            r = self.client.get("/api/auth/me", headers={"Authorization": "Bearer fake-jwt-token"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["username"], "testfarmer")

    def test_me_no_token(self):
        r = self.client.get("/api/auth/me")
        self.assertEqual(r.status_code, 401)

    def test_me_invalid_token(self):
        with patch("server.routers.auth.supabase") as mock_sb:
            mock_sb.auth.get_user.side_effect = Exception("JWT expired")
            r = self.client.get("/api/auth/me", headers={"Authorization": "Bearer bad-token"})
        self.assertEqual(r.status_code, 401)

    # ------------------------------------------------------------------
    # Logout
    # ------------------------------------------------------------------

    def test_logout(self):
        with patch("server.routers.auth.supabase") as mock_sb:
            mock_sb.auth.sign_out.return_value = None
            r = self.client.post("/api/auth/logout", headers={"Authorization": "Bearer fake-jwt-token"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])

    # ------------------------------------------------------------------
    # No Supabase configured
    # ------------------------------------------------------------------

    def test_register_without_supabase_returns_503(self):
        with patch("server.routers.auth.supabase", None):
            r = self.client.post("/api/auth/register", json={
                "username": "testfarmer",
                "email": "farmer@example.com",
                "password": "securepassword123",
            })
        self.assertEqual(r.status_code, 503)


if __name__ == "__main__":
    unittest.main()
