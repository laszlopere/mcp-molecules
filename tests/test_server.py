# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""Tests for the mcp-molecules server and molecular-weight calculation.

Expected values are byte-for-byte parity with the original C ``mwc`` tool.
"""

import asyncio

import pytest

from mcp_molecules.formula import FormulaError, parse_formula
from mcp_molecules.server import info, mcp, molecular_weight_calculator


def test_info_reports_name() -> None:
    result = info()
    assert result["name"] == "mcp-molecules"
    assert result["status"] == "available"


def test_tools_registered() -> None:
    names = {tool.name for tool in asyncio.run(mcp.list_tools())}
    assert "info" in names
    assert "molecular_weight_calculator" in names


# --- formula parser --------------------------------------------------------


def test_parse_simple() -> None:
    assert parse_formula("H2O") == [("H", 2), ("O", 1)]


def test_parse_nested_groups() -> None:
    assert parse_formula("Fe2(SO4)3") == [("Fe", 2), ("S", 3), ("O", 12)]
    assert parse_formula("((CH3)2CH)2") == [("C", 6), ("H", 14)]


def test_parse_repeats_are_summed_in_order() -> None:
    assert parse_formula("CH3CH2OH") == [("C", 2), ("H", 6), ("O", 1)]


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
