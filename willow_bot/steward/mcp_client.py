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
_mcp_task: "asyncio.Task | None" = None
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


def _run(loop: asyncio.AbstractEventLoop, argv: list[str], ready: threading.Event) -> None:
    global _mcp_task
    _mcp_task = loop.create_task(_lifecycle(argv, ready))
    try:
        loop.run_until_complete(_mcp_task)
    except (asyncio.CancelledError, Exception):  # noqa: BLE001 — a cancelled or failed lifecycle ends the thread, nothing else
        pass
    finally:
        loop.close()


_START_TIMEOUT_S = 90.0


def start(argv: list[str] | None = None, *, timeout_s: float | None = None) -> None:
    global _mcp_loop, _mcp_thread, _mcp_argv
    if _mcp_session is not None:
        return
    timeout_s = _START_TIMEOUT_S if timeout_s is None else timeout_s
    argv = argv or default_mcp_argv()
    _mcp_argv = argv
    loop = asyncio.new_event_loop()
    _mcp_loop = loop
    ready = threading.Event()
    _mcp_thread = threading.Thread(
        target=_run, args=(loop, argv, ready), daemon=True, name="willow-bot-mcp",
    )
    _mcp_thread.start()
    if not ready.wait(timeout=timeout_s):
        # Tear the half-started lifecycle down before raising, or the thread
        # and its hung child outlive the failure and the next call spawns
        # another: five orphaned servers per tick on 2026-09-14, one per tool.
        # A lifecycle stuck inside initialize() never reaches the stop event,
        # so shutdown() cancels the task; stdio_client's exit ends the child.
        shutdown()
        raise RuntimeError(f"MCP server did not initialize within {timeout_s:g}s")


def shutdown() -> None:
    global _mcp_session, _mcp_loop, _mcp_thread, _mcp_stop_event, _mcp_task
    loop, task, stop = _mcp_loop, _mcp_task, _mcp_stop_event
    if loop is not None and not loop.is_closed():
        if stop is not None:
            loop.call_soon_threadsafe(stop.set)
        if task is not None:
            loop.call_soon_threadsafe(task.cancel)
    if _mcp_thread is not None:
        _mcp_thread.join(timeout=10)
    _mcp_session = None
    _mcp_loop = None
    _mcp_thread = None
    _mcp_stop_event = None
    _mcp_task = None


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
