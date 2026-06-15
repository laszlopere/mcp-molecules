# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""Shared test fixtures.

Keeps the suite hermetic: every test gets its own empty Tier-2 cache (in a temp
dir) and the online fallback off, so tests never read the developer's real
``~/.local/share`` cache nor touch the network unless they opt in explicitly.
"""

from __future__ import annotations

import pytest

from mcp_molecules import cache


@pytest.fixture(autouse=True)
def isolate_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_MOLECULES_CACHE_DB", str(tmp_path / "names_cache.db"))
    monkeypatch.delenv("MCP_MOLECULES_ONLINE", raising=False)
    monkeypatch.delenv("MCP_MOLECULES_NEGCACHE_TTL", raising=False)
    cache._connect.cache_clear()
    yield
    cache._connect.cache_clear()
