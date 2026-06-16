# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""Live functional test of the Tier-3 EPA CompTox fallback (TODO 2.3).

Hits the real CCTE Chemical API at api-ccte.epa.gov, the end-to-end check that
the EPA online fallback parses the live endpoints correctly. The API needs a free
``x-api-key``; this test is skipped unless ``$MCP_MOLECULES_EPA_API_KEY`` is set
(so a keyless run -- the default -- degrades to a skip, not a failure) and again
when the API is unreachable. Marked ``network`` (``pytest -m "not network"``
excludes it).

Because PubChem is queried before EPA in ``find_chemical_compound``, this drives
the EPA fetchers directly rather than through the layered lookup, where a PubChem
hit would shadow them.

The conftest fixture gives each test an isolated temp cache and forces the online
fallback off; this test opts back in explicitly.
"""

from __future__ import annotations

import os

import pytest

from mcp_molecules import remote

ASPIRIN = "aspirin"
EXPECTED_FORMULA = "C9H8O4"

_HAS_KEY = bool(os.environ.get("MCP_MOLECULES_EPA_API_KEY", "").strip())

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(not _HAS_KEY, reason="MCP_MOLECULES_EPA_API_KEY not set"),
]


def _epa_reachable() -> bool:
    return bool(remote.epa_by_name("water"))


def test_epa_by_name_live(monkeypatch) -> None:
    monkeypatch.setenv("MCP_MOLECULES_ONLINE", "1")  # conftest forces it off
    if not _epa_reachable():
        pytest.skip("EPA CCTE API unreachable")

    recs = remote.epa_by_name(ASPIRIN)
    assert recs, "EPA returned no record for aspirin"
    assert any(r["formulas"] == [EXPECTED_FORMULA] for r in recs)
    assert all(r["ref"].startswith("DTXSID") for r in recs)


def test_epa_by_formula_live(monkeypatch) -> None:
    monkeypatch.setenv("MCP_MOLECULES_ONLINE", "1")
    if not _epa_reachable():
        pytest.skip("EPA CCTE API unreachable")

    recs = remote.epa_by_formula(EXPECTED_FORMULA, limit=3)
    assert recs, "EPA returned no record for the formula"
    assert all(r["formulas"] == [EXPECTED_FORMULA] for r in recs)
