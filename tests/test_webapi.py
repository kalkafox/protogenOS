import http.client
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from pathlib import Path

from protogenos_installer.webapi import InstallSession, create_server


def _request(url: str, *, method: str = "GET", body: dict | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


class WebApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.dist_dir = Path(self.temporary.name) / "dist"
        self.dist_dir.mkdir()
        (self.dist_dir / "index.html").write_text("<html>installer</html>")

        self.session = InstallSession()
        self.server = create_server(
            profiles_dir=Path(__file__).resolve().parents[1] / "profiles",
            dist_dir=self.dist_dir,
            session=self.session,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base_url = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temporary.cleanup()

    def test_personas(self) -> None:
        status, payload = _request(f"{self.base_url}/api/personas")
        self.assertEqual(status, 200)
        self.assertEqual(payload["personas"], ["general", "gamer", "developer", "server", "minimal"])

    def test_system_reports_detected_firmware(self) -> None:
        status, payload = _request(f"{self.base_url}/api/system")
        self.assertEqual(status, 200)
        self.assertIn(payload["firmware"], {"uefi", "bios"})
        self.assertFalse(payload["dry_run"])

    def test_system_reports_optional_feature_support(self) -> None:
        _, payload = _request(f"{self.base_url}/api/system")
        self.assertEqual(
            set(payload["features"]),
            {"tpm2", "fingerprint_reader", "secure_boot_setup_mode", "secure_boot_enabled"},
        )
        self.assertIn("nvidia_open_supported", payload["hardware"])

    def test_locale_suggestion(self) -> None:
        with patch("protogenos_installer.webapi.suggest_locale", return_value="de_DE.UTF-8") as suggest:
            status, payload = _request(f"{self.base_url}/api/locales/suggest?timezone=Europe/Berlin&layout=de")
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"locale": "de_DE.UTF-8"})
        suggest.assert_called_once_with("Europe/Berlin", "de")

    def test_log_upload_shares_finished_install_log(self) -> None:
        uploads: list[bytes] = []

        def fake_upload(data: bytes) -> str:
            uploads.append(data)
            return "https://paste.rs/abc"

        server = create_server(
            profiles_dir=Path(__file__).resolve().parents[1] / "profiles",
            dist_dir=self.dist_dir,
            session=self.session,
            log_uploader=fake_upload,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address[:2]
        url = f"http://{host}:{port}/api/install/log/upload"
        try:
            status, payload = _request(url, method="POST")
            self.assertEqual(status, 404)
            self.session.start()
            self.session.append_log("+ pacstrap /mnt base")
            status, payload = _request(url, method="POST")
            self.assertEqual(status, 409)
            self.session.finish("pacstrap failed")
            status, payload = _request(url, method="POST")
            self.assertEqual((status, payload), (200, {"url": "https://paste.rs/abc"}))
            self.assertEqual(uploads, [b"+ pacstrap /mnt base\n"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_reboot_refused_while_installing(self) -> None:
        self.assertTrue(self.session.start())
        status, payload = _request(f"{self.base_url}/api/system/reboot", method="POST")
        self.assertEqual(status, 409)
        self.assertIn("still running", payload["error"])

    def test_session_tracks_steps_and_warnings(self) -> None:
        session = InstallSession()
        session.start()
        session.append_log("[protogenos] step 3/6: Installing packages")
        session.append_log("==> WARNING: mkinitcpio noise is not an installer warning")
        session.append_log("[protogenos] warning: something was skipped")
        snapshot = session.snapshot(0)
        self.assertEqual(snapshot["step"], {"index": 3, "total": 6, "title": "Installing packages"})
        self.assertEqual(snapshot["warnings"], ["something was skipped"])

    def test_keyboard_choice_is_validated_and_remembered(self) -> None:
        status, payload = _request(f"{self.base_url}/api/keyboard")
        self.assertEqual((status, payload["applied"]), (200, False))
        status, payload = _request(
            f"{self.base_url}/api/keyboard", method="POST", body={"layout": "../x"}
        )
        self.assertEqual(status, 400)

    def test_keyboard_state_file_restores_layout_after_kiosk_restart(self) -> None:
        state = Path(self.temporary.name) / "state" / "keyboard"
        state.parent.mkdir()
        state.write_text("de\nnodeadkeys\n")
        server = create_server(
            profiles_dir=Path(__file__).resolve().parents[1] / "profiles",
            dist_dir=self.dist_dir,
            keyboard_state=state,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address[:2]
            status, payload = _request(f"http://{host}:{port}/api/keyboard")
            self.assertEqual(payload, {"layout": "de", "variant": "nodeadkeys", "applied": True})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_desktop_session_applies_layout_without_restarting(self) -> None:
        applied: list[tuple[str, str, str]] = []
        state = Path(self.temporary.name) / "state" / "keyboard"
        server = create_server(
            profiles_dir=Path(__file__).resolve().parents[1] / "profiles",
            dist_dir=self.dist_dir,
            keyboard_state=state,
            desktop_user="live",
            desktop_keyboard=lambda *args: applied.append(args),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address[:2]
            with patch("protogenos_installer.webapi.subprocess.run"):
                status, payload = _request(
                    f"http://{host}:{port}/api/keyboard",
                    method="POST",
                    body={"layout": "de", "variant": "nodeadkeys"},
                )
            self.assertEqual(status, 200)
            self.assertFalse(payload["restart"])
            self.assertEqual(applied, [("live", "de", "nodeadkeys")])
            self.assertFalse((state.parent / "restart").exists())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_preflight_reports_checks(self) -> None:
        from protogenos_installer.preflight import Check

        server = create_server(
            profiles_dir=Path(__file__).resolve().parents[1] / "profiles",
            dist_dir=self.dist_dir,
            preflight=lambda: [Check("power", "warning", "Running on battery (50%)")],
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address[:2]
            status, payload = _request(f"http://{host}:{port}/api/preflight")
            self.assertEqual(status, 200)
            self.assertEqual(
                payload["checks"],
                [{"id": "power", "status": "warning", "title": "Running on battery (50%)", "detail": ""}],
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_options_requires_persona(self) -> None:
        status, payload = _request(f"{self.base_url}/api/options")
        self.assertEqual(status, 400)
        self.assertIn("persona", payload["error"])

    def test_options_for_persona(self) -> None:
        status, payload = _request(f"{self.base_url}/api/options?persona=general")
        self.assertEqual(status, 200)
        self.assertTrue(any(group["name"] == "kernel" for group in payload["groups"]))

    def test_resolve_plan(self) -> None:
        status, payload = _request(
            f"{self.base_url}/api/plan/resolve",
            method="POST",
            body={"persona": "general", "selections": {}},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["persona"], "general")
        self.assertIn("base", payload["packages"])

    def test_resolve_plan_invalid_persona(self) -> None:
        status, payload = _request(
            f"{self.base_url}/api/plan/resolve",
            method="POST",
            body={"persona": "nonexistent", "selections": {}},
        )
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_config_validate_rejects_bad_hostname(self) -> None:
        status, payload = _request(
            f"{self.base_url}/api/config/validate",
            method="POST",
            body={
                "disk": "/dev/sda",
                "firmware": "uefi",
                "hostname": "bad hostname",
                "username": "fox",
                "user_password": "hunter2",
            },
        )
        self.assertEqual(status, 200)
        self.assertFalse(payload["valid"])

    def test_config_validate_checks_persona_settings(self) -> None:
        body = {
            "disk": "/dev/sda",
            "firmware": "uefi",
            "hostname": "proto-box",
            "username": "fox",
            "user_password": "hunter2",
            "timezone": "UTC",
        }
        status, payload = _request(f"{self.base_url}/api/config/validate", method="POST", body={**body, "persona": "server"})
        self.assertEqual(status, 200)
        self.assertFalse(payload["valid"])
        self.assertIn("SSH public key", payload["error"])

    def test_github_keys_route(self) -> None:
        from protogenos_installer.server import ServerConfigError

        def fake_keys(username: str) -> tuple[str, ...]:
            if username == "fox":
                return ("ssh-ed25519 AAAAC3Nz fox",)
            raise ServerConfigError(f"GitHub user {username} was not found")

        server = create_server(
            profiles_dir=Path(__file__).resolve().parents[1] / "profiles",
            dist_dir=self.dist_dir,
            session=self.session,
            github_keys=fake_keys,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address[:2]
        try:
            status, payload = _request(f"http://{host}:{port}/api/ssh/github-keys?username=fox")
            self.assertEqual((status, payload), (200, {"keys": ["ssh-ed25519 AAAAC3Nz fox"]}))
            status, payload = _request(f"http://{host}:{port}/api/ssh/github-keys?username=ghost")
            self.assertEqual(status, 400)
            self.assertIn("was not found", payload["error"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_static_serves_index_for_unknown_paths(self) -> None:
        with urllib.request.urlopen(f"{self.base_url}/some/spa/route") as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b"installer", response.read())

    def test_static_rejects_path_traversal(self) -> None:
        host, port = self.server.server_address[:2]
        connection = http.client.HTTPConnection(host, port)
        try:
            connection.request("GET", "/../../../../etc/passwd")
            response = connection.getresponse()
            response.read()
            self.assertEqual(response.status, 403)
        finally:
            connection.close()


class InstallLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.dist_dir = Path(self.temporary.name) / "dist"
        self.dist_dir.mkdir()
        (self.dist_dir / "index.html").write_text("<html>installer</html>")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_double_start_conflicts(self) -> None:
        session = InstallSession()
        self.assertTrue(session.start())
        self.assertFalse(session.start())

    def test_aur_gate_blocks_start_without_confirmation(self) -> None:
        session = InstallSession()
        server = create_server(
            profiles_dir=Path(__file__).resolve().parents[1] / "profiles",
            dist_dir=self.dist_dir,
            session=session,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address[:2]
        base_url = f"http://{host}:{port}"
        try:
            status, payload = _request(
                f"{base_url}/api/install/start",
                method="POST",
                body={
                    "plan": {
                        "persona": "gamer",
                        "packages": ["base"],
                        "selections": {},
                        "aur_packages": ["some-aur-package"],
                        "multilib_required": False,
                    },
                    "config": {
                        "disk": "/dev/sda",
                        "firmware": "uefi",
                        "hostname": "proto-box",
                        "username": "fox",
                        "user_password": "hunter2",
                    },
                    "aur_confirmed": False,
                },
            )
            self.assertEqual(status, 400)
            self.assertIn("aur_confirmed", payload["error"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
