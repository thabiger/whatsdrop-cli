"""Dropbox OAuth (PKCE, offline access) and local credential storage.

The CLI is a public client: it ships only the app key (client id), never a
client secret. Each user authorizes once; we store a long-lived refresh token
and mint short-lived access tokens on demand. The archive lives in the Dropbox
App Folder, which is namespaced per app, so the CLI must use the same app key
as the extension to see the same folder.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .errors import RemoteError

AUTHORIZE_URL = "https://www.dropbox.com/oauth2/authorize"
DEFAULT_TOKEN_URL = "https://api.dropboxapi.com/oauth2/token"
DEFAULT_REVOKE_URL = "https://api.dropboxapi.com/2/auth/token/revoke"
DEFAULT_API_BASE = "https://api.dropboxapi.com/2"
DEFAULT_CONTENT_BASE = "https://content.dropboxapi.com/2"

# Read-only scopes for the CLI. The extension additionally requests
# files.content.write so it can upload the archive.
CLI_SCOPES = ("account_info.read", "files.metadata.read", "files.content.read")
REQUIRED_SCOPES = ("files.metadata.read", "files.content.read")

# Set at release; must match the extension's DEFAULT_DROPBOX_APP_KEY. The app
# key is a public client id and is safe to ship. Never ship a client secret.
DEFAULT_APP_KEY = "ydtsl7okrf4xuee"

EXPIRY_SKEW_SECONDS = 60
CREDENTIALS_VERSION = 1


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) using S256."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, _b64url(digest)


def build_authorize_url(
    app_key: str,
    *,
    code_challenge: str,
    redirect_uri: str | None = None,
    state: str | None = None,
    scopes: tuple[str, ...] = CLI_SCOPES,
) -> str:
    params = {
        "client_id": app_key,
        "response_type": "code",
        "token_access_type": "offline",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "scope": " ".join(scopes),
    }
    if redirect_uri:
        params["redirect_uri"] = redirect_uri
    if state:
        params["state"] = state
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def _read_error_body(exc: urllib.error.HTTPError) -> str:
    cached = getattr(exc, "_cached_body", None)
    if cached is not None:
        return cached
    try:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
    except Exception:
        detail = ""
    exc._cached_body = detail
    return detail


def _token_error(detail: str, context: str) -> RemoteError:
    try:
        document = json.loads(detail)
    except (json.JSONDecodeError, TypeError):
        return RemoteError(f"Dropbox token request failed: {detail or 'unknown error'}")
    code = document.get("error")
    description = document.get("error_description") or ""
    if code == "invalid_grant":
        if context == "refresh token":
            return RemoteError(
                "Dropbox refresh token was revoked or expired; run `whatsdrop auth` again"
            )
        return RemoteError(
            "the authorization code expired or was already used; run `whatsdrop auth` again"
        )
    if code in ("invalid_scope", "missing_scope"):
        return RemoteError(
            f"Dropbox scope error ({code}): {description or 'enable the required scopes in the App Console'}"
        )
    if code == "unauthorized_client":
        return RemoteError(
            "Dropbox rejected the app key; check WHATSDROP_DROPBOX_APP_KEY / --dropbox-app-key"
        )
    message = f"Dropbox token request failed: {code or 'unknown'}"
    if description:
        message += f" ({description})"
    return RemoteError(message)


def _token_request(token_url: str, payload: dict, timeout: float, context: str) -> dict:
    request = urllib.request.Request(
        token_url,
        data=urllib.parse.urlencode(payload).encode("utf-8"),
        method="POST",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise _token_error(_read_error_body(exc), context) from exc
    except urllib.error.URLError as exc:
        raise RemoteError(f"cannot reach Dropbox: {exc.reason}") from exc


def exchange_code(
    *,
    app_key: str,
    code: str,
    code_verifier: str,
    token_url: str = DEFAULT_TOKEN_URL,
    redirect_uri: str | None = None,
    client_secret: str | None = None,
    timeout: float = 60.0,
) -> dict:
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": app_key,
        "code_verifier": code_verifier,
    }
    if redirect_uri:
        payload["redirect_uri"] = redirect_uri
    if client_secret:
        payload["client_secret"] = client_secret
    return _token_request(token_url, payload, timeout, context="authorization code")


def refresh_access_token(
    *,
    app_key: str,
    refresh_token: str,
    token_url: str = DEFAULT_TOKEN_URL,
    client_secret: str | None = None,
    timeout: float = 60.0,
) -> dict:
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": app_key,
    }
    if client_secret:
        payload["client_secret"] = client_secret
    return _token_request(token_url, payload, timeout, context="refresh token")


def _split_scope(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return str(value).split()


def parse_token_document(document: dict) -> dict:
    access_token = document.get("access_token")
    if not access_token:
        raise RemoteError("Dropbox token response contained no access token")
    expires_in = document.get("expires_in")
    return {
        "access_token": access_token,
        "refresh_token": document.get("refresh_token"),
        "expires_at": time.time() + float(expires_in) if expires_in else None,
        "scope": _split_scope(document.get("scope")),
        "account_id": document.get("account_id"),
    }


def missing_scopes(granted, required: tuple[str, ...] = REQUIRED_SCOPES) -> list[str]:
    granted_set = set(granted or [])
    return [scope for scope in required if scope not in granted_set]


@dataclass
class Credentials:
    app_key: str
    refresh_token: str
    access_token: str | None = None
    expires_at: float | None = None
    scope: list[str] = field(default_factory=list)
    account_id: str | None = None
    account_name: str | None = None
    email: str | None = None
    obtained_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "version": CREDENTIALS_VERSION,
            "app_key": self.app_key,
            "refresh_token": self.refresh_token,
            "access_token": self.access_token,
            "expires_at": self.expires_at,
            "scope": list(self.scope),
            "account_id": self.account_id,
            "account_name": self.account_name,
            "email": self.email,
            "obtained_at": self.obtained_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Credentials:
        return cls(
            app_key=str(data["app_key"]),
            refresh_token=str(data.get("refresh_token") or ""),
            access_token=data.get("access_token"),
            expires_at=float(data["expires_at"]) if data.get("expires_at") is not None else None,
            scope=_split_scope(data.get("scope")),
            account_id=data.get("account_id"),
            account_name=data.get("account_name"),
            email=data.get("email"),
            obtained_at=data.get("obtained_at"),
        )


def default_credentials_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home())
        return base / "whatsdrop" / "credentials.json"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "whatsdrop" / "credentials.json"


def resolve_credentials_path(args) -> Path:
    explicit = getattr(args, "credentials_file", None) or os.environ.get("WHATSDROP_CREDENTIALS")
    if explicit:
        return Path(str(explicit)).expanduser()
    return default_credentials_path()


def load_credentials(path) -> Credentials | None:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("version") != CREDENTIALS_VERSION:
        return None
    try:
        return Credentials.from_dict(data)
    except (KeyError, TypeError, ValueError):
        return None


def save_credentials(path, creds: Credentials) -> None:
    path = Path(path)
    parent = path.parent
    existed = parent.exists()
    parent.mkdir(parents=True, exist_ok=True)
    if not existed or parent == default_credentials_path().parent:
        try:
            os.chmod(parent, 0o700)
        except OSError:
            pass
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".credentials-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(creds.to_dict(), handle, indent=2)
            handle.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def delete_credentials(path) -> bool:
    try:
        Path(path).unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False
