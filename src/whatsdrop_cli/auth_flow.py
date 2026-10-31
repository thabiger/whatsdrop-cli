"""Interactive Dropbox authorization flows: paste-code and loopback.

The paste flow is the default: it needs no redirect URI registration and works
over SSH and on headless hosts (the user opens the URL anywhere and pastes the
code back). The loopback flow is optional and gives a smoother desktop
experience; its redirect URI must be registered in the App Console.
"""

from __future__ import annotations

import hmac
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .auth import (
    CLI_SCOPES,
    build_authorize_url,
    exchange_code,
    generate_pkce,
    parse_token_document,
)
from .errors import RemoteError, UsageError

LOOPBACK_PORT = 53682
LOOPBACK_PATH = "/callback"


def _open_browser(url: str, open_browser: bool) -> None:
    if not open_browser:
        return
    try:
        webbrowser.open(url)
    except Exception:
        pass


def run_paste_flow(
    *,
    app_key: str,
    token_url: str,
    client_secret: str | None = None,
    open_browser: bool = True,
    code: str | None = None,
    input_fn=input,
    print_fn=print,
    timeout: float = 60.0,
) -> dict:
    verifier, challenge = generate_pkce()
    url = build_authorize_url(app_key, code_challenge=challenge, scopes=CLI_SCOPES)

    print_fn("Connect WhatsDrop to Dropbox:")
    print_fn("  1. Open this URL in a browser:")
    print_fn(f"     {url}")
    print_fn('  2. Click "Allow", then copy the authorization code Dropbox shows.')

    if code is None and not sys.stdin.isatty():
        raise UsageError(
            "cannot prompt for the authorization code in a non-interactive session; "
            "pass --code <CODE> or use --loopback"
        )
    _open_browser(url, open_browser)

    if code is None:
        code = input_fn("  Paste the authorization code: ").strip()
    if not code:
        raise UsageError("no authorization code provided")

    document = exchange_code(
        app_key=app_key,
        code=code,
        code_verifier=verifier,
        token_url=token_url,
        client_secret=client_secret,
        timeout=timeout,
    )
    return parse_token_document(document)


def _result_page(ok: bool) -> str:
    if ok:
        body = "WhatsDrop connected to Dropbox. You can close this tab and return to the terminal."
    else:
        body = "WhatsDrop could not complete the connection. Check the terminal for details."
    return f"<!doctype html><html><head><meta charset=utf-8></head><body><h1>WhatsDrop</h1><p>{body}</p></body></html>"


def run_loopback_flow(
    *,
    app_key: str,
    token_url: str,
    port: int = LOOPBACK_PORT,
    client_secret: str | None = None,
    open_browser: bool = True,
    timeout: float = 300.0,
    print_fn=print,
) -> dict:
    verifier, challenge = generate_pkce()
    state = secrets.token_urlsafe(24)
    redirect_uri = f"http://127.0.0.1:{port}{LOOPBACK_PATH}"
    url = build_authorize_url(
        app_key, code_challenge=challenge, redirect_uri=redirect_uri, state=state, scopes=CLI_SCOPES
    )

    result: dict = {"code": None, "error": None, "state_ok": False}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence request logging
            return

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path != LOOPBACK_PATH:
                self.send_response(404)
                self.end_headers()
                return
            query = parse_qs(parsed.query)
            got_state = (query.get("state") or [""])[0]
            state_ok = hmac.compare_digest(got_state, state)
            result["state_ok"] = state_ok
            if not state_ok:
                self.send_response(400)
                self.send_header("content-type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(_result_page(False).encode("utf-8"))
                return
            if "code" in query:
                result["code"] = query["code"][0]
            elif "error" in query:
                description = (query.get("error_description") or [""])[0]
                result["error"] = f"{query['error'][0]} {description}".strip()
            else:
                result["error"] = "missing authorization code"
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_result_page(result["code"] is not None).encode("utf-8"))
            done.set()

    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        raise UsageError(
            f"cannot bind the loopback callback on 127.0.0.1:{port} ({exc}); "
            "pick another --port or use the paste flow"
        ) from exc

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        print_fn(
            f"Waiting for Dropbox authorization at {redirect_uri} (timeout {int(timeout)}s)..."
        )
        _open_browser(url, open_browser)
        if not done.wait(timeout):
            raise UsageError(
                "timed out waiting for Dropbox authorization; run again or use the paste flow"
            )
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)

    if not result["state_ok"]:
        raise RemoteError(
            "authorization state mismatch (possible CSRF); run `whatsdrop auth` again"
        )
    if result["error"]:
        raise UsageError(f"Dropbox authorization failed: {result['error']}")

    document = exchange_code(
        app_key=app_key,
        code=result["code"],
        code_verifier=verifier,
        token_url=token_url,
        redirect_uri=redirect_uri,
        client_secret=client_secret,
        timeout=60.0,
    )
    return parse_token_document(document)
