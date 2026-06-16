# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""Shared test fixtures.

Keeps the suite hermetic: every test gets its own empty Tier-2 cache dir (in a
temp dir) and the online fallback explicitly off (it is on by default at
runtime), so tests never read the developer's real ``~/.local/share`` cache nor
touch the network unless they opt in by setting ``MCP_MOLECULES_ONLINE`` back on.
"""

from __future__ import annotations

import pytest

from mcp_molecules import cache


@pytest.fixture(autouse=True)
def isolate_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_MOLECULES_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MCP_MOLECULES_ONLINE", "0")  # default is on; force offline
    monkeypatch.delenv("MCP_MOLECULES_NEGCACHE_TTL", raising=False)
    cache._connect.cache_clear()
    yield
    cache._connect.cache_clear()
