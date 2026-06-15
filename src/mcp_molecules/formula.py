# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""Chemical-formula parser.

Recursive-descent parser for chemical formulae, ported from the C ``mwc``
project. Supports element symbols, integer multipliers, arbitrarily nested
parenthetical groups, and isotope labels (``D``, ``T``). Produces an ordered
atom tally: a list of ``(symbol, count)`` pairs in first-appearance order, with
repeated elements summed.
"""

from __future__ import annotations


class FormulaError(ValueError):
    """Raised when a formula cannot be parsed."""


# Unicode subscript digits U+2080-U+2089 -> ASCII "0"-"9", so that formulae
# written with typographic subscripts (e.g. "H₂O") parse like "H2O".
_SUBSCRIPT_DIGITS = {0x2080 + d: ord("0") + d for d in range(10)}


# A parsed term is one of:
#   ("elem", symbol, count)
#   ("group", [term, ...], count)
_Term = tuple


class _Parser:
    def __init__(self, src: str) -> None:
        self.src = src
        self.pos = 0

    def peek(self) -> str:
        return self.src[self.pos] if self.pos < len(self.src) else ""

    def parse_count(self) -> int:
        start = self.pos
        while "0" <= self.peek() <= "9":
            self.pos += 1
        if self.pos == start:
            return 1
        return int(self.src[start : self.pos])

    def parse_term(self) -> _Term:
        c = self.peek()
        if c == "(":
            self.pos += 1
            inner = self.parse_seq(in_group=True)
            if self.peek() != ")":
                raise FormulaError(f"expected ')' at pos {self.pos}")
            self.pos += 1
            return ("group", inner, self.parse_count())
        if c.isascii() and c.isupper():
            start = self.pos
            self.pos += 1
            while self.peek().isascii() and self.peek().islower():
                self.pos += 1
            symbol = self.src[start : self.pos]
            return ("elem", symbol, self.parse_count())
        raise FormulaError(f"expected element or '(' at pos {self.pos}")

    def parse_seq(self, in_group: bool) -> list[_Term]:
        terms: list[_Term] = []
        while True:
            c = self.peek()
            if c == "":
                break
            if c == ")":
                if not in_group:
                    raise FormulaError(f"unexpected ')' at pos {self.pos}")
                break
            terms.append(self.parse_term())
        if not terms:
            raise FormulaError(f"empty formula at pos {self.pos}")
        return terms


def parse_formula(src: str) -> list[tuple[str, int]]:
    """Parse ``src`` into an ordered atom tally of ``(symbol, count)`` pairs.

    Unicode subscript digits (``U+2080``-``U+2089``) are accepted and treated
    as their ASCII equivalents, so ``"H₂O"`` parses the same as ``"H2O"``.

    Raises :class:`FormulaError` on malformed input.
    """
    src = src.translate(_SUBSCRIPT_DIGITS)
    parser = _Parser(src)
    terms = parser.parse_seq(in_group=False)
    if parser.peek() != "":
        raise FormulaError(f"trailing input at pos {parser.pos}")

    order: dict[str, int] = {}
    tally: list[list] = []  # [symbol, count], mutable for accumulation

    def add(symbol: str, count: int) -> None:
        idx = order.get(symbol)
        if idx is None:
            order[symbol] = len(tally)
            tally.append([symbol, count])
        else:
            tally[idx][1] += count

    def walk(term: _Term, multiplier: int) -> None:
        if term[0] == "elem":
            add(term[1], multiplier * term[2])
        else:  # group
            sub = multiplier * term[2]
            for child in term[1]:
                walk(child, sub)

    for top in terms:
        walk(top, 1)

    return [(symbol, count) for symbol, count in tally]
