import unittest
from fastapi.testclient import TestClient
from server.app import app, USERS_DB
import json

class TestAppEndpoints(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        # Clear users database for testing
        USERS_DB.write_text("[]")

    def test_index(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

    def test_manifest(self):
        response = self.client.get("/static/manifest.webmanifest")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], "MavunoGuard AI")

    def test_icons(self):
        for icon in ["icon-192.png", "icon-512.png", "icon-maskable-512.png"]:
            response = self.client.get(f"/static/icons/{icon}")
            self.assertEqual(response.status_code, 200)

    def test_service_worker(self):
        response = self.client.get("/sw.js")
        self.assertEqual(response.status_code, 200)

    def test_registration_and_login(self):
        # 1. Register new user
        reg_res = self.client.post("/api/auth/register", json={
            "username": "testfarmer",
            "password": "securepassword123",
            "email": "farmer@example.com"
        })
        self.assertEqual(reg_res.status_code, 200)
        data = reg_res.json()
        self.assertIn("token", data)
        self.assertEqual(data["user"]["username"], "testfarmer")

        token = data["token"]

        # 2. Get profile with session token
        me_res = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(me_res.status_code, 200)
        self.assertEqual(me_res.json()["username"], "testfarmer")

        # 3. Login with credentials
        login_res = self.client.post("/api/auth/login", json={
            "username": "testfarmer",
            "password": "securepassword123"
        })
        self.assertEqual(login_res.status_code, 200)
        self.assertIn("token", login_res.json())

        # 4. Logout
        logout_res = self.client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(logout_res.status_code, 200)

if __name__ == "__main__":
    unittest.main()
