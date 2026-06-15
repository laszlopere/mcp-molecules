# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""Tier-2 writable name<->formula cache (TODO 1.4.2 / 3).

A small SQLite store in the user's data directory that holds records fetched
on-demand from the online fallback (:mod:`mcp_molecules.remote`, TODO 1.4.3). It
sits between the bundled subset (Tier 1, read-only) and the network (Tier 3): it
is read after the bundle and before any HTTP call, and written with whatever the
network returns so a second lookup of the same compound never hits the wire.

The schema mirrors the bundled store (``compounds`` / ``names`` / ``formulas``)
so the same query shape works, plus a per-compound source/license (the cache
mixes sources) and a ``negcache`` table for negative results with a TTL -- a
remembered "not found" so repeated misses do not re-query the network forever.

Path: ``$MCP_MOLECULES_CACHE_DB`` if set, else ``$XDG_DATA_HOME/mcp-molecules/
names_cache.db`` (``~/.local/share/...`` when XDG is unset). The file is created
lazily on the first write; pure reads of a missing cache are a no-op.
"""

from __future__ import annotations

import os
import sqlite3
import time
import unicodedata
from functools import lru_cache
from pathlib import Path

from .naming import FormulaError, hill_formula, normalize_name

# Negative-cache lifetime: how long a remembered miss suppresses a re-query.
_DEFAULT_NEGCACHE_TTL = 7 * 24 * 3600  # one week

_DDL = """
CREATE TABLE IF NOT EXISTS compounds (
    id             INTEGER PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    source         TEXT NOT NULL,
    license        TEXT NOT NULL,
    source_ref     TEXT,
    fetched_at     REAL NOT NULL,
    UNIQUE (source, source_ref)
);

