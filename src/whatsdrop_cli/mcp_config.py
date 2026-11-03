"""MCP server settings, environment file loading and precedence."""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from .auth import resolve_credentials_path
from .config import parse_context_before, parse_duration, parse_tz
from .errors import CliError, UsageError

DEFAULT_LAST = "6h"
DEFAULT_CONTEXT_BEFORE = "30m"
DEFAULT_MAX_CHARS_PER_CHAT = 0
DEFAULT_MAX_TOTAL_CHARS = 0
DEFAULT_MAX_CHATS = 0
DEFAULT_MAX_PARTS = 1000
DEFAULT_CLI_TIMEOUT_SECONDS = 120.0
DEFAULT_ACCOUNT = "primary"

ACCOUNT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
ENV_KEY_PREFIX = "WHATSDROP_"

CONFIG_KEYS = {
    "default_last",
    "default_context_before",
    "max_chars_per_chat",
    "max_total_chars",
    "max_chats",
    "max_parts",
    "cli_timeout_seconds",
    "account",
    "credentials_file",
    "env_file",
    "tz",
}


@dataclass(frozen=True)
class McpSettings:
    default_last: str = DEFAULT_LAST
    default_context_before: str = DEFAULT_CONTEXT_BEFORE
    max_chars_per_chat: int = DEFAULT_MAX_CHARS_PER_CHAT
    max_total_chars: int = DEFAULT_MAX_TOTAL_CHARS
    max_chats: int = DEFAULT_MAX_CHATS
    max_parts: int = DEFAULT_MAX_PARTS
    cli_timeout_seconds: float = DEFAULT_CLI_TIMEOUT_SECONDS
    account: str = DEFAULT_ACCOUNT
    credentials_file: str | None = None
    env_file: str | None = None
    tz: str | None = None

    @property
    def credentials_path(self) -> Path:
        return resolve_credentials_path(SimpleNamespace(credentials_file=self.credentials_file))

    @property
    def env_path(self) -> Path:
        if self.env_file:
            return Path(self.env_file).expanduser()
        return self.credentials_path.parent / "env"


def default_config_path() -> Path:
    explicit = _env_text("WHATSDROP_MCP_CONFIG")
    if explicit is not None:
        return Path(explicit).expanduser()
    credentials = resolve_credentials_path(
        SimpleNamespace(credentials_file=_env_text("WHATSDROP_CREDENTIALS"))
    )
    return credentials.parent / "mcp.json"


def load_env_file(path: Path) -> dict[str, str]:
    path = Path(path).expanduser()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return {}
    except OSError:
        return {}

    if os.name != "nt":
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except OSError:
            return {}
        if mode & 0o077:
            raise UsageError(
                f"env file {path} must not be readable or writable by group/others; "
                f"run `chmod 600 {path}`"
            )

    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key or not key.startswith(ENV_KEY_PREFIX):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def load_settings() -> McpSettings:
    config_path = default_config_path()
    document = _read_config(config_path)
    values = {key: document[key] for key in CONFIG_KEYS if key in document}
    values = _apply_env(values)
    settings = McpSettings(**values)
    _validate(settings)
    return settings


def _read_config(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ValueError(f"could not read {path}: {exc}") from exc

    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc

    if not isinstance(document, dict):
        raise ValueError(f"{path} must contain a JSON object")
    if document.get("version") != 1:
        raise ValueError(f'{path} must contain "version": 1')
    return document


def _apply_env(values: dict) -> dict:
    merged = dict(values)
    overrides = {
        "default_last": "WHATSDROP_MCP_DEFAULT_LAST",
        "default_context_before": "WHATSDROP_MCP_DEFAULT_CONTEXT_BEFORE",
        "max_chars_per_chat": "WHATSDROP_MCP_MAX_CHARS",
        "max_total_chars": "WHATSDROP_MCP_MAX_TOTAL_CHARS",
        "max_chats": "WHATSDROP_MCP_MAX_CHATS",
        "max_parts": "WHATSDROP_MCP_MAX_PARTS",
        "cli_timeout_seconds": "WHATSDROP_MCP_TIMEOUT",
        "env_file": "WHATSDROP_MCP_ENV_FILE",
    }
    for field, env_name in overrides.items():
        raw = _env_text(env_name)
        if raw is None:
            continue
        if field in {"max_chars_per_chat", "max_total_chars", "max_chats", "max_parts"}:
            merged[field] = _parse_int(raw, env_name)
        elif field == "cli_timeout_seconds":
            merged[field] = _parse_float(raw, env_name)
        else:
            merged[field] = raw
    return merged


def _validate(settings: McpSettings) -> None:
    for field in ("default_last", "default_context_before", "account"):
        if not isinstance(getattr(settings, field), str):
            raise ValueError(f"{field} must be a string")
    for field in ("max_chars_per_chat", "max_total_chars", "max_chats", "max_parts"):
        value = getattr(settings, field)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{field} must be an integer")
    if not isinstance(settings.cli_timeout_seconds, (int, float)) or isinstance(
        settings.cli_timeout_seconds, bool
    ):
        raise ValueError("cli_timeout_seconds must be a number")
    if settings.max_chars_per_chat < 0:
        raise ValueError("max_chars_per_chat must be zero or positive")
    if settings.max_total_chars < 0:
        raise ValueError("max_total_chars must be zero or positive")
    if settings.max_chats < 0:
        raise ValueError("max_chats must be zero or positive")
    if not 1 <= settings.max_parts <= 1000:
        raise ValueError("max_parts must be between 1 and 1000")
    if not 1 <= settings.cli_timeout_seconds <= 600:
        raise ValueError("cli_timeout_seconds must be between 1 and 600")
    if not ACCOUNT_RE.match(settings.account or ""):
        raise ValueError("account alias may only contain letters, digits and . _ -")
    try:
        parse_duration(settings.default_last)
    except CliError as exc:
        raise ValueError(f"invalid default_last: {exc}") from exc
    try:
        parse_context_before(settings.default_context_before)
    except CliError as exc:
        raise ValueError(f"invalid default_context_before: {exc}") from exc
    if settings.tz:
        try:
            parse_tz(settings.tz)
        except CliError as exc:
            raise ValueError(f"invalid tz: {exc}") from exc


def _env_text(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _parse_int(raw: str, name: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _parse_float(raw: str, name: str) -> float:
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
