"""Run the public whatsdrop CLI as a subprocess."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass

from .errors import CliError, UsageError
from .mcp_config import ACCOUNT_RE, DEFAULT_ACCOUNT, McpSettings, load_env_file

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CHAT_HEADER = "=== CHAT "
CHAT_SEPARATOR = " | "
FAILURE_DETAIL_LIMIT = 2000


@dataclass(frozen=True)
class CliResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


@dataclass(frozen=True)
class LimitedText:
    text: str
    truncated: bool
    included_chats: int
    omitted_chats: int


def base_command() -> list[str]:
    return [sys.executable, "-m", "whatsdrop_cli"]


def _base_env(settings: McpSettings) -> dict[str, str]:
    env = dict(os.environ)
    for key, value in load_env_file(settings.env_path).items():
        env.setdefault(key, value)
    if settings.account:
        env.setdefault("WHATSDROP_ACCOUNT", settings.account)
    return env


def build_env(settings: McpSettings) -> dict[str, str]:
    env = _base_env(settings)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    return env


def effective_account(settings: McpSettings) -> str:
    account = (_base_env(settings).get("WHATSDROP_ACCOUNT") or "").strip() or DEFAULT_ACCOUNT
    if not ACCOUNT_RE.match(account):
        raise UsageError("account alias may only contain letters, digits and . _ -")
    return account


def run_cli(args: list[str], settings: McpSettings) -> CliResult:
    command = base_command() + args
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=settings.cli_timeout_seconds,
            env=build_env(settings),
            check=False,
            creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        return CliResult(
            -1,
            _as_text(exc.stdout),
            _as_text(exc.stderr),
            timed_out=True,
        )
    except CliError as exc:
        return CliResult(exc.exit_code, "", str(exc))
    except OSError as exc:
        return CliResult(-1, "", str(exc))
    return CliResult(completed.returncode, completed.stdout, completed.stderr)


def chats_args(
    *,
    names: list[str] | None = None,
    numbers: list[str] | None = None,
    hashes: list[str] | None = None,
    jids: list[str] | None = None,
) -> list[str]:
    args = ["chats", "--quiet"]
    for value in names or []:
        args += ["--name", value]
    for value in numbers or []:
        args += ["--number", value]
    for value in hashes or []:
        args += ["--chat", value]
    for value in jids or []:
        args += ["--jid", value]
    return args


def context_args(
    *,
    last: str | None,
    start: str | None,
    end: str | None,
    context_before: str,
    max_chars_per_chat: int,
    max_parts: int,
    tz: str | None,
    names: list[str] | None = None,
    numbers: list[str] | None = None,
    hashes: list[str] | None = None,
    jids: list[str] | None = None,
) -> list[str]:
    args = [
        "context",
        "--format",
        "text",
        "--quiet",
        "--context-before",
        context_before,
        "--max-chars",
        str(max_chars_per_chat),
        "--max-parts",
        str(max_parts),
    ]
    if last:
        args += ["--last", last]
    elif start:
        args += ["--from", start]
    if end:
        args += ["--to", end]
    if tz:
        args += ["--tz", tz]
    for value in names or []:
        args += ["--name", value]
    for value in numbers or []:
        args += ["--number", value]
    for value in hashes or []:
        args += ["--chat", value]
    for value in jids or []:
        args += ["--jid", value]
    return args


def limit_cli_text(
    text: str,
    *,
    max_chats: int,
    max_total_chars: int,
) -> LimitedText:
    identities = _chat_identities(text)
    if max_chats <= 0 and max_total_chars <= 0:
        return LimitedText(text, False, len(identities), 0)

    blocks = _split_blocks(text)
    if not blocks:
        if max_total_chars > 0 and len(text) > max_total_chars:
            excerpt = _truncate_at_line(text, max_total_chars)
            return LimitedText(
                excerpt + "\n[whatsdrop-mcp: output truncated]\n",
                True,
                0,
                0,
            )
        return LimitedText(text, False, 0, 0)

    allowed = set(identities[:max_chats]) if max_chats > 0 else set(identities)
    selected: list[str] = []
    used = 0
    stopped = False

    for block in blocks:
        identity = _block_identity(block)
        if identity not in allowed:
            continue
        if max_total_chars > 0 and used + len(block) > max_total_chars:
            if not selected:
                excerpt = _truncate_at_line(block, max_total_chars)
                selected.append(excerpt)
                used = len(excerpt)
            stopped = True
            break
        selected.append(block)
        used += len(block)

    included = {_block_identity(block) for block in selected if _block_identity(block)}
    omitted = len([identity for identity in identities if identity not in included])
    truncated = stopped or omitted > 0
    result = "".join(selected)

    if truncated:
        if omitted:
            notice = (
                f"[whatsdrop-mcp: output truncated; omitted {omitted} chat(s). "
                "Narrow the window or raise max_total_chars.]\n"
            )
        else:
            notice = "[whatsdrop-mcp: output truncated; raise max_total_chars.]\n"
        if not result.endswith("\n"):
            result += "\n"
        result += notice
    return LimitedText(result, truncated, len(included), omitted)


def describe_failure(result: CliResult, action: str, timeout_seconds: float) -> str:
    detail = _clean_detail(result.stderr)
    suffix = f" {detail}" if detail else ""

    if result.timed_out:
        return (
            f"The whatsdrop CLI timed out after {timeout_seconds:g}s while trying to "
            f"{action}. Narrow the window, select fewer chats, lower max_parts, "
            f"or raise cli_timeout_seconds.{suffix}"
        )
    if result.returncode == 2:
        return f"WhatsDrop CLI configuration or usage error.{suffix}"
    if result.returncode == 3:
        return (
            "No archived chats or messages matched. Try a longer window or a "
            f"different chat selector.{suffix}"
        )
    if result.returncode == 4:
        return (
            "Too many archive parts would be fetched. Narrow the window or chat "
            f"selection, or lower max_parts.{suffix}"
        )
    if result.returncode == 5:
        return (
            "Could not read the Dropbox archive. Check `whatsdrop auth --status` "
            f"and the archive key.{suffix}"
        )
    if result.returncode == -1:
        return f"Could not start the whatsdrop CLI.{suffix}"
    return f"WhatsDrop CLI failed with exit code {result.returncode}.{suffix}"


def _split_blocks(text: str) -> list[str]:
    blocks = re.split(r"(?=^=== CHAT )", text, flags=re.MULTILINE)
    return [block for block in blocks if block.startswith(CHAT_HEADER)]


def _block_identity(block: str) -> str:
    first_line = block.splitlines()[0] if block else ""
    header = first_line[len(CHAT_HEADER) :] if first_line.startswith(CHAT_HEADER) else first_line
    identity = header.partition(CHAT_SEPARATOR)[0].strip()
    return identity or header.strip()


def _chat_identities(text: str) -> list[str]:
    identities: list[str] = []
    seen: set[str] = set()
    for block in _split_blocks(text):
        identity = _block_identity(block)
        if identity not in seen:
            seen.add(identity)
            identities.append(identity)
    return identities


def _truncate_at_line(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    excerpt = text[:limit]
    if len(text) > limit:
        newline = excerpt.rfind("\n")
        if newline > 0:
            excerpt = excerpt[: newline + 1]
    return excerpt


def _clean_detail(value: str) -> str:
    detail = (value or "").strip()
    if len(detail) > FAILURE_DETAIL_LIMIT:
        detail = detail[:FAILURE_DETAIL_LIMIT].rstrip() + "..."
    return detail


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
