import io
import unittest
import urllib.error

from protogenos_installer.server import (
    ServerConfigError,
    fetch_github_keys,
    firewall_services,
    parse_authorized_keys,
    static_connection_keyfile,
    validate_static_network,
)

ED25519 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKexampleKeyMaterial fox@den"
RSA = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQDexample=="


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def _opener(body: bytes = b"", error: Exception | None = None):
    calls: list[str] = []

    def opener(url: str, timeout: float):
        calls.append(url)
        if error is not None:
            raise error
        return _Response(body)

    opener.calls = calls  # type: ignore[attr-defined]
    return opener


class AuthorizedKeyTests(unittest.TestCase):
    def test_parses_keys_skipping_blanks_comments_and_duplicates(self) -> None:
        text = f"# laptop\n{ED25519}\n\n{RSA}\n{ED25519}\n"
        self.assertEqual(parse_authorized_keys(text), (ED25519, RSA))

    def test_rejects_private_keys_and_garbage(self) -> None:
        with self.assertRaisesRegex(ServerConfigError, "not an SSH public key"):
            parse_authorized_keys("-----BEGIN OPENSSH PRIVATE KEY-----")
        for line in ("ssh-ed25519", "ssh-dss AAAAB3NzaC1kc3M=", "ssh-ed25519 AAAA!invalid"):
            with self.subTest(line=line), self.assertRaises(ServerConfigError):
                parse_authorized_keys(line)


class GithubKeyTests(unittest.TestCase):
    def test_fetches_keys_for_a_user(self) -> None:
        opener = _opener(f"{ED25519}\n{RSA}\n".encode())
        self.assertEqual(fetch_github_keys("kalkafox", opener), (ED25519, RSA))
        self.assertEqual(opener.calls, ["https://github.com/kalkafox.keys"])

    def test_invalid_username_never_reaches_the_network(self) -> None:
        opener = _opener()
        with self.assertRaisesRegex(ServerConfigError, "invalid GitHub username"):
            fetch_github_keys("../etc/passwd", opener)
        self.assertEqual(opener.calls, [])

    def test_reports_missing_users_and_users_without_keys(self) -> None:
        missing = urllib.error.HTTPError("url", 404, "Not Found", None, io.BytesIO())
        self.addCleanup(missing.close)
        with self.assertRaisesRegex(ServerConfigError, "was not found"):
            fetch_github_keys("nobody", _opener(error=missing))
        with self.assertRaisesRegex(ServerConfigError, "has no public SSH keys"):
            fetch_github_keys("keyless", _opener(b"\n"))

    def test_network_failures_are_reported(self) -> None:
        with self.assertRaisesRegex(ServerConfigError, "could not fetch keys"):
            fetch_github_keys("fox", _opener(error=urllib.error.URLError("offline")))


class StaticNetworkTests(unittest.TestCase):
    def test_empty_settings_mean_dhcp(self) -> None:
        validate_static_network("", "", (), "")

    def test_rejects_incomplete_or_invalid_settings(self) -> None:
        cases = [
            (("", "192.168.1.1", (), ""), "needs a static address"),
            (("192.168.1.10", "", (), ""), "prefix length"),
            (("192.168.1.300/24", "", (), ""), "must look like"),
            (("192.168.1.10/24", "fe80::1", (), ""), "both be IPv4 or IPv6"),
            (("192.168.1.10/24", "", ("dns.example",), ""), "invalid DNS server"),
            (("192.168.1.10/24", "", (), "eth0; rm"), "invalid network interface"),
        ]
        for arguments, message in cases:
            with self.subTest(arguments=arguments), self.assertRaisesRegex(ServerConfigError, message):
                validate_static_network(*arguments)

    def test_ipv4_keyfile_sets_address_gateway_and_dns(self) -> None:
        keyfile = static_connection_keyfile("192.168.1.10/24", "192.168.1.1", ("1.1.1.1", "2606:4700::1111"), "enp1s0")
        self.assertIn("interface-name=enp1s0\n", keyfile)
        self.assertIn("[ipv4]\nmethod=manual\naddress1=192.168.1.10/24,192.168.1.1\ndns=1.1.1.1;\nignore-auto-dns=true\n", keyfile)
        self.assertIn("[ipv6]\nmethod=auto\ndns=2606:4700::1111;\n", keyfile)

    def test_ipv6_keyfile_without_gateway_or_interface(self) -> None:
        keyfile = static_connection_keyfile("2001:db8::10/64", "", (), "")
        self.assertNotIn("interface-name", keyfile)
        self.assertIn("[ipv6]\nmethod=manual\naddress1=2001:db8::10/64\n", keyfile)
        self.assertIn("[ipv4]\nmethod=auto\n", keyfile)


class FirewallTests(unittest.TestCase):
    def test_opens_only_selected_web_services(self) -> None:
        self.assertEqual(firewall_services(cockpit=False, netdata=False), ())
        self.assertEqual(firewall_services(cockpit=True, netdata=True), ("cockpit", "netdata-dashboard"))


if __name__ == "__main__":
    unittest.main()
