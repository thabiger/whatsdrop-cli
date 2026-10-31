"""Minimal Dropbox API client (stdlib urllib)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .auth import (
    EXPIRY_SKEW_SECONDS,
    load_credentials,
    parse_token_document,
    refresh_access_token,
    save_credentials,
)
from .config import DropboxConfig
from .errors import RemoteError


class DropboxClient:
    def __init__(self, config: DropboxConfig):
        self.config = config
        self._access_token = config.token
        self._expires_at: float | None = config.token_expires_at

    # --- auth ---------------------------------------------------------------
    def _refresh(self) -> None:
        if not (self.config.refresh_token and self.config.app_key):
            raise RemoteError(
                "Dropbox access token expired and no refresh credentials are configured; "
                "run `whatsdrop auth` to connect"
            )
        document = refresh_access_token(
            app_key=self.config.app_key,
            refresh_token=self.config.refresh_token,
            token_url=self.config.token_url,
            client_secret=self.config.app_secret or None,
            timeout=self.config.timeout,
        )
        parsed = parse_token_document(document)
        self._access_token = parsed["access_token"]
        self._expires_at = parsed["expires_at"]
        self._persist(parsed)

    def _persist(self, parsed: dict) -> None:
        """Write a refreshed access token back to the credentials file (if any)."""
        if not (self.config.from_file and self.config.credentials_path):
            return
        creds = load_credentials(self.config.credentials_path)
        if creds is None:
            return
        creds.access_token = parsed["access_token"]
        creds.expires_at = parsed["expires_at"]
        if parsed.get("refresh_token"):
            creds.refresh_token = parsed["refresh_token"]
        save_credentials(self.config.credentials_path, creds)

    def _token(self) -> str:
        expired = (
            self._expires_at is not None and time.time() >= self._expires_at - EXPIRY_SKEW_SECONDS
        )
        if self._access_token and not expired:
            return self._access_token
        if self.config.refresh_token and self.config.app_key:
            self._refresh()
            return self._access_token or ""
        if self._access_token:
            return self._access_token
        raise RemoteError("no Dropbox access token available")

    # --- transport ----------------------------------------------------------
    def _send(self, url: str, data: bytes, extra_headers: dict, retry: bool = True):
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"authorization": f"Bearer {self._token()}", **extra_headers},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                return response.read(), None
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and retry and self.config.refresh_token and self.config.app_key:
                detail = _body(exc)
                if "missing_scope" not in detail:
                    self._refresh()
                    return self._send(url, data, extra_headers, retry=False)
            return None, exc
        except urllib.error.URLError as exc:
            raise RemoteError(f"cannot reach Dropbox: {exc.reason}") from exc

    def _rpc(self, path: str, payload: dict, allow_not_found: bool = False):
        data, error = self._send(
            self.config.api_base + path,
            json.dumps(payload).encode("utf-8"),
            {"content-type": "application/json"},
        )
        if error is None:
            try:
                return json.loads(data.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise RemoteError("Dropbox returned an invalid JSON response") from exc
        detail = _body(error)
        if error.code == 401:
            raise RemoteError(
                _scope_guidance(detail) or f"Dropbox authorization failed: HTTP 401 {detail}"
            )
        if error.code == 409 and allow_not_found and "not_found" in detail:
            return None
        if error.code == 429:
            retry_after = error.headers.get("retry-after") if error.headers else None
            raise RemoteError(f"Dropbox rate limit (retry after {retry_after or '?'}s)")
        raise RemoteError(f"Dropbox API error HTTP {error.code}: {detail}")

    # --- account / lifecycle ------------------------------------------------
    def ensure_access_token(self) -> str:
        return self._token()

    def get_current_account(self) -> dict:
        data, error = self._send(
            self.config.api_base + "/users/get_current_account",
            b"null",
            {"content-type": "application/json"},
        )
        if error is None:
            try:
                return json.loads(data.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise RemoteError("Dropbox returned an invalid JSON response") from exc
        detail = _body(error)
        if error.code == 401:
            raise RemoteError(
                _scope_guidance(detail) or f"Dropbox authorization failed: HTTP 401 {detail}"
            )
        raise RemoteError(f"Dropbox account lookup failed: HTTP {error.code} {detail}")

    def revoke(self) -> bool:
        """Revoke the current access token (and its refresh token). Returns True on success."""
        token = self._token()
        request = urllib.request.Request(
            self.config.revoke_url,
            method="POST",
            headers={"authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout):
                return True
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 401):
                return False
            raise RemoteError(f"Dropbox token revoke failed: HTTP {exc.code} {_body(exc)}") from exc
        except urllib.error.URLError as exc:
            raise RemoteError(f"cannot reach Dropbox: {exc.reason}") from exc

    # --- endpoints ----------------------------------------------------------
    def list_folder(self, path: str, missing_ok: bool = True) -> list[dict]:
        document = self._rpc(
            "/files/list_folder", {"path": path, "recursive": False}, allow_not_found=missing_ok
        )
        if document is None:
            return []
        entries = list(document.get("entries") or [])
        while document.get("has_more"):
            document = self._rpc("/files/list_folder/continue", {"cursor": document.get("cursor")})
            entries.extend(document.get("entries") or [])
        return entries

    def download(self, path: str) -> bytes | None:
        data, error = self._send(
            self.config.content_base + "/files/download",
            b"",
            {"dropbox-api-arg": json.dumps({"path": path})},
        )
        if error is None:
            return data
        detail = _body(error)
        if error.code == 409 and "not_found" in detail:
            return None
        if error.code == 401:
            raise RemoteError(
                _scope_guidance(detail) or f"Dropbox authorization failed: HTTP 401 {detail}"
            )
        raise RemoteError(f"Dropbox download failed: HTTP {error.code} {detail}")


def _body(exc: urllib.error.HTTPError) -> str:
    cached = getattr(exc, "_cached_body", None)
    if cached is not None:
        return cached
    try:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
    except Exception:
        detail = ""
    exc._cached_body = detail
    return detail


def _scope_guidance(detail: str) -> str | None:
    try:
        document = json.loads(detail)
    except json.JSONDecodeError:
        return None
    error = document.get("error")
    summary = str(document.get("error_summary") or "")
    if isinstance(error, dict) and (
        error.get(".tag") == "missing_scope" or "missing_scope" in summary
    ):
        scope = error.get("required_scope")
        if isinstance(scope, list):
            scope = ", ".join(str(item) for item in scope)
        return (
            f"Dropbox token is missing required scope {scope or 'unknown'}; "
            "enable it in the Dropbox app console and re-authorize"
        )
    return None
