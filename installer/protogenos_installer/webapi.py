"""Stdlib-only JSON API and static file server for the web GUI installer."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .backend import (
    STEP_PREFIX,
    WARNING_PREFIX,
    INSTALL_LOG,
    CommandRunner,
    InstallConfig,
    InstallError,
    InstallerBackend,
    detect_firmware,
    list_install_disks,
    list_timezones,
)
from .hardware import detect_features, detect_hardware
from .locales import suggest_locale
from .keyboard import LAYOUT_PATTERN, VARIANT_PATTERN, console_keymap, list_layouts
from .mirrors import parse_reflector_countries
from .storage import StorageError, read_disk_layout
from .models import OptionGroup, PackageChoice
from .network import IwdClient, NetworkError, is_online
from .profiles import PERSONAS, ProfileError, ProfileRepository


def _serialize_choice(choice: PackageChoice) -> dict[str, Any]:
    data = asdict(choice)
    data["profiles"] = sorted(choice.profiles)
    return data


def _serialize_group(group: OptionGroup) -> dict[str, Any]:
    return {
        "name": group.name,
        "selection": group.selection,
        "choices": [_serialize_choice(choice) for choice in group.choices],
    }


class InstallSession:
    """Mutable state for the single install run a live session ever performs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.status: str = "idle"
        self.log_lines: list[str] = []
        self.error: str | None = None
        self.step: dict[str, Any] | None = None
        self.warnings: list[str] = []

    def append_log(self, line: str) -> None:
        with self._lock:
            self.log_lines.append(line)
            if line.startswith(STEP_PREFIX):
                counter, _, title = line[len(STEP_PREFIX):].partition(": ")
                index, _, total = counter.partition("/")
                if index.isdigit() and total.isdigit():
                    self.step = {"index": int(index), "total": int(total), "title": title}
            elif line.startswith(WARNING_PREFIX):
                warning = line[len(WARNING_PREFIX):]
                if warning not in self.warnings:
                    self.warnings.append(warning)

    def start(self) -> bool:
        with self._lock:
            if self.status == "running":
                return False
            self.status = "running"
            self.log_lines = []
            self.error = None
            self.step = None
            self.warnings = []
            return True

    def finish(self, error: str | None) -> None:
        with self._lock:
            self.status = "error" if error else "done"
            self.error = error

    def snapshot(self, since: int) -> dict[str, Any]:
        with self._lock:
            lines = self.log_lines[since:]
            return {
                "status": self.status,
                "lines": lines,
                "next_since": len(self.log_lines),
                "error": self.error,
                "step": self.step,
                "warnings": list(self.warnings),
            }


