"""Internet connectivity checks and iwd Wi-Fi control for the live session.

The live ISO manages wireless links with iwd (plus systemd-networkd for
DHCP). Scanning reads iwd's D-Bus API via busctl; joining goes through
iwctl's built-in agent, because iwd caches passphrases in memory and
re-reads hand-written network files unreliably after a failed attempt. The
passphrase is briefly visible to other root processes in the single-user
live session, and is never written to the install log.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

IWD_SERVICE = "net.connman.iwd"
IWD_STORAGE = Path("/var/lib/iwd")
CONNECTIVITY_URLS = (
    "https://geo.mirror.pkgbuild.com/",
    "https://archlinux.org/",
)
_PLAIN_SSID_CHARACTERS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_ "
)

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


class NetworkError(RuntimeError):
    """Raised when Wi-Fi control fails in a way the user should see."""


@dataclass(frozen=True, slots=True)
class WifiNetwork:
    ssid: str
    security: str
    signal: int
    connected: bool
    known: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WifiCredential:
    ssid: str
    security: str
    secret: str | None


def is_online(urls: Sequence[str] = CONNECTIVITY_URLS, timeout: float = 5.0) -> bool:
    for url in urls:
        request = urllib.request.Request(url, method="HEAD")
        try:
            with urllib.request.urlopen(request, timeout=timeout):
                return True
        except urllib.error.HTTPError:
            # Any HTTP answer at all proves DNS, routing, and TLS work.
            return True
        except (urllib.error.URLError, OSError, ValueError):
            continue
    return False



def iwd_file_name(ssid: str, security: str) -> str:
    """Mirror iwd's storage naming: plain SSIDs verbatim, others hex-encoded."""
    if ssid and all(character in _PLAIN_SSID_CHARACTERS for character in ssid):
        stem = ssid
    else:
        stem = "=" + ssid.encode("utf-8").hex()
    return f"{stem}.{security}"


def _ssid_from_file_stem(stem: str) -> str | None:
    if stem.startswith("="):
        try:
            return bytes.fromhex(stem[1:]).decode("utf-8")
        except ValueError:
            return None
    return stem


def read_iwd_credentials(storage: Path = IWD_STORAGE) -> tuple[WifiCredential, ...]:
    credentials: list[WifiCredential] = []
    if not storage.is_dir():
        return ()
    for path in sorted(storage.iterdir()):
        if path.suffix not in {".psk", ".open"} or not path.is_file():
            continue
        ssid = _ssid_from_file_stem(path.stem)
        if not ssid:
            continue
        if path.suffix == ".open":
            credentials.append(WifiCredential(ssid=ssid, security="open", secret=None))
            continue
        values: dict[str, str] = {}
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            key, separator, value = line.partition("=")
            if separator:
                values[key.strip()] = value.strip()
        secret = values.get("Passphrase") or values.get("PreSharedKey")
        if secret:
            credentials.append(WifiCredential(ssid=ssid, security="psk", secret=secret))
    return tuple(credentials)


def networkmanager_keyfile(credential: WifiCredential) -> str:
    """Render a NetworkManager keyfile for a Wi-Fi network joined live."""
    ssid = credential.ssid
    if all(32 <= ord(character) < 127 and character not in ";\\" for character in ssid):
        ssid_value = ssid
    else:
        # Keyfile also accepts the SSID as a list of byte values.
        ssid_value = "".join(f"{byte};" for byte in ssid.encode("utf-8"))
    lines = [
        "[connection]",
        f"id={ssid_value}",
        "type=wifi",
        "",
        "[wifi]",
        "mode=infrastructure",
        f"ssid={ssid_value}",
        "",
    ]
    if credential.security == "psk" and credential.secret:
        lines += ["[wifi-security]", "key-mgmt=wpa-psk", f"psk={credential.secret}", ""]
    lines += ["[ipv4]", "method=auto", "", "[ipv6]", "method=auto", ""]
    return "\n".join(lines)


def _default_runner(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), text=True, capture_output=True, check=False)


