# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""Behaviour-focused tests for the isotope_distribution tool.

These complement the cases in ``test_server.py`` (the ``--- isotope
distribution ---`` block). The expected numbers are anchored to real isotope
chemistry -- textbook MS patterns and NIST natural abundances (13C 1.07%,
34S 4.25%, 29Si 4.68% / 30Si 3.09%, 35Cl 75.76% / 37Cl 24.24%, 79Br 50.69% /
81Br 49.31%) -- not to whatever the implementation happens to emit.
"""

from __future__ import annotations

import pytest

from mcp_molecules import isotopes
from mcp_molecules.server import isotope_distribution, molecular_weight_calculator

# Proton mass used by the implementation for [M+/-nH] ions.
_PROTON = 1.007276466


def _rel(result: dict) -> dict[int, float]:
    """Map nominal mass -> relative intensity for easy assertions."""
    return {p["nominal"]: p["relative"] for p in result["peaks"]}


# --- result/peak shape -----------------------------------------------------


def test_neutral_peak_keys_and_no_mz() -> None:
    r = isotope_distribution("C6H12O6")
    for p in r["peaks"]:
        assert set(p) == {"nominal", "mass", "relative", "abundance"}
        assert isinstance(p["nominal"], int)
        assert p["mass"] > 0
        assert 0.0 < p["relative"] <= 100.0
        assert p["abundance"] > 0
    # Neutral mode: no m/z anywhere.
    assert r["monoisotopic_mz"] is None
    assert "mz" not in r["base_peak"]


def test_base_peak_is_most_intense_and_sorted_descending() -> None:
    r = isotope_distribution("CH2Cl2", threshold=0.0, limit=100)
    peaks = r["peaks"]
    # base_peak is the first (most intense) peak and normalised to 100%.
    assert r["base_peak"] is peaks[0]
    assert r["base_peak"]["relative"] == 100.0
    assert r["base_peak"]["abundance"] == max(p["abundance"] for p in peaks)
    # Peaks come back sorted by intensity, descending.
    abundances = [p["abundance"] for p in peaks]
    assert abundances == sorted(abundances, reverse=True)


def test_charged_result_carries_mz_keys() -> None:
    r = isotope_distribution("C6H12O6", charge=1)
    assert r["monoisotopic_mz"] is not None
    for p in r["peaks"]:
        assert set(p) == {"nominal", "mass", "relative", "abundance", "mz"}
        assert p["mz"] == pytest.approx(p["mass"] + _PROTON, abs=1e-3)


# --- grouping: unit vs exact -----------------------------------------------


@pytest.mark.parametrize("formula", ["CH2Cl2", "C6H5Br", "C6H12O6"])
def test_exact_has_at_least_as_many_peaks_as_unit(formula: str) -> None:
    # 'exact' resolves isotopologues that share a nominal mass, so for a
    # multi-isotope molecule it can only have >= as many peaks as 'unit'.
    unit = isotope_distribution(formula, threshold=0.0, limit=100)
    exact = isotope_distribution(formula, grouping="exact", threshold=0.0, limit=100)
    assert len(exact["peaks"]) >= len(unit["peaks"])


def test_unit_grouping_yields_distinct_integer_nominals() -> None:
    r = isotope_distribution("C6H5Br", threshold=0.0, limit=100)
    nominals = [p["nominal"] for p in r["peaks"]]
    assert all(isinstance(n, int) for n in nominals)
    assert len(nominals) == len(set(nominals))  # one peak per integer mass
    # In 'unit' the reported mass is the centroid, sitting near its integer.
    for p in r["peaks"]:
        assert abs(p["mass"] - p["nominal"]) < 0.5


def test_exact_multi_isotope_has_overlapping_nominals() -> None:
    # 'exact' keeps several distinct exact masses under one nominal; 'unit'
    # collapses them, so exact has strictly more peaks here.
    unit = isotope_distribution("C6H12O6", threshold=0.0, limit=100)
    exact = isotope_distribution("C6H12O6", grouping="exact", threshold=0.0, limit=100)
    assert len(exact["peaks"]) > len(unit["peaks"])
    exact_nominals = [p["nominal"] for p in exact["peaks"]]
    assert len(exact_nominals) != len(set(exact_nominals))  # repeats exist


# --- threshold / limit interaction -----------------------------------------


def test_threshold_filters_and_base_peak_survives() -> None:
    r = isotope_distribution("C6H12O6", threshold=2.0, limit=100)
    assert all(p["relative"] >= 2.0 for p in r["peaks"])
    # The base peak (100%) always clears a sane threshold.
    assert r["base_peak"]["relative"] == 100.0
    assert 0 in {p["nominal"] - r["base_peak"]["nominal"] for p in r["peaks"]}


def test_limit_caps_count_but_keeps_the_strongest() -> None:
    full = isotope_distribution("CH2Cl2", threshold=0.0, limit=100)
    capped = isotope_distribution("CH2Cl2", threshold=0.0, limit=2)
    assert len(capped["peaks"]) == 2
    # The two strongest peaks of the full pattern are exactly what we keep.
    strongest = sorted(full["peaks"], key=lambda p: -p["abundance"])[:2]
    assert {p["nominal"] for p in capped["peaks"]} == {p["nominal"] for p in strongest}


# --- quantitative isotope patterns -----------------------------------------


@pytest.mark.parametrize("carbons,expected_m1", [(5, 5.4), (10, 10.8), (20, 21.6)])
def test_carbon_count_drives_m_plus_one(carbons: int, expected_m1: float) -> None:
    # Each carbon adds ~1.08% to the M+1 peak (13C natural abundance 1.07%).
    r = isotope_distribution(f"C{carbons}", threshold=0.0, limit=10)
    assert _rel(r)[carbons * 12 + 1] == pytest.approx(expected_m1, abs=0.6)


def test_sulfur_m_plus_two_from_34s() -> None:
    # 34S (~4.25%) dominates the M+2 of an SO2 ion (the two 18O add ~0.4%).
    rel = _rel(isotope_distribution("SO2"))
    assert rel[64] == 100.0
    assert rel[66] == pytest.approx(4.9, abs=0.6)


def test_single_silicon_pattern() -> None:
    # One Si: 29Si ~5.1% at M+1, 30Si ~3.4% at M+2.
    rel = _rel(isotope_distribution("Si"))
    assert rel[28] == 100.0
    assert rel[29] == pytest.approx(5.1, abs=0.5)
    assert rel[30] == pytest.approx(3.35, abs=0.5)


def test_two_chlorines_nine_six_one() -> None:
    # Cl2 textbook M:M+2:M+4 ~ 9:6:1 (here 100:64:10.2 from 75.76/24.24).
    rel = _rel(isotope_distribution("Cl2"))
    assert rel[70] == 100.0
    assert rel[72] == pytest.approx(64.0, abs=1.5)
    assert rel[74] == pytest.approx(10.2, abs=1.0)


def test_two_bromines_one_two_one_centroid_base() -> None:
    # Br2: M:M+2:M+4 ~ 1:2:1, so the M+2 (one 79Br, one 81Br) is the base peak.
    r = isotope_distribution("Br2")
    rel = _rel(r)
    assert r["base_peak"]["nominal"] == 160  # the mixed 79/81 peak
    assert rel[158] == pytest.approx(51.4, abs=1.5)
    assert rel[162] == pytest.approx(48.6, abs=1.5)


# --- masses ----------------------------------------------------------------


def test_monoisotopic_equals_sum_of_most_abundant_masses() -> None:
    # CHCl3: 12C + 1H + 3x 35Cl, computed independently from exact masses.
    expected = 12.0 + 1.00782503 + 3 * 34.96885268
    assert isotope_distribution("CHCl3")["monoisotopic_mass"] == pytest.approx(expected, abs=1e-3)


def test_average_mass_matches_weight_calculator() -> None:
    # The isotope tool's average_mass should agree with the molar mass.
    iso = isotope_distribution("C6H12O6")["average_mass"]
    mw = molecular_weight_calculator("C6H12O6")["weight"]
    assert iso == pytest.approx(mw, abs=0.01)


# --- charge / m/z arithmetic -----------------------------------------------


def test_triply_charged_mz() -> None:
    r = isotope_distribution("C6H12O6", charge=3)
    base = r["base_peak"]
    assert base["mz"] == pytest.approx((base["mass"] + 3 * _PROTON) / 3, abs=1e-3)
    assert r["monoisotopic_mz"] == pytest.approx(
        (r["monoisotopic_mass"] + 3 * _PROTON) / 3, abs=1e-3
    )


def test_doubly_negative_charge_subtracts_two_protons() -> None:
    r = isotope_distribution("C6H12O6", charge=-2)
    base = r["base_peak"]
    assert base["mz"] == pytest.approx((base["mass"] - 2 * _PROTON) / 2, abs=1e-3)


# --- fallback / validation -------------------------------------------------


def test_no_natural_abundance_element_uses_most_stable() -> None:
    # Promethium has no natural isotopes; falls back to most-stable Pm-145.
    assert isotope_distribution("Pm")["base_peak"]["nominal"] == 145


def test_invalid_grouping_raises() -> None:
    with pytest.raises(ValueError, match="grouping must be"):
        isotope_distribution("H2O", grouping="centroid")


def test_limit_below_one_raises() -> None:
    with pytest.raises(ValueError, match="limit must be"):
        isotope_distribution("H2O", limit=0)


# --- internal convolution peak-cap -----------------------------------------


def test_conv_caps_peak_count_keeping_most_intense() -> None:
    """``_conv`` keeps the ``_MAX_PEAKS`` most intense peaks, re-sorted by mass.

    During convolution of a large molecule the intermediate peak list can grow
    past :data:`isotopes._MAX_PEAKS`; the cap drops the least intense peaks so
    the computation stays bounded. We drive it directly with two distributions
    whose pairwise mass-sums are all distinct (50 * 100 = 5000 > 4000), with
    intensity increasing in ``j`` so the survivors are predictable.
    """
    cap = isotopes._MAX_PEAKS
    # a: masses 0..99, unit intensity. b: masses 0,100,..,4900, intensity j+1.
    # Sum masses i + 100*j are unique (i < 100), so 5000 distinct peaks form,
    # each peak's intensity == (j+1). Nothing is pruned (all >> the 1e-12 cut).
    a = [(float(i), 1.0) for i in range(100)]
    b = [(float(100 * j), float(j + 1)) for j in range(50)]

    out = isotopes._conv(a, b)

    assert len(out) == cap  # capped from 5000 down to 4000
    masses = [m for m, _ in out]
    assert masses == sorted(masses)  # re-sorted ascending by mass after the cap
    # 5000 - 4000 = 1000 dropped = the 10 least-intense j-bands (j=0..9, 100 each),
    # so every survivor has intensity >= 11 and mass >= 100*10.
    assert min(p for _, p in out) >= 11.0
    assert min(masses) >= 1000.0
