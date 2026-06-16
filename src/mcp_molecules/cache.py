# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""Tier-2 writable name<->formula cache, one SQLite file per source (TODO 2.0).

A set of small SQLite stores in the user's data directory holding records
fetched on-demand from the online fallback (:mod:`mcp_molecules.remote`, TODO
1.4.3). The cache sits between the bundled subset (Tier 1, read-only) and the
network (Tier 3): it is read after the bundle and before any HTTP call, and
written with whatever the network returns so a second lookup of the same compound
never hits the wire.

Each source gets its own file, ``names_cache_<source>.db`` (e.g.
``names_cache_wikidata.db``), so sources can be added, refreshed, or dropped
independently and the query layer (``CacheSource`` in :mod:`mcp_molecules.names`)
simply iterates the files it finds. Per-file provenance replaces the old per-row
``source`` / ``license`` columns: the file name carries the source and the
per-file ``meta`` table records the source + license. Each file also keeps its
own ``negcache`` table of negative results with a TTL -- a remembered "not found"
so repeated misses do not re-query that source forever.

Directory: ``$MCP_MOLECULES_CACHE_DIR`` if set, else ``$XDG_DATA_HOME/
mcp-molecules`` (``~/.local/share/mcp-molecules`` when XDG is unset). A source's
file is created lazily on the first write to it; pure reads of a missing file are
a no-op.
"""

from __future__ import annotations

import os
import sqlite3
import time
import unicodedata
from functools import cache as _cache
from pathlib import Path

from .naming import FormulaError, hill_formula, normalize_name

# Negative-cache lifetime: how long a remembered miss suppresses a re-query.
_DEFAULT_NEGCACHE_TTL = 7 * 24 * 3600  # one week

_FILE_PREFIX = "names_cache_"
_FILE_SUFFIX = ".db"

_DDL = """
CREATE TABLE IF NOT EXISTS compounds (
    id             INTEGER PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    source_ref     TEXT,
    fetched_at     REAL NOT NULL,
    UNIQUE (source_ref)
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


def cache_dir() -> Path:
    """Resolve the cache directory (env override, else XDG data dir)."""
    override = os.environ.get("MCP_MOLECULES_CACHE_DIR")
    if override:
        return Path(override)
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "mcp-molecules"


def cache_path(source: str) -> Path:
    """Path of a single source's cache file, ``names_cache_<source>.db``."""
    return cache_dir() / f"{_FILE_PREFIX}{source}{_FILE_SUFFIX}"


def list_sources() -> list[str]:
    """Names of the sources that have a cache file present, sorted.

    Discovers ``names_cache_<source>.db`` files in :func:`cache_dir`; the query
    layer iterates these. Returns ``[]`` when the directory does not exist yet.
    """
    directory = cache_dir()
    if not directory.is_dir():
        return []
    out: list[str] = []
    for entry in directory.iterdir():
        name = entry.name
        if name.startswith(_FILE_PREFIX) and name.endswith(_FILE_SUFFIX) and entry.is_file():
            out.append(name[len(_FILE_PREFIX) : -len(_FILE_SUFFIX)])
    return sorted(out)


def negcache_ttl() -> float:
    """Negative-cache TTL in seconds (``$MCP_MOLECULES_NEGCACHE_TTL`` override)."""
    raw = os.environ.get("MCP_MOLECULES_NEGCACHE_TTL")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return _DEFAULT_NEGCACHE_TTL


@_cache
def _connect(source: str) -> sqlite3.Connection:
    """Open (creating dir + schema) one source's cache file; cached per process.

    Only called once a write is needed or the file already exists -- callers
    guard pure reads with :func:`cache_path`.exists() so reading a never-written
    source does not create its file.
    """
    path = cache_path(source)
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


def source_license(source: str) -> tuple[str, str]:
    """Return one source's ``(source, license)`` from its file's ``meta`` table."""
    if not cache_path(source).exists():
        return "", ""
    rows = _connect(source).execute("SELECT key, value FROM meta").fetchall()
    meta = {r["key"]: r["value"] for r in rows}
    return meta.get("source", source), meta.get("license", "")


def lookup_formula(source: str, name: str) -> list[dict]:
    """Resolve a name to cached compounds in one source's file.

    Returns ``{"name", "formula"}`` matches (empty if uncached or no file).
    Provenance is per-file -- use :func:`source_license` for the source/license.
    """
    key = normalize_name(name)
    if not key or not cache_path(source).exists():
        return []
    rows = (
        _connect(source)
        .execute(
            """
        SELECT c.canonical_name AS name, f.formula_norm AS formula
        FROM names n
        JOIN compounds c ON c.id = n.compound_id
        JOIN formulas f ON f.compound_id = c.id
        WHERE n.name_norm = ?
        ORDER BY f.is_primary DESC, c.id ASC
        """,
            (key,),
        )
        .fetchall()
    )
    return _dedup(rows)


def lookup_names(source: str, formula: str, limit: int = 5) -> list[dict]:
    """Resolve a formula to cached compound names in one source's file."""
    key = formula_key(formula)
    if not key or not cache_path(source).exists():
        return []
    rows = (
        _connect(source)
        .execute(
            """
        SELECT c.canonical_name AS name
        FROM formulas f
        JOIN compounds c ON c.id = f.compound_id
        WHERE f.formula_norm = ?
        ORDER BY c.id ASC
        LIMIT ?
        """,
            (key, limit),
        )
        .fetchall()
    )
    return [{"name": r["name"], "formula": key} for r in rows]


def _dedup(rows: list[sqlite3.Row]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for r in rows:
        item = (r["name"], r["formula"])
        if item in seen:
            continue
        seen.add(item)
        out.append({"name": r["name"], "formula": r["formula"]})
    return out


# --- negative cache --------------------------------------------------------


def is_negative(source: str, query_norm: str, direction: str) -> bool:
    """True if ``source`` holds a still-fresh remembered miss for (query, direction)."""
    if not query_norm or not cache_path(source).exists():
        return False
    row = (
        _connect(source)
        .execute(
            "SELECT fetched_at FROM negcache WHERE query_norm = ? AND direction = ?",
            (query_norm, direction),
        )
        .fetchone()
    )
    if row is None:
        return False
    return (time.time() - row["fetched_at"]) < negcache_ttl()


def remember_miss(source: str, query_norm: str, direction: str) -> None:
    """Record (or refresh) a negative-cache entry in ``source``'s file."""
    if not query_norm:
        return
    con = _connect(source)
    con.execute(
        "INSERT OR REPLACE INTO negcache (query_norm, direction, fetched_at) VALUES (?, ?, ?)",
        (query_norm, direction, time.time()),
    )
    con.commit()


# --- writes ----------------------------------------------------------------


def store(records: list[dict], source: str, license: str) -> int:
    """Insert fetched ``records`` into ``source``'s cache file; return the number added.

    Each record is ``{"ref", "name", "aliases", "formulas"}`` (the shape the
    fetchers emit). A record already present (same ``ref``) is skipped, so
    re-fetching is idempotent. The source + license are written to the file's
    ``meta`` table on first use. Recording a hit also clears any matching
    name/formula negative-cache entries in that file.
    """
    con = _connect(source)
    con.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('source', ?)", (source,))
    con.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('license', ?)", (license,))
    added = 0
    now = time.time()
    for rec in records:
        name = rec.get("name") or ""
        formulas = [f for f in rec.get("formulas", []) if f]
        if not name or not formulas:
            continue
        ref = str(rec.get("ref")) if rec.get("ref") is not None else None
        existing = con.execute("SELECT id FROM compounds WHERE source_ref IS ?", (ref,)).fetchone()
        if existing is not None:
            continue
        cur = con.execute(
            "INSERT INTO compounds (canonical_name, source_ref, fetched_at) VALUES (?, ?, ?)",
            (name, ref, now),
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
            con.execute("DELETE FROM negcache WHERE query_norm = ? AND direction = 'name'", (nm,))
        added += 1
    con.commit()
    return added
