#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""Build a bundled name<->formula SQLite store from JSONL (offline tool).

Reads JSONL produced by a fetcher (one of ``tools/fetch_*.py``) with records
``{ref, name, aliases, formulas, rank}`` and writes a single-source ``.db`` under
``src/mcp_molecules/data/``. ``ref`` is the source's own id (Wikidata QID,
PubChem CID, ...) and ``rank`` is a notability proxy (higher = more notable);
compounds are inserted in descending ``rank`` so lower ``id`` ~= more notable --
this is how ``formula -> name`` surfaces a preferred name without a rank column.

Names are normalized and formulas Hill-canonicalized (folding Unicode subscripts
to ASCII) via the shared helpers in ``mcp_molecules.naming``. Not shipped in the
wheel.

Usage:
  python tools/build_namedb.py <input.jsonl> [output.db]
      [--source NAME] [--license SPDX] [--subset-rule TEXT]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mcp_molecules.naming import FormulaError, hill_formula, normalize_name  # noqa: E402

SCHEMA_VERSION = 1
_DATA_DIR = Path(__file__).resolve().parent.parent / "src/mcp_molecules/data"

_DDL = """
CREATE TABLE compounds (
    id             INTEGER PRIMARY KEY,
    canonical_name TEXT NOT NULL
);  -- source/license constant per single-source DB -> kept only in meta;
    -- the source id (ref) is used for build-time dedup but not stored

-- WITHOUT ROWID: the table IS the B-tree keyed by name_norm, so the name is
-- stored once and looked up directly -- no separate (duplicate) index.
CREATE TABLE names (
    name_norm   TEXT NOT NULL,
    compound_id INTEGER NOT NULL,
    PRIMARY KEY (name_norm, compound_id)
) WITHOUT ROWID;

-- Clustered by compound_id for the name->formula direction (no rowid copy);
-- the reverse (formula->name) direction uses idx_formulas_norm below.
CREATE TABLE formulas (
    compound_id  INTEGER NOT NULL,
    formula_norm TEXT NOT NULL,
    is_primary   INTEGER NOT NULL DEFAULT 0,
    is_parsed    INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (compound_id, formula_norm)
) WITHOUT ROWID;

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _canon_formula(raw: str) -> tuple[str, int]:
    """Return (canonical_formula, is_parsed) for a raw formula string."""
    try:
        return hill_formula(raw), 1
    except FormulaError:
        return unicodedata.normalize("NFKC", raw).strip(), 0


def load_records(path: str) -> list[dict]:
    """Read JSONL, drop records lacking a usable name or formula, dedup by ref."""
    by_ref: dict[str, dict] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            name = rec.get("name") or ""
            aliases = [a for a in rec.get("aliases", []) if a]
            # Promote an alias to canonical when the preferred name is missing.
            if not name and aliases:
                name = aliases.pop(0)
            formulas = [f for f in rec.get("formulas", []) if f]
            if not name or not formulas:
                continue
            rec["name"] = name
            rec["aliases"] = aliases
            rec["formulas"] = formulas
            by_ref[str(rec["ref"])] = rec
    # Most notable first -> lower id.
    return sorted(by_ref.values(), key=lambda r: r.get("rank", 0), reverse=True)


def build(
    records: list[dict],
    out: str,
    source: str = "wikidata",
    license: str = "CC0-1.0",
    subset_rule: str = "",
) -> dict[str, int]:
    Path(out).unlink(missing_ok=True)
    con = sqlite3.connect(out)
    con.executescript(_DDL)
    n_names = n_formulas = 0
    for cid, rec in enumerate(records, start=1):
        con.execute(
            "INSERT INTO compounds (id, canonical_name) VALUES (?, ?)",
            (cid, rec["name"]),
        )
        norms = {normalize_name(n) for n in [rec["name"], *rec["aliases"]]}
        norms.discard("")
        con.executemany(
            "INSERT OR IGNORE INTO names (name_norm, compound_id) VALUES (?, ?)",
            [(nm, cid) for nm in norms],
        )
        n_names += len(norms)
        seen: set[str] = set()
        for i, raw in enumerate(rec["formulas"]):
            canon, is_parsed = _canon_formula(raw)
            if not canon or canon in seen:
                continue
            seen.add(canon)
            con.execute(
                "INSERT OR IGNORE INTO formulas (compound_id, formula_norm, is_primary, is_parsed)"
                " VALUES (?,?,?,?)",
                (cid, canon, 1 if i == 0 else 0, is_parsed),
            )
            n_formulas += 1
    # names needs no index (it is the clustered B-tree); formulas needs one for
    # the reverse (formula -> name) direction.
    con.execute("CREATE INDEX idx_formulas_norm ON formulas(formula_norm)")
    stats = {"compounds": len(records), "names": n_names, "formulas": n_formulas}
    meta = {
        "schema_version": str(SCHEMA_VERSION),
        "source": source,
        "license": license,
        "subset_rule": subset_rule,
        "compound_count": str(stats["compounds"]),
        "name_count": str(stats["names"]),
    }
    con.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", list(meta.items()))
    con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    con.commit()
    con.execute("VACUUM")
    con.close()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output", nargs="?")
    ap.add_argument("--source", default="wikidata")
    ap.add_argument("--license", default="CC0-1.0")
    ap.add_argument("--subset-rule", default="")
    args = ap.parse_args()

    out = args.output or str(_DATA_DIR / f"names_{args.source}.db")
    records = load_records(args.input)
    stats = build(records, out, args.source, args.license, args.subset_rule)
    size_mb = Path(out).stat().st_size / 1e6
    print(
        f"DONE: {stats['compounds']} compounds, {stats['names']} names, "
        f"{stats['formulas']} formulas -> {out} ({size_mb:.1f} MB)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
