# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""Tolerate LLM-mangled tool-call JSON, and answer with actionable errors.

Malformed ``arguments`` on a ``tools/call`` fail at two different points of the
inbound MCP path, so there are two independent fixes here:

* **Fix A — stringified / double-encoded args.** The whole ``arguments`` object
  (or a nested field) arrives as a JSON *string* instead of an object, e.g.
  ``"arguments": "{\\"formula\\": \\"H2O\\"}"``. The session's strict
  ``CallToolRequestParams`` validation (``arguments: dict | None``) rejects a
  string *before* any FastMCP/tool hook runs, so it cannot be caught inside a
  tool. We repair it in a stdio stream interposer, while the bytes are still the
  loosely-typed ``JSONRPCRequest.params``. If the string is unrepairable we
  answer the request ourselves with an actionable ``-32700`` parse error instead
  of letting the SDK emit a bare ``-32602``.

* **Fix B — well-formed JSON, wrong shape.** A missing field or wrong type
  passes the session but fails FastMCP's per-tool pydantic validation, surfaced
  as a ``ToolError`` whose ``__cause__`` is the ``ValidationError``. The
  ``MoleculesFastMCP`` subclass reshapes only that case into a message naming the
  field and the expected-vs-received type.

No SDK types are monkeypatched; both hooks rely only on stable message shapes.
See ``/home/pipas/llm-json-errors-howto.text`` for the full rationale.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import anyio
from json_repair import loads as repair_loads
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.shared.message import SessionMessage
from mcp.types import (
    PARSE_ERROR,
    ContentBlock,
    ErrorData,
    JSONRPCError,
    JSONRPCMessage,
    JSONRPCRequest,
)
from pydantic import ValidationError

# pydantic error type -> human phrase, for expected-vs-received messages.
_EXPECTED: dict[str, str] = {
    "string_type": "a string",
    "int_type": "an integer",
    "int_parsing": "an integer",
    "float_type": "a number",
    "float_parsing": "a number",
    "bool_type": "a boolean",
    "bool_parsing": "a boolean",
    "list_type": "an array",
    "dict_type": "an object",
}


# --- Fix A: tolerant parse of stringified arguments -------------------------


def repair_arguments(arguments: Any) -> Any:
    """Coerce a stringified ``arguments`` payload back into an object.

    A non-string is already an object and is returned untouched. A string is
    parsed strictly first (cheap and the common case once repaired), then with
    ``json-repair`` for the bareword / single-quote / trailing-comma offenders.
    ``json-repair`` never raises -- it coerces unsalvageable junk to ``""`` -- so
    the result is only accepted when it is a ``dict``; otherwise the ORIGINAL is
    handed back so a real error is not masked by silent garbage.
    """
    if not isinstance(arguments, str):
        return arguments
    try:
        parsed = json.loads(arguments)  # strict first (cheap, common)
    except ValueError:
        try:
            parsed = repair_loads(arguments)
        except ValueError:
            return arguments
    return parsed if isinstance(parsed, dict) else arguments


def parse_error_reply(request_id: Any, tool_name: str) -> SessionMessage:
    """Build an actionable ``-32700`` reply for unrepairable ``arguments``."""
    msg = (
        f"The 'arguments' for tool {tool_name!r} were not valid JSON. "
        'Send `arguments` as a JSON object -- e.g. {"formula": "H2O"} -- not a '
        "quoted string; check for unbalanced braces, single quotes, or missing "
        "quotes around keys/values."
    )
    err = JSONRPCError(
        jsonrpc="2.0",
        id=request_id,
        error=ErrorData(code=PARSE_ERROR, message=msg),
    )
    return SessionMessage(message=JSONRPCMessage(err))


