# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""Tests for tolerating LLM-mangled tool-call JSON (jsonfix).

Two layers under test: the pure repair/format helpers, and one honest
end-to-end that speaks raw newline-delimited JSON-RPC over stdio -- the typed
``ClientSession`` cannot send malformed args (it serializes a dict), so the
stringified-arguments path can only be exercised by going raw.
"""

from __future__ import annotations

import json
import select
import subprocess
import sys

import pytest
from mcp.shared.message import SessionMessage
from mcp.types import PARSE_ERROR, JSONRPCError, JSONRPCMessage, JSONRPCRequest
from pydantic import BaseModel, ValidationError

from mcp_molecules.jsonfix import (
    format_validation_error,
    process_incoming,
    repair_arguments,
)

# --- repair_arguments (the four offenders + guards) -------------------------


def test_repair_passes_objects_through_untouched() -> None:
    obj = {"formula": "H2O"}
    assert repair_arguments(obj) is obj


def test_repair_well_formed_string() -> None:
    assert repair_arguments('{"formula": "H2O"}') == {"formula": "H2O"}


def test_repair_bareword_value() -> None:
    assert repair_arguments('{"variable": n}') == {"variable": "n"}


def test_repair_single_quotes() -> None:
    assert repair_arguments("{'formula': 'H2O'}") == {"formula": "H2O"}


def test_repair_trailing_comma() -> None:
    assert repair_arguments('{"formula": "H2O",}') == {"formula": "H2O"}


@pytest.mark.parametrize("junk", ["not json at all", "", "[1, 2, 3]", "42"])
def test_repair_returns_original_on_unsalvageable_or_non_object(junk: str) -> None:
    # json-repair never raises -- it coerces junk to ""/{}; we must hand the
    # ORIGINAL back (not silent garbage) so a real error is not masked.
    assert repair_arguments(junk) == junk


# --- process_incoming (the interposer's message router) ---------------------


def _call_request(arguments: object, *, request_id: int = 1, name: str = "info") -> SessionMessage:
    root = JSONRPCRequest(
        jsonrpc="2.0",
        id=request_id,
        method="tools/call",
        params={"name": name, "arguments": arguments},
    )
    return SessionMessage(message=JSONRPCMessage(root))


def test_process_repairs_stringified_arguments() -> None:
    forward, error = process_incoming(_call_request('{"formula": "H2O"}'))
    assert error is None
    assert forward is not None
    root = forward.message.root
    assert isinstance(root, JSONRPCRequest)
    assert root.params is not None
    assert root.params["arguments"] == {"formula": "H2O"}


def test_process_forwards_object_arguments_unchanged() -> None:
    msg = _call_request({"formula": "H2O"})
    forward, error = process_incoming(msg)
    assert error is None
    assert forward is msg  # untouched, same object


def test_process_emits_parse_error_for_unrepairable_arguments() -> None:
    forward, error = process_incoming(_call_request("totally not json", name="molecular_weight"))
    assert forward is None
    assert error is not None
    root = error.message.root
    assert isinstance(root, JSONRPCError)
    assert root.id == 1
    assert root.error.code == PARSE_ERROR
    assert "molecular_weight" in root.error.message
    assert "not valid JSON" in root.error.message


def test_process_ignores_non_tool_call_requests() -> None:
    root = JSONRPCRequest(jsonrpc="2.0", id=2, method="tools/list", params={})
    msg = SessionMessage(message=JSONRPCMessage(root))
    forward, error = process_incoming(msg)
    assert error is None
    assert forward is msg


# --- format_validation_error (Fix B phrasing) -------------------------------


class _Model(BaseModel):
    formula: str
    limit: int


def _validation_error(data: dict) -> ValidationError:
    try:
        _Model(**data)
    except ValidationError as exc:
        return exc
    raise AssertionError("expected a ValidationError")


def test_format_names_missing_field() -> None:
    msg = format_validation_error("molecular_weight_calculator", _validation_error({"limit": 1}))
    assert "'formula' is required but was not provided" in msg
    assert msg.startswith("Invalid arguments for tool 'molecular_weight_calculator':")


def test_format_reports_expected_vs_received_type() -> None:
    msg = format_validation_error("t", _validation_error({"formula": 123, "limit": 1}))
    assert "argument 'formula' expected a string, but received 123 (int)" in msg


def test_format_is_free_of_pydantic_url_and_stack() -> None:
    msg = format_validation_error("t", _validation_error({"formula": "H2O", "limit": "nope"}))
    assert "errors.pydantic.dev" not in msg
    assert "Traceback" not in msg
    assert "argument 'limit' expected an integer" in msg


# --- Fix B integration: the FastMCP subclass reshapes real shape errors -----


def test_call_tool_reshapes_wrong_type_argument() -> None:
    import asyncio

    from mcp.server.fastmcp.exceptions import ToolError

    from mcp_molecules.server import mcp

    # `formula` must be a string; an integer trips per-tool pydantic validation.
    with pytest.raises(ToolError) as excinfo:
        asyncio.run(mcp.call_tool("molecular_weight_calculator", {"formula": 123}))
    msg = str(excinfo.value)
    assert "Invalid arguments for tool 'molecular_weight_calculator'" in msg
    assert "argument 'formula' expected a string" in msg
    assert "errors.pydantic.dev" not in msg


def test_call_tool_leaves_tool_body_errors_alone() -> None:
    import asyncio

    from mcp.server.fastmcp.exceptions import ToolError

    from mcp_molecules.server import mcp

    # A well-shaped call whose formula is unparseable raises inside the tool
    # body; that error must pass through unchanged (not be reshaped as a
    # validation error).
    with pytest.raises(ToolError) as excinfo:
        asyncio.run(mcp.call_tool("molecular_weight_calculator", {"formula": "not-a-formula"}))
    assert "Invalid arguments for tool" not in str(excinfo.value)


# --- end-to-end: raw stdio with a STRING arguments blob ---------------------


def _read_response(proc: subprocess.Popen, want_id: int, timeout: float = 10.0) -> dict:
    """Read newline-delimited JSON-RPC from ``proc`` until the reply to ``want_id``.

    Uses select() so a regression fails fast instead of hanging the suite.
    """
    assert proc.stdout is not None
    while True:
        ready, _, _ = select.select([proc.stdout], [], [], timeout)
        if not ready:
            raise AssertionError(f"timed out waiting for response id={want_id}")
        line = proc.stdout.readline()
        if not line:
            raise AssertionError("server closed stdout before replying")
        msg = json.loads(line)
        if msg.get("id") == want_id:
            return msg


def _send(proc: subprocess.Popen, obj: dict) -> None:
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()


@pytest.mark.parametrize(
    ("blob", "expect"),
    [
        ('{"formula": "H2O"}', "result"),  # repaired -> tool runs
        ("{'formula': 'H2O'}", "result"),  # single quotes repaired
        ("not json at all", "parse_error"),  # unrepairable -> -32700
    ],
)
def test_e2e_stringified_arguments(blob: str, expect: str) -> None:
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_molecules"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            },
        )
        _read_response(proc, 0)
        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        # arguments is a STRING, not an object -- the malformed shape under test.
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "molecular_weight_calculator", "arguments": blob},
            },
        )
        reply = _read_response(proc, 1)
        if expect == "result":
            assert "result" in reply, reply
            assert reply["result"].get("isError") is not True
        else:
            assert "error" in reply, reply
            assert reply["error"]["code"] == PARSE_ERROR
    finally:
        proc.terminate()
        proc.wait(timeout=10)
