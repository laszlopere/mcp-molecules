# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""Smoke tests for the mcp-molecules scaffold."""

import asyncio

from mcp_molecules.server import info, mcp


def test_info_reports_name() -> None:
    result = info()
    assert result["name"] == "mcp-molecules"
    assert result["status"] == "available"


def test_tools_registered() -> None:
    names = {tool.name for tool in asyncio.run(mcp.list_tools())}
    assert "info" in names
    assert "molecular_weight_calculator" in names
