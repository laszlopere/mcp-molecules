# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""mcp-molecules — NIST-backed molecular weight calculation MCP server."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mcp-molecules")
except PackageNotFoundError:  # running from an unbuilt source tree
    __version__ = "0.0.0+unknown"
