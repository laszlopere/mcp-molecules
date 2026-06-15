# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""Live functional test of the Tier-3 Wikidata fallback (TODO 1.4.3 / 2.2).

Unlike test_cache_remote.py, this hits the real Wikidata API -- it is the
end-to-end check that the online fallback is actually wired into
``find_chemical_compound``. Marked ``network`` and skipped when Wikidata is
unreachable, so an offline run degrades to a skip rather than a failure
(``pytest -m "not network"`` excludes it entirely).

The conftest fixture gives each test an isolated temp cache and forces the
online fallback off; this test opts back in explicitly.
"""

from __future__ import annotations

import urllib.parse
import urllib.request

import pytest

from mcp_molecules import names, remote
from mcp_molecules.server import find_chemical_compound

# A compound deliberately absent from the bundled local DB but present in
# Wikidata with a stable English label and P274 formula. If a future bundled
# subset starts shipping it, the precondition assertion below fails loudly --
# the signal to pick a different Wikidata-only compound here.
WIKIDATA_ONLY = "ferrocene"
EXPECTED_FORMULA = "C10H10Fe"


def _wikidata_reachable() -> bool:
    url = remote._API + "?" + urllib.parse.urlencode(
        {"action": "wbsearchentities", "search": "water",
         "language": "en", "format": "json", "limit": 1}
    )
    req = urllib.request.Request(url, headers={"User-Agent": remote._UA})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001 -- any failure means "offline" -> skip
        return False


@pytest.mark.network
def test_find_chemical_compound_resolves_wikidata_only_compound(monkeypatch) -> None:
    if not _wikidata_reachable():
        pytest.skip("Wikidata API unreachable")

    # Precondition: the compound is genuinely NOT in the bundled local DB, so a
    # hit can only come from the online fallback.
    assert names.lookup_formula(WIKIDATA_ONLY) == [], (
        f"{WIKIDATA_ONLY!r} is now in the bundled DB; pick a Wikidata-only compound"
    )

    # Opt back into the network (conftest forces it off for hermeticity).
    monkeypatch.setenv("MCP_MOLECULES_ONLINE", "1")

    result = find_chemical_compound(WIKIDATA_ONLY, by="name")
    assert result["source"] == "wikidata"
    assert result["license"] == "CC0-1.0"
    assert result["matches"][0]["name"].casefold() == WIKIDATA_ONLY
    assert result["matches"][0]["formula"] == EXPECTED_FORMULA

    # The fetched record was written to the Tier-2 cache: a second lookup with
    # the network back off still resolves it (no further HTTP).
    monkeypatch.setenv("MCP_MOLECULES_ONLINE", "0")
    monkeypatch.setattr(
        remote, "_get_json", lambda url: pytest.fail("should be served from cache")
    )
    cached = find_chemical_compound(WIKIDATA_ONLY, by="name")
    assert cached["source"] == "wikidata"
    assert cached["matches"][0]["formula"] == EXPECTED_FORMULA
