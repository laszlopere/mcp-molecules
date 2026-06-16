# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""End-to-end functional tests for the ``find_chemical_compound`` MCP tool.

Unlike ``test_names.py`` (which builds a tiny fixture DB) these exercise the
*real* bundled PubChem subset shipped in the package, driving the public MCP
tool surface. The conftest fixture forces ``MCP_MOLECULES_ONLINE=0`` and an
isolated empty Tier-2 cache, so every assertion is hermetic, offline, and
deterministic. Every compound asserted on has been verified present in the
bundled database.
"""

from __future__ import annotations

import pytest

from mcp_molecules import names
from mcp_molecules.server import find_chemical_compound

# Provenance tags carried by every hit served from the bundled subset.
_SOURCE = "pubchem"
_LICENSE = "public-domain"


# --- the full result contract ----------------------------------------------


def test_result_contract_for_a_name_hit() -> None:
    r = find_chemical_compound("caffeine")
    # Exact key set the MCP tool promises its callers.
    assert set(r) == {"query", "interpreted_as", "matches", "source", "license"}
    assert r["query"] == "caffeine"
    assert r["interpreted_as"] == "name"
    assert r["source"] == _SOURCE
    assert r["license"] == _LICENSE
    assert r["matches"] == [{"name": "Caffeine", "formula": "C8H10N4O2"}]
    # Every match is a {"name", "formula"} pair.
    for m in r["matches"]:
        assert set(m) == {"name", "formula"}


def test_not_found_raises_value_error_with_query_in_message() -> None:
    # Fe2O3 is absent from the everyday subset; offline there is no fallback.
    with pytest.raises(ValueError, match=r"no chemical compound found for 'Fe2O3'"):
        find_chemical_compound("Fe2O3")


# --- name -> formula and formula -> name over real data --------------------


@pytest.mark.parametrize(
    ("query", "name", "formula"),
    [
        ("caffeine", "Caffeine", "C8H10N4O2"),
        ("ethanol", "Ethanol", "C2H6O"),
        ("benzene", "Benzene", "C6H6"),
        ("methane", "Methane", "CH4"),
        ("acetone", "Acetone", "C3H6O"),
        ("aspirin", "Aspirin", "C9H8O4"),
        ("water", "Water", "H2O"),
    ],
)
def test_name_resolves_to_formula(query: str, name: str, formula: str) -> None:
    r = find_chemical_compound(query)
    assert r["interpreted_as"] == "name"
    assert r["matches"][0] == {"name": name, "formula": formula}


@pytest.mark.parametrize(
    ("formula", "expected_name"),
    [
        ("C8H10N4O2", "Caffeine"),
        ("C6H6", "Benzene"),
        ("CH4", "Methane"),
        ("CO", "Carbon Monoxide"),
    ],
)
def test_formula_resolves_to_name(formula: str, expected_name: str) -> None:
    r = find_chemical_compound(formula)
    assert r["interpreted_as"] == "formula"
    assert r["matches"][0]["name"] == expected_name
    assert r["matches"][0]["formula"] == formula


# --- by="auto" routing on real data ----------------------------------------


def test_auto_interprets_parseable_formula_as_formula() -> None:
    assert find_chemical_compound("C2H6O")["interpreted_as"] == "formula"


def test_auto_interprets_word_as_name() -> None:
    assert find_chemical_compound("ethanol")["interpreted_as"] == "name"


def test_auto_falls_back_to_formula_when_name_misses() -> None:
    # "C5H14NO+" cannot be parsed as a formula (the trailing charge), so auto
    # tries it as a name first -- a miss -- then falls through to a raw formula
    # lookup, which matches the cation choline. The hit is reported as a formula.
    r = find_chemical_compound("C5H14NO+")
    assert r["interpreted_as"] == "formula"
    assert r["matches"][0] == {"name": "Choline", "formula": "C5H14NO+"}
    # Pinning the failing direction confirms the fallback is what rescued it.
    with pytest.raises(ValueError, match="no chemical compound found"):
        find_chemical_compound("C5H14NO+", by="name")


# --- by="name" vs by="formula" pinning -------------------------------------


def test_pinning_formula_direction_on_a_name_misses() -> None:
    # "ethanol" is a name; forcing a formula reading finds nothing and raises.
    with pytest.raises(ValueError, match="no chemical compound found"):
        find_chemical_compound("ethanol", by="formula")
    # The pin is the only difference: as a name it resolves cleanly.
    assert find_chemical_compound("ethanol", by="name")["matches"][0]["formula"] == "C2H6O"


def test_pinning_name_direction_on_a_formula_misses() -> None:
    # "C8H10N4O2" is a formula and is not stored as a name; forcing a name
    # reading finds nothing and raises.
    with pytest.raises(ValueError, match="no chemical compound found"):
        find_chemical_compound("C8H10N4O2", by="name")
    # As a formula it resolves to caffeine.
    r = find_chemical_compound("C8H10N4O2", by="formula")
    assert r["matches"][0]["name"] == "Caffeine"


def test_formula_with_multiple_isomers_lists_them() -> None:
    r = find_chemical_compound("C2H6O", by="formula")
    found = {m["name"] for m in r["matches"]}
    assert {"Ethanol", "Dimethyl Ether"} <= found


def test_find_compound_empty_matches_does_not_raise() -> None:
    # The library layer returns empty matches (the tool is what raises). The
    # interpreted_as still reflects the first attempted direction.
    r = names.find_compound("ethanol", by="formula")
    assert r["matches"] == []
    assert r["interpreted_as"] == "formula"
    assert r["source"] == ""
    assert r["license"] == ""


# --- limit caps isomer matches ---------------------------------------------

# C6H12O6 has 7 isomers in the bundled subset, preferred name first.
_HEXOSE_FIRST = "An inositol"
_HEXOSE_TOTAL = 7


@pytest.mark.parametrize(
    ("limit", "expected"),
    [(1, 1), (3, 3), (7, 7), (50, _HEXOSE_TOTAL)],
)
def test_limit_caps_isomer_matches(limit: int, expected: int) -> None:
    r = find_chemical_compound("C6H12O6", limit=limit)
    assert len(r["matches"]) == expected
    # Ordering is preferred-first and stable regardless of the cap.
    assert r["matches"][0]["name"] == _HEXOSE_FIRST
    assert all(m["formula"] == "C6H12O6" for m in r["matches"])


def test_limit_one_is_a_strict_prefix_of_a_larger_limit() -> None:
    few = [m["name"] for m in find_chemical_compound("C6H12O6", limit=1)["matches"]]
    more = [m["name"] for m in find_chemical_compound("C6H12O6", limit=5)["matches"]]
    assert more[:1] == few
    assert len(more) > len(few)


# --- case-insensitivity and alias / annotation resolution ------------------


@pytest.mark.parametrize("query", ["caffeine", "CAFFEINE", "Caffeine", "CaFfEiNe"])
def test_name_lookup_is_case_insensitive(query: str) -> None:
    r = find_chemical_compound(query)
    assert r["matches"][0] == {"name": "Caffeine", "formula": "C8H10N4O2"}


def test_alias_resolves_to_canonical_name() -> None:
    # An alias maps to the compound's canonical display name, not the alias.
    r = find_chemical_compound("acetylsalicylic acid")
    assert r["interpreted_as"] == "name"
    assert r["matches"][0] == {"name": "Aspirin", "formula": "C9H8O4"}


@pytest.mark.parametrize("query", ["caffeine (9CI)", "Caffeine (USAN)", "caffeine (8CI)"])
def test_trailing_registry_annotation_is_stripped(query: str) -> None:
    r = find_chemical_compound(query)
    assert r["matches"][0] == {"name": "Caffeine", "formula": "C8H10N4O2"}


# --- formula canonicalization through the tool -----------------------------


@pytest.mark.parametrize("query", ["H2O", "OH2", "H₂O"])
def test_formula_forms_canonicalize_to_same_compound(query: str) -> None:
    r = find_chemical_compound(query)
    assert r["interpreted_as"] == "formula"
    assert r["matches"][0]["name"] == "Water"
    assert r["matches"][0]["formula"] == "H2O"


def test_reordered_formula_equals_hill_form() -> None:
    canonical = find_chemical_compound("C6H12O6", limit=7)["matches"]
    reordered = find_chemical_compound("O6C6H12", limit=7)["matches"]
    assert reordered == canonical


def test_carbon_free_formula_uses_alphabetical_hill_key() -> None:
    # No carbon -> elements sorted alphabetically: NaCl canonicalizes to ClNa.
    r = find_chemical_compound("NaCl")
    assert r["interpreted_as"] == "formula"
    assert r["matches"][0] == {"name": "Sodium Chloride", "formula": "ClNa"}


# --- determinism -----------------------------------------------------------


@pytest.mark.parametrize("query", ["C6H12O6", "caffeine", "C2H6O", "H2O"])
def test_repeated_query_is_identical(query: str) -> None:
    first = find_chemical_compound(query, limit=10)
    second = find_chemical_compound(query, limit=10)
    assert first == second