class StreamingCommandRunner(CommandRunner):
    """CommandRunner that tees subprocess output into an InstallSession live."""

    stream_output = True

    def __init__(
        self, session: InstallSession, *, dry_run: bool = False, log_path: Path | None = None
    ) -> None:
        super().__init__(dry_run=dry_run, log_path=log_path)
        self.session = session

    def emit(self, line: str) -> None:
        self.session.append_log(line)
        self._write_log(line)


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def make_handler(
    *,
    profiles_dir: Path,
    dist_dir: Path,
    session: InstallSession,
    dry_run: bool = False,
    online_check: Callable[[], bool] = is_online,
    wifi_client: IwdClient | None = None,
    log_path: Path | None = None,
    keyboard_state: Path | None = None,
) -> type[BaseHTTPRequestHandler]:
    repository = ProfileRepository(profiles_dir)
    wifi_client = wifi_client or IwdClient()
    layouts_cache: list[dict[str, Any]] = []
    countries_cache: list[dict[str, Any]] = []
    keyboard = {"layout": "us", "variant": "", "applied": False}
    if keyboard_state is not None and keyboard_state.is_file():
        # The kiosk restarts after a layout change; pick the choice back up.
        saved_layout, _, saved_variant = keyboard_state.read_text().partition("\n")
        keyboard.update(layout=saved_layout.strip() or "us", variant=saved_variant.strip(), applied=True)
    if log_path is None:
        log_path = (
            Path(tempfile.gettempdir()) / "protogenos-dryrun-install.log"
            if dry_run
            else INSTALL_LOG
        )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

        def _send_json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as error:
                raise ApiError(HTTPStatus.BAD_REQUEST, f"invalid JSON body: {error}") from error
            if not isinstance(data, dict):
                raise ApiError(HTTPStatus.BAD_REQUEST, "JSON body must be an object")
            return data

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def _dispatch(self, method: str) -> None:
            split = urlsplit(self.path)
            path = split.path
            query = {key: values[0] for key, values in parse_qs(split.query).items()}
            try:
                if path == "/api/personas" and method == "GET":
                    self._send_json(HTTPStatus.OK, {"personas": list(PERSONAS)})
                elif path == "/api/options" and method == "GET":
                    self._handle_options(query)
                elif path == "/api/plan/resolve" and method == "POST":
                    self._handle_resolve()
                elif path == "/api/system" and method == "GET":
                    self._send_json(
                        HTTPStatus.OK,
                        {
                            "firmware": detect_firmware(),
                            "dry_run": dry_run,
                            "hardware": detect_hardware().describe(),
                            "features": detect_features().describe(),
                        },
                    )
                elif path == "/api/network" and method == "GET":
                    self._send_json(
                        HTTPStatus.OK,
                        {"online": online_check(), "devices": list(wifi_client.devices())},
                    )
                elif path == "/api/network/scan" and method == "POST":
                    self._handle_wifi_scan()
                elif path == "/api/network/connect" and method == "POST":
                    self._handle_wifi_connect()
                elif path == "/api/system/reboot" and method == "POST":
                    self._handle_reboot()
                elif path == "/api/keyboard" and method == "GET":
                    self._send_json(HTTPStatus.OK, keyboard)
                elif path == "/api/keyboard/layouts" and method == "GET":
                    if not layouts_cache:
                        layouts_cache.extend(layout.to_dict() for layout in list_layouts())
                    self._send_json(HTTPStatus.OK, {"layouts": layouts_cache})
                elif path == "/api/keyboard" and method == "POST":
                    self._handle_keyboard()
                elif path == "/api/mirrors/countries" and method == "GET":
                    self._handle_mirror_countries()
                elif path == "/api/disks/layout" and method == "GET":
                    disk = query.get("disk", "")
                    if not disk.startswith("/dev/"):
                        raise ApiError(HTTPStatus.BAD_REQUEST, "disk query parameter is required")
                    layout = read_disk_layout(CommandRunner(), disk)
                    self._send_json(HTTPStatus.OK, layout.to_dict())
                elif path == "/api/disks" and method == "GET":
                    self._handle_disks()
                elif path == "/api/timezones" and method == "GET":
                    self._handle_timezones()
                elif path == "/api/locales/suggest" and method == "GET":
                    self._send_json(
                        HTTPStatus.OK,
                        {"locale": suggest_locale(query.get("timezone", "UTC"), query.get("layout", ""))},
                    )
                elif path == "/api/config/validate" and method == "POST":
                    self._handle_validate()
                elif path == "/api/install/start" and method == "POST":
                    self._handle_install_start()
                elif path == "/api/install/status" and method == "GET":
                    self._handle_install_status(query)
                elif method == "GET" and not path.startswith("/api/"):
                    self._serve_static(path)
                else:
                    raise ApiError(HTTPStatus.NOT_FOUND, "not found")
            except ApiError as error:
                self._send_json(error.status, {"error": error.message})
            except (ProfileError, InstallError, NetworkError, StorageError) as error:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})

        def _handle_options(self, query: dict[str, str]) -> None:
            persona = query.get("persona")
            if persona is None:
                raise ApiError(HTTPStatus.BAD_REQUEST, "persona query parameter is required")
            groups = repository.groups_for(persona)
            self._send_json(HTTPStatus.OK, {"groups": [_serialize_group(group) for group in groups]})

        def _handle_resolve(self) -> None:
            body = self._read_json()
            persona = body.get("persona")
            if not isinstance(persona, str):
                raise ApiError(HTTPStatus.BAD_REQUEST, "persona is required")
            selections = body.get("selections") or {}
            if not isinstance(selections, dict):
                raise ApiError(HTTPStatus.BAD_REQUEST, "selections must be an object")
            plan = repository.resolve(persona, selections)
            self._send_json(HTTPStatus.OK, plan.to_dict())

        def _handle_disks(self) -> None:
            disks = list_install_disks()
            self._send_json(
                HTTPStatus.OK,
                {"disks": [asdict(disk) for disk in disks]},
            )

        def _handle_timezones(self) -> None:
            self._send_json(HTTPStatus.OK, {"timezones": list(list_timezones())})

        def _handle_validate(self) -> None:
            body = self._read_json()
            try:
                config = InstallConfig(**body)
            except TypeError as error:
                self._send_json(HTTPStatus.OK, {"valid": False, "error": str(error)})
                return
            try:
                config.validate()
            except InstallError as error:
                self._send_json(HTTPStatus.OK, {"valid": False, "error": str(error)})
                return
            self._send_json(HTTPStatus.OK, {"valid": True})

        def _handle_install_start(self) -> None:
            body = self._read_json()
            plan_data = body.get("plan")
            config_data = body.get("config")
            aur_confirmed = bool(body.get("aur_confirmed"))
            if not isinstance(plan_data, dict) or not isinstance(config_data, dict):
                raise ApiError(HTTPStatus.BAD_REQUEST, "plan and config are required")

            from .models import InstallPlan

            try:
                plan = InstallPlan(
                    persona=plan_data["persona"],
                    packages=tuple(plan_data["packages"]),
                    selections={k: tuple(v) for k, v in plan_data["selections"].items()},
                    aur_packages=tuple(plan_data["aur_packages"]),
                    multilib_required=bool(plan_data["multilib_required"]),
                )
                config = InstallConfig(**config_data)
            except (KeyError, TypeError) as error:
                raise ApiError(HTTPStatus.BAD_REQUEST, f"invalid plan or config: {error}") from error

            if plan.aur_packages and not aur_confirmed:
                raise ApiError(
                    HTTPStatus.BAD_REQUEST,
                    "aur_confirmed must be true when the plan includes AUR packages",
                )

            if not session.start():
                raise ApiError(HTTPStatus.CONFLICT, "an installation is already running")

            def _run() -> None:
                backend = InstallerBackend(
                    runner=StreamingCommandRunner(session, dry_run=dry_run, log_path=log_path),
                    dry_run=dry_run,
                )
                if dry_run:
                    session.append_log(f"+ dry-run target root: {backend.target_root}")
                try:
                    backend.install(plan, config)
                except InstallError as error:
                    session.finish(str(error))
                except Exception as error:  # noqa: BLE001
                    session.finish(str(error))
                else:
                    session.finish(None)

            threading.Thread(target=_run, daemon=True).start()
            self._send_json(HTTPStatus.ACCEPTED, {"started": True})

        def _handle_keyboard(self) -> None:
            body = self._read_json()
            layout = body.get("layout")
            variant = body.get("variant") or ""
            if not isinstance(layout, str) or not LAYOUT_PATTERN.fullmatch(layout):
                raise ApiError(HTTPStatus.BAD_REQUEST, "invalid keyboard layout")
            if not isinstance(variant, str) or not VARIANT_PATTERN.fullmatch(variant):
                raise ApiError(HTTPStatus.BAD_REQUEST, "invalid keyboard variant")
            changed = (layout, variant) != (keyboard["layout"], keyboard["variant"])
            keyboard.update(layout=layout, variant=variant, applied=True)
            restart = False
            if not dry_run:
                # Console too, so the text installer and shells on other VTs match.
                subprocess.run(
                    ["loadkeys", console_keymap(layout, variant)],
                    capture_output=True,
                    check=False,
                )
                if keyboard_state is not None:
                    keyboard_state.parent.mkdir(parents=True, exist_ok=True)
                    keyboard_state.write_text(f"{layout}\n{variant}\n")
                    # The compositor only reads its XKB layout at startup;
                    # the launcher restarts it when this flag exists.
                    restart = changed
                    if restart:
                        (keyboard_state.parent / "restart").touch()
            self._send_json(HTTPStatus.OK, {**keyboard, "restart": restart})
            if restart:
                threading.Timer(0.5, lambda: subprocess.run(["pkill", "-x", "cage"], check=False)).start()

        def _handle_mirror_countries(self) -> None:
            if not countries_cache:
                try:
                    result = subprocess.run(
                        ["reflector", "--list-countries"],
                        capture_output=True,
                        text=True,
                        timeout=30,
                        check=False,
                    )
                    countries_cache.extend(
                        country.to_dict() for country in parse_reflector_countries(result.stdout)
                    )
                except (OSError, subprocess.TimeoutExpired):
                    pass
            self._send_json(HTTPStatus.OK, {"countries": countries_cache})

        def _handle_wifi_scan(self) -> None:
            body = self._read_json()
            device = body.get("device")
            if not isinstance(device, str) or not device:
                raise ApiError(HTTPStatus.BAD_REQUEST, "device is required")
            networks = wifi_client.scan(device)
            self._send_json(
                HTTPStatus.OK, {"networks": [network.to_dict() for network in networks]}
            )

        def _handle_wifi_connect(self) -> None:
            body = self._read_json()
            device = body.get("device")
            ssid = body.get("ssid")
            passphrase = body.get("passphrase") or None
            if not isinstance(device, str) or not isinstance(ssid, str) or not ssid:
                raise ApiError(HTTPStatus.BAD_REQUEST, "device and ssid are required")
            if passphrase is not None and not isinstance(passphrase, str):
                raise ApiError(HTTPStatus.BAD_REQUEST, "passphrase must be a string")
            wifi_client.connect(device, ssid, passphrase)
            # DHCP usually lands a moment after the association completes.
            online = False
            for _ in range(10):
                if online_check():
                    online = True
                    break
                time.sleep(1)
            self._send_json(HTTPStatus.OK, {"connected": True, "online": online})

        def _handle_reboot(self) -> None:
            # The kiosk browser has no other way out once installation ends.
            if session.status == "running":
                raise ApiError(HTTPStatus.CONFLICT, "an installation is still running")
            if not dry_run:
                subprocess.Popen(["systemctl", "reboot"])
            self._send_json(HTTPStatus.ACCEPTED, {"rebooting": not dry_run})

        def _handle_install_status(self, query: dict[str, str]) -> None:
            try:
                since = int(query.get("since", "0"))
            except ValueError:
                since = 0
            self._send_json(HTTPStatus.OK, session.snapshot(since))

        def _serve_static(self, path: str) -> None:
            relative = path.lstrip("/") or "index.html"
            candidate = (dist_dir / relative).resolve()
            try:
                candidate.relative_to(dist_dir.resolve())
            except ValueError:
                raise ApiError(HTTPStatus.FORBIDDEN, "forbidden") from None
            if not candidate.is_file():
                candidate = (dist_dir / "index.html").resolve()
            if not candidate.is_file():
                raise ApiError(HTTPStatus.NOT_FOUND, "not found")
            data = candidate.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", _content_type(candidate))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def _content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".html": "text/html; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".json": "application/json",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".ico": "image/x-icon",
        ".woff2": "font/woff2",
    }.get(suffix, "application/octet-stream")


