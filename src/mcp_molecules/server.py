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
from mcp_molecules.isotopes import isotope_distribution as _isotope_distribution
from mcp_molecules.names import find_compound
from mcp_molecules.weights import load_nominal, load_weights

# Output unit -> (scale factor from g/mol, default decimal places).
_UNITS: dict[str, tuple[float, int]] = {
    "g/mol": (1.0, 2),
    "kg/mol": (1e-3, 5),
    "Da": (1.0, 2),
    "u": (1.0, 2),
    "kDa": (1e-3, 5),
}


def _format_mass(value: float, sigma: float, unit: str, decimals: int) -> str:
    """Render ``value`` (optionally ``value ± sigma``) at enough precision.

    When ``sigma`` is positive the field is widened until the first significant
    digit of the uncertainty shows; a non-positive ``sigma`` formats the value
    alone.
    """
    if sigma > 0.0:
        need = math.ceil(-math.log10(sigma))
        if need > decimals:
            decimals = need
        return f"{value:.{decimals}f} ± {sigma:.{decimals}f} {unit}"
    return f"{value:.{decimals}f} {unit}"


mcp = FastMCP(
    "mcp-molecules",
    instructions=(
        "Compute molecular weights / molar masses from chemical formulae, backed "
        "by the NIST Atomic Weights and Isotopic Compositions database. Parses "
        "formulae with nested groups and isotope labels (e.g. 'Ca(OH)2', 'D2O'), "
        "and can report propagated uncertainties, monoisotopic mass, and percent "
        "composition -- offline and deterministic. Also resolves compound names "
        "<-> formulae from a bundled database, with an on-by-default online "
        "fallback for misses (PubChem, Wikidata, and -- with an API key -- EPA "
        "CompTox; set MCP_MOLECULES_ONLINE to a falsy value to keep lookups fully "
        "offline)."
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
                "Selects which mass flavor is reported at the top level (weight / "
                "uncertainty / formatted): false (default) for the standard atomic "
                "weight, true for the monoisotopic mass. All three flavors "
                "(nominal, average, monoisotopic) are always returned under "
                "'masses' regardless of this flag."
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
    the total weight in the requested ``unit``. Every call reports all three
    distinct mass flavors under ``masses`` so callers never conflate them or have
    to re-ask:

    * ``nominal`` -- sum of the integer mass numbers of the most abundant
      isotopes,
    * ``average`` -- the standard atomic weight (average molar mass),
    * ``monoisotopic`` -- the exact mass of the most abundant isotopes.

    The ``monoisotopic`` flag selects which of these is mirrored at the top level
    (``weight`` / ``uncertainty`` / ``formatted``) and named by ``primary``.
    Optionally propagates NIST uncertainties (``uncertainty``) and/or reports
    percent composition by mass (``composition``).

    Raises ``ValueError`` for an unparseable formula or an unknown element.
    """
    tally = parse_formula(formula)
    avg_weights = load_weights(False)
    mono_weights = load_weights(True)
    nominal_weights = load_nominal()

    def _weigh(table: dict[str, tuple[float, float]]) -> tuple[float, float]:
        mw = 0.0
        variance = 0.0
        for symbol, count in tally:
            entry = table.get(symbol)
            if entry is None:
                raise ValueError(f"unknown element '{symbol}'")
            weight, sigma = entry
            mw += weight * count
            variance += (count * sigma) ** 2
        return mw, math.sqrt(variance)

    avg_mw, avg_sigma = _weigh(avg_weights)
    mono_mw, mono_sigma = _weigh(mono_weights)
    nominal_mw = float(sum(nominal_weights[symbol] * count for symbol, count in tally))

    factor, decimals = _UNITS[unit]
    # Monoisotopic masses shift in the 4th decimal; widen so the shift shows.
    mono_decimals = max(decimals, 4)

    # (flavor name, mass in g/mol, sigma in g/mol, base decimal places).
    specs = (
        ("nominal", nominal_mw, 0.0, decimals),
        ("average", avg_mw, avg_sigma, decimals),
        ("monoisotopic", mono_mw, mono_sigma, mono_decimals),
    )
    masses: dict[str, dict] = {}
    for name, mw, sigma, base_decimals in specs:
        value = mw * factor
        display_sigma = sigma * factor
        masses[name] = {
            "weight": value,
            "uncertainty": display_sigma if uncertainty else None,
            "formatted": _format_mass(
                value, display_sigma if uncertainty else 0.0, unit, base_decimals
            ),
        }

    primary = "monoisotopic" if monoisotopic else "average"
    chosen = masses[primary]

    result: dict = {
        "formula": formula,
        "unit": unit,
        "weight": chosen["weight"],
        "uncertainty": chosen["uncertainty"],
        "monoisotopic": monoisotopic,
        "primary": primary,
        "atoms": {symbol: count for symbol, count in tally},
        "masses": masses,
        "formatted": chosen["formatted"],
    }

    if composition:
        # Percent composition is reported by mass in g/mol, using the primary
        # flavor's per-element weights, independent of the output unit.
        weights = mono_weights if monoisotopic else avg_weights
        total = mono_mw if monoisotopic else avg_mw
        rows = []
        for symbol, count in tally:
            weight, _sigma = weights[symbol]
            subtotal = weight * count
            rows.append(
                {
                    "element": symbol,
                    "count": count,
                    "subtotal_g_per_mol": subtotal,
                    "percent": (100.0 * subtotal / total) if total else 0.0,
                }
            )
        result["composition"] = rows
        result["total_weight_g_per_mol"] = total

    return result


@mcp.tool()
def isotope_distribution(
    formula: Annotated[
        str,
        Field(
            description=(
                "Chemical formula whose isotopic pattern to compute. Same syntax "
                "as molecular_weight_calculator: element symbols, integer "
                "multipliers, nested groups, isotope labels D/T, Unicode "
                "subscripts. Examples: 'CHCl3', 'C6H5Br', 'C254H377N65O75S6'."
            )
        ),
    ],
    charge: Annotated[
        int,
        Field(
            description=(
                "Ion charge. 0 (default) reports neutral isotopologue masses. A "
                "non-zero n reports m/z for the [M+nH] ion (positive) or [M-nH] "
                "ion (negative): (mass +/- n*proton)/|n|."
            ),
            ge=-10,
            le=10,
        ),
    ] = 0,
    threshold: Annotated[
        float,
        Field(
            description=("Drop peaks below this percent of the base (most intense) peak."),
            ge=0.0,
            le=100.0,
        ),
    ] = 0.1,
    limit: Annotated[
        int,
        Field(
            description="Maximum number of peaks to return, most intense first.",
            ge=1,
            le=100,
        ),
    ] = 10,
    grouping: Annotated[
        Literal["unit", "exact"],
        Field(
            description=(
                "'unit' (default) collapses peaks to nominal integer masses "
                "(intensity-weighted centroid) -- the low-resolution spectrum a "
                "chemist eyeballs. 'exact' keeps every resolved isotopologue."
            )
        ),
    ] = "unit",
) -> dict:
    """Compute the natural isotopic pattern (isotope distribution) of a formula.

    Returns the set of isotopologue peaks a mass spectrometer would see: each
    peak's neutral ``mass`` (and ``mz`` when ``charge`` is non-zero), its
    intensity ``relative`` to the base peak, and its absolute ``abundance``.
    Also reports the ``monoisotopic_mass`` (most-abundant isotope of each
    element) and the ``average_mass``. Backed by the bundled NIST Atomic Weights
    and Isotopic Compositions database -- offline and deterministic.

    Peaks below ``threshold`` percent of the base peak are dropped; at most
    ``limit`` are returned. ``grouping`` selects nominal-mass (``unit``) or
    fully resolved (``exact``) peaks.

    Raises ``ValueError`` for an unparseable formula or an unknown element.
    """
    return _isotope_distribution(formula, charge, threshold, limit, grouping)


@mcp.tool()
def find_chemical_compound(
    query: Annotated[
        str,
        Field(
            description=(
                "Compound to look up -- either a name or a molecular formula. "
                "Names match the canonical name or any alias, case-insensitively, "
                "ignoring trailing registry annotations like '(9CI)' or '(USAN)'. "
                "Formulae are canonicalized to the Hill system, so 'C6H12O6', "
                "'O6C6H12', and 'C₆H₁₂O₆' are equivalent. Examples: 'aspirin', "
                "'acetylsalicylic acid', 'H2O', 'C9H8O4'."
            )
        ),
    ],
    by: Annotated[
        Literal["auto", "name", "formula"],
        Field(
            description=(
                "How to read the query. 'auto' (default) treats a parseable "
                "formula as a formula and anything else as a name, falling back "
                "to the other direction on a miss. 'name' or 'formula' pin it."
            )
        ),
    ] = "auto",
    limit: Annotated[
        int,
        Field(
            description=(
                "Maximum number of compounds to return for a formula lookup. "
                "Isomers share a formula; results are ordered with the most "
                "notable (preferred) name first."
            ),
            ge=1,
            le=50,
        ),
    ] = 5,
) -> dict:
    """Look up a chemical compound by name or molecular formula.

    Searches the bundled name<->formula database (a PubChem subset) first, then
    the writable user cache, then -- unless disabled -- the online fallback
    (PubChem, Wikidata, and, when an API key is set, EPA CompTox). A name
    resolves to its molecular formula(e); a formula resolves to the compound
    name(s) sharing it (isomers), ordered with the preferred name first. The
    direction is chosen by ``by``. Returns the ``query``, how it was interpreted
    (``interpreted_as``), the ``matches`` (each ``{"name", "formula"}``, the
    preferred result first), and the resolving ``source`` / ``license``.

    Raises ``ValueError`` if nothing matches.
    """
    result = find_compound(query, by, limit)
    if not result["matches"]:
        raise ValueError(f"no chemical compound found for {query!r}")
    return result
