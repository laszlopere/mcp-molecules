# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""Atomic-weight table built from the bundled NIST data.

Ported from the C ``mwc`` project. Builds a ``{symbol: (weight, sigma)}`` table
from the NIST Atomic Weights and Isotopic Compositions database
(``data/nist_atomic_weights.json``).

For each element Z the first record is the element's primary symbol. A later
record with a differing symbol but the same Z is an isotope-specific label
(``D``, ``T``) and uses its own relative atomic mass. In monoisotopic mode the
primary weight comes from the most abundant natural isotope (or the most stable
isotope for radioactive elements); D and T already name a single isotope, so
their weights are identical in both modes.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any

_MAX_Z = 128
_DATA_FILE = "nist_atomic_weights.json"


def _load_root() -> dict[str, Any]:
    text = files("mcp_molecules.data").joinpath(_DATA_FILE).read_text(encoding="utf-8")
    return json.loads(text)


def _extract_sigma(saw: Any) -> float:
    """Standard uncertainty of a standard-atomic-weight object.

    Uses an explicit ``uncertainty`` when present, otherwise the half-width of an
    ``interval`` (``(hi - lo) / 2``); ``0.0`` if neither is available.
    """
    if not isinstance(saw, dict):
        return 0.0
    if "uncertainty" in saw:
        return float(saw["uncertainty"])
    iv = saw.get("interval")
    if isinstance(iv, list) and len(iv) == 2:
        return (float(iv[1]) - float(iv[0])) / 2.0
    return 0.0


def _isotope_mass(isos: list, target_z: int, mass_number: int) -> tuple[float, float]:
    """Relative atomic mass (value, uncertainty) of isotope (Z, A)."""
    for r in isos:
        if r.get("atomic_number") != target_z or r.get("mass_number") != mass_number:
            continue
        ram = r.get("relative_atomic_mass") or {}
        return float(ram.get("value", 0.0)), float(ram.get("uncertainty", 0.0))
    return 0.0, 0.0


def _most_abundant_mass_number(isos: list, target_z: int) -> int:
    """Mass number of the most abundant natural isotope of Z, or -1 if none."""
    best = -1.0
    best_a = -1
    for r in isos:
        if r.get("atomic_number") != target_z:
            continue
        v = (r.get("isotopic_composition") or {}).get("value")
        if v is None:
            continue
        abund = float(v)
        if abund > best:
            best = abund
            best_a = int(r.get("mass_number"))
    return best_a


@lru_cache(maxsize=2)
def load_weights(monoisotopic: bool = False) -> dict[str, tuple[float, float]]:
    """Build the ``{symbol: (weight, sigma)}`` table.

    Cached per ``monoisotopic`` value. ``weight`` and ``sigma`` are in unified
    atomic mass units (equivalently g/mol).
    """
    root = _load_root()
    isos = root.get("isotopes")
    if not isinstance(isos, list):
        raise RuntimeError("NIST data: missing or non-array 'isotopes'")

    table: dict[str, tuple[float, float]] = {}
    primary: dict[int, str] = {}

    for r in isos:
        z = r.get("atomic_number")
        if not isinstance(z, int) or z < 1 or z >= _MAX_Z:
            continue
        sym = r.get("symbol")
        saw = r.get("standard_atomic_weight")
        ram = r.get("relative_atomic_mass")

        if z not in primary:
            primary[z] = sym
            weight = 0.0
            sigma = 0.0
            if monoisotopic:
                a = _most_abundant_mass_number(isos, z)
                if a < 0 and isinstance(saw, dict) and "most_stable_mass_number" in saw:
                    a = int(saw["most_stable_mass_number"])
                if a > 0:
                    weight, sigma = _isotope_mass(isos, z, a)
            elif isinstance(saw, dict) and "value" in saw:
                weight = float(saw["value"])
                sigma = _extract_sigma(saw)
            elif isinstance(saw, dict) and "most_stable_mass_number" in saw:
                weight, sigma = _isotope_mass(isos, z, int(saw["most_stable_mass_number"]))
            elif isinstance(ram, dict):
                weight = float(ram.get("value", 0.0))
                sigma = float(ram.get("uncertainty", 0.0))
            table[sym] = (weight, sigma)
        elif primary[z] != sym and isinstance(ram, dict) and "value" in ram:
            table[sym] = (float(ram["value"]), float(ram.get("uncertainty", 0.0)))

    return table
