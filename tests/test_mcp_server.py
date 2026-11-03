"""MCP tool registration and behaviour."""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

pytest.importorskip("mcp")

from conftest import KEY, TOKEN
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.server import MCPServer

from whatsdrop_cli.mcp_config import McpSettings
from whatsdrop_cli.mcp_server import SERVER_INSTRUCTIONS, build_server


def _configure(monkeypatch, api, token=TOKEN, key=KEY):
    monkeypatch.setenv("WHATSDROP_DROPBOX_TOKEN", token)
    monkeypatch.setenv("WHATSDROP_ACCOUNT", "primary")
    monkeypatch.setenv("WHATSDROP_TEST_DROPBOX_BASE", api.endpoint)
    if key is None:
        monkeypatch.delenv("WHATSDROP_KEY", raising=False)
    else:
        monkeypatch.setenv("WHATSDROP_KEY", key)


def _settings(tmp_path, **overrides):
    values = {"env_file": str(tmp_path / "missing.env")}
    values.update(overrides)
    return McpSettings(**values)


def _server(tmp_path, **overrides):
    return build_server(MCPServer, _settings(tmp_path, **overrides))


def _call(server, name, arguments):
    async def go():
        async with Client(server) as client:
            return await client.call_tool(name, arguments)

    return asyncio.run(go())


def _tools(server):
    async def go():
        async with Client(server) as client:
            return await client.list_tools()

    return asyncio.run(go())


def test_tools_are_registered(tmp_path):
    result = _tools(_server(tmp_path))

    assert {tool.name for tool in result.tools} == {"list_chats", "get_context"}
    for tool in result.tools:
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.open_world_hint is True


def test_server_instructions():
    assert "untrusted" in SERVER_INSTRUCTIONS
    assert "read-only" in SERVER_INSTRUCTIONS
    assert "real-time" in SERVER_INSTRUCTIONS


def test_list_chats(seeded, monkeypatch, tmp_path):
    _configure(monkeypatch, seeded)

    result = _call(_server(tmp_path), "list_chats", {})

    assert result.is_error is False
    text = result.content[0].text
    assert "Anna Schmidt" in text
    assert "Bob LID" in text
    assert "Family Group" in text
    assert "read_only=true" in text


def test_header_uses_env_file_account(seeded, monkeypatch, tmp_path):
    _configure(monkeypatch, seeded)
    monkeypatch.delenv("WHATSDROP_ACCOUNT", raising=False)
    env_file = tmp_path / "env"
    env_file.write_text("WHATSDROP_ACCOUNT=primary\n", encoding="utf-8")
    env_file.chmod(0o600)

    server = _server(tmp_path, env_file=str(env_file), account="work")
    result = _call(server, "list_chats", {})

    assert result.is_error is False
    assert "account=primary" in result.content[0].text


def test_list_chats_filtered(seeded, monkeypatch, tmp_path):
    _configure(monkeypatch, seeded)

    result = _call(_server(tmp_path), "list_chats", {"names": ["*Anna*"]})

    assert result.is_error is False
    text = result.content[0].text
    assert "Anna Schmidt" in text
    assert "Bob LID" not in text


def test_get_context(seeded, monkeypatch, tmp_path):
    _configure(monkeypatch, seeded)

    result = _call(_server(tmp_path), "get_context", {"last": "800w"})

    assert result.is_error is False
    text = result.content[0].text
    assert "=== CHAT" in text
    assert "truncated=false" in text
    assert "Anna Schmidt" in text


def test_get_context_by_name(seeded, monkeypatch, tmp_path):
    _configure(monkeypatch, seeded)

    result = _call(
        _server(tmp_path),
        "get_context",
        {"last": "800w", "names": ["*Anna*"]},
    )

    assert result.is_error is False
    text = result.content[0].text
    assert "Anna Schmidt" in text
    assert "Bob LID" not in text


def test_get_context_max_chats(seeded, monkeypatch, tmp_path):
    _configure(monkeypatch, seeded)

    result = _call(_server(tmp_path), "get_context", {"last": "800w", "max_chats": 1})

    assert result.is_error is False
    text = result.content[0].text
    assert "truncated=true" in text
    assert "omitted 2 chat(s)" in text


def test_get_context_requires_one_window(seeded, monkeypatch, tmp_path):
    _configure(monkeypatch, seeded)

    result = _call(
        _server(tmp_path),
        "get_context",
        {"last": "6h", "start": "2026-10-01"},
    )

    assert result.is_error is True
    assert "cannot be combined" in result.content[0].text


def test_get_context_missing_auth(monkeypatch, tmp_path):
    monkeypatch.delenv("WHATSDROP_DROPBOX_TOKEN", raising=False)
    monkeypatch.delenv("WHATSDROP_DROPBOX_REFRESH_TOKEN", raising=False)
    monkeypatch.setenv("WHATSDROP_CREDENTIALS", str(tmp_path / "missing.json"))

    result = _call(_server(tmp_path), "get_context", {"last": "6h"})

    assert result.is_error is True
    assert "whatsdrop auth" in result.content[0].text


def test_get_context_no_matches(seeded, monkeypatch, tmp_path):
    _configure(monkeypatch, seeded)

    result = _call(_server(tmp_path), "get_context", {"last": "1m"})

    assert result.is_error is True
    assert "No archived" in result.content[0].text


def test_get_context_max_parts_guard(seeded, monkeypatch, tmp_path):
    _configure(monkeypatch, seeded)

    result = _call(
        _server(tmp_path),
        "get_context",
        {"last": "800w", "max_parts": 1},
    )

    assert result.is_error is True
    assert "Too many archive parts" in result.content[0].text


def test_get_context_truncation(seeded, monkeypatch, tmp_path):
    _configure(monkeypatch, seeded)

    result = _call(
        _server(tmp_path),
        "get_context",
        {"last": "800w", "max_total_chars": 50},
    )

    assert result.is_error is False
    text = result.content[0].text
    assert "truncated=true" in text
    assert "output truncated" in text


def test_stdio_server(seeded, monkeypatch, tmp_path):
    _configure(monkeypatch, seeded)
    monkeypatch.setenv("WHATSDROP_MCP_CONFIG", str(tmp_path / "missing.json"))
    monkeypatch.setenv("WHATSDROP_CREDENTIALS", str(tmp_path / "missing.json"))
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "whatsdrop_cli.mcp_server"],
        env=dict(os.environ),
    )

    async def go():
        async with Client(params) as client:
            tools = await client.list_tools()
            result = await client.call_tool("list_chats", {})
            return tools, result

    tools, result = asyncio.run(go())

    assert {tool.name for tool in tools.tools} == {"list_chats", "get_context"}
    assert result.is_error is False
    assert "Anna Schmidt" in result.content[0].text
