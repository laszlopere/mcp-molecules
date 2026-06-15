# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""Read-only name <-> formula lookups over the bundled SQLite store.

Opens the bundled name database read-only (and immutable, since it never changes
at runtime), resolving it through :func:`importlib.resources.as_file` so it works
for both normal and zip-imported installs. The connection is cached for the
process lifetime, mirroring :func:`mcp_molecules.weights.load_weights`.
"""

from __future__ import annotations

import contextlib
import sqlite3
import unicodedata
from functools import lru_cache
from importlib.resources import as_file, files
from typing import Literal, Protocol

from .naming import FormulaError, hill_formula, normalize_name

Direction = Literal["auto", "name", "formula"]

_DB_FILE = "names_pubchem.db"

# Keeps any temp file produced by as_file (zipimport case) alive for the process.
_stack = contextlib.ExitStack()


class NameDBUnavailable(RuntimeError):
    """Raised when the bundled name database is missing."""


@lru_cache(maxsize=1)
def _connect() -> sqlite3.Connection:
    resource = files("mcp_molecules.data").joinpath(_DB_FILE)
    try:
        path = _stack.enter_context(as_file(resource))
    except (FileNotFoundError, ModuleNotFoundError) as exc:  # pragma: no cover
        raise NameDBUnavailable(f"bundled name database {_DB_FILE!r} not found") from exc
    if not path.is_file():
        raise NameDBUnavailable(f"bundled name database {_DB_FILE!r} not found")
    con = sqlite3.connect(
        f"file:{path}?mode=ro&immutable=1", uri=True, check_same_thread=False
    )
    con.row_factory = sqlite3.Row
    return con


@lru_cache(maxsize=1)
def _meta() -> dict[str, str]:
    rows = _connect().execute("SELECT key, value FROM meta").fetchall()
    return {r["key"]: r["value"] for r in rows}


def source_license() -> tuple[str, str]:
    """Return the ``(source, license)`` of the bundled dataset."""
    m = _meta()
    return m.get("source", ""), m.get("license", "")


def lookup_formula(name: str) -> list[dict]:
    """Return compounds whose canonical name or any alias matches ``name``.

    Each result is ``{"name", "formula"}`` (``name`` is the canonical display
    name). Empty list if nothing matches.
    """
    key = normalize_name(name)
    if not key:
        return []
    rows = _connect().execute(
        """
        SELECT c.canonical_name AS name, f.formula_norm AS formula
        FROM names n
        JOIN compounds c ON c.id = n.compound_id
        JOIN formulas f ON f.compound_id = c.id
        WHERE n.name_norm = ?
        ORDER BY f.is_primary DESC, c.id ASC
        """,
        (key,),
    ).fetchall()
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for r in rows:
        item = (r["name"], r["formula"])
        if item in seen:
            continue
        seen.add(item)
        out.append({"name": r["name"], "formula": r["formula"]})
    return out


def lookup_names(formula: str, limit: int = 5) -> list[dict]:
    """Return compounds with the given molecular ``formula`` (isomers share one).

    The formula is canonicalized to a Hill string; if it cannot be parsed, the
    NFKC + subscript-folded raw form is matched instead. Results are ordered with
    the preferred (most notable) name first. Each item is ``{"name", "formula"}``
    (``formula`` is the canonical key, identical across the isomers).
    """
    try:
        key = hill_formula(formula)
    except FormulaError:
        key = unicodedata.normalize("NFKC", formula).strip()
    if not key:
        return []
    rows = _connect().execute(
        """
        SELECT c.canonical_name AS name
        FROM formulas f
        JOIN compounds c ON c.id = f.compound_id
        WHERE f.formula_norm = ?
        ORDER BY c.id ASC
        LIMIT ?
        """,
        (key, limit),
    ).fetchall()
    return [{"name": r["name"], "formula": key} for r in rows]


# --- layered lookup (TODO 1.4) ---------------------------------------------


class Source(Protocol):
    """One tier of the layered name<->formula lookup.

    Implementations search a single backing store and are consulted in the order
    they appear in :data:`SOURCES`: the bundled subset first, then -- once built
    (TODO 1.4.2 / 1.4.3) -- the writable user cache and the online fallback.
    """

    label: str

    def by_name(self, name: str) -> list[dict]: ...
    def by_formula(self, formula: str, limit: int) -> list[dict]: ...
    def source_license(self) -> tuple[str, str]: ...


class BundledSource:
    """Tier 1: the package-shipped, read-only SQLite subset (TODO 1.4.1)."""

    label = "bundled"

    def by_name(self, name: str) -> list[dict]:
        return lookup_formula(name)

    def by_formula(self, formula: str, limit: int) -> list[dict]:
        return lookup_names(formula, limit)

    def source_license(self) -> tuple[str, str]:
        return source_license()


# Lookup order: bundled subset -> (later) user cache -> (later) online fallback.
SOURCES: list[Source] = [BundledSource()]


def _directions(query: str, by: Direction) -> tuple[str, ...]:
    """Resolve which lookup direction(s) to try, in order.

    ``"name"`` / ``"formula"`` pin the direction. ``"auto"`` guesses from the
    query -- a parseable formula is tried as a formula first, anything else as a
    name -- and falls back to the other direction if the first finds nothing.
    """
    if by == "name":
        return ("name",)
    if by == "formula":
        return ("formula",)
    try:
        hill_formula(query)
    except FormulaError:
        return ("name", "formula")
    return ("formula", "name")


def find_compound(query: str, by: Direction = "auto", limit: int = 5) -> dict:
    """Resolve ``query`` (a name or a formula) against the layered sources.

    Tries the direction(s) chosen by ``by`` (see :func:`_directions`) across the
    sources in :data:`SOURCES`, returning the first non-empty hit. The result is
    ``{"query", "interpreted_as", "matches", "source", "license"}`` where each
    match is ``{"name", "formula"}``; ``matches`` is empty if nothing is found.
    """
    directions = _directions(query, by)
    for direction in directions:
        for source in SOURCES:
            hits = (
                source.by_name(query)
                if direction == "name"
                else source.by_formula(query, limit)
            )
            if hits:
                src, lic = source.source_license()
                return {
                    "query": query,
                    "interpreted_as": direction,
                    "matches": hits,
                    "source": src,
                    "license": lic,
                }
    return {
        "query": query,
        "interpreted_as": directions[0],
        "matches": [],
        "source": "",
        "license": "",
    }
