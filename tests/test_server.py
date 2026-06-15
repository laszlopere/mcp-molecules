# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""Tests for the mcp-molecules server and molecular-weight calculation.

Expected values are byte-for-byte parity with the original C ``mwc`` tool.
"""

import asyncio

import pytest

from mcp_molecules.formula import FormulaError, parse_formula
from mcp_molecules.server import (
    find_chemical_compound,
    info,
    isotope_distribution,
    mcp,
    molecular_weight_calculator,
)


def test_info_reports_name() -> None:
    result = info()
    assert result["name"] == "mcp-molecules"
    assert result["status"] == "available"


def test_tools_registered() -> None:
    names = {tool.name for tool in asyncio.run(mcp.list_tools())}
    assert "info" in names
    assert "molecular_weight_calculator" in names
    assert "find_chemical_compound" in names
    assert "isotope_distribution" in names
    # The directional tools were folded into find_chemical_compound.
    assert "name_to_formula" not in names
    assert "formula_to_name" not in names


# --- find_chemical_compound (end-to-end over the bundled DB) ----------------


def test_find_chemical_compound_by_name() -> None:
    r = find_chemical_compound("aspirin")
    assert r["interpreted_as"] == "name"
    assert r["matches"][0]["formula"] == "C9H8O4"


def test_find_chemical_compound_by_formula() -> None:
    r = find_chemical_compound("C9H8O4")
    assert r["interpreted_as"] == "formula"
    assert any(m["name"].lower() == "aspirin" for m in r["matches"])


def test_find_chemical_compound_not_found() -> None:
    with pytest.raises(ValueError, match="no chemical compound found"):
        find_chemical_compound("zzznotacompoundzzz")


# --- formula parser --------------------------------------------------------


def test_parse_simple() -> None:
    assert parse_formula("H2O") == [("H", 2), ("O", 1)]


def test_parse_nested_groups() -> None:
    assert parse_formula("Fe2(SO4)3") == [("Fe", 2), ("S", 3), ("O", 12)]
    assert parse_formula("((CH3)2CH)2") == [("C", 6), ("H", 14)]


def test_parse_repeats_are_summed_in_order() -> None:
    assert parse_formula("CH3CH2OH") == [("C", 2), ("H", 6), ("O", 1)]


def test_parse_unicode_subscripts() -> None:
    assert parse_formula("H₂O") == parse_formula("H2O")
    assert parse_formula("Fe₂(SO₄)₃") == parse_formula("Fe2(SO4)3")
    assert parse_formula("C₆H₁₂O₆") == [("C", 6), ("H", 12), ("O", 6)]


@pytest.mark.parametrize("bad", ["", "H2O)", "(H2O", "2H", ")"])
def test_parse_errors(bad: str) -> None:
    with pytest.raises(FormulaError):
        parse_formula(bad)


# --- molecular weight (parity with C mwc) ----------------------------------


@pytest.mark.parametrize(
    ("formula", "expected"),
    [
        ("H2O", "18.02 g/mol"),
        ("C6H12O6", "180.16 g/mol"),
        ("Ca(OH)2", "74.09 g/mol"),
        ("Fe2(SO4)3", "399.89 g/mol"),
        ("((CH3)2CH)2", "86.18 g/mol"),
        ("D2O", "20.03 g/mol"),
        ("Tc", "97.91 g/mol"),
    ],
)
def test_molecular_weight(formula: str, expected: str) -> None:
    assert molecular_weight_calculator(formula)["formatted"] == expected


def test_units() -> None:
    assert molecular_weight_calculator("C6H12O6", unit="Da")["formatted"] == "180.16 Da"
    assert molecular_weight_calculator("C6H12O6", unit="kg/mol")["formatted"] == "0.18016 kg/mol"


def test_uncertainty_widens_precision() -> None:
    # The half-width of the NIST interval is propagated in quadrature.
    assert molecular_weight_calculator("H2O", uncertainty=True)["formatted"] == (
        "18.0153 ± 0.0005 g/mol"
    )
    r = molecular_weight_calculator("C6H12O6", unit="kg/mol", uncertainty=True)
    assert r["formatted"] == "0.180156 ± 0.000007 kg/mol"


def test_monoisotopic_widens_to_four_decimals() -> None:
    r = molecular_weight_calculator("D2O", monoisotopic=True)
    assert r["formatted"] == "20.0231 g/mol"
    assert r["monoisotopic"] is True


def test_composition() -> None:
    r = molecular_weight_calculator("H2O", composition=True)
    comp = {row["element"]: row for row in r["composition"]}
    assert comp["H"]["count"] == 2
    assert comp["O"]["count"] == 1
    assert comp["H"]["percent"] == pytest.approx(11.19, abs=0.01)
    assert comp["O"]["percent"] == pytest.approx(88.81, abs=0.01)
    assert r["total_weight_g_per_mol"] == pytest.approx(18.015, abs=0.01)


def test_unknown_element_raises() -> None:
    with pytest.raises(ValueError, match="unknown element 'Xx'"):
        molecular_weight_calculator("Xx2")


# --- isotope distribution --------------------------------------------------


def _rel(result: dict) -> dict[int, float]:
    """Map nominal mass -> relative intensity for easy assertions."""
    return {p["nominal"]: p["relative"] for p in result["peaks"]}


def test_isotope_pattern_three_chlorines() -> None:
    # Chloroform's textbook M:M+2:M+4:M+6 ~ 100:96:31:3 triplet-plus.
    rel = _rel(isotope_distribution("CHCl3"))
    assert rel[118] == 100.0
    assert rel[120] == pytest.approx(96.0, abs=1.0)
    assert rel[122] == pytest.approx(30.7, abs=1.0)
    assert rel[124] == pytest.approx(3.3, abs=0.5)


def test_isotope_pattern_bromine_doublet() -> None:
    # One bromine gives a near-1:1 M / M+2 doublet (79Br / 81Br).
    rel = _rel(isotope_distribution("C6H5Br"))
    assert rel[156] == 100.0
    assert rel[158] == pytest.approx(97.3, abs=1.0)


def test_isotope_monoisotopic_and_average_mass() -> None:
    r = isotope_distribution("H2O")
    assert r["monoisotopic_mass"] == pytest.approx(18.0106, abs=1e-3)
    assert r["average_mass"] == pytest.approx(18.0153, abs=1e-3)


def test_isotope_charge_reports_mz() -> None:
    # [M+H]+ of water: (18.0106 + 1.00728) / 1.
    r = isotope_distribution("H2O", charge=1)
    base = r["base_peak"]
    assert base["mz"] == pytest.approx(19.0178, abs=1e-3)
    assert r["monoisotopic_mz"] == pytest.approx(19.0178, abs=1e-3)
    # Neutral mode carries no m/z.
    assert "mz" not in isotope_distribution("H2O")["base_peak"]


def test_isotope_negative_charge_subtracts_proton() -> None:
    # [M-H]- halves nothing at |z|=1 but removes a proton.
    r = isotope_distribution("CH2O2", charge=-1)  # formic acid
    assert r["base_peak"]["mz"] == pytest.approx(r["base_peak"]["mass"] - 1.00728, abs=1e-3)


def test_isotope_doubly_charged_halves_mz() -> None:
    r = isotope_distribution("C6H12O6", charge=2)
    base = r["base_peak"]
    assert base["mz"] == pytest.approx((base["mass"] + 2 * 1.00728) / 2, abs=1e-3)


def test_isotope_exact_grouping_resolves_isotopologues() -> None:
    # CCl4's base peak is M+2 (one 37Cl), not the all-35Cl peak.
    r = isotope_distribution("CCl4", grouping="exact", limit=4)
    assert r["base_peak"]["nominal"] == 154
    assert _rel(r)[152] == pytest.approx(78.1, abs=1.0)


def test_isotope_label_shifts_pattern() -> None:
    # Explicit D pins deuterium: D2O base peak sits at nominal 20.
    r = isotope_distribution("D2O")
    assert r["base_peak"]["nominal"] == 20


def test_isotope_no_natural_abundance_element() -> None:
    # Tc has no natural isotopes; falls back to the most stable (Tc-98).
    r = isotope_distribution("Tc")
    assert r["base_peak"]["nominal"] == 98


def test_isotope_threshold_and_limit() -> None:
    full = isotope_distribution("CHCl3", threshold=0.0, limit=100)
    trimmed = isotope_distribution("CHCl3", threshold=5.0, limit=3)
    assert len(trimmed["peaks"]) <= 3
    assert all(p["relative"] >= 5.0 for p in trimmed["peaks"])
    assert len(full["peaks"]) > len(trimmed["peaks"])


def test_isotope_unknown_element_raises() -> None:
    with pytest.raises(ValueError, match="no isotope data for element 'Xx'"):
        isotope_distribution("Xx2")
