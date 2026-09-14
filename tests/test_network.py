import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from protogenos_installer.network import (
    IwdClient,
    NetworkError,
    WifiCredential,
    iwd_file_name,
    networkmanager_keyfile,
    read_iwd_credentials,
)

STATION = "/net/connman/iwd/0/4"


def _variant(value):
    kind = "b" if isinstance(value, bool) else "s"
    return {"type": kind, "data": value}


class FakeBusctl:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.objects = {
            STATION: {
                "net.connman.iwd.Device": {"Name": _variant("wlan0")},
                "net.connman.iwd.Station": {"State": _variant("disconnected")},
            },
            f"{STATION}/aa_psk": {
                "net.connman.iwd.Network": {
                    "Name": _variant("Home Wi-Fi"),
                    "Type": _variant("psk"),
                    "Connected": _variant(False),
                    "Device": {"type": "o", "data": STATION},
                }
            },
            f"{STATION}/bb_open": {
                "net.connman.iwd.Network": {
                    "Name": _variant("Cafe"),
                    "Type": _variant("open"),
                    "Connected": _variant(False),
                    "Device": {"type": "o", "data": STATION},
                }
            },
        }

    def __call__(self, args):
        command = tuple(args)
        self.calls.append(command)
        if command[0] == "iwctl":
            if "--passphrase" in command and command[command.index("--passphrase") + 1] != "right-passphrase":
                return subprocess.CompletedProcess(command, 1, "\x1b[0;91mOperation failed\n\x1b[0m", "")
            return subprocess.CompletedProcess(command, 0, "", "")
        method = command[-1]

        def ok(payload=None):
            stdout = json.dumps(payload) if payload is not None else ""
            return subprocess.CompletedProcess(command, 0, stdout, "")

        if method == "GetManagedObjects":
            return ok({"type": "a{oa{sa{sv}}}", "data": [self.objects]})
        if method == "GetOrderedNetworks":
            return ok(
                {"type": "a(on)", "data": [[[f"{STATION}/bb_open", -4000], [f"{STATION}/aa_psk", -6500]]]}
            )
        if method == "Forget":
            self.objects[f"{STATION}/aa_psk"]["net.connman.iwd.Network"].pop("KnownNetwork", None)
            return ok()
        if method == "Scan":
            return subprocess.CompletedProcess(command, 1, "", "Call failed: Operation already in progress")
        if method == "Scanning":
            return ok({"type": "b", "data": False})
        if method == "State":
            return ok({"type": "s", "data": "connected"})
        raise AssertionError(f"unexpected busctl call: {command}")


class NetworkHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.storage = Path(self.temporary.name) / "iwd"
        self.busctl = FakeBusctl()
        self.client = IwdClient(self.busctl, sleep=lambda _: None)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _home_network(self) -> dict:
        return self.busctl.objects[f"{STATION}/aa_psk"]["net.connman.iwd.Network"]

    def test_iwd_file_names_hex_encode_unusual_ssids(self) -> None:
        self.assertEqual(iwd_file_name("Home Wi-Fi_2", "psk"), "Home Wi-Fi_2.psk")
        self.assertEqual(iwd_file_name("Café", "psk"), "=436166c3a9.psk")

    def test_scan_tolerates_scan_in_progress_and_orders_by_signal(self) -> None:
        networks = self.client.scan("wlan0")
        self.assertEqual([network.ssid for network in networks], ["Cafe", "Home Wi-Fi"])
        self.assertEqual(networks[1].signal, -65)
        self.assertEqual(networks[1].security, "psk")

    def test_connect_uses_iwctl_agent_with_passphrase(self) -> None:
        self.client.connect("wlan0", "Home Wi-Fi", "right-passphrase")
        self.assertIn(
            ("iwctl", "--passphrase", "right-passphrase", "station", "wlan0", "connect", "Home Wi-Fi"),
            self.busctl.calls,
        )

    def test_new_passphrase_forgets_stored_one_first(self) -> None:
        self._home_network()["KnownNetwork"] = {"type": "o", "data": "/net/connman/iwd/known"}
        self.client.connect("wlan0", "Home Wi-Fi", "right-passphrase")
        methods = [call[-1] if call[0] == "busctl" else "iwctl" for call in self.busctl.calls]
        self.assertLess(methods.index("Forget"), methods.index("iwctl"))

    def test_known_network_connects_without_passphrase(self) -> None:
        self._home_network()["KnownNetwork"] = {"type": "o", "data": "/net/connman/iwd/known"}
        self.client.connect("wlan0", "Home Wi-Fi")
        self.assertIn(("iwctl", "station", "wlan0", "connect", "Home Wi-Fi"), self.busctl.calls)

    def test_open_network_connects_without_passphrase(self) -> None:
        self.client.connect("wlan0", "Cafe")
        self.assertIn(("iwctl", "station", "wlan0", "connect", "Cafe"), self.busctl.calls)

    def test_connect_requires_valid_passphrase_for_psk(self) -> None:
        with self.assertRaisesRegex(NetworkError, "requires a passphrase"):
            self.client.connect("wlan0", "Home Wi-Fi")
        with self.assertRaisesRegex(NetworkError, "8 to 63"):
            self.client.connect("wlan0", "Home Wi-Fi", "short")

    def test_connect_failure_strips_colors_and_mentions_passphrase(self) -> None:
        with self.assertRaisesRegex(NetworkError, r"\(Operation failed\); check the passphrase"):
            self.client.connect("wlan0", "Home Wi-Fi", "wrong-passphrase")

    def test_unknown_device_is_reported(self) -> None:
        with self.assertRaisesRegex(NetworkError, "wlan9"):
            self.client.scan("wlan9")

    def test_iwd_credentials_are_read_back_for_the_target(self) -> None:
        self.storage.mkdir()
        (self.storage / "Home Wi-Fi.psk").write_text(
            "[Security]\nPreSharedKey=abcdef\nPassphrase=right-passphrase\n"
        )
        (self.storage / "=436166c3a9.open").write_text("")
        credentials = read_iwd_credentials(self.storage)
        self.assertEqual(
            credentials,
            (
                WifiCredential("Café", "open", None),
                WifiCredential("Home Wi-Fi", "psk", "right-passphrase"),
            ),
        )

    def test_networkmanager_keyfile_escapes_awkward_ssids(self) -> None:
        keyfile = networkmanager_keyfile(WifiCredential("a;b", "open", None))
        self.assertIn("ssid=97;59;98;", keyfile)
        self.assertNotIn("wifi-security", keyfile)


if __name__ == "__main__":
    unittest.main()
