# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""Shared normalization helpers for the name <-> formula store.

The same functions run at build time (populating the SQLite store) and at query
time (normalizing user input), so the indexed columns and the lookup keys always
agree. ``normalize_name`` folds names to a canonical match key; ``hill_formula``
canonicalizes a formula to a Hill-system ASCII string.
"""

from __future__ import annotations

import re
import unicodedata

from .formula import FormulaError, parse_formula

# Trailing chemical-registry annotations to strip from names, e.g. the CAS index
# suffixes "(9CI)"/"(8CI)" or nomenclature tags "(USAN)", "(INN)". Anchored at
# the end so leading stereo/locant parentheticals -- "(R)-", "(2S,3R)-" -- are
# preserved. Looped at call sites so stacked tags ("...(8CI)(9CI)") all go.
_ANNOTATION = re.compile(
    r"\s*\(\s*(?:[89]CI|USAN|INN|BAN|JAN|USP|NF|DCF|pINN|rINN|JP\d*)\s*\)\s*$",
    re.IGNORECASE,
)
_WHITESPACE = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """Fold ``name`` to its canonical match key.

    NFKC-normalizes (which also folds Unicode subscripts and full-width forms),
    case-folds, strips trailing registry annotations, and collapses whitespace.
    Returns ``""`` if nothing is left.
    """
    s = unicodedata.normalize("NFKC", name).casefold()
    while True:
        stripped = _ANNOTATION.sub("", s)
        if stripped == s:
            break
        s = stripped
    return _WHITESPACE.sub(" ", s).strip()


def hill_formula(src: str) -> str:
    """Canonicalize ``src`` to a Hill-system ASCII formula string.

    Carbon first, hydrogen second, remaining elements alphabetical (all elements
    alphabetical when no carbon is present). Counts of 1 are omitted, so
    ``"C₆H₁₂O₆"`` and ``"O6C6H12"`` both yield ``"C6H12O6"``.

    Raises :class:`~mcp_molecules.formula.FormulaError` if ``src`` cannot be
    parsed (the caller decides whether to fall back to the raw string).
    """
    tally = dict(parse_formula(src))
    if "C" in tally:
        order = ["C"]
        if "H" in tally:
            order.append("H")
        order += sorted(s for s in tally if s not in ("C", "H"))
    else:
        order = sorted(tally)
    return "".join(s if tally[s] == 1 else f"{s}{tally[s]}" for s in order)


__all__ = ["FormulaError", "hill_formula", "normalize_name"]
