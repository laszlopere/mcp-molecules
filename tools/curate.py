#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""Curate a raw fetched JSONL into the bundled 'everyday' subset (offline tool).

Keeps a compound only if its synonym-count rank is high enough (notable) AND its
cleaned display name reads like an everyday name (not a systematic IUPAC name).
Combines the two orthogonal signals:
  - rank >= --min-rank            -> drops obscure compounds
  - is_common_name(clean Title)   -> drops systematic names
PubChem Title cruft is stripped (clean_title), and aliases are filtered to
name-like synonyms (CAS numbers / codes dropped). Output feeds build_namedb.py.

Usage: python tools/curate.py <raw.jsonl> <curated.jsonl> [--min-rank N]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # tools/ for name_filter
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from name_filter import clean_title, is_common_name  # noqa: E402

from mcp_molecules.naming import normalize_name  # noqa: E402


def curate(records: list[dict], min_rank: int) -> list[dict]:
    out: list[dict] = []
    for r in records:
        if r.get("rank", 0) < min_rank:
            continue
        name = clean_title(r.get("name", ""))
        if not is_common_name(name):  # display name must be everyday-looking
            continue
        seen = {normalize_name(name)}
        aliases: list[str] = []
        for a in r.get("aliases", []):
            if is_common_name(a):  # keep name-like aliases, drop CAS/codes
                na = normalize_name(a)
                if na and na not in seen:
                    seen.add(na)
                    aliases.append(a)
        out.append(
            {
                "ref": r["ref"],
                "name": name,
                "aliases": aliases,
                "formulas": r["formulas"],
                "rank": r["rank"],
            }
        )
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--min-rank", type=int, default=50)
    args = ap.parse_args()

    records = []
    with open(args.input, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    curated = curate(records, args.min_rank)
    with open(args.output, "w", encoding="utf-8") as fh:
        for r in curated:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    n_aliases = sum(len(r["aliases"]) for r in curated)
    print(
        f"DONE: {len(records)} -> {len(curated)} compounds "
        f"(rank>={args.min_rank}, {n_aliases} aliases) -> {args.output}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
