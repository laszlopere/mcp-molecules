# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""Behavior-focused functional tests for molecular_weight_calculator.

These complement ``test_server.py`` (which pins byte-for-byte parity with the C
``mwc`` tool) by probing invariants and edge cases the parity suite does not:
unit-conversion identities, the ordering/typing invariants of the ``masses``
dict, composition with the monoisotopic flavor, hand-derived uncertainty
propagation in quadrature, and parser corner cases (adjacent one/two-letter
symbols, deep nesting, multi-digit multipliers, isotope labels, whitespace).

Expected numbers are derived from standard atomic weights / NIST isotope masses
or computed in closed form; assertions target meaningful chemistry, not just
"whatever the code returns".
"""

from __future__ import annotations

import math

import pytest

from mcp_molecules.formula import FormulaError, parse_formula
from mcp_molecules.server import molecular_weight_calculator as mwc

# --- unit conversions -------------------------------------------------------


def test_da_and_u_equal_g_per_mol_numerically() -> None:
    # The dalton (Da) and unified atomic mass unit (u) are numerically equal to
    # g/mol; only the label differs, the weight must be identical.
    g = mwc("C6H12O6", unit="g/mol")["weight"]
    da = mwc("C6H12O6", unit="Da")
    u = mwc("C6H12O6", unit="u")
    assert da["weight"] == g
    assert u["weight"] == g
    assert da["formatted"].endswith(" Da")
    assert u["formatted"].endswith(" u")
    # ...and the numeric prefix matches g/mol's rendering.
    assert da["formatted"].split()[0] == u["formatted"].split()[0]


def test_kda_scales_by_one_thousandth() -> None:
    # kDa is g/mol / 1000, same factor as kg/mol.
    g = mwc("C6H12O6", unit="g/mol")["weight"]
    kda = mwc("C6H12O6", unit="kDa")["weight"]
    assert kda == pytest.approx(g / 1000.0)
    assert kda == pytest.approx(mwc("C6H12O6", unit="kg/mol")["weight"])


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        ("g/mol", "180.16 g/mol"),  # 2 decimals
        ("Da", "180.16 Da"),  # 2 decimals
        ("u", "180.16 u"),  # 2 decimals
        ("kg/mol", "0.18016 kg/mol"),  # 5 decimals
        ("kDa", "0.18016 kDa"),  # 5 decimals
    ],
)
def test_default_decimal_places_per_unit(unit: str, expected: str) -> None:
    assert mwc("C6H12O6", unit=unit)["formatted"] == expected


# --- masses dict invariants -------------------------------------------------


@pytest.mark.parametrize("formula", ["H2O", "C6H12O6", "CH4", "C2H5OH", "C6H6", "C9H8O4"])
def test_flavor_ordering_for_organics(formula: str) -> None:
    # For typical organics: nominal <= monoisotopic < average. The hydrogen mass
    # excess pushes the monoisotopic mass just above the integer nominal sum,
    # while averaging over heavier isotopes pulls the average mass higher still.
    m = mwc(formula)["masses"]
    nominal = m["nominal"]["weight"]
    mono = m["monoisotopic"]["weight"]
    avg = m["average"]["weight"]
    assert nominal <= mono < avg


def test_nominal_is_integer_valued() -> None:
    # The nominal mass is a sum of integer mass numbers; it must be a whole
    # number regardless of how the float is stored.
    nominal = mwc("C9H8O4")["masses"]["nominal"]["weight"]
    assert nominal == pytest.approx(round(nominal))
    # Aspirin C9H8O4: 9*12 + 8*1 + 4*16 = 180.
    assert nominal == pytest.approx(180.0)


@pytest.mark.parametrize("formula", ["H2O", "C6H12O6", "CH4", "Tc"])
def test_monoisotopic_formatted_shows_at_least_four_decimals(formula: str) -> None:
    # Monoisotopic masses differ from nominal in the 4th decimal, so the field is
    # always widened to >= 4 fractional digits even in a 2-decimal unit.
    formatted = mwc(formula, unit="g/mol")["masses"]["monoisotopic"]["formatted"]
    frac = formatted.split()[0].split(".")[1]
    assert len(frac) >= 4


# --- composition ------------------------------------------------------------


def test_composition_monoisotopic_percentages_and_subtotals() -> None:
    r = mwc("H2O", composition=True, monoisotopic=True)
    rows = {row["element"]: row for row in r["composition"]}
    # Subtotals must sum back to the reported total (monoisotopic flavor here).
    total = r["total_weight_g_per_mol"]
    assert sum(row["subtotal_g_per_mol"] for row in r["composition"]) == pytest.approx(total)
    # Percent rows sum to ~100.
    assert sum(row["percent"] for row in r["composition"]) == pytest.approx(100.0)
    # Monoisotopic water = 2*1.0078250 + 15.9949146 = 18.01056 u.
    assert total == pytest.approx(18.01056, abs=1e-4)
    # H share is slightly higher monoisotopically than averaged (~11.19%).
    assert rows["H"]["percent"] == pytest.approx(11.19, abs=0.05)


def test_composition_is_independent_of_output_unit() -> None:
    # Composition is computed in g/mol by mass and should not depend on the unit
    # chosen for the headline weight.
    base = mwc("C2H6O", composition=True, unit="g/mol")
    scaled = mwc("C2H6O", composition=True, unit="kDa")
    assert base["composition"] == scaled["composition"]
    assert base["total_weight_g_per_mol"] == scaled["total_weight_g_per_mol"]


# --- uncertainty propagation ------------------------------------------------


def test_uncertainty_combines_in_quadrature() -> None:
    # CO2: sigma = sqrt((1*sigma_C)^2 + (2*sigma_O)^2). Standard-atomic-weight
    # half-widths: C ~= 0.001, O ~= 0.00037. Compute the closed-form expectation
    # from the table and assert the tool matches it.
    from mcp_molecules.weights import load_weights

    aw = load_weights(False)
    expected = math.sqrt((1 * aw["C"][1]) ** 2 + (2 * aw["O"][1]) ** 2)
    assert expected == pytest.approx(0.001244, abs=1e-6)
    got = mwc("CO2", uncertainty=True)["masses"]["average"]["uncertainty"]
    assert got == pytest.approx(expected)


def test_nominal_flavor_uncertainty_is_zero_or_none() -> None:
    # Nominal masses are exact integers, so their uncertainty is 0.0 when
    # requested and None when not.
    on = mwc("C6H12O6", uncertainty=True)["masses"]["nominal"]
    off = mwc("C6H12O6", uncertainty=False)["masses"]["nominal"]
    assert on["uncertainty"] == 0.0
    assert off["uncertainty"] is None


# --- elements without a stable isotope --------------------------------------


def test_radioactive_element_still_yields_all_flavors() -> None:
    # Technetium has no stable isotope; the table falls back to the most stable
    # one (Tc-98). All three flavors must still be present.
    m = mwc("Tc")["masses"]
    assert set(m) == {"nominal", "average", "monoisotopic"}
    assert m["nominal"]["weight"] == pytest.approx(98.0)
    # With a single isotope, average and monoisotopic coincide.
    assert m["average"]["weight"] == pytest.approx(m["monoisotopic"]["weight"])
    assert m["average"]["weight"] == pytest.approx(97.907, abs=1e-3)


# --- parser: symbol disambiguation ------------------------------------------


@pytest.mark.parametrize(
    ("formula", "expected"),
    [
        # Two-letter symbol Co adjacent to Cl (two-letter) vs C+O+Cl (one-letter).
        ("CoCl2", [("Co", 1), ("Cl", 2)]),
        ("CoCl", [("Co", 1), ("Cl", 1)]),
        ("COCl", [("C", 1), ("O", 1), ("Cl", 1)]),
        ("COCl2", [("C", 1), ("O", 1), ("Cl", 2)]),  # phosgene
    ],
)
def test_one_vs_two_letter_symbol_adjacency(formula: str, expected) -> None:
    assert parse_formula(formula) == expected


def test_single_atom_without_count() -> None:
    assert parse_formula("Mg") == [("Mg", 1)]
    assert parse_formula("H") == [("H", 1)]


def test_isotope_labels_combined_with_normal_elements() -> None:
    # D (deuterium) and T (tritium) are first-class single-isotope symbols.
    assert parse_formula("DTO") == [("D", 1), ("T", 1), ("O", 1)]
    # Repeated H (first atom and the OH) is summed; D stays distinct.
    assert parse_formula("CHD2OH") == [("C", 1), ("H", 2), ("D", 2), ("O", 1)]


# --- parser: nesting and multipliers ----------------------------------------


def test_deeply_nested_groups() -> None:
    # Triple-nested wrap collapses to the bare atom.
    assert parse_formula("(((H)))") == [("H", 1)]
    # Nested groups with their own multipliers compound multiplicatively.
    assert parse_formula("(C(H2)2)3") == [("C", 3), ("H", 12)]
    # Tutton's salt-style hydrate: Fe(NH4)2(SO4)2(H2O)6.
    assert parse_formula("Fe(NH4)2(SO4)2(H2O)6") == [
        ("Fe", 1),
        ("N", 2),
        ("H", 20),
        ("S", 2),
        ("O", 14),
    ]


def test_multi_digit_multipliers() -> None:
    assert parse_formula("C100") == [("C", 100)]
    assert parse_formula("(CH2)12") == [("C", 12), ("H", 24)]


# --- parser: error behavior -------------------------------------------------


@pytest.mark.parametrize("bad", ["()", "C-2", "123", "(CH2", "CH2)"])
def test_additional_parse_errors(bad: str) -> None:
    with pytest.raises(FormulaError):
        parse_formula(bad)


@pytest.mark.parametrize("formula", [" H2O", "H2O ", "  H2O  ", "\tH2O\n"])
def test_leading_and_trailing_whitespace_is_trimmed(formula: str) -> None:
    # Surrounding whitespace is stripped before parsing (TODO 7.1).
    assert parse_formula(formula) == [("H", 2), ("O", 1)]


@pytest.mark.parametrize("bad", ["H2 O", "C 6 H12 O6", "Na Cl"])
def test_internal_whitespace_is_still_rejected(bad: str) -> None:
    # Whitespace *inside* a formula remains an unexpected character.
    with pytest.raises(FormulaError):
        parse_formula(bad)


@pytest.mark.parametrize("bad", ["H0", "H00", "C0H4", "(CH2)0", "O2H0"])
def test_zero_atom_count_is_rejected(bad: str) -> None:
    # A zero multiplier is meaningless and now errors instead of yielding a
    # count-0 entry (TODO 7.2).
    with pytest.raises(FormulaError):
        parse_formula(bad)


def test_explicit_count_of_one_is_accepted() -> None:
    # An explicit "1" is fine and equals the implicit (no-digit) count.
    assert parse_formula("H1") == [("H", 1)]
    assert parse_formula("H1") == parse_formula("H")
    assert parse_formula("C1H4") == [("C", 1), ("H", 4)]


# --- molecular_weight_calculator error surfacing ----------------------------


def test_unknown_element_message_names_the_symbol() -> None:
    with pytest.raises(ValueError, match="unknown element 'Zz'"):
        mwc("Zz3")


def test_unparseable_formula_raises_valueerror() -> None:
    # parse_formula raises FormulaError, which is a ValueError subclass.
    with pytest.raises(ValueError):
        mwc("(((")


def test_empty_formula_raises() -> None:
    with pytest.raises(ValueError):
        mwc("")
