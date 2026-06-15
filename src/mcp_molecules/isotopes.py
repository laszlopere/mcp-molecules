# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""Isotopic-pattern (isotope distribution) calculator.

Computes the natural isotope distribution of a chemical formula -- the set of
isotopologue peaks a mass spectrometer would see -- from the bundled NIST Atomic
Weights and Isotopic Compositions database (``data/nist_atomic_weights.json``).

Each element's per-isotope (exact mass, natural abundance) pairs form a small
probability polynomial; the molecule's pattern is that polynomial raised to the
atom count (by binary exponentiation) and convolved across elements. Peaks are
pruned during convolution to keep the computation bounded, then grouped either
by unit (nominal) mass -- what a low-resolution spectrum shows -- or kept as
individual resolved isotopologues. Offline and deterministic.
"""

from __future__ import annotations

import json
from collections import defaultdict
from functools import lru_cache
from importlib.resources import files
from typing import Any

from mcp_molecules.formula import parse_formula

_DATA_FILE = "nist_atomic_weights.json"

# Proton mass (u); used to turn a neutral mass into m/z for a charged ion.
_PROTON_MASS = 1.007276466

# Internal convolution bounds: drop peaks below this fraction of the running
# base peak, and cap the intermediate peak count, so large molecules stay fast.
_PRUNE_REL = 1e-12
_MAX_PEAKS = 4000
# Masses summed during convolution are merged when equal to this many decimals.
_MASS_DP = 6


@lru_cache(maxsize=1)
def _load_root() -> dict[str, Any]:
    text = files("mcp_molecules.data").joinpath(_DATA_FILE).read_text(encoding="utf-8")
    return json.loads(text)


@lru_cache(maxsize=1)
def _isotope_sources() -> tuple[dict[str, list], dict[str, list]]:
    """Build ``(element_isotopes, label_isotopes)`` from the NIST data.

    ``element_isotopes`` maps a primary element symbol to its natural isotope
    list ``[(mass, abundance), ...]`` (abundances normalised to sum to 1). For
    an element with no natural composition (e.g. Tc) it falls back to the single
    most-stable isotope at 100%. ``label_isotopes`` maps isotope-specific labels
    (``D``, ``T``) to that one isotope at 100%.
    """
    isos = _load_root().get("isotopes")
    if not isinstance(isos, list):
        raise RuntimeError("NIST data: missing or non-array 'isotopes'")

    primary: dict[int, str] = {}
    for r in isos:
        z = r.get("atomic_number")
        if isinstance(z, int) and z >= 1 and z not in primary:
            primary[z] = r.get("symbol")

    natural: dict[int, list] = defaultdict(list)
    stable_fallback: dict[int, float] = {}
    labels: dict[str, list] = {}

    for r in isos:
        z = r.get("atomic_number")
        if not isinstance(z, int) or z < 1:
            continue
        ram = (r.get("relative_atomic_mass") or {}).get("value")
        if ram is None:
            continue
        mass = float(ram)
        abundance = (r.get("isotopic_composition") or {}).get("value")
        if abundance:
            natural[z].append((mass, float(abundance)))
        saw = r.get("standard_atomic_weight")
        if isinstance(saw, dict) and r.get("mass_number") == saw.get("most_stable_mass_number"):
            stable_fallback[z] = mass
        sym = r.get("symbol")
        if sym != primary.get(z):  # isotope-specific label (D, T)
            labels[sym] = [(mass, 1.0)]

    element: dict[str, list] = {}
    for z, sym in primary.items():
        if natural.get(z):
            total = sum(a for _, a in natural[z])
            element[sym] = [(m, a / total) for m, a in natural[z]]
        elif z in stable_fallback:
            element[sym] = [(stable_fallback[z], 1.0)]
    return element, labels


def _isotopes_for(symbol: str) -> list:
    """Isotope list ``[(mass, abundance), ...]`` for a formula symbol."""
    element, labels = _isotope_sources()
    if symbol in labels:  # explicit D / T -> that isotope only
        return labels[symbol]
    if symbol in element:
        return element[symbol]
    raise ValueError(f"no isotope data for element '{symbol}'")


def _conv(a: list, b: list) -> list:
    """Convolve two distributions, merging equal masses and pruning tiny peaks."""
    out: dict[float, float] = defaultdict(float)
    for m1, p1 in a:
        for m2, p2 in b:
            out[round(m1 + m2, _MASS_DP)] += p1 * p2
    items = sorted(out.items())
    if not items:
        return items
    cut = max(p for _, p in items) * _PRUNE_REL
    items = [(m, p) for m, p in items if p >= cut]
    if len(items) > _MAX_PEAKS:
        items.sort(key=lambda mp: -mp[1])
        items = items[:_MAX_PEAKS]
        items.sort()
    return items


def _poly_pow(base: list, n: int) -> list:
    """``base`` distribution raised to the ``n``-th power (binary exponentiation)."""
    result = [(0.0, 1.0)]
    while n > 0:
        if n & 1:
            result = _conv(result, base)
        n >>= 1
        if n:
            base = _conv(base, base)
    return result


def _mz(mass: float, charge: int) -> float:
    """m/z of an ion: ``(mass ± n·proton) / n`` for ``[M±nH]`` ions."""
    n = abs(charge)
    return (mass + (n if charge > 0 else -n) * _PROTON_MASS) / n


def isotope_distribution(
    formula: str,
    charge: int = 0,
    threshold: float = 0.1,
    limit: int = 10,
    grouping: str = "unit",
) -> dict:
    """Compute the natural isotopic pattern of ``formula``.

    Returns the neutral isotopologue masses (and, when ``charge`` is non-zero,
    the corresponding m/z), each peak's intensity relative to the base peak, plus
    the monoisotopic and average masses. ``grouping='unit'`` collapses peaks to
    nominal masses (intensity-weighted centroid); ``'exact'`` keeps every
    resolved isotopologue. Peaks below ``threshold`` percent of the base peak are
    dropped and at most ``limit`` peaks (most intense first) are returned.

    Raises ``ValueError`` for an unparseable formula, an unknown element, or an
    out-of-range argument.
    """
    if grouping not in ("unit", "exact"):
        raise ValueError("grouping must be 'unit' or 'exact'")
    if limit < 1:
        raise ValueError("limit must be >= 1")

    tally = parse_formula(formula)

    dist: list = [(0.0, 1.0)]
    monoisotopic = 0.0
    average = 0.0
    for symbol, count in tally:
        isos = _isotopes_for(symbol)
        dist = _conv(dist, _poly_pow(isos, count))
        monoisotopic += max(isos, key=lambda ma: ma[1])[0] * count
        average += sum(m * a for m, a in isos) / sum(a for _, a in isos) * count

    if grouping == "unit":
        binned: dict[int, list] = defaultdict(lambda: [0.0, 0.0])
        for mass, prob in dist:
            slot = binned[round(mass)]
            slot[0] += prob
            slot[1] += mass * prob
        raw = [(centroid / prob, prob) for prob, centroid in binned.values()]
    else:
        raw = list(dist)

    base = max(prob for _, prob in raw)
    charged = charge != 0
    peaks = []
    for mass, prob in raw:
        relative = 100.0 * prob / base
        if relative < threshold:
            continue
        peak = {
            "nominal": round(mass),
            "mass": round(mass, 5),
            "relative": round(relative, 3),
            "abundance": prob,
        }
        if charged:
            peak["mz"] = round(_mz(mass, charge), 5)
        peaks.append(peak)
    peaks.sort(key=lambda p: -p["abundance"])
    peaks = peaks[:limit]

    base_peak = peaks[0]
    parts = [f"{p['nominal']} ({p['relative']:.0f}%)" for p in peaks]

    root = _load_root()
    return {
        "formula": formula,
        "charge": charge,
        "grouping": grouping,
        "monoisotopic_mass": round(monoisotopic, 5),
        "average_mass": round(average, 5),
        "monoisotopic_mz": round(_mz(monoisotopic, charge), 5) if charged else None,
        "base_peak": base_peak,
        "peaks": peaks,
        "formatted": ", ".join(parts),
        "source": "NIST Atomic Weights and Isotopic Compositions",
        "license": root.get("license"),
    }
