# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""Tests for inverse elemental analysis (TODO 4.9).

Round-trips known compounds: their percent composition (as a combustion / CHN
analyzer would report it) should recover the empirical formula, and -- given the
molar mass -- the molecular formula. Offline and deterministic.
"""

import pytest

from mcp_molecules.elemental import infer_formula
from mcp_molecules.server import elemental_analysis


def test_glucose_round_trip() -> None:
    # C6H12O6: C 40.00%, H 6.71%, O 53.29% -> empirical CH2O, molecular C6H12O6.
    r = infer_formula({"C": 40.00, "H": 6.71, "O": 53.29}, molar_mass=180.16)
    assert r["empirical_formula"] == "CH2O"
    assert r["molecular_formula"] == "C6H12O6"
    assert r["molecular_multiplier"] == 6
    assert r["fit"]["rms_error"] < 0.05
    assert r["warnings"] == []


def test_empirical_only_without_molar_mass() -> None:
    # Ethanol C2H6O: C 52.14%, H 13.13%, O 34.73%. Already in lowest terms.
    r = infer_formula({"C": 52.14, "H": 13.13, "O": 34.73})
    assert r["empirical_formula"] == "C2H6O"
    assert r["molecular_formula"] is None
    assert r["molecular_multiplier"] is None


def test_multiplier_search_needs_doubling() -> None:
    # Ethane C2H6: the mole ratio C:H is 1:3, so the empirical formula is CH3.
    r = infer_formula({"C": 79.89, "H": 20.11})
    assert r["empirical_formula"] == "CH3"


def test_benzene_reduces_to_ch() -> None:
    r = infer_formula({"C": 92.26, "H": 7.74}, molar_mass=78.11)
    assert r["empirical_formula"] == "CH"
    assert r["molecular_formula"] == "C6H6"


def test_oxygen_by_difference() -> None:
    # Caffeine C8H10N4O2 measured by CHN (C, H, N); O is the balance to 100%.
    r = infer_formula(
        {"C": 49.48, "H": 5.19, "N": 28.85},
        balance="O",
        molar_mass=194.19,
    )
    assert r["balance_element"] == "O"
    assert r["resolved_percentages"]["O"] == pytest.approx(16.48, abs=0.01)
    assert r["empirical_formula"] == "C4H5N2O"
    assert r["molecular_formula"] == "C8H10N4O2"


def test_predicted_composition_and_residuals() -> None:
    r = infer_formula({"C": 40.00, "H": 6.71, "O": 53.29})
    pred = r["fit"]["predicted_percentages"]
    assert set(pred) == {"C", "H", "O"}
    # Predicted composition sums to 100% and residuals are predicted - input.
    assert sum(pred.values()) == pytest.approx(100.0, abs=1e-6)
    resolved = r["resolved_percentages"]
    for sym in ("C", "H", "O"):
        assert r["fit"]["residuals"][sym] == pytest.approx(pred[sym] - resolved[sym])


def test_warns_when_percentages_do_not_sum_to_100() -> None:
    # Only C and H given for glucose; ~53% oxygen is silently missing.
    r = infer_formula({"C": 40.00, "H": 6.71})
    assert any("not ~100%" in w for w in r["warnings"])


def test_unknown_element_rejected() -> None:
    with pytest.raises(ValueError, match="unknown element 'Xx'"):
        infer_formula({"Xx": 100.0})


def test_non_positive_percentage_rejected() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        infer_formula({"C": 40.0, "H": 0.0})


def test_balance_element_also_given_rejected() -> None:
    with pytest.raises(ValueError, match="also given a percentage"):
        infer_formula({"C": 40.0, "O": 53.29}, balance="O")


def test_balance_with_no_remainder_rejected() -> None:
    with pytest.raises(ValueError, match="nothing left"):
        infer_formula({"C": 60.0, "H": 40.0}, balance="O")


def test_far_molar_mass_warns() -> None:
    # Glucose composition but a molar mass nowhere near a whole multiple of CH2O.
    r = infer_formula({"C": 40.00, "H": 6.71, "O": 53.29}, molar_mass=100.0)
    assert any(">5%" in w for w in r["warnings"])


def test_tool_matches_core() -> None:
    tool = elemental_analysis({"C": 40.00, "H": 6.71, "O": 53.29}, molar_mass=180.16)
    core = infer_formula({"C": 40.00, "H": 6.71, "O": 53.29}, molar_mass=180.16)
    assert tool == core
