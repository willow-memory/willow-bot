"""Thin MCP stdio client for steward prove-phase tool calls.

Re-points the lifecycle pattern from ratatosk.mcp_client (Rule 11) — same env
forwarding lesson, no dependency on the ratatosk package.
"""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import sys
import threading
from typing import Any

_mcp_session = None
_mcp_loop: asyncio.AbstractEventLoop | None = None
_mcp_stop_event: asyncio.Event | None = None
_mcp_thread: threading.Thread | None = None
_mcp_argv: list[str] | None = None

_ENV_PREFIXES = ("WILLOW_", "WILLOW_BOT_", "PG")


def default_mcp_argv() -> list[str]:
    override = os.environ.get("WILLOW_BOT_MCP_COMMAND", "").strip()
    if override:
        return shlex.split(override)
    module = os.environ.get("WILLOW_BOT_MCP_MODULE", "willow_mcp")
    return [sys.executable, "-m", module]


def server_env() -> dict[str, str]:
    from mcp.client.stdio import get_default_environment

    if os.environ.get("WILLOW_BOT_MCP_INHERIT_ENV", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        return dict(os.environ)
    env = dict(get_default_environment())
    env.update({k: v for k, v in os.environ.items() if k.startswith(_ENV_PREFIXES)})
    return env


def _call_sync(coro):
    assert _mcp_loop is not None
    return asyncio.run_coroutine_threadsafe(coro, _mcp_loop).result(timeout=90)


async def _lifecycle(argv: list[str], ready: threading.Event) -> None:
    global _mcp_session, _mcp_stop_event
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    stop = asyncio.Event()
    _mcp_stop_event = stop
    params = StdioServerParameters(command=argv[0], args=argv[1:], env=server_env())
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            _mcp_session = session
            ready.set()
            await stop.wait()


def start(argv: list[str] | None = None) -> None:
    global _mcp_loop, _mcp_thread, _mcp_argv
    if _mcp_session is not None:
        return
    argv = argv or default_mcp_argv()
    _mcp_argv = argv
    loop = asyncio.new_event_loop()
    _mcp_loop = loop
    ready = threading.Event()
    _mcp_thread = threading.Thread(
        target=lambda: loop.run_until_complete(_lifecycle(argv, ready)),
        daemon=True,
        name="willow-bot-mcp",
    )
    _mcp_thread.start()
    if not ready.wait(timeout=90):
        raise RuntimeError("MCP server did not initialize within 90s")


def shutdown() -> None:
    global _mcp_session, _mcp_loop, _mcp_thread, _mcp_stop_event
    if _mcp_stop_event is not None and _mcp_loop is not None:
        _mcp_loop.call_soon_threadsafe(_mcp_stop_event.set)
    if _mcp_thread is not None:
        _mcp_thread.join(timeout=10)
    _mcp_session = None
    _mcp_loop = None
    _mcp_thread = None
    _mcp_stop_event = None


def call(name: str, inputs: dict[str, Any]) -> Any:
    start()
    assert _mcp_session is not None
    result = _call_sync(_mcp_session.call_tool(name, inputs))
    # SDK 1.x isError / 2.x is_error
    is_err = getattr(result, "isError", None)
    if is_err is None:
        is_err = getattr(result, "is_error", False)
    texts = []
    for block in getattr(result, "content", None) or []:
        t = getattr(block, "text", None)
        if t is not None:
            texts.append(t)
    payload = "\n".join(texts) if texts else str(result)
    if is_err:
        raise RuntimeError(payload)
    try:
        return json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return payload