CREATE TABLE IF NOT EXISTS names (
    name_norm   TEXT NOT NULL,
    compound_id INTEGER NOT NULL,
    PRIMARY KEY (name_norm, compound_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS formulas (
    compound_id  INTEGER NOT NULL,
    formula_norm TEXT NOT NULL,
    is_primary   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (compound_id, formula_norm)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_formulas_norm ON formulas(formula_norm);

-- A remembered miss: (normalized query, direction) -> when it was recorded.
CREATE TABLE IF NOT EXISTS negcache (
    query_norm TEXT NOT NULL,
    direction  TEXT NOT NULL,
    fetched_at REAL NOT NULL,
    PRIMARY KEY (query_norm, direction)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def cache_path() -> Path:
    """Resolve the cache database path (env override, else XDG data dir)."""
    override = os.environ.get("MCP_MOLECULES_CACHE_DB")
    if override:
        return Path(override)
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "mcp-molecules" / "names_cache.db"


def negcache_ttl() -> float:
    """Negative-cache TTL in seconds (``$MCP_MOLECULES_NEGCACHE_TTL`` override)."""
    raw = os.environ.get("MCP_MOLECULES_NEGCACHE_TTL")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return _DEFAULT_NEGCACHE_TTL


@lru_cache(maxsize=1)
def _connect() -> sqlite3.Connection:
    """Open (creating dir + schema) the writable cache; cached per process.

    Only called once a write is needed or the file already exists -- callers
    guard pure reads with :func:`cache_path`.exists() so reading a never-written
    cache does not create it.
    """
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(_DDL)
    con.commit()
    return con


def formula_key(formula: str) -> str:
    """Hill-canonicalize ``formula``, falling back to the NFKC raw form."""
    try:
        return hill_formula(formula)
    except FormulaError:
        return unicodedata.normalize("NFKC", formula).strip()


# --- reads -----------------------------------------------------------------


def lookup_formula(name: str) -> tuple[list[dict], str, str]:
    """Resolve a name to cached compounds; return (matches, source, license).

    ``matches`` are ``{"name", "formula"}`` (empty if uncached or no file).
    ``source`` / ``license`` describe the first match's provenance -- the cache
    mixes sources, so this reports the winning record's origin.
    """
    key = normalize_name(name)
    if not key or not cache_path().exists():
        return [], "", ""
    rows = _connect().execute(
        """
        SELECT c.canonical_name AS name, f.formula_norm AS formula,
               c.source AS source, c.license AS license
        FROM names n
        JOIN compounds c ON c.id = n.compound_id
        JOIN formulas f ON f.compound_id = c.id
        WHERE n.name_norm = ?
        ORDER BY f.is_primary DESC, c.id ASC
        """,
        (key,),
    ).fetchall()
    return _dedup(rows)


def lookup_names(formula: str, limit: int = 5) -> tuple[list[dict], str, str]:
    """Resolve a formula to cached compound names; return (matches, source, license)."""
    key = formula_key(formula)
    if not key or not cache_path().exists():
        return [], "", ""
    rows = _connect().execute(
        """
        SELECT c.canonical_name AS name, c.source AS source, c.license AS license
        FROM formulas f
        JOIN compounds c ON c.id = f.compound_id
        WHERE f.formula_norm = ?
        ORDER BY c.id ASC
        LIMIT ?
        """,
        (key, limit),
    ).fetchall()
    matches = [{"name": r["name"], "formula": key} for r in rows]
    if not rows:
        return [], "", ""
    return matches, rows[0]["source"], rows[0]["license"]


def _dedup(rows: list[sqlite3.Row]) -> tuple[list[dict], str, str]:
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for r in rows:
        item = (r["name"], r["formula"])
        if item in seen:
            continue
        seen.add(item)
        out.append({"name": r["name"], "formula": r["formula"]})
    if not out:
        return [], "", ""
    return out, rows[0]["source"], rows[0]["license"]


# --- negative cache --------------------------------------------------------


def is_negative(query_norm: str, direction: str) -> bool:
    """True if a still-fresh remembered miss exists for (query, direction)."""
    if not query_norm or not cache_path().exists():
        return False
    row = _connect().execute(
        "SELECT fetched_at FROM negcache WHERE query_norm = ? AND direction = ?",
        (query_norm, direction),
    ).fetchone()
    if row is None:
        return False
    return (time.time() - row["fetched_at"]) < negcache_ttl()


def remember_miss(query_norm: str, direction: str) -> None:
    """Record (or refresh) a negative-cache entry for (query, direction)."""
    if not query_norm:
        return
    con = _connect()
    con.execute(
        "INSERT OR REPLACE INTO negcache (query_norm, direction, fetched_at) VALUES (?, ?, ?)",
        (query_norm, direction, time.time()),
    )
    con.commit()


# --- writes ----------------------------------------------------------------


def store(records: list[dict], source: str, license: str) -> int:
    """Insert fetched ``records`` into the cache; return the number added.

    Each record is ``{"ref", "name", "aliases", "formulas"}`` (the shape the
    fetchers emit). A record already present (same ``source`` + ``ref``) is
    skipped, so re-fetching is idempotent. Recording a hit also clears any
    matching name/formula negative-cache entries.
    """
    added = 0
    con = _connect()
    now = time.time()
    for rec in records:
        name = rec.get("name") or ""
        formulas = [f for f in rec.get("formulas", []) if f]
        if not name or not formulas:
            continue
        ref = str(rec.get("ref")) if rec.get("ref") is not None else None
        existing = con.execute(
            "SELECT id FROM compounds WHERE source = ? AND source_ref IS ?",
            (source, ref),
        ).fetchone()
        if existing is not None:
            continue
        cur = con.execute(
            "INSERT INTO compounds (canonical_name, source, license, source_ref, fetched_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (name, source, license, ref, now),
        )
        cid = cur.lastrowid
        norms = {normalize_name(n) for n in [name, *rec.get("aliases", [])]}
        norms.discard("")
        con.executemany(
            "INSERT OR IGNORE INTO names (name_norm, compound_id) VALUES (?, ?)",
            [(nm, cid) for nm in norms],
        )
        for i, raw in enumerate(formulas):
            key = formula_key(raw)
            if not key:
                continue
            con.execute(
                "INSERT OR IGNORE INTO formulas (compound_id, formula_norm, is_primary)"
                " VALUES (?, ?, ?)",
                (cid, key, 1 if i == 0 else 0),
            )
            con.execute(
                "DELETE FROM negcache WHERE query_norm = ? AND direction = 'formula'", (key,)
            )
        for nm in norms:
            con.execute(
                "DELETE FROM negcache WHERE query_norm = ? AND direction = 'name'", (nm,)
            )
        added += 1
    con.commit()
    return added
