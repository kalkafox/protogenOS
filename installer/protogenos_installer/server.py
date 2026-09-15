"""Server persona helpers: SSH keys, static networking, and service config text."""

from __future__ import annotations

import ipaddress
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable

SERVER_PERSONA = "server"
GITHUB_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
INTERFACE_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,15}$")
SSH_KEY_PATTERN = re.compile(
    r"^(?:ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521)|"
    r"sk-ssh-ed25519@openssh\.com|sk-ecdsa-sha2-nistp256@openssh\.com)"
    r" [A-Za-z0-9+/]+={0,3}(?: [^\n\r]*)?$"
)
GITHUB_KEYS_URL = "https://github.com/{username}.keys"
GITHUB_TIMEOUT = 15

# Settings only the Server persona uses, with their "off" values.
SERVER_ONLY_SETTINGS = {
    "ssh_authorized_keys": (),
    "cockpit": False,
    "netdata": False,
    "fail2ban": False,
    "update_downloads": False,
    "serial_console": False,
    "static_address": "",
    "static_gateway": "",
    "static_dns": (),
    "static_interface": "",
}

SSHD_CONFIG = """# protogenOS server: key-only SSH logins.
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
"""

# Reads sshd's journal entries; bans through firewalld so rules survive reloads.
FAIL2BAN_JAIL = """[DEFAULT]
banaction = firewallcmd-rich-rules
banaction_allports = firewallcmd-rich-rules

[sshd]
enabled = true
backend = systemd
"""

UPDATE_DOWNLOAD_SERVICE = """[Unit]
Description=Download pending package updates without installing them
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
# checkupdates syncs a private copy of the package databases, so this never
# causes a partial upgrade. It exits 2 when nothing is pending.
ExecStart=/usr/bin/checkupdates --download
SuccessExitStatus=2
"""

UPDATE_DOWNLOAD_TIMER = """[Unit]
Description=Download pending package updates daily

[Timer]
OnCalendar=daily
RandomizedDelaySec=1h
Persistent=true

[Install]
WantedBy=timers.target
"""

GRUB_SERIAL_COMMAND = "serial --unit=0 --speed=115200"


class ServerConfigError(ValueError):
    """Raised for invalid server settings."""


def parse_authorized_keys(text: str) -> tuple[str, ...]:
    """Return the valid public keys in text, ignoring blank lines and comments."""
    keys: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if not SSH_KEY_PATTERN.fullmatch(line):
            raise ServerConfigError(f"not an SSH public key: {line[:40]!r}")
        keys.append(line)
    return tuple(dict.fromkeys(keys))


def fetch_github_keys(
    username: str,
    opener: Callable[..., object] = urllib.request.urlopen,
) -> tuple[str, ...]:
    """Download a GitHub account's public SSH keys."""
    if not GITHUB_USERNAME_PATTERN.fullmatch(username):
        raise ServerConfigError(f"invalid GitHub username: {username!r}")
    url = GITHUB_KEYS_URL.format(username=username)
    try:
        with opener(url, timeout=GITHUB_TIMEOUT) as response:  # type: ignore[attr-defined]
            text = response.read(256_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise ServerConfigError(f"GitHub user {username} was not found") from error
        raise ServerConfigError(f"could not fetch keys from GitHub: HTTP {error.code}") from error
    except (urllib.error.URLError, OSError) as error:
        raise ServerConfigError(f"could not fetch keys from GitHub: {error}") from error
    keys = parse_authorized_keys(text)
    if not keys:
        raise ServerConfigError(f"GitHub user {username} has no public SSH keys")
    return keys


def validate_static_network(
    address: str, gateway: str, dns: Iterable[str], interface: str
) -> None:
    if not address:
        if gateway or tuple(dns) or interface:
            raise ServerConfigError("a static gateway, DNS, or interface needs a static address")
        return
    try:
        network = ipaddress.ip_interface(address)
    except ValueError as error:
        raise ServerConfigError(f"static address must look like 192.168.1.10/24: {address!r}") from error
    if "/" not in address:
        raise ServerConfigError("static address needs a prefix length, such as /24")
    if gateway:
        try:
            gateway_address = ipaddress.ip_address(gateway)
        except ValueError as error:
            raise ServerConfigError(f"invalid gateway address: {gateway!r}") from error
        if gateway_address.version != network.version:
            raise ServerConfigError("gateway and static address must both be IPv4 or IPv6")
    for server in dns:
        try:
            ipaddress.ip_address(server)
        except ValueError as error:
            raise ServerConfigError(f"invalid DNS server: {server!r}") from error
    if interface and not INTERFACE_PATTERN.fullmatch(interface):
        raise ServerConfigError(f"invalid network interface name: {interface!r}")


def static_connection_keyfile(
    address: str, gateway: str, dns: Iterable[str], interface: str
) -> str:
    """NetworkManager keyfile for a wired connection with a static address."""
    network = ipaddress.ip_interface(address)
    family, other = ("ipv4", "ipv6") if network.version == 4 else ("ipv6", "ipv4")
    servers = list(dns)
    same_family = [server for server in servers if ipaddress.ip_address(server).version == network.version]
    other_family = [server for server in servers if ipaddress.ip_address(server).version != network.version]

    lines = ["[connection]", "id=protogenos-static", "type=ethernet", "autoconnect=true", "autoconnect-priority=100"]
    if interface:
        lines.append(f"interface-name={interface}")
    lines += ["", f"[{family}]", "method=manual", f"address1={network.with_prefixlen}" + (f",{gateway}" if gateway else "")]
    if same_family:
        lines.append("dns=" + ";".join(same_family) + ";")
        lines.append("ignore-auto-dns=true")
    # The other address family keeps automatic configuration.
    lines += ["", f"[{other}]", "method=auto"]
    if other_family:
        lines.append("dns=" + ";".join(other_family) + ";")
    return "\n".join(lines) + "\n"


def firewall_services(*, cockpit: bool, netdata: bool) -> tuple[str, ...]:
    """firewalld services to open beyond the public zone's defaults (ssh, dhcpv6-client)."""
    services: list[str] = []
    if cockpit:
        services.append("cockpit")
    if netdata:
        services.append("netdata-dashboard")
    return tuple(services)
