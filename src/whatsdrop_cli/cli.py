"""whatsdrop command line interface (encrypted or plaintext Dropbox archive)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from . import __version__
from .archive import ArchiveCodec, resolve_archive
from .auth import (
    Credentials,
    delete_credentials,
    load_credentials,
    missing_scopes,
    save_credentials,
)
from .auth_flow import run_loopback_flow, run_paste_flow
from .chats import ChatEntry, load_directory, select_chats
from .config import (
    AuthConfig,
    DropboxConfig,
    Window,
    load_config,
    parse_context_before,
    resolve_auth,
    resolve_window,
)
from .context import (
    attach_media_exports,
    build_chunks,
    group_records,
    is_media_record,
    render_chunks_ndjson,
    render_chunks_text,
)
from .dropbox import DropboxClient
from .errors import CliError, NoMatchesError, RemoteError, UsageError
from .fetch import collect, plan_tasks
from .media import export_media, write_manifest
from .output import render, safe_cell, write_lines

CHAT_HEADERS = ["NAME", "PHONE", "TYPE", "HASH", "CHAT ID"]
PLAINTEXT_WARNING = (
    "whatsdrop: warning: archive is unencrypted (plaintext); anyone with Dropbox access can read it"
)


def _add_window_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--from",
        dest="from_text",
        metavar="BOUND",
        help="start bound: YYYY-MM-DD[THH[:MM[:SS]]] (required unless --last)",
    )
    parser.add_argument(
        "--to",
        dest="to_text",
        metavar="BOUND",
        help="end bound (inclusive by precision; default: now)",
    )
    parser.add_argument(
        "--last",
        dest="last_text",
        metavar="DURATION",
        help="relative window, e.g. 90m, 36h, 7d, 2w (excludes --from/--to)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="whatsdrop",
        description="Query an encrypted or plaintext WhatsApp archive stored in a Dropbox app folder.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dropbox-token", help="Dropbox access token (or WHATSDROP_DROPBOX_TOKEN)")
    common.add_argument(
        "--dropbox-refresh-token", help="Dropbox refresh token (or WHATSDROP_DROPBOX_REFRESH_TOKEN)"
    )
    common.add_argument("--dropbox-app-key", help="Dropbox app key (or WHATSDROP_DROPBOX_APP_KEY)")
    common.add_argument(
        "--dropbox-app-secret", help="Dropbox app secret (or WHATSDROP_DROPBOX_APP_SECRET)"
    )
    common.add_argument(
        "--credentials-file", help="Dropbox credentials file (or WHATSDROP_CREDENTIALS)"
    )
    common.add_argument(
        "--key",
        help="archive encryption key for encrypted archives, base64 32 bytes (or WHATSDROP_KEY)",
    )
    common.add_argument(
        "--account", help="account alias folder (or WHATSDROP_ACCOUNT; default: primary)"
    )
    common.add_argument("--folder", help="subfolder inside the app folder (or WHATSDROP_FOLDER)")
    common.add_argument("--tz", help="IANA timezone for date bounds (default: system local)")
    common.add_argument("--timeout", type=float, default=60.0, help="HTTP timeout in seconds")
    common.add_argument(
        "--max-parts", type=int, default=1000, help="abort if more part objects would be fetched"
    )
    common.add_argument("--quiet", action="store_true", help="suppress progress lines on stderr")

    selectors = argparse.ArgumentParser(add_help=False)
    selectors.add_argument(
        "--name",
        action="append",
        default=[],
        metavar="GLOB",
        help="chat name glob/substring (repeatable, case-insensitive)",
    )
    selectors.add_argument(
        "--number",
        action="append",
        default=[],
        metavar="PHONE",
        help="phone number, any format (repeatable)",
    )
    selectors.add_argument(
        "--chat", action="append", default=[], metavar="HASH", help="chat hash folder (repeatable)"
    )
    selectors.add_argument(
        "--jid",
        action="append",
        default=[],
        metavar="JID",
        help="raw chat jid, e.g. 49171234567@c.us (repeatable)",
    )

    subparsers.add_parser(
        "chats",
        parents=[common, selectors],
        help="resolve chats to hashes without fetching messages",
    )

    query = subparsers.add_parser("query", parents=[common, selectors], help="fetch messages")
    _add_window_arguments(query)
    query.add_argument("--format", choices=["ndjson", "text", "csv"], default="ndjson")
    query.add_argument("--out", metavar="FILE", help="write output to a file instead of stdout")
    query.add_argument("--media-out", metavar="DIR", help="export full-resolution media into DIR")
    query.add_argument(
        "--media-manifest",
        metavar="FILE",
        help="manifest path (default: DIR/manifest.ndjson)",
    )
    query.add_argument(
        "--media-max-bytes",
        type=int,
        default=25 * 1024 * 1024,
        help="skip media larger than this many bytes (default: 25 MiB)",
    )
    query.add_argument(
        "--media-fail-fast",
        action="store_true",
        help="abort on the first media export error",
    )

    context = subparsers.add_parser(
        "context", parents=[common, selectors], help="fetch per-chat conversation chunks"
    )
    _add_window_arguments(context)
    context.add_argument(
        "--context-before",
        dest="context_before",
        default="0",
        metavar="DURATION",
        help="include this much before the analysis window as context (default: 0)",
    )
    context.add_argument(
        "--max-chars",
        type=int,
        default=0,
        metavar="N",
        help="split each chat into parts of at most N rendered message characters (default: 0 = no split)",
    )
    context.add_argument("--format", choices=["ndjson", "text"], default="ndjson")
    context.add_argument("--out", metavar="FILE", help="write output to a file instead of stdout")
    context.add_argument(
        "--media-out",
        metavar="DIR",
        help="export images/stickers/GIFs referenced by the chunks into DIR",
    )
    context.add_argument(
        "--media-manifest",
        metavar="FILE",
        help="manifest path (default: DIR/manifest.ndjson)",
    )
    context.add_argument(
        "--media-max-bytes",
        type=int,
        default=25 * 1024 * 1024,
        help="skip media larger than this many bytes (default: 25 MiB)",
    )
    context.add_argument(
        "--media-fail-fast",
        action="store_true",
        help="abort on the first media export error",
    )

    auth = subparsers.add_parser(
        "auth", help="connect to Dropbox (OAuth PKCE) or manage credentials"
    )
    auth.add_argument("--status", action="store_true", help="show account, scopes and token health")
    auth.add_argument(
        "--revoke",
        action="store_true",
        help="revoke the Dropbox token and delete local credentials",
    )
    auth.add_argument(
        "--no-browser", action="store_true", help="do not open a browser; print the URL only"
    )
    auth.add_argument(
        "--loopback",
        action="store_true",
        help="use a localhost callback instead of pasting the code",
    )
    auth.add_argument(
        "--port", type=int, default=53682, help="loopback callback port (default: 53682)"
    )
    auth.add_argument(
        "--code", metavar="CODE", help="authorization code to exchange (non-interactive)"
    )
    auth.add_argument(
        "--json", action="store_true", dest="as_json", help="machine-readable output for --status"
    )
    auth.add_argument("--dropbox-app-key", help="Dropbox app key (or WHATSDROP_DROPBOX_APP_KEY)")
    auth.add_argument(
        "--dropbox-app-secret",
        help="Dropbox app secret (or WHATSDROP_DROPBOX_APP_SECRET); not needed for the built-in app",
    )
    auth.add_argument(
        "--credentials-file", help="Dropbox credentials file (or WHATSDROP_CREDENTIALS)"
    )
    auth.add_argument("--timeout", type=float, default=60.0, help="HTTP timeout in seconds")

    return parser


def _open_archive(args):
    cfg = load_config(args)
    client = DropboxClient(cfg)
    codec = resolve_archive(client, cfg)
    if codec.is_plaintext:
        print(PLAINTEXT_WARNING, file=sys.stderr)
    return cfg, client, codec


def _print_chats(entries: list[ChatEntry]) -> None:
    rows = [
        [
            safe_cell(entry.chat_name),
            safe_cell(entry.chat_phone or "-"),
            "group" if entry.is_group else "dm",
            safe_cell(entry.hash),
            safe_cell(entry.chat_id),
        ]
        for entry in sorted(entries, key=lambda item: item.chat_name.casefold())
    ]
    widths = [len(header) for header in CHAT_HEADERS]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    print(
        "  ".join(header.ljust(widths[index]) for index, header in enumerate(CHAT_HEADERS)).rstrip()
    )
    for row in rows:
        print("  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)).rstrip())


def _has_selectors(args) -> bool:
    return bool(args.name or args.number or args.chat or args.jid)


def _selected_chats(args, entries: list[ChatEntry], codec: ArchiveCodec) -> list[ChatEntry]:
    if _has_selectors(args):
        selected = select_chats(entries, args.name, args.number, args.chat, args.jid, codec)
        if not selected:
            raise NoMatchesError("no chats matched the selectors; try `whatsdrop chats --name '*'`")
        return selected
    if not entries:
        raise NoMatchesError("chat directory is empty (has anything been uploaded yet?)")
    return entries


def _run_chats(args) -> int:
    cfg, client, codec = _open_archive(args)
    entries = load_directory(client, cfg, codec)
    selected = select_chats(entries, args.name, args.number, args.chat, args.jid, codec)

    if not entries:
        raise NoMatchesError("chat directory is empty (has anything been uploaded yet?)")
    if _has_selectors(args) and not selected:
        raise NoMatchesError("no chats matched the selectors; try --name '*' to list everything")

    _print_chats(selected if selected else entries)
    return 0


def _run_query(args) -> int:
    cfg, client, codec = _open_archive(args)
    window = resolve_window(args.from_text, args.to_text, args.last_text, cfg.tz)
    entries = load_directory(client, cfg, codec)
    selected = _selected_chats(args, entries, codec)

    if not args.quiet:
        print(f"whatsdrop: window {window.label}", file=sys.stderr)
        if _has_selectors(args):
            print(f"whatsdrop: {len(selected)} chat(s) matched", file=sys.stderr)
        else:
            print(f"whatsdrop: all chats ({len(selected)})", file=sys.stderr)

    tasks = plan_tasks(client, cfg, codec, selected, window)
    if not args.quiet:
        print(f"whatsdrop: {len(tasks)} part object(s) in range", file=sys.stderr)

    records = collect(client, cfg, codec, tasks, window)
    if not records:
        raise NoMatchesError("no archived messages found for the matched chats in this window")

    lines = render(records, args.format, cfg.tz)
    write_lines(lines, args.out)

    if args.media_out:
        if args.media_max_bytes < 1:
            raise UsageError("--media-max-bytes must be positive")
        entries = export_media(
            client,
            cfg,
            codec,
            records,
            args.media_out,
            max_bytes=args.media_max_bytes,
            fail_fast=args.media_fail_fast,
        )
        manifest = args.media_manifest or str(Path(args.media_out) / "manifest.ndjson")
        write_manifest(manifest, entries)
        exported = sum(1 for entry in entries if entry["status"] == "exported")
        failed = sum(1 for entry in entries if entry["status"] == "failed")
        skipped = sum(1 for entry in entries if entry["status"] == "skipped")
        if not args.quiet:
            print(
                f"whatsdrop: media {exported} exported, {skipped} skipped, {failed} failed "
                f"(manifest: {manifest})",
                file=sys.stderr,
            )

    if not args.quiet:
        print(f"whatsdrop: {len(records)} record(s) written", file=sys.stderr)
    return 0


def _run_context(args) -> int:
    cfg, client, codec = _open_archive(args)
    analysis = resolve_window(args.from_text, args.to_text, args.last_text, cfg.tz)
    context_before = parse_context_before(args.context_before)
    context_ms = int(context_before.total_seconds() * 1000)
    if args.max_chars < 0:
        raise UsageError("--max-chars must be zero or positive")
    if args.media_out and args.media_max_bytes < 1:
        raise UsageError("--media-max-bytes must be positive")

    if context_ms > 0:
        fetch_start = analysis.start - context_before
        fetch_window = Window(
            start_ms=int(fetch_start.timestamp() * 1000),
            end_ms=analysis.end_ms,
            start=fetch_start,
            end=analysis.end,
            label=f"context before {args.context_before} + {analysis.label}",
        )
    else:
        fetch_window = analysis

    entries = load_directory(client, cfg, codec)
    selected = _selected_chats(args, entries, codec)

    if not args.quiet:
        print(f"whatsdrop: analysis window {analysis.label}", file=sys.stderr)
        if context_ms > 0:
            print(f"whatsdrop: fetching context before {args.context_before}", file=sys.stderr)
        if _has_selectors(args):
            print(f"whatsdrop: {len(selected)} chat(s) matched", file=sys.stderr)
        else:
            print(f"whatsdrop: all chats ({len(selected)})", file=sys.stderr)

    tasks = plan_tasks(client, cfg, codec, selected, fetch_window)
    if not args.quiet:
        print(f"whatsdrop: {len(tasks)} part object(s) in range", file=sys.stderr)

    records = collect(client, cfg, codec, tasks, fetch_window)
    threads = group_records(records, selected, analysis)
    if not threads:
        raise NoMatchesError(
            "no archived messages found in the analysis window (context-only records ignored)"
        )

    if args.media_out:
        media_dir = Path(args.media_out).expanduser().resolve()
        media_records = [
            message for thread in threads for message in thread.messages if is_media_record(message)
        ]
        media_entries = export_media(
            client,
            cfg,
            codec,
            media_records,
            media_dir,
            max_bytes=args.media_max_bytes,
            fail_fast=args.media_fail_fast,
        )
        attach_media_exports(threads, media_entries)
        manifest = args.media_manifest or str(media_dir / "manifest.ndjson")
        write_manifest(manifest, media_entries)
        if not args.quiet:
            exported = sum(1 for entry in media_entries if entry["status"] == "exported")
            skipped = sum(1 for entry in media_entries if entry["status"] == "skipped")
            failed = sum(1 for entry in media_entries if entry["status"] == "failed")
            print(
                f"whatsdrop: media {exported} exported, {skipped} skipped, {failed} failed "
                f"(manifest: {manifest})",
                file=sys.stderr,
            )

    chunks = build_chunks(threads, analysis, context_ms, args.max_chars, cfg.tz)
    lines = (
        render_chunks_ndjson(chunks)
        if args.format == "ndjson"
        else render_chunks_text(chunks, cfg.tz)
    )
    write_lines(lines, args.out)

    if not args.quiet:
        in_window = sum(
            1 for thread in threads for message in thread.messages if message["inWindow"]
        )
        context = sum(
            1 for thread in threads for message in thread.messages if not message["inWindow"]
        )
        print(
            f"whatsdrop: {in_window} message(s) in window, {context} context message(s)",
            file=sys.stderr,
        )
        print(f"whatsdrop: {len(chunks)} chunk(s) written", file=sys.stderr)
    return 0


def _auth_config_for_client(auth: AuthConfig) -> DropboxConfig:
    """Build a client config for account/status/revoke (no archive key needed)."""
    return DropboxConfig(
        token=auth.token,
        refresh_token=auth.refresh_token,
        app_key=auth.app_key,
        app_secret=auth.app_secret,
        archive_key="",
        account="primary",
        folder="",
        tz=datetime.now().astimezone().tzinfo,
        timeout=auth.timeout,
        max_parts=1000,
        api_base=auth.api_base,
        content_base=auth.content_base,
        token_url=auth.token_url,
        revoke_url=auth.revoke_url,
        credentials_path=auth.credentials_path,
        from_file=auth.from_file,
        token_expires_at=auth.token_expires_at,
    )


def _run_auth(args) -> int:
    if args.revoke:
        return _auth_revoke(args)
    if args.status:
        return _auth_status(args)
    return _auth_login(args)


def _auth_login(args) -> int:
    auth = resolve_auth(args)
    if not auth.app_key:
        raise UsageError(
            "no Dropbox app key configured; set WHATSDROP_DROPBOX_APP_KEY or --dropbox-app-key"
        )
    if args.dropbox_app_secret:
        print(
            "whatsdrop: warning: --dropbox-app-secret is not needed for the built-in app and is never stored",
            file=sys.stderr,
        )

    open_browser = not args.no_browser
    if args.loopback:
        parsed = run_loopback_flow(
            app_key=auth.app_key,
            token_url=auth.token_url,
            port=args.port,
            client_secret=auth.app_secret or None,
            open_browser=open_browser,
            timeout=max(auth.timeout * 5, 120.0),
            print_fn=lambda message: print(message, file=sys.stderr),
        )
    else:
        parsed = run_paste_flow(
            app_key=auth.app_key,
            token_url=auth.token_url,
            client_secret=auth.app_secret or None,
            open_browser=open_browser,
            code=args.code,
            print_fn=lambda message: print(message, file=sys.stderr),
            timeout=auth.timeout,
        )

    missing = missing_scopes(parsed["scope"])
    if missing:
        raise UsageError(
            f"Dropbox authorized but missing required scopes: {', '.join(missing)}. "
            "Enable them in the App Console and run `whatsdrop auth` again."
        )
    if not parsed.get("refresh_token"):
        raise RemoteError(
            "Dropbox did not return a refresh token; the app must allow offline access (token_access_type=offline)"
        )

    creds = Credentials(
        app_key=auth.app_key,
        refresh_token=parsed["refresh_token"],
        access_token=parsed["access_token"],
        expires_at=parsed["expires_at"],
        scope=parsed["scope"],
        account_id=parsed.get("account_id"),
        obtained_at=datetime.now().astimezone().isoformat(),
    )

    try:
        client = DropboxClient(_auth_config_for_client(auth))
        account = client.get_current_account()
        name = account.get("name") or {}
        creds.account_name = name.get("display_name") or name.get("given_name")
        creds.email = account.get("email")
    except RemoteError:
        pass

    save_credentials(auth.credentials_path, creds)
    who = creds.account_name or creds.email or creds.account_id or "Dropbox"
    print(f"whatsdrop: connected as {who}")
    print(f"whatsdrop: credentials stored at {auth.credentials_path}")
    return 0


def _auth_status(args) -> int:
    auth = resolve_auth(args)
    if not (auth.token or (auth.refresh_token and auth.app_key)):
        raise UsageError("Dropbox is not connected. Run `whatsdrop auth`.")

    client = DropboxClient(_auth_config_for_client(auth))
    account = client.get_current_account()
    name = account.get("name") or {}
    account_name = name.get("display_name") or name.get("given_name")

    creds = load_credentials(auth.credentials_path) if auth.from_file else None
    scopes = (creds.scope if creds else None) or []
    missing = missing_scopes(scopes) if scopes else []

    if args.as_json:
        payload = {
            "ok": not missing,
            "appKey": auth.app_key,
            "account": {
                "name": account_name,
                "email": account.get("email"),
                "accountId": account.get("account_id"),
            },
            "scopes": scopes,
            "missingScopes": missing,
            "credentialsPath": auth.credentials_path if creds else None,
        }
        print(json.dumps(payload, indent=2))
    else:
        who = account_name or account.get("email") or account.get("account_id") or "Dropbox"
        print(f"Dropbox: connected as {who}")
        if scopes:
            print(f"Scopes: {' '.join(scopes)}")
        if missing:
            print(f"Missing required scopes: {' '.join(missing)}", file=sys.stderr)
        if creds:
            print(f"Credentials: {auth.credentials_path}")
    return 0 if not missing else 2


def _auth_revoke(args) -> int:
    auth = resolve_auth(args)
    attempted = bool(auth.token or (auth.refresh_token and auth.app_key))
    revoked = False
    revoke_error: RemoteError | None = None
    if attempted:
        try:
            client = DropboxClient(_auth_config_for_client(auth))
            revoked = client.revoke()
        except RemoteError as exc:
            revoke_error = exc
            print(f"whatsdrop: {exc}", file=sys.stderr)

    if revoke_error is not None:
        print(
            "whatsdrop: could not revoke Dropbox access; local credentials were kept",
            file=sys.stderr,
        )
        return revoke_error.exit_code

    deleted = delete_credentials(auth.credentials_path)
    if revoked:
        print("whatsdrop: revoked Dropbox access and removed local credentials")
    elif deleted:
        print(
            "whatsdrop: token already invalid; removed local credentials "
            "(revoke in Dropbox settings if needed)",
            file=sys.stderr,
        )
    else:
        print("whatsdrop: no Dropbox credentials to revoke", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "auth":
            return _run_auth(args)
        if args.command == "chats":
            return _run_chats(args)
        if args.command == "context":
            return _run_context(args)
        return _run_query(args)
    except CliError as exc:
        print(f"whatsdrop: {exc}", file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
