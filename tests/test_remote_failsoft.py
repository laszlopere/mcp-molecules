# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""Offline tests for the Tier-3 online fallback's fail-soft contract.

The live ``test_functional_*`` suites only run when the network is up (they are
``network``-marked and skip offline), so the error-handling code in
:mod:`mcp_molecules.remote` -- the module's headline promise that "every call
fails soft ... return ``[]``" -- never executes in an ordinary offline run.

These tests pin that contract deterministically and without a network: a
network error, a malformed/empty API response, an empty query, or an
unparseable formula must degrade to ``[]`` (or ``None`` at the transport layer)
rather than raise. ``_get_json`` is monkeypatched to feed canned responses, so
no socket is opened. The online fallback is forced back on per-test (the
conftest fixture forces it off for hermeticity).
"""

from __future__ import annotations

import urllib.error

import pytest

from mcp_molecules import remote


@pytest.fixture
def online(monkeypatch):
    """Force the online fallback on (conftest forces it off)."""
    monkeypatch.setenv("MCP_MOLECULES_ONLINE", "1")


@pytest.fixture
def epa_on(monkeypatch):
    """Force the online fallback on with an EPA API key configured."""
    monkeypatch.setenv("MCP_MOLECULES_ONLINE", "1")
    monkeypatch.setenv(remote.EPA_API_KEY_ENV, "test-key")


def _feed(monkeypatch, responses):
    """Monkeypatch ``remote._get_json`` to return queued values in order.

    ``responses`` is a list; each call pops the next one. A shorter list than
    the number of calls falls through to ``None`` (a missing/failed fetch).
    """
    queue = list(responses)

    def fake_get_json(url, headers=None):  # noqa: ARG001 -- signature parity
        return queue.pop(0) if queue else None

    monkeypatch.setattr(remote, "_get_json", fake_get_json)


# --- transport: _get_json ---------------------------------------------------


def test_get_json_returns_none_on_network_error(monkeypatch):
    """A raised urlopen (offline / DNS / timeout) degrades to ``None``."""

    def boom(*args, **kwargs):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(remote.urllib.request, "urlopen", boom)
    assert remote._get_json("https://example.invalid/x") is None


def test_get_json_returns_none_on_malformed_json(monkeypatch):
    """A 200 body that is not JSON degrades to ``None`` rather than raising."""

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"<html>not json</html>"

    monkeypatch.setattr(remote.urllib.request, "urlopen", lambda *a, **k: FakeResp())
    assert remote._get_json("https://example.invalid/x") is None


def test_get_json_merges_custom_headers(monkeypatch):
    """Custom headers (e.g. EPA's ``x-api-key``) ride on top of the defaults."""
    seen = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"ok": true}'

    def capture(req, timeout=None):  # noqa: ARG001
        seen.update(req.headers)
        return FakeResp()

    monkeypatch.setattr(remote.urllib.request, "urlopen", capture)
    out = remote._get_json("https://example.invalid/x", headers={"x-api-key": "secret"})
    assert out == {"ok": True}
    # urllib title-cases header keys; the default UA and the custom key both ride.
    assert seen.get("X-api-key") == "secret"
    assert "User-agent" in seen


# --- _hill_or_raw fallbacks -------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, ""),  # non-string -> ""
        (123, ""),  # non-string -> ""
        ("", ""),  # empty -> ""
        ("  notaformula  ", "notaformula"),  # unparseable -> stripped raw
        ("H2O", "H2O"),  # parseable -> Hill-canonical
    ],
)
def test_hill_or_raw(raw, expected):
    assert remote._hill_or_raw(raw) == expected


# --- empty query / disabled guards ------------------------------------------


def test_disabled_sources_return_empty(monkeypatch):
    """With the fallback opted out, every fetcher returns ``[]`` without a call."""
    monkeypatch.setenv("MCP_MOLECULES_ONLINE", "0")
    monkeypatch.setattr(remote, "_get_json", lambda *a, **k: pytest.fail("queried"))
    assert remote.wikidata_by_name("water") == []
    assert remote.wikidata_by_formula("H2O") == []
    assert remote.pubchem_by_name("water") == []
    assert remote.pubchem_by_formula("H2O") == []
    assert remote.epa_by_name("water") == []
    assert remote.epa_by_formula("H2O") == []


@pytest.mark.usefixtures("online")
def test_empty_query_short_circuits(monkeypatch):
    """A blank/whitespace query returns ``[]`` before any network call."""
    monkeypatch.setattr(remote, "_get_json", lambda *a, **k: pytest.fail("queried"))
    assert remote.wikidata_by_name("   ") == []  # normalizes to empty key
    assert remote.pubchem_by_name("   ") == []


@pytest.mark.usefixtures("epa_on")
def test_epa_empty_query_short_circuits(monkeypatch):
    monkeypatch.setattr(remote, "_get_json", lambda *a, **k: pytest.fail("queried"))
    assert remote.epa_by_name("   ") == []


@pytest.mark.usefixtures("online")
def test_unparseable_formula_returns_empty(monkeypatch):
    """An unparseable formula short-circuits the *_by_formula fetchers."""
    monkeypatch.setattr(remote, "_get_json", lambda *a, **k: pytest.fail("queried"))
    assert remote.wikidata_by_formula("notaformula") == []
    assert remote.pubchem_by_formula("notaformula") == []


