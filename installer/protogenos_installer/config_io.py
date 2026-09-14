"""Save and load installation choices for review, reuse, and unattended installs.

Configuration and credentials are separate documents (like archinstall's
--config/--creds) so the configuration can be shared or kept on the
installed system without leaking passwords.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .backend import InstallConfig
    from .models import InstallPlan

CONFIG_VERSION = 1
SECRET_FIELDS = ("user_password", "root_password", "encryption_passphrase")


class ConfigFileError(ValueError):
    """Raised for malformed configuration or credential documents."""


def export_config(plan: InstallPlan, config: InstallConfig) -> dict[str, Any]:
    install = dataclasses.asdict(config)
    for name in SECRET_FIELDS:
        install.pop(name, None)
    install["additional_users"] = [
        {"username": user.username, "sudo": user.sudo} for user in config.additional_users
    ]
    return {
        "version": CONFIG_VERSION,
        "persona": plan.persona,
        "selections": {name: list(values) for name, values in plan.selections.items()},
        "allow_aur": bool(plan.aur_packages),
        "install": install,
    }


def export_credentials(config: InstallConfig) -> dict[str, Any]:
    return {
        "version": CONFIG_VERSION,
        "user_password": config.user_password,
        "root_password": config.root_password,
        "encryption_passphrase": config.encryption_passphrase,
        "additional_users": {user.username: user.password for user in config.additional_users},
    }


def load_documents(
    config_data: dict[str, Any], credentials: dict[str, Any] | None = None
) -> tuple[str, dict[str, tuple[str, ...]], bool, InstallConfig]:
    from .backend import InstallConfig

    if not isinstance(config_data, dict) or config_data.get("version") != CONFIG_VERSION:
        raise ConfigFileError(f"configuration must be an object with version {CONFIG_VERSION}")
    persona = config_data.get("persona")
    if not isinstance(persona, str):
        raise ConfigFileError("configuration is missing persona")
    raw_selections = config_data.get("selections") or {}
    if not isinstance(raw_selections, dict):
        raise ConfigFileError("selections must be an object")
    selections = {str(name): tuple(str(item) for item in values) for name, values in raw_selections.items()}
    install = dict(config_data.get("install") or {})
    credentials = credentials or {}
    for name in SECRET_FIELDS:
        if credentials.get(name) is not None:
            install[name] = credentials[name]
    install.setdefault("user_password", "")
    user_passwords = credentials.get("additional_users") or {}
    install["additional_users"] = [
        {**user, "password": user_passwords.get(user.get("username"), "")}
        for user in install.get("additional_users") or []
    ]
    known = {item.name for item in dataclasses.fields(InstallConfig)}
    unknown = set(install) - known
    if unknown:
        raise ConfigFileError(f"unknown install settings: {', '.join(sorted(unknown))}")
    try:
        config = InstallConfig(**install)
    except TypeError as error:
        raise ConfigFileError(f"invalid install settings: {error}") from error
    return persona, selections, bool(config_data.get("allow_aur")), config


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigFileError(f"could not read {path}: {error}") from error
    if not isinstance(data, dict):
        raise ConfigFileError(f"{path} must contain a JSON object")
    return data


def write_json(path: Path, data: dict[str, Any], *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if private:
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
    path.write_text(json.dumps(data, indent=2) + "\n")
