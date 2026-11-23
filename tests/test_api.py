import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from rhos.api import create_app


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.client = TestClient(create_app(self.temp.name))
        from rhos.auth import AuthStore
        AuthStore(self.temp.name).bootstrap("admin", "correct horse battery staple")
        response = self.client.post("/api/v1/auth/login", json={"username": "admin", "password": "correct horse battery staple"})
        self.csrf = response.json()["csrf_token"]
        self.headers = {"X-CSRF-Token": self.csrf}
        self.client.cookies.set("rhos_session", self.client.cookies.get("rhos_session"), domain="testserver.local", path="/")

    def test_configuration_requires_revision_and_commit(self):
        config = {"system": {"hostname": "edge01"}, "interfaces": [], "addresses": [], "routes": [], "firewall": {"forward": [], "nat": []}}
        response = self.client.put("/api/v1/configuration", json={"config": config}, headers=self.headers)
        self.assertEqual(response.status_code, 428)
        response = self.client.put("/api/v1/configuration", headers={"If-Match": "0", **self.headers}, json={"config": config})
        self.assertEqual(response.status_code, 200)
        response = self.client.post("/api/v1/configuration/commit", json={"revision": 0, "apply": False}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["revision"], 1)

    def test_dashboard_serves(self):
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Router overview", response.text)
        anonymous = TestClient(create_app(self.temp.name)).get("/")
        self.assertEqual(anonymous.status_code, 401)

    def test_diff_reports_candidate_and_revision_conflict(self):
        config = {"system": {"hostname": "edge01"}, "interfaces": [], "addresses": [], "routes": [], "firewall": {"forward": [], "nat": []}}
        response = self.client.put("/api/v1/configuration", headers={"If-Match": "0", **self.headers}, json={"config": config})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.client.get("/api/v1/configuration/diff").json()["changed"])
        stale = self.client.put("/api/v1/configuration", headers={"If-Match": "9", **self.headers}, json={"config": config})
        self.assertEqual(stale.status_code, 409)

    def test_configuration_mutations_require_auth_and_csrf(self):
        unauthenticated = TestClient(create_app(self.temp.name))
        response = unauthenticated.put("/api/v1/configuration", headers={"If-Match": "0"}, json={"config": {
            "system": {"hostname": "edge01"}, "interfaces": [], "addresses": [], "routes": [], "firewall": {"nat": []},
        }})
        self.assertEqual(response.status_code, 401)
        response = self.client.post("/api/v1/configuration/commit", json={"revision": 0, "apply": False}, headers={"X-CSRF-Token": "wrong"})
        self.assertEqual(response.status_code, 403)

    def test_confirmed_commit_endpoints_require_admin_csrf_and_record_pending_revision(self):
        config = {"system": {"hostname": "edge01"}, "interfaces": [], "addresses": [], "routes": [], "firewall": {"forward": [], "nat": []}}
        self.client.put("/api/v1/configuration", headers={"If-Match": "0", **self.headers}, json={"config": config})
        with patch.dict("os.environ", {"RHOS_ALLOW_NETWORK_APPLY": "1"}), patch("rhos.linux.apply_via_worker"):
            response = self.client.post("/api/v1/configuration/commit-confirmed", headers=self.headers, json={"revision": 0, "timeout": 30})
            self.assertEqual(response.status_code, 200)
            revision = response.json()["revision"]
            confirm = self.client.post("/api/v1/configuration/confirm", headers=self.headers, json={"revision": revision})
        self.assertEqual(confirm.status_code, 200)
        self.assertNotIn("pending_confirmed", confirm.json())

    def test_login_rejects_wrong_password_and_bootstrap_is_one_time(self):
        wrong = self.client.post("/api/v1/auth/login", json={"username": "admin", "password": "incorrect password"})
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(self.client.get("/api/v1/auth/status").headers["cache-control"], "no-store")
        headers = self.client.get("/api/v1/auth/status").headers
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertEqual(headers["x-frame-options"], "DENY")

    def test_login_rate_limit_after_ten_failures(self):
        for _ in range(10):
            self.client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong password"})
        response = self.client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong password"})
        self.assertEqual(response.status_code, 429)


if __name__ == "__main__":
    unittest.main()
