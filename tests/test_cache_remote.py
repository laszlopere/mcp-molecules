# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""Tests for the Tier-2 cache, the Tier-3 Wikidata fallback, and their wiring.

Network is never actually hit: ``remote._get_json`` is monkeypatched to serve
canned API/SPARQL payloads, so the tests exercise the parsing, caching, and
layered-lookup logic deterministically and offline.
"""

from __future__ import annotations

import json

import pytest

from mcp_molecules import cache, names, remote

# --- Tier-2 cache store ----------------------------------------------------


def test_cache_missing_file_reads_empty() -> None:
    assert cache.list_sources() == []
    assert not cache.cache_path("wikidata").exists()
    assert cache.lookup_formula("wikidata", "water") == []
    assert cache.lookup_names("wikidata", "H2O") == []
    assert cache.is_negative("wikidata", "water", "name") is False
    assert cache.source_license("wikidata") == ("", "")
    # Pure reads must not create the cache file.
    assert not cache.cache_path("wikidata").exists()


def test_cache_store_and_lookup_roundtrip() -> None:
    rec = {"ref": "Q283", "name": "water", "aliases": ["dihydrogen monoxide"], "formulas": ["H2O"]}
    assert cache.store([rec], "wikidata", "CC0-1.0") == 1
    assert cache.cache_path("wikidata").exists()
    # A store wrote one per-source file, discoverable by name.
    assert cache.list_sources() == ["wikidata"]
    assert cache.source_license("wikidata") == ("wikidata", "CC0-1.0")

    matches = cache.lookup_formula("wikidata", "Dihydrogen Monoxide")  # via alias, normalized
    assert matches[0] == {"name": "water", "formula": "H2O"}

    matches = cache.lookup_names("wikidata", "OH2")  # Hill-normalized to H2O
    assert matches[0]["name"] == "water"


def test_cache_sources_are_separate_files() -> None:
    cache.store(
        [{"ref": "Q283", "name": "water", "aliases": [], "formulas": ["H2O"]}],
        "wikidata",
        "CC0-1.0",
    )
    cache.store(
        [{"ref": "C1", "name": "Water", "aliases": [], "formulas": ["H2O"]}],
        "pubchem",
        "public-domain",
    )
    assert cache.list_sources() == ["pubchem", "wikidata"]
    assert cache.cache_path("wikidata").exists()
    assert cache.cache_path("pubchem").exists()
    # Each file carries its own provenance; a lookup in one is blind to the other.
    assert cache.source_license("pubchem") == ("pubchem", "public-domain")
    assert cache.lookup_formula("pubchem", "water")[0]["name"] == "Water"
    assert cache.lookup_formula("wikidata", "water")[0]["name"] == "water"


def test_cache_store_is_idempotent() -> None:
    rec = {"ref": "Q283", "name": "water", "aliases": [], "formulas": ["H2O"]}
    assert cache.store([rec], "wikidata", "CC0-1.0") == 1
    assert cache.store([rec], "wikidata", "CC0-1.0") == 0  # same ref -> skipped


def test_negative_cache_ttl(monkeypatch) -> None:
    cache.remember_miss("wikidata", "unobtanium", "name")
    assert cache.is_negative("wikidata", "unobtanium", "name") is True
    assert cache.is_negative("wikidata", "unobtanium", "formula") is False
    # The negcache is per-source: another source's file has not seen the miss.
    assert cache.is_negative("pubchem", "unobtanium", "name") is False
    # A zero TTL makes any remembered miss immediately stale.
    monkeypatch.setenv("MCP_MOLECULES_NEGCACHE_TTL", "0")
    assert cache.is_negative("wikidata", "unobtanium", "name") is False


def test_store_clears_matching_negative() -> None:
    cache.remember_miss("wikidata", "water", "name")
    assert cache.is_negative("wikidata", "water", "name") is True
    cache.store(
        [{"ref": "Q283", "name": "water", "aliases": [], "formulas": ["H2O"]}],
        "wikidata",
        "CC0-1.0",
    )
    assert cache.is_negative("wikidata", "water", "name") is False


# --- Tier-3 Wikidata client (mocked HTTP) ----------------------------------


def _canned(monkeypatch, responses: dict[str, dict]) -> None:
    """Route remote._get_json by a substring found in the requested URL."""

    def fake(url: str):
        for needle, payload in responses.items():
            if needle in url:
                return payload
        return None

    monkeypatch.setattr(remote, "_get_json", fake)


def test_online_disabled_returns_empty(monkeypatch) -> None:
    # conftest forces MCP_MOLECULES_ONLINE=0 -> hard offline, no network touched.
    monkeypatch.setattr(remote, "_get_json", lambda url: pytest.fail("must not hit network"))
    assert remote.wikidata_by_name("water") == []
    assert remote.wikidata_by_formula("H2O") == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, True),  # unset -> on by default
        ("", True),
        ("1", True),
        ("true", True),
        ("anything", True),  # only explicit falsy values opt out
        ("0", False),
        ("false", False),
        ("No", False),
        ("OFF", False),
    ],
)
def test_online_enabled_defaults_on(monkeypatch, value, expected) -> None:
    if value is None:
        monkeypatch.delenv("MCP_MOLECULES_ONLINE", raising=False)
    else:
        monkeypatch.setenv("MCP_MOLECULES_ONLINE", value)
    assert remote.online_enabled() is expected


def test_wikidata_by_name(monkeypatch) -> None:
    monkeypatch.setenv("MCP_MOLECULES_ONLINE", "1")
    _canned(
        monkeypatch,
        {
            "wbsearchentities": {"search": [{"id": "Q283"}, {"id": "Q99999"}]},
            "wbgetentities": {
                "entities": {
                    "Q283": {
                        "labels": {"en": {"value": "water"}},
                        "aliases": {"en": [{"value": "dihydrogen monoxide"}]},
                        "claims": {
                            "P274": [
                                {"mainsnak": {"snaktype": "value", "datavalue": {"value": "H2O"}}}
                            ]
                        },
                    },
                    "Q99999": {  # label does not match the query -> filtered out
                        "labels": {"en": {"value": "something else"}},
                        "aliases": {"en": []},
                        "claims": {
                            "P274": [
                                {"mainsnak": {"snaktype": "value", "datavalue": {"value": "C2H6"}}}
                            ]
                        },
                    },
                }
            },
        },
    )
    recs = remote.wikidata_by_name("Water")
    assert len(recs) == 1
    assert recs[0]["ref"] == "Q283"
    assert recs[0]["formulas"] == ["H2O"]


def test_wikidata_by_name_normalizes_subscript_formula(monkeypatch) -> None:
    monkeypatch.setenv("MCP_MOLECULES_ONLINE", "1")
    _canned(
        monkeypatch,
        {
            "wbsearchentities": {"search": [{"id": "Q283"}]},
            "wbgetentities": {
                "entities": {
                    "Q283": {
                        "labels": {"en": {"value": "water"}},
                        "aliases": {"en": []},
                        "claims": {
                            "P274": [
                                {"mainsnak": {"snaktype": "value", "datavalue": {"value": "H₂O"}}}
                            ]
                        },
                    }
                }
            },
        },
    )
    assert remote.wikidata_by_name("water")[0]["formulas"] == ["H2O"]


def test_wikidata_by_formula(monkeypatch) -> None:
    monkeypatch.setenv("MCP_MOLECULES_ONLINE", "1")
    _canned(
        monkeypatch,
        {
            "sparql": {
                "results": {
                    "bindings": [
                        {
                            "item": {"value": "http://www.wikidata.org/entity/Q283"},
                            "itemLabel": {"value": "water"},
                        },
                        {
                            "item": {"value": "http://www.wikidata.org/entity/Q404"},
                            "itemLabel": {"value": "Q404"},
                        },  # no English label -> dropped
                    ]
                }
            }
        },
    )
    recs = remote.wikidata_by_formula("OH2")  # Hill-normalized to H2O
    assert [r["name"] for r in recs] == ["water"]
    assert recs[0]["formulas"] == ["H2O"]


def test_fail_soft_on_network_error(monkeypatch) -> None:
    monkeypatch.setenv("MCP_MOLECULES_ONLINE", "1")
    monkeypatch.setattr(remote, "_get_json", lambda url: None)  # simulate timeout/HTTP error
    assert remote.wikidata_by_name("water") == []
    assert remote.wikidata_by_formula("H2O") == []


# --- layered find_compound across all three tiers --------------------------


def test_remote_miss_is_negatively_cached(monkeypatch) -> None:
    monkeypatch.setenv("MCP_MOLECULES_ONLINE", "1")
    calls = {"n": 0}

    def fake(url: str):
        calls["n"] += 1
        return {"search": []}  # empty search -> no records

    monkeypatch.setattr(remote, "_get_json", fake)
    src = names.RemoteSource()
    assert src.by_name("nonesuch") == []
    assert cache.is_negative(remote.SOURCE, "nonesuch", "name") is True
    before = calls["n"]
    # The remembered miss short-circuits the next lookup -> no further HTTP.
    assert src.by_name("nonesuch") == []
    assert calls["n"] == before


def test_find_compound_falls_through_to_remote_and_caches(monkeypatch) -> None:
    monkeypatch.setenv("MCP_MOLECULES_ONLINE", "1")
    # A compound absent from the bundled subset; served by the mocked remote.
    _canned(
        monkeypatch,
        {
            "wbsearchentities": {"search": [{"id": "Q12345"}]},
            "wbgetentities": {
                "entities": {
                    "Q12345": {
                        "labels": {"en": {"value": "zzfakecompound"}},
                        "aliases": {"en": []},
                        "claims": {
                            "P274": [
                                {
                                    "mainsnak": {
                                        "snaktype": "value",
                                        "datavalue": {"value": "C99H99"},
                                    }
                                }
                            ]
                        },
                    }
                }
            },
        },
    )
    r = names.find_compound("zzfakecompound", by="name")
    assert r["matches"][0] == {"name": "zzfakecompound", "formula": "C99H99"}
    assert r["source"] == "wikidata"

    # Now cached at Tier 2: a second lookup resolves with the network disabled.
    monkeypatch.setenv("MCP_MOLECULES_ONLINE", "0")
    monkeypatch.setattr(remote, "_get_json", lambda url: pytest.fail("should be cached"))
    r2 = names.find_compound("zzfakecompound", by="name")
    assert r2["matches"][0]["formula"] == "C99H99"
    assert r2["source"] == "wikidata"


def test_bundled_hit_skips_remote(monkeypatch) -> None:
    monkeypatch.setenv("MCP_MOLECULES_ONLINE", "1")
    monkeypatch.setattr(remote, "_get_json", lambda url: pytest.fail("bundled should win"))
    r = names.find_compound("aspirin", by="name")
    assert r["matches"][0]["formula"] == "C9H8O4"
    assert r["source"] == "pubchem"  # the bundled DB's source tag


def test_ua_string_is_descriptive() -> None:
    assert remote._UA.startswith("mcp-molecules/")
    assert "github.com/laszlopere/mcp-molecules" in remote._UA


def test_records_are_json_serializable(monkeypatch) -> None:
    # Guards the fetcher record shape the cache + builder both consume.
    monkeypatch.setenv("MCP_MOLECULES_ONLINE", "1")
    _canned(
        monkeypatch,
        {
            "wbsearchentities": {"search": [{"id": "Q283"}]},
            "wbgetentities": {
                "entities": {
                    "Q283": {
                        "labels": {"en": {"value": "water"}},
                        "aliases": {"en": [{"value": "aqua"}]},
                        "claims": {
                            "P274": [
                                {"mainsnak": {"snaktype": "value", "datavalue": {"value": "H2O"}}}
                            ]
                        },
                    }
                }
            },
        },
    )
    recs = remote.wikidata_by_name("water")
    assert json.loads(json.dumps(recs)) == recs
    assert set(recs[0]) == {"ref", "name", "aliases", "formulas"}
