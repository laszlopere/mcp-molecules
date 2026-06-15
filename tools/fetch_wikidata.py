#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""Fetch a bounded name/formula sample from Wikidata into JSONL (offline tool).

Bounds the set by Wikidata sitelink count (``wikibase:sitelinks``, a cheap
precomputed property), keeping compounds notable enough to have at least
``--min-sitelinks`` linked wikis -- a small, high-quality slice (water, aspirin,
caffeine, glucose, ...) suitable for bundling. The sitelink count is captured
only so the builder can order by notability; it is not stored in the database.

Two-phase to stay within the public endpoint's limits:
  1. one light query -> {qid, sitelinks, formula(s)} for the bounded set;
  2. VALUES-batched queries -> English label + aliases for those qids.
The heavy label/alias join is thus scoped to small id batches, not the full set.

Output: one JSON object per line: {qid, name, aliases:[...], formulas:[...],
sitelinks:int}. Full-dataset ingest and shipped-subset tuning are deferred to
TODO 1.2/1.2.1.

Usage: python tools/fetch_wikidata.py [out.jsonl] [--min-sitelinks N] [--batch N]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request

ENDPOINT = "https://query.wikidata.org/sparql"
UA = "mcp-molecules/0.1 (https://github.com/laszlopere/mcp-molecules; laszlopere@gmail.com)"

# Phase 1 is chunked into narrow sitelink bands: one big "?sl >= N" query is
# unreliable on the loaded public endpoint, but each band returns a small,
# dependable result. Bands get finer toward the low end where compounds cluster.
_PHASE1 = """
SELECT ?c ?sl ?formula WHERE {{
  ?c wdt:P274 ?formula . FILTER(isLiteral(?formula))
  ?c wikibase:sitelinks ?sl . FILTER(?sl >= {lo}){hi_clause}
}}
"""

# Upper band edges (descending coverage), kept narrow at the low end where
# compounds cluster, so each band's response stays small enough to not truncate.
_BAND_EDGES = [100, 70, 50, 40, 33, 28, 24, 21, 18, 16, 15, 14, 13, 12, 11]


def _bands(floor: int) -> list[tuple[int, int | None]]:
    """Descending (lo, hi) sitelink bands covering [floor, inf), hi exclusive."""
    edges = [e for e in _BAND_EDGES if e > floor]
    bounds: list[tuple[int, int | None]] = [(edges[0], None)]
    for lo, hi in zip(edges[1:], edges[:-1], strict=True):
        bounds.append((lo, hi))
    bounds.append((floor, edges[-1] if edges else None))
    return bounds

# Labels/aliases for an explicit id list -- cheap because VALUES bounds the scan.
_PHASE2 = """
SELECT ?c ?cLabel (GROUP_CONCAT(DISTINCT ?alias; separator="||") AS ?aliases) WHERE {{
  VALUES ?c {{ {values} }}
  OPTIONAL {{ ?c skos:altLabel ?alias. FILTER(LANG(?alias)="en") }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
GROUP BY ?c ?cLabel
"""


def query(sparql: str) -> list[dict]:
    for attempt in range(6):
        # A trailing comment changes the cache key, so a retry re-executes
        # instead of re-reading a cached truncated/504 response.
        url = ENDPOINT + "?" + urllib.parse.urlencode({"query": f"{sparql}\n#cb{attempt}"})
        req = urllib.request.Request(
            url, headers={"Accept": "application/sparql-results+json", "User-Agent": UA}
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                # strict=False: some literals carry stray control characters.
                return json.loads(resp.read().decode("utf-8"), strict=False)["results"]["bindings"]
        except Exception as exc:  # noqa: BLE001
            wait = 5 * (attempt + 1)
            print(f"  retry {attempt + 1}: {exc} (sleep {wait}s)", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError("query failed after retries")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default="wikidata_names.jsonl")
    ap.add_argument("--min-sitelinks", type=int, default=5)
    ap.add_argument("--batch", type=int, default=400)
    args = ap.parse_args()

    print(f"phase 1: sitelinks >= {args.min_sitelinks} (banded) ...", file=sys.stderr)
    compounds: dict[str, dict] = {}
    for lo, hi in _bands(args.min_sitelinks):
        hi_clause = f" FILTER(?sl < {hi})" if hi is not None else ""
        rows = query(_PHASE1.format(lo=lo, hi_clause=hi_clause))
        for r in rows:
            qid = r["c"]["value"].rsplit("/", 1)[-1]
            rec = compounds.setdefault(
                qid, {"ref": qid, "rank": int(r["sl"]["value"]), "formulas": []}
            )
            f = r["formula"]["value"]
            if f not in rec["formulas"]:
                rec["formulas"].append(f)
        band = f">={lo}" + (f",<{hi}" if hi else "")
        print(f"  band {band}: +{len(rows)} rows -> {len(compounds)} compounds", file=sys.stderr)
        time.sleep(1)
    qids = list(compounds)
    print(f"  {len(qids)} compounds total", file=sys.stderr)

    print("phase 2: labels + aliases ...", file=sys.stderr)
    for i in range(0, len(qids), args.batch):
        chunk = qids[i : i + args.batch]
        values = " ".join(f"wd:{q}" for q in chunk)
        for r in query(_PHASE2.format(values=values)):
            qid = r["c"]["value"].rsplit("/", 1)[-1]
            label = r.get("cLabel", {}).get("value", "")
            if label == qid:  # service echoes the QID when no English label
                label = ""
            compounds[qid]["name"] = label
            compounds[qid]["aliases"] = [
                a for a in r.get("aliases", {}).get("value", "").split("||") if a
            ]
        print(f"  {min(i + args.batch, len(qids))}/{len(qids)}", file=sys.stderr)
        time.sleep(1)  # be polite to the public endpoint

    with open(args.out, "w", encoding="utf-8") as fh:
        for rec in compounds.values():
            rec.setdefault("name", "")
            rec.setdefault("aliases", [])
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"DONE: {len(compounds)} records -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