@pytest.mark.usefixtures("epa_on")
def test_epa_unparseable_formula_returns_empty(monkeypatch):
    monkeypatch.setattr(remote, "_get_json", lambda *a, **k: pytest.fail("queried"))
    assert remote.epa_by_formula("notaformula") == []


def test_epa_skipped_without_api_key(monkeypatch):
    """No EPA key -> source unavailable -> ``[]`` with no query (like offline)."""
    monkeypatch.setenv("MCP_MOLECULES_ONLINE", "1")
    monkeypatch.delenv(remote.EPA_API_KEY_ENV, raising=False)
    assert remote.epa_available() is False
    monkeypatch.setattr(remote, "_get_json", lambda *a, **k: pytest.fail("queried"))
    assert remote.epa_by_name("water") == []
    assert remote.epa_by_formula("H2O") == []


# --- malformed / empty API responses ----------------------------------------


@pytest.mark.usefixtures("online")
def test_wikidata_by_name_failsoft(monkeypatch):
    # search fetch fails (None) -> []
    _feed(monkeypatch, [None])
    assert remote.wikidata_by_name("water") == []

    # search returns no hits -> []
    _feed(monkeypatch, [{"search": []}])
    assert remote.wikidata_by_name("water") == []

    # search OK, entities fetch fails -> []
    _feed(monkeypatch, [{"search": [{"id": "Q283"}]}, None])
    assert remote.wikidata_by_name("water") == []

    # entity missing / name mismatch / no formula -> dropped -> []
    _feed(
        monkeypatch,
        [
            {"search": [{"id": "Q283"}, {"id": "Q9"}]},
            {
                "entities": {
                    # Q283 present but label doesn't match the query "water"
                    "Q283": {"labels": {"en": {"value": "ethanol"}}, "claims": {}},
                    # Q9 omitted entirely -> entity is None -> skipped
                }
            },
        ],
    )
    assert remote.wikidata_by_name("water") == []

    # entity matches the query but its only P274 claim is a non-"value" snak
    # (e.g. "no value") -> no usable formula -> dropped -> []
    _feed(
        monkeypatch,
        [
            {"search": [{"id": "Q283"}]},
            {
                "entities": {
                    "Q283": {
                        "labels": {"en": {"value": "water"}},
                        "claims": {"P274": [{"mainsnak": {"snaktype": "novalue"}}]},
                    }
                }
            },
        ],
    )
    assert remote.wikidata_by_name("water") == []

    # entity matches the query but carries no formula claim at all -> dropped
    _feed(
        monkeypatch,
        [
            {"search": [{"id": "Q283"}]},
            {"entities": {"Q283": {"labels": {"en": {"value": "water"}}, "claims": {}}}},
        ],
    )
    assert remote.wikidata_by_name("water") == []


@pytest.mark.usefixtures("online")
def test_wikidata_by_formula_failsoft(monkeypatch):
    _feed(monkeypatch, [None])
    assert remote.wikidata_by_formula("H2O") == []

    # bindings present but label echoes the QID (no English label) -> dropped
    _feed(
        monkeypatch,
        [
            {
                "results": {
                    "bindings": [
                        {
                            "item": {"value": "http://www.wikidata.org/entity/Q283"},
                            "itemLabel": {"value": "Q283"},
                        }
                    ]
                }
            }
        ],
    )
    assert remote.wikidata_by_formula("H2O") == []


@pytest.mark.usefixtures("online")
def test_pubchem_failsoft(monkeypatch):
    # name lookup: PUG-REST 404 -> _get_json None -> []
    _feed(monkeypatch, [None])
    assert remote.pubchem_by_name("nope") == []

    # property rows missing CID / Title / formula -> dropped -> []
    _feed(
        monkeypatch,
        [{"PropertyTable": {"Properties": [{"CID": 1}, {"Title": "x"}]}}],
    )
    assert remote.pubchem_by_name("water") == []

    # formula lookup: fastformula returns no CIDs -> []
    _feed(monkeypatch, [{"IdentifierList": {"CID": []}}])
    assert remote.pubchem_by_formula("H2O") == []

    # formula lookup: fastformula fetch fails -> []
    _feed(monkeypatch, [None])
    assert remote.pubchem_by_formula("H2O") == []


@pytest.mark.usefixtures("epa_on")
def test_epa_failsoft(monkeypatch):
    # name search returns a non-list (error shape) -> []
    _feed(monkeypatch, [{"error": "bad"}])
    assert remote.epa_by_name("water") == []

    # hit without a dtxsid -> skipped; detail never fetched -> []
    _feed(monkeypatch, [[{"casrn": "7732-18-5"}]])
    assert remote.epa_by_name("water") == []

    # hit with dtxsid but detail fetch fails (None) -> dropped -> []
    _feed(monkeypatch, [[{"dtxsid": "DTXSID6020001"}], None])
    assert remote.epa_by_name("water") == []

    # detail present but missing name/formula -> dropped -> []
    _feed(monkeypatch, [[{"dtxsid": "DTXSID6020001"}], {"casrn": "x"}])
    assert remote.epa_by_name("water") == []

    # formula search returns a non-list -> []
    _feed(monkeypatch, [{"error": "bad"}])
    assert remote.epa_by_formula("H2O") == []

    # formula search yields blank / non-string ids -> skipped -> []
    _feed(monkeypatch, [["", 123, "  "]])
    assert remote.epa_by_formula("H2O") == []
