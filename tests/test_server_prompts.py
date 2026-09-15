from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from protogenos_installer import cli, tui
from protogenos_installer.backend import InstallConfig
from protogenos_installer.server import ServerConfigError

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKexampleKeyMaterial fox@den"
PROFILES = Path(__file__).resolve().parents[1] / "profiles"


class CliServerPromptTests(unittest.TestCase):
    def test_pasted_key_and_static_address(self) -> None:
        answers = [
            "paste", "not a key",            # rejected, asked again
            "paste", KEY, "n",               # accepted, no more keys
            "y", "10.0.0.5/24", "10.0.0.1", "1.1.1.1 9.9.9.9", "",
        ]
        with patch("builtins.input", side_effect=answers), patch("sys.stdout", StringIO()) as output:
            settings = cli._collect_server_settings("fox")
        self.assertIn("not an SSH public key", output.getvalue())
        self.assertEqual(
            settings,
            {
                "ssh_authorized_keys": (KEY,),
                "static_address": "10.0.0.5/24",
                "static_gateway": "10.0.0.1",
                "static_dns": ("1.1.1.1", "9.9.9.9"),
                "static_interface": "",
            },
        )

    def test_github_import_retries_after_errors_and_keeps_dhcp(self) -> None:
        fetch = [ServerConfigError("GitHub user ghost was not found"), (KEY,)]
        answers = ["github", "ghost", "github", "fox", "n", "n"]
        with patch("builtins.input", side_effect=answers), patch("sys.stdout", StringIO()), patch.object(
            cli, "fetch_github_keys", side_effect=fetch
        ):
            settings = cli._collect_server_settings("fox")
        self.assertEqual(settings, {"ssh_authorized_keys": (KEY,)})

    def test_saved_server_config_without_keys_is_rejected(self) -> None:
        config = {
            "version": 1,
            "persona": "server",
            "selections": {},
            "install": {"disk": "/dev/vda", "firmware": "uefi", "hostname": "box", "username": "fox"},
        }
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "server.json"
            creds_path = Path(temporary) / "creds.json"
            config_path.write_text(json.dumps(config))
            creds_path.write_text(json.dumps({"version": 1, "user_password": "hunter22"}))
            errors = StringIO()
            with patch("sys.stdout", StringIO()), patch("sys.stderr", errors):
                result = cli.main(
                    ["--profiles-dir", str(PROFILES), "--config", str(config_path), "--creds", str(creds_path), "--dry-run"]
                )
        self.assertEqual(result, 2)
        self.assertIn("at least one SSH public key", errors.getvalue())

    def test_summary_describes_server_network(self) -> None:
        config = InstallConfig(
            disk="/dev/vda", firmware="uefi", hostname="box", username="fox", user_password="x",
            static_address="10.0.0.5/24", static_gateway="10.0.0.1", static_dns=("1.1.1.1",),
        )
        self.assertEqual(cli.describe_network(config), "static 10.0.0.5/24 via 10.0.0.1 DNS 1.1.1.1")


class TuiServerPromptTests(unittest.TestCase):
    def test_collects_keys_from_both_sources_then_dhcp(self) -> None:
        other = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQDexample== fox@laptop"
        with patch.object(tui, "_run_list", side_effect=[("select", 0), ("select", 1)]), patch.object(
            tui, "_text_input", side_effect=[KEY, "fox"]
        ), patch.object(tui, "_confirm", side_effect=[True, False, False]), patch.object(
            tui, "_show_status"
        ), patch.object(tui, "fetch_github_keys", return_value=(KEY, other)):
            settings = tui._collect_server_settings(None, "fox")
        self.assertEqual(settings, {"ssh_authorized_keys": (KEY, other)})

    def test_invalid_static_settings_are_asked_again(self) -> None:
        inputs = ["10.0.0.5", "", "", "", "10.0.0.5/24", "", "", "eth0"]
        with patch.object(tui, "_confirm", return_value=True), patch.object(
            tui, "_text_input", side_effect=inputs
        ), patch.object(tui, "_show_message") as message:
            settings = tui._collect_static_network(None)
        self.assertIn("prefix length", message.call_args.args[1])
        self.assertEqual(settings["static_address"], "10.0.0.5/24")
        self.assertEqual(settings["static_interface"], "eth0")

    def test_escape_cancels_key_collection(self) -> None:
        with patch.object(tui, "_run_list", return_value=("quit", 0)):
            self.assertIsNone(tui._collect_server_settings(None, "fox"))


if __name__ == "__main__":
    unittest.main()
