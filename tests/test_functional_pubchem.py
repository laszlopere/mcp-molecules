# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""Live functional test of the Tier-3 PubChem fallback (TODO 1.4.3 / 2.1).

Like test_functional_wikidata.py, this hits the real PUG-REST API -- the
end-to-end check that the PubChem online fallback is wired into
``find_chemical_compound`` and that its records cache. Marked ``network`` and
skipped when PubChem is unreachable, so an offline run degrades to a skip rather
than a failure (``pytest -m "not network"`` excludes it entirely).

The conftest fixture gives each test an isolated temp cache and forces the online
fallback off; this test opts back in explicitly.
"""

from __future__ import annotations

import urllib.parse
import urllib.request

import pytest

from mcp_molecules import names, remote
from mcp_molecules.server import find_chemical_compound

# A compound deliberately absent from the bundled local DB but present in
# PubChem with a stable preferred name and molecular formula. If a future bundled
# subset starts shipping it, the precondition assertion below fails loudly --
# the signal to pick a different PubChem-only compound here.
PUBCHEM_ONLY = "ferrocene"
EXPECTED_FORMULA = "C10H10Fe"


def _pubchem_reachable() -> bool:
    url = (
        remote._PUBCHEM
        + "/compound/name/"
        + urllib.parse.quote("water", safe="")
        + "/property/MolecularFormula,Title/JSON"
    )
    req = urllib.request.Request(url, headers={"User-Agent": remote._UA})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001 -- any failure means "offline" -> skip
        return False


@pytest.mark.network
def test_find_chemical_compound_resolves_pubchem_compound(monkeypatch) -> None:
    if not _pubchem_reachable():
        pytest.skip("PubChem PUG-REST unreachable")

    # Precondition: the compound is genuinely NOT in the bundled local DB, so a
    # hit can only come from the online fallback.
    assert names.lookup_formula(PUBCHEM_ONLY) == [], (
        f"{PUBCHEM_ONLY!r} is now in the bundled DB; pick a PubChem-only compound"
    )

    # Opt back into the network (conftest forces it off for hermeticity).
    monkeypatch.setenv("MCP_MOLECULES_ONLINE", "1")

    result = find_chemical_compound(PUBCHEM_ONLY, by="name")
    assert result["source"] == "pubchem"
    assert result["license"] == "public-domain"
    assert result["matches"][0]["formula"] == EXPECTED_FORMULA

    # The fetched record was written to the Tier-2 cache: a second lookup with
    # the network back off still resolves it (no further HTTP).
    monkeypatch.setenv("MCP_MOLECULES_ONLINE", "0")
    monkeypatch.setattr(remote, "_get_json", lambda url: pytest.fail("should be served from cache"))
    cached = find_chemical_compound(PUBCHEM_ONLY, by="name")
    assert cached["source"] == "pubchem"
    assert cached["matches"][0]["formula"] == EXPECTED_FORMULA


@pytest.mark.network
def test_find_chemical_compound_resolves_pubchem_by_formula(monkeypatch) -> None:
    if not _pubchem_reachable():
        pytest.skip("PubChem PUG-REST unreachable")

    monkeypatch.setenv("MCP_MOLECULES_ONLINE", "1")
    result = find_chemical_compound(EXPECTED_FORMULA, by="formula", limit=3)
    assert result["source"] == "pubchem"
    assert result["interpreted_as"] == "formula"
    assert result["matches"]
    assert all(m["formula"] == EXPECTED_FORMULA for m in result["matches"])
