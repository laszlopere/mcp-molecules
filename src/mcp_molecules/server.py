# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""FastMCP application for mcp-molecules. All tools register on this app.

This module is currently a scaffold: the ``molecular_weight_calculator`` tool
exposes its full interface but the calculation itself is not yet implemented.
"""

import platform
from importlib.metadata import version
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from mcp_molecules import __version__

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

    NOTE: implementation is pending — this scaffold defines the interface only.
    """
    raise NotImplementedError(
        "molecular_weight_calculator is not implemented yet; this is a scaffold."
    )