def process_incoming(
    message: SessionMessage,
) -> tuple[SessionMessage | None, SessionMessage | None]:
    """Route one inbound message for the interposer.

    Returns ``(to_forward, error_reply)`` of which at most one is non-``None``:

    * not a ``tools/call``, or ``arguments`` already an object -> forward as-is;
    * stringified ``arguments`` repaired to an object -> forward the repaired copy;
    * stringified ``arguments`` that cannot be repaired -> reply with a parse
      error and forward nothing (so the session never double-answers that id).
    """
    root = message.message.root
    if not isinstance(root, JSONRPCRequest) or root.method != "tools/call":
        return message, None
    params = root.params
    if not isinstance(params, dict) or not isinstance(params.get("arguments"), str):
        return message, None
    fixed = repair_arguments(params["arguments"])
    if not isinstance(fixed, dict):
        name_val = params.get("name")
        tool_name = name_val if isinstance(name_val, str) else "?"
        return None, parse_error_reply(root.id, tool_name)
    new_root = root.model_copy(update={"params": {**params, "arguments": fixed}})
    repaired = SessionMessage(message=JSONRPCMessage(new_root), metadata=message.metadata)
    return repaired, None


async def run_stdio_repaired(mcp: FastMCP) -> None:
    """Serve ``mcp`` over stdio with the tolerant-parse interposer in front.

    Replaces ``FastMCP.run()`` for the stdio transport: every inbound message is
    routed through :func:`process_incoming` before the session's strict
    validation sees it. Relies only on stable message shapes, not SDK internals.
    """
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        send, recv = anyio.create_memory_object_stream[SessionMessage | Exception](0)

        async def pump() -> None:
            async with send:
                async for item in read_stream:
                    if isinstance(item, SessionMessage):
                        forward, error_reply = process_incoming(item)
                        if error_reply is not None:
                            await write_stream.send(error_reply)
                            continue
                        assert forward is not None
                        item = forward
                    await send.send(item)

        async with anyio.create_task_group() as tg, recv:
            tg.start_soon(pump)
            await mcp._mcp_server.run(
                recv,
                write_stream,
                mcp._mcp_server.create_initialization_options(),
            )
            tg.cancel_scope.cancel()


# --- Fix B: actionable errors for well-formed-but-wrong-shape args ----------


def format_validation_error(tool_name: str, exc: ValidationError) -> str:
    """Reshape a pydantic ``ValidationError`` into a model-readable message.

    Built from ``exc.errors()`` (structured; the ``errors.pydantic.dev`` URL only
    appears in ``str(exc)``, so this stays URL- and stack-trace-free). Names each
    offending field and what it expected versus what it received.
    """
    parts: list[str] = []
    for e in exc.errors():
        field = ".".join(map(str, e.get("loc", ()))) or "arguments"
        if e["type"] == "missing":
            parts.append(f"argument {field!r} is required but was not provided")
            continue
        got = e.get("input")
        want = _EXPECTED.get(e["type"])
        if want:
            parts.append(
                f"argument {field!r} expected {want}, but received {got!r} ({type(got).__name__})"
            )
        else:
            parts.append(f"argument {field!r}: {e.get('msg', 'invalid value')} (received {got!r})")
    return f"Invalid arguments for tool {tool_name!r}: {'; '.join(parts)}."


class MoleculesFastMCP(FastMCP):
    """FastMCP whose ``call_tool`` rewrites argument-shape errors actionably.

    FastMCP wraps a per-tool pydantic ``ValidationError`` as a ``ToolError`` with
    ``__cause__`` set to the ``ValidationError``; only that case is rewritten.
    Errors raised inside a tool body have a different cause and pass through
    unchanged. The subclass instance IS the app (handlers bind ``self.call_tool``
    at construction), so this override is what runs.
    """

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> Sequence[ContentBlock] | dict[str, Any]:
        try:
            return await super().call_tool(name, arguments)
        except ToolError as exc:
            if isinstance(exc.__cause__, ValidationError):
                raise ToolError(format_validation_error(name, exc.__cause__)) from exc.__cause__
            raise
