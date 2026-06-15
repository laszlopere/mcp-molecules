#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Smoke-test the bundled name<->formula DB through the MCP tool functions."""

from mcp_molecules.names import source_license
from mcp_molecules.server import formula_to_name, name_to_formula


def main() -> None:
    src, lic = source_license()
    print(f"source={src} license={lic}")

    for name, expect in [("aspirin", "C9H8O4"), ("caffeine", "C8H10N4O2"), ("water", "H2O")]:
        r = name_to_formula(name)
        ok = r["formula"] == expect
        print(f"name_to_formula({name!r}) -> {r['formula']} {'OK' if ok else 'FAIL exp ' + expect}")
        assert ok, r

    for formula in ["H2O", "C6H12O6", "C9H8O4"]:
        r = formula_to_name(formula)
        names = [m["name"] for m in r["matches"]]
        print(f"formula_to_name({formula!r}) -> preferred={r['preferred_name']!r} matches={names}")
        assert r["matches"], formula

    # Subscript + Hill-reorder equivalence through the tool.
    assert formula_to_name("H₂O")["matches"], "subscript H2O failed"
    print("all end-to-end checks passed")


if __name__ == "__main__":
    main()