def create_server(
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    profiles_dir: Path,
    dist_dir: Path,
    session: InstallSession | None = None,
    dry_run: bool = False,
    online_check: Callable[[], bool] = is_online,
    wifi_client: IwdClient | None = None,
    log_path: Path | None = None,
    keyboard_state: Path | None = None,
) -> ThreadingHTTPServer:
    handler = make_handler(
        profiles_dir=profiles_dir,
        dist_dir=dist_dir,
        session=session or InstallSession(),
        dry_run=dry_run,
        online_check=online_check,
        wifi_client=wifi_client,
        log_path=log_path,
        keyboard_state=keyboard_state,
    )
    return ThreadingHTTPServer((host, port), handler)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="protogenOS web installer API server")
    parser.add_argument("--profiles-dir", type=Path, default=_project_root() / "profiles")
    parser.add_argument(
        "--dist", type=Path, default=_project_root() / "installer-web" / "dist"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="log installation commands instead of running them, writing to a "
        "throwaway directory instead of the target disk (no root/disk required) "
        "-- for iterating on the GUI/API without booting a VM",
    )
    parser.add_argument(
        "--keyboard-state",
        type=Path,
        help="file shared with the kiosk launcher to persist the keyboard layout "
        "and request a compositor restart when it changes",
    )
    args = parser.parse_args(argv)

    server = create_server(
        host=args.host,
        port=args.port,
        profiles_dir=args.profiles_dir,
        dist_dir=args.dist,
        dry_run=args.dry_run,
        keyboard_state=args.keyboard_state,
    )
    host, port = server.server_address[:2]
    print(f"protogenOS web installer listening on http://{host}:{port}/")
    if args.dry_run:
        print("Running in --dry-run mode: no disks or system files will be touched.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
