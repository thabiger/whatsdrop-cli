"""Private environment files for archive credentials."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace

from .auth import resolve_credentials_path
from .errors import UsageError

ENV_KEY_PREFIX = "WHATSDROP_"
MANAGED_KEYS = ("WHATSDROP_KEY", "WHATSDROP_ACCOUNT")


def default_env_path(args=None) -> Path:
    credentials = resolve_credentials_path(args or SimpleNamespace(credentials_file=None))
    return credentials.parent / "env"


def parse_env_text(text: str) -> dict[str, str]:
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

    return parse_env_text(text)


def read_env_file_unchecked(path: Path) -> dict[str, str]:
    """Read an env file without enforcing permissions (used before repairing it)."""
    path = Path(path).expanduser()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise UsageError(f"could not read {path}: {exc}") from exc
    return parse_env_text(text)


def write_env_file(path: Path, *, key: str, account: str) -> None:
    path = Path(path).expanduser()
    existing = _read_text_unchecked(path)
    lines = _strip_managed_keys(existing)
    if not lines:
        lines.append("# WhatsDrop CLI environment; keep this file private (chmod 600).")
    elif lines[-1].strip():
        lines.append("")
    lines.append(f"WHATSDROP_KEY={key}")
    lines.append(f"WHATSDROP_ACCOUNT={account}")
    _atomic_write_private(path, "\n".join(lines).rstrip("\n") + "\n")


def _read_text_unchecked(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    except OSError as exc:
        raise UsageError(f"could not read {path}: {exc}") from exc


def _strip_managed_keys(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in text.splitlines():
        parsed = parse_env_text(raw_line)
        if any(name in parsed for name in MANAGED_KEYS):
            continue
        lines.append(raw_line)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _atomic_write_private(path: Path, text: str) -> None:
    parent = path.parent
    existed = parent.exists()
    parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt" and (not existed or parent == default_env_path().parent):
        try:
            os.chmod(parent, 0o700)
        except OSError:
            pass

    fd, tmp = tempfile.mkstemp(dir=str(parent), prefix=".env-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
