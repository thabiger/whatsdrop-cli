"""Configuration, bound parsing and time-window resolution."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo

from .auth import (
    DEFAULT_API_BASE,
    DEFAULT_APP_KEY,
    DEFAULT_CONTENT_BASE,
    DEFAULT_REVOKE_URL,
    DEFAULT_TOKEN_URL,
    load_credentials,
    resolve_credentials_path,
)
from .crypto import is_valid_key
from .errors import UsageError

BOUND_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})"
    r"(?:[T ](\d{2})(?::(\d{2})(?::(\d{2}))?)?)?$"
)
DURATION_RE = re.compile(r"^(\d+)([mhdw])$")
ACCOUNT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

PRECISION_UNITS = {
    "date": "days",
    "hour": "hours",
    "minute": "minutes",
    "second": "seconds",
}

DEFAULT_ACCOUNT = "primary"


@dataclass(frozen=True)
class Window:
    start_ms: int  # inclusive
    end_ms: int  # exclusive
    start: datetime
    end: datetime
    label: str


@dataclass(frozen=True)
class AuthConfig:
    """Resolved Dropbox credentials (no archive key required)."""

    app_key: str | None
    token: str | None
    refresh_token: str | None
    app_secret: str | None
    credentials_path: str
    timeout: float
    api_base: str = DEFAULT_API_BASE
    content_base: str = DEFAULT_CONTENT_BASE
    token_url: str = DEFAULT_TOKEN_URL
    revoke_url: str = DEFAULT_REVOKE_URL
    from_file: bool = False
    token_expires_at: float | None = None


@dataclass(frozen=True)
class DropboxConfig:
    token: str | None
    refresh_token: str | None
    app_key: str | None
    app_secret: str | None
    archive_key: str
    account: str
    folder: str
    tz: tzinfo
    timeout: float
    max_parts: int
    api_base: str = DEFAULT_API_BASE
    content_base: str = DEFAULT_CONTENT_BASE
    token_url: str = DEFAULT_TOKEN_URL
    revoke_url: str = DEFAULT_REVOKE_URL
    credentials_path: str | None = None
    from_file: bool = False
    token_expires_at: float | None = None

    @property
    def account_root(self) -> str:
        return f"{self.folder}/account={self.account}"


def _dropbox_urls(base: str) -> dict:
    if base:
        return {
            "api_base": f"{base}/2",
            "content_base": f"{base}/2",
            "token_url": f"{base}/oauth2/token",
            "revoke_url": f"{base}/2/auth/token/revoke",
        }
    return {}


def resolve_auth(args) -> AuthConfig:
    """Resolve Dropbox credentials from flags, env, then the local file.

    Precedence: CLI flag > environment variable > credentials file. An explicit
    access or refresh token (flag/env) disables file lookups so secrets injected
    by a host are never overwritten.
    """

    def value(flag: str, env: str) -> str:
        return str(getattr(args, flag, None) or os.environ.get(env) or "").strip()

    app_key = value("dropbox_app_key", "WHATSDROP_DROPBOX_APP_KEY") or None
    token = value("dropbox_token", "WHATSDROP_DROPBOX_TOKEN") or None
    refresh_token = value("dropbox_refresh_token", "WHATSDROP_DROPBOX_REFRESH_TOKEN") or None
    app_secret = value("dropbox_app_secret", "WHATSDROP_DROPBOX_APP_SECRET") or None

    timeout = getattr(args, "timeout", None)
    timeout = 60.0 if timeout is None else float(timeout)

    credentials_path = resolve_credentials_path(args)
    from_file = False
    token_expires_at: float | None = None
    if token is None and refresh_token is None:
        creds = load_credentials(credentials_path)
        if creds is not None:
            token = creds.access_token
            refresh_token = creds.refresh_token or None
            token_expires_at = creds.expires_at
            if app_key is None:
                app_key = creds.app_key or None
            from_file = True

    if app_key is None:
        app_key = DEFAULT_APP_KEY or None

    base = (os.environ.get("WHATSDROP_DROPBOX_BASE") or "").strip().rstrip("/")

    return AuthConfig(
        app_key=app_key,
        token=token,
        refresh_token=refresh_token,
        app_secret=app_secret,
        credentials_path=str(credentials_path),
        timeout=timeout,
        from_file=from_file,
        token_expires_at=token_expires_at,
        **_dropbox_urls(base),
    )


def parse_tz(name: str | None) -> tzinfo:
    if not name:
        return datetime.now().astimezone().tzinfo
    try:
        return ZoneInfo(name)
    except Exception as exc:
        raise UsageError(f"unknown timezone: {name}") from exc


def _normalize_folder(value: str | None) -> str:
    folder = str(value or "").strip().strip("/")
    return f"/{folder}" if folder else ""


def load_config(args) -> DropboxConfig:
    auth = resolve_auth(args)

    if not auth.token and not (auth.refresh_token and auth.app_key):
        raise UsageError(
            "Dropbox is not connected. Run `whatsdrop auth` to connect, or set "
            "WHATSDROP_DROPBOX_TOKEN (or WHATSDROP_DROPBOX_REFRESH_TOKEN + "
            "WHATSDROP_DROPBOX_APP_KEY) for testing."
        )

    def value(flag: str, env: str) -> str:
        return str(getattr(args, flag, None) or os.environ.get(env) or "").strip()

    archive_key = value("key", "WHATSDROP_KEY")
    if not archive_key:
        raise UsageError("archive encryption key required (--key or WHATSDROP_KEY)")
    if not is_valid_key(archive_key):
        raise UsageError("archive encryption key must be 32 bytes encoded as base64")

    account = value("account", "WHATSDROP_ACCOUNT") or DEFAULT_ACCOUNT
    if not ACCOUNT_RE.match(account):
        raise UsageError("account alias may only contain letters, digits and . _ -")

    folder = _normalize_folder(value("folder", "WHATSDROP_FOLDER"))

    if auth.timeout <= 0:
        raise UsageError("--timeout must be positive")

    max_parts = getattr(args, "max_parts", 1000)
    if not isinstance(max_parts, int) or max_parts < 1:
        raise UsageError("--max-parts must be a positive integer")

    return DropboxConfig(
        token=auth.token,
        refresh_token=auth.refresh_token,
        app_key=auth.app_key,
        app_secret=auth.app_secret,
        archive_key=archive_key,
        account=account,
        folder=folder,
        tz=parse_tz(getattr(args, "tz", None)),
        timeout=auth.timeout,
        max_parts=max_parts,
        api_base=auth.api_base,
        content_base=auth.content_base,
        token_url=auth.token_url,
        revoke_url=auth.revoke_url,
        credentials_path=auth.credentials_path,
        from_file=auth.from_file,
        token_expires_at=auth.token_expires_at,
    )


def parse_bound(text: str, tz: tzinfo) -> tuple[datetime, str]:
    """Parse a bound and return (datetime, precision).

    Accepted: YYYY-MM-DD, YYYY-MM-DDTHH, YYYY-MM-DDTHH:MM,
    YYYY-MM-DDTHH:MM:SS (space separator also allowed).
    """
    match = BOUND_RE.match((text or "").strip())
    if not match:
        raise UsageError(
            f"invalid date/time {text!r}; use YYYY-MM-DD, YYYY-MM-DDTHH, "
            "YYYY-MM-DDTHH:MM or YYYY-MM-DDTHH:MM:SS"
        )
    year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    hour = int(match.group(4) or 0)
    minute = int(match.group(5) or 0)
    second = int(match.group(6) or 0)
    try:
        moment = datetime(year, month, day, hour, minute, second, tzinfo=tz)
    except ValueError as exc:
        raise UsageError(f"invalid date/time {text!r}: {exc}") from exc

    if match.group(6) is not None:
        precision = "second"
    elif match.group(5) is not None:
        precision = "minute"
    elif match.group(4) is not None:
        precision = "hour"
    else:
        precision = "date"
    return moment, precision


def _precision_delta(precision: str) -> timedelta:
    return timedelta(**{PRECISION_UNITS[precision]: 1})


def snap_start(moment: datetime, precision: str) -> datetime:
    if precision == "date":
        return moment.replace(hour=0, minute=0, second=0, microsecond=0)
    if precision == "hour":
        return moment.replace(minute=0, second=0, microsecond=0)
    if precision == "minute":
        return moment.replace(second=0, microsecond=0)
    return moment.replace(microsecond=0)


def snap_end_exclusive(moment: datetime, precision: str) -> datetime:
    return snap_start(moment, precision) + _precision_delta(precision)


def parse_duration(text: str) -> timedelta:
    match = DURATION_RE.match((text or "").strip().lower())
    if not match:
        raise UsageError(f"invalid --last {text!r}; use e.g. 90m, 36h, 7d, 2w")
    amount = int(match.group(1))
    if amount < 1:
        raise UsageError("--last must be at least 1 unit")
    unit = match.group(2)
    unit_name = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}[unit]
    return timedelta(**{unit_name: amount})


def parse_context_before(text: str | None) -> timedelta:
    value = (text or "").strip().lower()
    if value in ("", "0"):
        return timedelta(0)
    match = DURATION_RE.match(value)
    if not match:
        raise UsageError(f"invalid --context-before {text!r}; use 0 or e.g. 30m, 36h, 7d, 2w")
    amount = int(match.group(1))
    unit = match.group(2)
    unit_name = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}[unit]
    return timedelta(**{unit_name: amount})


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def resolve_window(
    from_text: str | None,
    to_text: str | None,
    last_text: str | None,
    tz: tzinfo,
    now: datetime | None = None,
) -> Window:
    now = now or datetime.now(tz)

    if last_text:
        if from_text or to_text:
            raise UsageError("--last cannot be combined with --from/--to")
        duration = parse_duration(last_text)
        end = now
        start = end - duration
        label = f"last {last_text} ({start.isoformat()} .. {end.isoformat()})"
        return Window(_ms(start), _ms(end) + 1, start, end, label)

    if not from_text:
        raise UsageError("provide --from or --last")

    start_dt, start_precision = parse_bound(from_text, tz)
    start = snap_start(start_dt, start_precision)

    if to_text:
        to_dt, to_precision = parse_bound(to_text, tz)
        end = snap_end_exclusive(to_dt, to_precision)
    else:
        end = now

    if end <= start:
        raise UsageError("--to must be after --from")

    label = f"{start.isoformat()} .. {end.isoformat()} (end exclusive)"
    return Window(_ms(start), _ms(end), start, end, label)