class IwdClient:
    def __init__(
        self,
        runner: Runner = _default_runner,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.runner = runner
        self.sleep = sleep

    def _busctl(self, *args: str) -> object:
        result = self.runner(["busctl", "--json=short", *args])
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "").strip()
            raise NetworkError(message or "iwd D-Bus call failed")
        output = (result.stdout or "").strip()
        if not output:
            return None
        try:
            return json.loads(output)
        except json.JSONDecodeError as error:
            raise NetworkError(f"unexpected busctl output: {error}") from error

    def _objects(self) -> dict[str, dict[str, dict[str, object]]]:
        payload = self._busctl(
            "call", IWD_SERVICE, "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects"
        )
        if not isinstance(payload, dict) or not payload.get("data"):
            raise NetworkError("iwd is not running")
        objects: dict[str, dict[str, dict[str, object]]] = {}
        for path, interfaces in payload["data"][0].items():
            objects[path] = {
                name: {key: value.get("data") for key, value in properties.items()}
                for name, properties in interfaces.items()
            }
        return objects

    def _station_path(self, objects, device: str) -> str:
        for path, interfaces in objects.items():
            if (
                "net.connman.iwd.Station" in interfaces
                and interfaces.get("net.connman.iwd.Device", {}).get("Name") == device
            ):
                return path
        raise NetworkError(f"wireless device {device!r} is not available in station mode")

    def devices(self) -> tuple[str, ...]:
        try:
            objects = self._objects()
        except NetworkError:
            return ()
        return tuple(
            sorted(
                str(interfaces["net.connman.iwd.Device"].get("Name"))
                for interfaces in objects.values()
                if "net.connman.iwd.Device" in interfaces
                and "net.connman.iwd.Station" in interfaces
            )
        )

    def scan(self, device: str, *, wait_seconds: float = 10.0) -> tuple[WifiNetwork, ...]:
        station = self._station_path(self._objects(), device)
        try:
            self._busctl("call", IWD_SERVICE, station, "net.connman.iwd.Station", "Scan")
        except NetworkError as error:
            # A scan iwd already started on its own is just as good.
            if "busy" not in str(error).lower() and "progress" not in str(error).lower():
                raise
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            scanning = self._busctl(
                "get-property", IWD_SERVICE, station, "net.connman.iwd.Station", "Scanning"
            )
            if not (isinstance(scanning, dict) and scanning.get("data")):
                break
            self.sleep(0.5)
        return self.networks(device)

    def networks(self, device: str) -> tuple[WifiNetwork, ...]:
        objects = self._objects()
        station = self._station_path(objects, device)
        ordered = self._busctl(
            "call", IWD_SERVICE, station, "net.connman.iwd.Station", "GetOrderedNetworks"
        )
        entries = ordered.get("data", [[]])[0] if isinstance(ordered, dict) else []
        networks: list[WifiNetwork] = []
        seen: set[str] = set()
        for network_path, signal in entries:
            properties = objects.get(network_path, {}).get("net.connman.iwd.Network", {})
            ssid = str(properties.get("Name") or "")
            if not ssid or ssid in seen:
                continue
            seen.add(ssid)
            networks.append(
                WifiNetwork(
                    ssid=ssid,
                    security=str(properties.get("Type") or "open"),
                    signal=int(signal) // 100,
                    connected=bool(properties.get("Connected")),
                    known=bool(properties.get("KnownNetwork")),
                )
            )
        return tuple(networks)

    def connect(
        self,
        device: str,
        ssid: str,
        passphrase: str | None = None,
        *,
        wait_seconds: float = 30.0,
    ) -> None:
        objects = self._objects()
        station = self._station_path(objects, device)
        network: dict[str, object] | None = None
        for interfaces in objects.values():
            properties = interfaces.get("net.connman.iwd.Network")
            # A Network's Device property is the station's own object path.
            if properties and properties.get("Name") == ssid and properties.get("Device") == station:
                network = properties
                break
        if network is None:
            raise NetworkError(f"network {ssid!r} was not found; scan again")
        security = str(network.get("Type") or "open")
        if security == "8021x":
            raise NetworkError("WPA-Enterprise (802.1X) networks need manual setup with iwctl")

        command = ["iwctl"]
        if security == "psk":
            if passphrase:
                if not 8 <= len(passphrase) <= 63:
                    raise NetworkError("Wi-Fi passphrase must be 8 to 63 characters")
                known_path = network.get("KnownNetwork")
                if known_path:
                    # A stored passphrase would be used instead of the new one.
                    self._busctl(
                        "call", IWD_SERVICE, str(known_path), "net.connman.iwd.KnownNetwork", "Forget"
                    )
                command += ["--passphrase", passphrase]
            elif not network.get("KnownNetwork"):
                raise NetworkError("this network requires a passphrase")
        command += ["station", device, "connect", ssid]

        result = self.runner(command)
        if result.returncode != 0:
            detail = _strip_ansi((result.stderr or "") + (result.stdout or "")).strip()
            raise NetworkError(
                f"could not join {ssid!r} ({detail or 'connection failed'}); "
                "check the passphrase and signal"
            )

        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            state = self._busctl(
                "get-property", IWD_SERVICE, station, "net.connman.iwd.Station", "State"
            )
            if isinstance(state, dict) and state.get("data") == "connected":
                return
            self.sleep(0.5)
        raise NetworkError(f"timed out joining {ssid!r}")


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
