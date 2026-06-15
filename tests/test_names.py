# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""Tests for the name<->formula store: normalization, builder, and lookups."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from mcp_molecules import names
from mcp_molecules.naming import hill_formula, normalize_name

_ROOT = Path(__file__).resolve().parent.parent


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "build_namedb", _ROOT / "tools" / "build_namedb.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- name normalization ----------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Aspirin (USAN)", "aspirin"),
        ("Foo (8CI)(9CI)", "foo"),  # stacked annotations
        ("  Water  ", "water"),
        ("acetylsalicylic acid (9CI)", "acetylsalicylic acid"),
        ("(R)-limonene", "(r)-limonene"),  # leading stereo descriptor preserved
        ("D₂O", "d2o"),  # subscript folded by NFKC
    ],
)
def test_normalize_name(raw: str, expected: str) -> None:
    assert normalize_name(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("C₆H₁₂O₆", "C6H12O6"),  # subscripts
        ("O6C6H12", "C6H12O6"),  # reordered -> Hill
        ("H2O", "H2O"),
        ("CH4", "CH4"),
        ("NaCl", "ClNa"),  # no carbon -> alphabetical
    ],
)
def test_hill_formula(raw: str, expected: str) -> None:
    assert hill_formula(raw) == expected


# --- builder + lookups -----------------------------------------------------

_FIXTURE = [
    {"ref": "Q1", "name": "water", "aliases": [], "formulas": ["H₂O"], "rank": 200},
    {
        "ref": "Q2",
        "name": "aspirin",
        "aliases": ["acetylsalicylic acid (9CI)"],
        "formulas": ["C9H8O4"],
        "rank": 100,
    },
    {
        "ref": "Q3",
        "name": "D-glucose",
        "aliases": ["glucose", "grape sugar"],
        "formulas": ["C6H12O6"],
        "rank": 50,
    },
    {
        "ref": "Q4",
        "name": "fructose",
        "aliases": ["fruit sugar"],
        "formulas": ["C6H12O6"],
        "rank": 40,
    },  # isomer of glucose
    {
        "ref": "Q5",
        "name": "copper(II) sulfate pentahydrate",
        "aliases": [],
        "formulas": ["CuSO4·5H2O"],
        "rank": 30,
    },  # unparseable -> raw
    {
        "ref": "Q6",
        "name": "",
        "aliases": ["acetone"],
        "formulas": ["C3H6O"],
        "rank": 20,
    },  # label promoted from alias
    {"ref": "Q7", "name": "", "aliases": [], "formulas": ["XX"], "rank": 5},  # dropped
    {"ref": "Q8", "name": "phlogiston", "aliases": [], "formulas": [], "rank": 1},  # dropped
]


@pytest.fixture
def db(tmp_path, monkeypatch):
    src = tmp_path / "fixture.jsonl"
    src.write_text("\n".join(json.dumps(r) for r in _FIXTURE), encoding="utf-8")
    builder = _load_builder()
    out = tmp_path / "names.db"
    stats = builder.build(builder.load_records(str(src)), str(out))

    con = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    names._connect.cache_clear()  # drop any real cached connection
    names._meta.cache_clear()
    monkeypatch.setattr(names, "_connect", lambda: con)
    yield stats
    # _connect is restored by monkeypatch; clear _meta so its fixture-bound
    # result does not leak into later tests.
    names._meta.cache_clear()


def test_builder_drops_and_counts(db) -> None:
    # Q7 (no name) and Q8 (no formula) dropped; 6 compounds remain.
    assert db["compounds"] == 6
    assert names.source_license() == ("wikidata", "CC0-1.0")


def test_name_to_formula_via_alias(db) -> None:
    matches = names.lookup_formula("grape sugar")
    assert matches[0]["formula"] == "C6H12O6"
    assert matches[0]["name"] == "D-glucose"


def test_name_to_formula_strips_annotation(db) -> None:
    assert names.lookup_formula("acetylsalicylic acid")[0]["formula"] == "C9H8O4"


def test_name_to_formula_promoted_alias(db) -> None:
    assert names.lookup_formula("acetone")[0]["formula"] == "C3H6O"


def test_formula_to_name_isomers_ordered(db) -> None:
    # glucose (more sitelinks -> lower id) ranks before fructose.
    matches = names.lookup_names("C6H12O6")
    found = [m["name"] for m in matches]
    assert found[0] == "D-glucose"
    assert "fructose" in found


def test_formula_to_name_subscript_and_reorder(db) -> None:
    assert names.lookup_names("C₆H₁₂O₆")[0]["name"] == "D-glucose"
    assert names.lookup_names("OH2")[0]["name"] == "water"  # Hill -> H2O


def test_unparseable_formula_raw_fallback(db) -> None:
    # The hydrate is stored raw (NFKC); querying the same raw form finds it.
    assert names.lookup_names("CuSO4·5H2O")[0]["name"].startswith("copper")


def test_missing_name_returns_empty(db) -> None:
    assert names.lookup_formula("unobtanium") == []
    assert names.lookup_names("Xe99") == []


# --- layered find_compound -------------------------------------------------


def test_directions_routing() -> None:
    # auto: parseable formula tried as formula first, else as name first.
    assert names._directions("C6H12O6", "auto") == ("formula", "name")
    assert names._directions("grape sugar", "auto") == ("name", "formula")
    # explicit overrides pin a single direction.
    assert names._directions("water", "name") == ("name",)
    assert names._directions("H2O", "formula") == ("formula",)


def test_find_compound_auto_name(db) -> None:
    r = names.find_compound("grape sugar")
    assert r["interpreted_as"] == "name"
    assert r["matches"][0] == {"name": "D-glucose", "formula": "C6H12O6"}
    assert r["source"] == "wikidata"


def test_find_compound_auto_formula(db) -> None:
    r = names.find_compound("C₆H₁₂O₆")
    assert r["interpreted_as"] == "formula"
    assert r["matches"][0] == {"name": "D-glucose", "formula": "C6H12O6"}


def test_find_compound_override(db) -> None:
    # "water" is a name; forcing a formula reading finds nothing.
    assert names.find_compound("water", by="formula")["matches"] == []
    assert names.find_compound("water", by="name")["matches"][0]["formula"] == "H2O"


def test_find_compound_not_found(db) -> None:
    r = names.find_compound("unobtanium")
    assert r["matches"] == []
    assert r["source"] == ""
