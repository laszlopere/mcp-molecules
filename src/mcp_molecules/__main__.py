# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""Console entry point: run the mcp-molecules server over stdio."""

import anyio

from mcp_molecules.jsonfix import run_stdio_repaired
from mcp_molecules.server import mcp


def main() -> None:
    """Start the MCP server on stdio, with the tolerant-parse interposer in front."""
    anyio.run(lambda: run_stdio_repaired(mcp))


if __name__ == "__main__":
    main()
