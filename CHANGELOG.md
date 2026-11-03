# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-11-02

### Added

- Optional `mcp` extra and `whatsdrop-mcp` stdio server. The server exposes
  read-only `list_chats` and `get_context` tools backed by the existing CLI.
  It works with any MCP-compatible client and does not contact an AI service.

### Removed

- Plaintext archive support, including unkeyed SHA-256 path hashes and the
  unencrypted-archive warning. Archives must now use the encrypted format;
  plaintext format markers are rejected with a clear error.

### Fixed

- MCP text output now escapes line breaks and Unicode line separators from
  archive fields, preventing fake chat headers in tool output.
- The MCP server reports the effective account alias and rejects env files that
  are readable or writable by group or others.

## [0.1.0] - 2026-10-31

### Added

- Initial public release as `whatsdrop-cli`, with the `whatsdrop` command.
- Dropbox OAuth 2.0 authorization using PKCE with offline refresh tokens.
- `auth`, `auth --status`, `auth --status --json` and `auth --revoke` commands.
- `chats`, `query` and `context` commands with name, number, chat-hash and JID
  selectors.
- Date-, hour-, minute- and second-precision windows, plus rolling `--last`
  windows.
- NDJSON, text and CSV output.
- Full-resolution image, sticker and GIF export with an NDJSON manifest.
- Per-chat context chunks with optional context-before and character splitting.
- Private permissions for credentials, output files and exported media.
- Terminal control-character escaping and CSV formula-injection protection.
- Apache-2.0 license and `NOTICE`.

### Changed

- Renamed the project from the internal `wa-archive-cli` name to
  `whatsdrop-cli`.
- Environment variables now use the `WHATSDROP_*` prefix.
- Credentials now default to `~/.config/whatsdrop/credentials.json`.

### Fixed

- The loopback OAuth flow now starts its local HTTP server and rejects
  mismatched OAuth state values.
- `auth --revoke` no longer deletes local credentials when the remote revoke
  fails.
- Custom Dropbox app keys stored in the credentials file are no longer
  overridden by the built-in app key.
- Chats with missing hashes no longer collapse during selector matching.
- Formatted phone numbers are normalized during number matching.
