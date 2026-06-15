#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Smoke-test the bundled name<->formula DB through the MCP tool functions."""

from mcp_molecules.names import source_license
from mcp_molecules.server import find_chemical_compound


def main() -> None:
    src, lic = source_license()
    print(f"source={src} license={lic}")

    for name, expect in [("aspirin", "C9H8O4"), ("caffeine", "C8H10N4O2"), ("water", "H2O")]:
        r = find_chemical_compound(name)
        got = r["matches"][0]["formula"]
        ok = r["interpreted_as"] == "name" and got == expect
        print(f"find_chemical_compound({name!r}) -> {got} {'OK' if ok else 'FAIL exp ' + expect}")
        assert ok, r

    for formula in ["H2O", "C6H12O6", "C9H8O4"]:
        r = find_chemical_compound(formula)
        names = [m["name"] for m in r["matches"]]
        print(f"find_chemical_compound({formula!r}) -> as={r['interpreted_as']} matches={names}")
        assert r["interpreted_as"] == "formula" and r["matches"], formula

    # Subscript + Hill-reorder equivalence through the tool.
    assert find_chemical_compound("H₂O")["matches"], "subscript H2O failed"
    print("all end-to-end checks passed")


if __name__ == "__main__":
    main()
