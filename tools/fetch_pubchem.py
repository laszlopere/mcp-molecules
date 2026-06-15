#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""Fetch a bounded name/formula sample from PubChem into JSONL (offline tool).

Uses the PubChem PUG-REST endpoints over a contiguous CID range. PubChem is
U.S. government work -- public domain (acknowledgment requested, not required).
Low CIDs are disproportionately the common compounds (water=962, aspirin=2244,
caffeine=2519, glucose=5793), so CIDs 1..N make a high-quality bounded sample.

Per CID batch it fetches two things:
  1. property/MolecularFormula,Title  -> canonical name + formula
  2. synonyms                         -> alias list AND a popularity signal
The synonym *count* is a strong notability proxy (aspirin ~696, caffeine ~413,
water ~319, vs obscure compounds ~17), so we store it as ``rank`` -- a far
better "preferred name" ordering than the old -CID proxy. Curation (alias
filtering, thresholding) happens at build time.

Output: one JSON object per line:
  {ref, name, aliases:[...], formulas:[...], rank}   # rank = synonym count

Usage: python tools/fetch_pubchem.py [out.jsonl] [--max-cid N] [--batch N]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid"
PROP_URL = f"{BASE}/property/MolecularFormula,Title/JSON"
SYN_URL = f"{BASE}/synonyms/JSON"
UA = "mcp-molecules/0.1 (https://github.com/laszlopere/mcp-molecules; laszlopere@gmail.com)"


def _post(url: str, cids: list[int]) -> dict | None:
    """POST a CID batch; return parsed JSON, or None if the batch is absent."""
    data = urllib.parse.urlencode({"cid": ",".join(map(str, cids))}).encode()
    headers = {"Accept": "application/json", "User-Agent": UA}
    req = urllib.request.Request(url, data=data, headers=headers)
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:  # nothing in this (sparse) range
                return None
            wait = 3 * (attempt + 1)
            print(f"  retry {attempt + 1}: {exc} (sleep {wait}s)", file=sys.stderr)
            time.sleep(wait)
        except Exception as exc:  # noqa: BLE001
            wait = 3 * (attempt + 1)
            print(f"  retry {attempt + 1}: {exc} (sleep {wait}s)", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"PubChem batch failed: {cids[0]}..{cids[-1]}")


def fetch_props(cids: list[int]) -> dict[int, tuple[str, str]]:
    payload = _post(PROP_URL, cids)
    out: dict[int, tuple[str, str]] = {}
    if not payload:
        return out
    for p in payload.get("PropertyTable", {}).get("Properties", []):
        out[p["CID"]] = (p.get("Title", ""), p.get("MolecularFormula", ""))
    return out


def fetch_synonyms(cids: list[int]) -> dict[int, list[str]]:
    payload = _post(SYN_URL, cids)
    out: dict[int, list[str]] = {}
    if not payload:
        return out
    for info in payload.get("InformationList", {}).get("Information", []):
        out[info["CID"]] = info.get("Synonym", [])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default="pubchem_names.jsonl")
    ap.add_argument("--max-cid", type=int, default=100_000)
    ap.add_argument("--batch", type=int, default=100)
    args = ap.parse_args()

    total = 0
    with open(args.out, "w", encoding="utf-8") as fh:
        for start in range(1, args.max_cid + 1, args.batch):
            cids = list(range(start, min(start + args.batch, args.max_cid + 1)))
            props = fetch_props(cids)
            syns = fetch_synonyms(cids)
            for cid, (title, formula) in props.items():
                if not title or not formula:
                    continue
                synonyms = syns.get(cid, [])
                aliases = [s for s in synonyms if s.strip() and s != title]
                rec = {
                    "ref": f"CID{cid}",
                    "name": title,
                    "aliases": aliases,
                    "formulas": [formula],
                    "rank": len(synonyms),  # popularity proxy (synonym count)
                }
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                total += 1
            print(f"  CID {cids[-1]}/{args.max_cid}: {total} records", file=sys.stderr)
            time.sleep(0.3)  # stay under PUG-REST's 5 req/s limit
    print(f"DONE: {total} records -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
