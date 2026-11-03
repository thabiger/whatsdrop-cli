# MCP server

`whatsdrop-mcp` is an optional local
[MCP](https://modelcontextprotocol.io/) stdio server that exposes the WhatsDrop
archive through two read-only tools:

- `list_chats`
- `get_context`

It is generic: it works with any MCP-compatible client, including local model
hosts. The server does not contact an AI service. It reads the archive from
Dropbox and returns selected text to the connected MCP client over local stdio.
If the client is cloud-backed, that client may transmit the data elsewhere; use
a local client for fully local operation.

The server is text-only in this version. It does not send messages, export
media, modify the archive, or change authentication.

## Install

```sh
pip install "whatsdrop-cli[mcp]"
```

Other installers:

```sh
uv tool install "whatsdrop-cli[mcp]"
pipx install "whatsdrop-cli[mcp]"
```

The `whatsdrop` command and the base package are unchanged when the `mcp` extra
is not installed. From a source checkout, use `uv sync --extra mcp`.

## Prerequisites

1. Connect the CLI:

   ```sh
   whatsdrop auth
   ```

2. Place the archive key in the CLI environment file. The MCP server reads it
   automatically when `WHATSDROP_KEY` is not already set:

   ```sh
   # ~/.config/whatsdrop/env  (must be 0600)
   WHATSDROP_KEY=<base64 archive key from the extension>
   WHATSDROP_ACCOUNT=primary
   ```

   On Windows the default location is `%APPDATA%\whatsdrop\env`. On macOS and
   Linux the file must not be readable or writable by group or others, and only
   `WHATSDROP_*` keys are read. The server rejects insecure env files.

   Only encrypted archives are supported.

## Register with an MCP client

Print a generic configuration snippet:

```sh
whatsdrop-mcp --print-setup
```

It prints the absolute executable path and a standard `mcpServers` block:

```json
{
  "mcpServers": {
    "whatsdrop": {
      "command": "/absolute/path/whatsdrop-mcp",
      "args": [],
      "env": {
        "WHATSDROP_ACCOUNT": "primary"
      }
    }
  }
}
```

The config file location and key names vary by client. Use the values printed
by `--print-setup` in your client's MCP server configuration, then restart the
client.

If the console script is not on `PATH`, `--print-setup` falls back to:

```json
{
  "mcpServers": {
    "whatsdrop": {
      "command": "/absolute/path/python",
      "args": ["-m", "whatsdrop_cli.mcp_server"]
    }
  }
}
```

Both forms are equivalent.

## Tools

### `list_chats`

Lists the chats visible in the archive.

| Argument | Type | Description |
|---|---|---|
| `names` | list of strings | Chat name glob/substring filters, case-insensitive |
| `numbers` | list of strings | Phone number filters, any format |
| `hashes` | list of strings | Archive chat hash filters |
| `jids` | list of strings | Raw chat JID filters |

With no selectors, all chats are listed.

### `get_context`

Returns per-chat conversation context, ordered by the most recent activity.

| Argument | Type | Default | Description |
|---|---|---|---|
| `last` | string | `6h` | Rolling window such as `90m`, `36h`, `7d`, `2w` |
| `start` | string | — | Start bound `YYYY-MM-DD[THH[:MM[:SS]]]` |
| `end` | string | now | End bound; requires `start` |
| `context_before` | string | `30m` | Extra context before the window; `0` = none |
| `names` | list of strings | all | Chat name filters |
| `numbers` | list of strings | all | Phone number filters |
| `hashes` | list of strings | all | Chat hash filters |
| `jids` | list of strings | all | Chat JID filters |
| `max_chars_per_chat` | integer | `0` | Split a chat into parts of at most this many characters; `0` = no split |
| `max_total_chars` | integer | `0` | Total output cap; `0` = uncapped |
| `max_chats` | integer | `0` | Keep the N most recent chats; `0` = all |
| `max_parts` | integer | `1000` | Abort if more archive parts would be fetched |
| `tz` | string | system | IANA timezone for date bounds |

`last` and `start`/`end` are mutually exclusive. With no selectors, all chats
are included, matching `whatsdrop context`.

The response starts with a metadata line:

```text
[whatsdrop-mcp] read_only=true | untrusted_archive_data=true | account=primary | generated=2026-11-01T12:00:00+00:00 | freshness=last_successful_dropbox_upload | truncated=false | chats=3 | omitted_chats=0
```

`truncated=true` and an `output truncated` notice appear whenever
`max_chats` or `max_total_chars` omits content. Nothing is removed silently.

For model clients with limited context, a practical starting configuration is:

```json
{
  "max_chars_per_chat": 6000,
  "max_total_chars": 32000,
  "max_chats": 12
}
```

## Configuration

The MCP server reads defaults from:

- macOS/Linux: `~/.config/whatsdrop/mcp.json`
- Windows: `%APPDATA%\whatsdrop\mcp.json`

Next to the CLI credentials file, unless overridden.

Example:

```json
{
  "version": 1,
  "default_last": "6h",
  "default_context_before": "30m",
  "max_chars_per_chat": 0,
  "max_total_chars": 0,
  "max_chats": 0,
  "max_parts": 1000,
  "cli_timeout_seconds": 120,
  "account": "primary",
  "credentials_file": null,
  "env_file": null,
  "tz": null
}
```

`0` means uncapped or all, not empty output. Defaults mirror the CLI's behavior;
the convenience defaults `6h` and `30m` apply only when the tool call omits the
window.

Precedence:

```text
tool arguments > environment > mcp.json > built-in defaults
```

Environment overrides:

| Variable | Field |
|---|---|
| `WHATSDROP_MCP_CONFIG` | path to `mcp.json` |
| `WHATSDROP_MCP_DEFAULT_LAST` | `default_last` |
| `WHATSDROP_MCP_DEFAULT_CONTEXT_BEFORE` | `default_context_before` |
| `WHATSDROP_MCP_MAX_CHARS` | `max_chars_per_chat` |
| `WHATSDROP_MCP_MAX_TOTAL_CHARS` | `max_total_chars` |
| `WHATSDROP_MCP_MAX_CHATS` | `max_chats` |
| `WHATSDROP_MCP_MAX_PARTS` | `max_parts` |
| `WHATSDROP_MCP_TIMEOUT` | `cli_timeout_seconds` |
| `WHATSDROP_MCP_ENV_FILE` | `env_file` |

Credentials, the archive key, the account alias and the timezone continue to use
the CLI's existing environment variables: `WHATSDROP_CREDENTIALS`,
`WHATSDROP_KEY`, `WHATSDROP_ACCOUNT`, `WHATSDROP_FOLDER`.

## Health check

```sh
whatsdrop-mcp --check
```

This runs `whatsdrop auth --status --json` and a chat-index read through the
public CLI. It prints a short result to stderr and exits non-zero on failure.

Useful diagnostics:

- `WHATSDROP_MCP_DEBUG=1` enables debug logging on stderr.
- `whatsdrop auth --status --json` checks Dropbox authentication.
- `whatsdrop chats` checks archive readability independently of MCP.

Server mode never writes non-protocol output to stdout. Use stderr or the
`logging` module for diagnostics.

## Multiple accounts

Each MCP server entry selects one archive alias. To expose two accounts, register
two server entries with different `WHATSDROP_ACCOUNT` values:

```json
{
  "mcpServers": {
    "whatsdrop-primary": {
      "command": "/absolute/path/whatsdrop-mcp",
      "env": { "WHATSDROP_ACCOUNT": "primary" }
    },
    "whatsdrop-work": {
      "command": "/absolute/path/whatsdrop-mcp",
      "env": { "WHATSDROP_ACCOUNT": "work" }
    }
  }
}
```

The credentials and archive key must be valid for each alias.

## Privacy and security

- The server has no telemetry and does not send archive content to any AI
  service.
- It reads from Dropbox and returns selected text to the connected MCP client
  over local stdio.
- If the client is cloud-backed, the client may transmit the returned data. Use
  a local client or local model host for fully local operation.
- Message content is untrusted input. The server is read-only and the server
  instructions tell clients to treat messages as data, never as instructions.
- No media is exported.
- No archive cache is written to disk.
- Errors never include the archive key or Dropbox tokens.
- The archive is not real-time. It reflects the last successful Dropbox upload;
  the WhatsDrop extension must be running with uploads enabled.

## Troubleshooting

### The `mcp` dependency is not installed

```sh
pip install "whatsdrop-cli[mcp]"
```

`whatsdrop-mcp --version` and `--print-setup` work without the extra. Starting
the server without it exits with code 2 and prints the install hint.

### Dropbox is not connected

Run:

```sh
whatsdrop auth
whatsdrop-mcp --check
```

### The archive key is missing

Put `WHATSDROP_KEY` in the CLI environment file next to the credentials file, or
set it in the MCP client's `env` block. Do not place the key in `mcp.json`.

### The CLI times out

Narrow `last`, select fewer chats, lower `max_parts`, or raise
`cli_timeout_seconds` (maximum 600). The default CLI timeout is 120 seconds.

### `max_parts` guard tripped

The requested window covers too many archive parts. Narrow the window or select
specific chats. This is the same guard as `whatsdrop --max-parts`.

### The client cannot start the server

Use the absolute command path printed by `--print-setup`. On macOS, GUI clients
often do not inherit the shell `PATH`. On Windows, check paths containing
spaces and use the `.exe` path if necessary.

### Output is truncated

Set `max_total_chars` higher, lower `max_chars_per_chat`, or narrow the window.
`truncated=true` in the metadata line means content was omitted intentionally.

## Limitations

- stdio transport only
- text output only
- no media export
- no write actions
- no real-time delivery
- one account alias per server entry
