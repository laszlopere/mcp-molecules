# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""Inverse elemental analysis (TODO 4.9).

The inverse of percent composition: given the mass percentages of the elements
in a compound -- the numbers a combustion / CHN analyzer reports -- recover the
best-fit empirical formula, and, when a molar mass is also known, scale it up to
the molecular formula.

Method (the classic hand calculation, made robust to noisy data):

1. Assume a 100 g sample, so each element's mass in grams equals its percentage.
2. Divide by the element's standard atomic weight to get relative moles.
3. Divide every mole count by the smallest, giving mole ratios normalised so the
   least-abundant element is 1.
4. Scale the ratios by successive integers ``1..max_multiplier`` and round to
   whole atom counts; keep the candidate whose predicted composition best fits
   the input (least RMS percentage-point error), preferring the smallest formula
   on a tie. Reducing that candidate to lowest terms gives the empirical formula.

All masses come from the bundled NIST standard atomic weights, so the whole
computation is offline and deterministic.
"""

from __future__ import annotations

from math import gcd, sqrt

from mcp_molecules.naming import hill_formula
from mcp_molecules.weights import load_weights


def _reduce(counts: dict[str, int]) -> dict[str, int]:
    """Divide ``counts`` by their greatest common divisor (lowest terms)."""
    g = 0
    for c in counts.values():
        g = gcd(g, c)
    if g <= 1:
        return dict(counts)
    return {s: c // g for s, c in counts.items()}


def _formula(counts: dict[str, int]) -> str:
    """Hill-system string for an atom-count mapping (all counts >= 1)."""
    return hill_formula("".join(f"{s}{c}" for s, c in counts.items()))


def _composition(
    counts: dict[str, int], weights: dict[str, tuple[float, float]]
) -> tuple[dict[str, float], float]:
    """Return ``(percent-by-mass per element, total formula weight)``."""
    total = sum(weights[s][0] * c for s, c in counts.items())
    percents = {s: (100.0 * weights[s][0] * c / total if total else 0.0) for s, c in counts.items()}
    return percents, total


def infer_formula(
    percentages: dict[str, float],
    molar_mass: float | None = None,
    balance: str | None = None,
    max_multiplier: int = 12,
    max_rms_error: float = 0.5,
) -> dict:
    """Infer the best-fit formula from per-element mass percentages.

    ``percentages`` maps element symbols to their mass percent (e.g.
    ``{"C": 40.0, "H": 6.71, "O": 53.29}``). ``balance``, when given, names an
    element assigned the remainder to 100% -- the usual convention for oxygen in
    CHN analysis, where only C, H and N are measured. ``molar_mass`` (g/mol), if
    known, scales the empirical formula up to the molecular formula.

    Returns a dict with the resolved input, the ``empirical_formula`` and its
    weight, the ``molecular_formula`` (``None`` without a molar mass), the
    integer ``multiplier`` search outcome, a ``fit`` block (predicted
    percentages, per-element residuals, RMS and max error) and any ``warnings``.

    Raises ``ValueError`` on an unknown element, a non-positive percentage, or
    percentages that leave no room for the balance element.
    """
    weights = load_weights(False)
    warnings: list[str] = []

    resolved: dict[str, float] = {}
    for sym, pct in percentages.items():
        if sym not in weights:
            raise ValueError(f"unknown element '{sym}'")
        value = float(pct)
        if value <= 0.0:
            raise ValueError(f"percentage for '{sym}' must be positive, got {value}")
        resolved[sym] = value

    if not resolved:
        raise ValueError("at least one element percentage is required")

    if balance is not None:
        if balance not in weights:
            raise ValueError(f"unknown element '{balance}'")
        if balance in resolved:
            raise ValueError(f"balance element '{balance}' was also given a percentage")
        remainder = 100.0 - sum(resolved.values())
        if remainder <= 0.0:
            raise ValueError(
                "given percentages already sum to >= 100%, nothing left for the "
                f"balance element '{balance}'"
            )
        resolved[balance] = remainder
    else:
        total_pct = sum(resolved.values())
        if abs(total_pct - 100.0) > 1.0:
            warnings.append(
                f"percentages sum to {total_pct:.2f}%, not ~100%; an element may be "
                "missing (pass 'balance' to assign the remainder, e.g. to oxygen)"
            )

    moles = {s: resolved[s] / weights[s][0] for s in resolved}
    min_moles = min(moles.values())
    ratios = {s: moles[s] / min_moles for s in moles}

    # Search integer multipliers of the mole ratios; keep the lowest-error whole
    # formula, and the smallest such (strict '<' never replaces an equal-error
    # multiple, so proportional counts like C2H4O2 never displace CH2O).
    best_counts: dict[str, int] | None = None
    best_err = float("inf")
    for k in range(1, max_multiplier + 1):
        counts = {s: round(ratios[s] * k) for s in ratios}
        if any(c < 1 for c in counts.values()):
            continue
        predicted, _ = _composition(counts, weights)
        err = sqrt(sum((predicted[s] - resolved[s]) ** 2 for s in resolved) / len(resolved))
        if err < best_err - 1e-9:
            best_err = err
            best_counts = counts

    assert best_counts is not None  # k=1 always yields counts >= 1 (min ratio is 1)
    empirical_counts = _reduce(best_counts)
    empirical_formula = _formula(empirical_counts)
    predicted, empirical_weight = _composition(empirical_counts, weights)

    residuals = {s: predicted[s] - resolved[s] for s in resolved}
    rms_error = sqrt(sum(r * r for r in residuals.values()) / len(residuals))
    max_abs_error = max(abs(r) for r in residuals.values())
    if rms_error > max_rms_error:
        warnings.append(
            f"best-fit RMS error is {rms_error:.2f} percentage points (> "
            f"{max_rms_error}); the data may be inconsistent or a component missing"
        )

    molecular_formula: str | None = None
    molecular_weight: float | None = None
    multiplier: int | None = None
    if molar_mass is not None:
        if molar_mass <= 0.0:
            raise ValueError(f"molar_mass must be positive, got {molar_mass}")
        multiplier = max(1, round(molar_mass / empirical_weight))
        molecular_counts = {s: c * multiplier for s, c in empirical_counts.items()}
        molecular_formula = _formula(molecular_counts)
        molecular_weight = empirical_weight * multiplier
        if abs(molecular_weight - molar_mass) / molar_mass > 0.05:
            warnings.append(
                f"nearest molecular weight {molecular_weight:.2f} g/mol is >5% from "
                f"the given {molar_mass:.2f} g/mol; the empirical formula or mass may be off"
            )

    return {
        "input_percentages": {s: percentages[s] for s in percentages},
        "balance_element": balance,
        "resolved_percentages": resolved,
        "empirical_formula": empirical_formula,
        "empirical_formula_weight": empirical_weight,
        "molecular_formula": molecular_formula,
        "molecular_formula_weight": molecular_weight,
        "molecular_multiplier": multiplier,
        "fit": {
            "predicted_percentages": predicted,
            "residuals": residuals,
            "rms_error": rms_error,
            "max_abs_error": max_abs_error,
        },
        "warnings": warnings,
    }


__all__ = ["infer_formula"]
