# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""FastMCP application for mcp-molecules. All tools register on this app."""

import math
import platform
from importlib.metadata import version
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from mcp_molecules import __version__
from mcp_molecules.formula import parse_formula
from mcp_molecules.weights import load_weights

# Output unit -> (scale factor from g/mol, default decimal places).
_UNITS: dict[str, tuple[float, int]] = {
    "g/mol": (1.0, 2),
    "kg/mol": (1e-3, 5),
    "Da": (1.0, 2),
    "u": (1.0, 2),
    "kDa": (1e-3, 5),
}

mcp = FastMCP(
    "mcp-molecules",
    instructions=(
        "Compute molecular weights / molar masses from chemical formulae, backed "
        "by the NIST Atomic Weights and Isotopic Compositions database. Parses "
        "formulae with nested groups and isotope labels (e.g. 'Ca(OH)2', 'D2O'), "
        "and can report propagated uncertainties, monoisotopic mass, and percent "
        "composition. Offline and deterministic; no network calls."
    ),
)


@mcp.tool()
def info() -> dict:
    """Report mcp-molecules server availability, version, and environment information."""
    return {
        "status": "available",
        "name": "mcp-molecules",
        "version": __version__,
        "python": platform.python_version(),
        "mcp_sdk": version("mcp"),
        "toolsets": [],
    }


@mcp.tool()
def molecular_weight_calculator(
    formula: Annotated[
        str,
        Field(
            description=(
                "Chemical formula to weigh. Supports element symbols, integer "
                "multipliers, arbitrarily nested parenthetical groups, and isotope "
                "labels D (deuterium) and T (tritium). Examples: 'H2O', "
                "'C6H12O6', 'Ca(OH)2', 'Fe2(SO4)3', '((CH3)2CH)2', 'D2O', 'Tc'."
            )
        ),
    ],
    unit: Annotated[
        Literal["g/mol", "kg/mol", "Da", "u", "kDa"],
        Field(description="Output unit for the reported mass. Defaults to grams per mole."),
    ] = "g/mol",
    uncertainty: Annotated[
        bool,
        Field(
            description=(
                "If true, propagate the per-element NIST standard uncertainties in "
                "quadrature and report the result as value ± sigma."
            )
        ),
    ] = False,
    monoisotopic: Annotated[
        bool,
        Field(
            description=(
                "If true, use the most abundant natural isotope mass for each "
                "element (mass-spectrometry monoisotopic mass) instead of the "
                "standard atomic weight."
            )
        ),
    ] = False,
    composition: Annotated[
        bool,
        Field(
            description=(
                "If true, return the per-element percent composition by mass "
                "(count, mass subtotal, and percentage) alongside the total weight."
            )
        ),
    ] = False,
) -> dict:
    """Compute the molecular weight (molar mass) of a chemical formula.

    Parses ``formula`` into an atom tally, looks up each element's mass in the
    bundled NIST Atomic Weights and Isotopic Compositions database, and returns
    the total weight in the requested ``unit``. Optionally propagates NIST
    uncertainties (``uncertainty``), switches to monoisotopic masses
    (``monoisotopic``), and/or reports percent composition by mass
    (``composition``).

    Raises ``ValueError`` for an unparseable formula or an unknown element.
    """
    tally = parse_formula(formula)
    weights = load_weights(monoisotopic)

    mw = 0.0
    variance = 0.0
    for symbol, count in tally:
        entry = weights.get(symbol)
        if entry is None:
            raise ValueError(f"unknown element '{symbol}'")
        weight, sigma = entry
        mw += weight * count
        variance += (count * sigma) ** 2
    sigma_mw = math.sqrt(variance)

    factor, decimals = _UNITS[unit]
    value = mw * factor
    display_sigma = sigma_mw * factor

    # Monoisotopic masses shift in the 4th decimal; widen so the shift shows.
    if monoisotopic and decimals < 4:
        decimals = 4
    # Widen until the first significant digit of the uncertainty is visible.
    if uncertainty and display_sigma > 0.0:
        need = math.ceil(-math.log10(display_sigma))
        if need > decimals:
            decimals = need

    if uncertainty:
        formatted = f"{value:.{decimals}f} ± {display_sigma:.{decimals}f} {unit}"
    else:
        formatted = f"{value:.{decimals}f} {unit}"

    result: dict = {
        "formula": formula,
        "unit": unit,
        "weight": value,
        "uncertainty": display_sigma if uncertainty else None,
        "monoisotopic": monoisotopic,
        "atoms": {symbol: count for symbol, count in tally},
        "formatted": formatted,
    }

    if composition:
        # Percent composition is reported by mass in g/mol, independent of unit.
        rows = []
        for symbol, count in tally:
            weight, _sigma = weights[symbol]
            subtotal = weight * count
            rows.append(
                {
                    "element": symbol,
                    "count": count,
                    "subtotal_g_per_mol": subtotal,
                    "percent": (100.0 * subtotal / mw) if mw else 0.0,
                }
            )
        result["composition"] = rows
        result["total_weight_g_per_mol"] = mw

    return result
