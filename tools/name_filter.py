#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""Heuristic 'everyday name' vs 'systematic name' classifier (offline).

``is_common_name(name)`` returns True for names a layperson would use (caffeine,
sulfuric acid, aspirin) and False for systematic IUPAC names that only carry
locants/brackets/stereochemistry (2-(acetyloxy)-..., 1,3,7-trimethyl-...). Used
at build time to select the bundled subset; tunable.

Run directly to analyse a JSONL file: prints keep/drop counts, evenly-spread
samples of each, and a sanity check on known names.

  python tools/name_filter.py <names.jsonl>
"""

from __future__ import annotations

import json
import re
import sys

# A digit glued to a locant separator: "2-", "-3", "1,3", "N,2".
_LOCANT = re.compile(r"\d[-,]|[-,]\d")
# Oxidation-state parens that ARE everyday: "(II)", "(III)", "(IV)"... allow them.
_OXSTATE = re.compile(r"\((?:i{1,3}|iv|v|vi{0,3}|ix|x)\)", re.IGNORECASE)
_BRACKETS = set("[]{}()")

_MAX_LEN = 30
_MAX_WORDS = 4
_MAX_DIGITS = 2


def clean_title(title: str) -> str:
    """Strip a trailing stereo/config descriptor from a PubChem Title.

    "Ibuprofen, (+-)-" -> "Ibuprofen"; "Chrysanthemic acid, cis-(+)-" ->
    "Chrysanthemic acid". Leaves real names (and salt forms like "Lidocaine
    Hydrochloride") untouched.
    """
    m = re.search(r",\s*([^,]*)$", title)
    if m and len(m.group(1)) <= 12 and re.search(r"[()±]|\bcis\b|\btrans\b", m.group(1), re.I):
        return title[: m.start()].strip()
    return title


def is_common_name(name: str) -> bool:
    """True if ``name`` looks like an everyday/trivial chemical name."""
    n = name.strip()
    if not n or len(n) > _MAX_LEN:
        return False
    # Allow roman-numeral oxidation states before the bracket check.
    probe = _OXSTATE.sub("", n)
    if any(c in _BRACKETS for c in probe):
        return False
    if "," in probe or _LOCANT.search(probe):
        return False
    if probe[:1].isdigit():
        return False
    if len(n.split()) > _MAX_WORDS:
        return False
    if sum(c.isdigit() for c in n) > _MAX_DIGITS:
        return False
    return True


_SANITY_KEEP = [
    "Caffeine",
    "Water",
    "Ethanol",
    "Aspirin",
    "D-Glucose",
    "Sulfuric acid",
    "Carbon dioxide",
    "Sodium chloride",
    "Copper(II) sulfate",
    "Vitamin B12",
    "beta-Carotene",
    "L-ascorbic acid",
    "Acetic acid",
    "Ammonia",
    "Methane",
]
_SANITY_DROP = [
    "2-(Acetyloxy)-3-carboxy-N,N,N-trimethylpropan-1-aminium",
    "1,3,7-trimethyl-3,7-dihydro-1H-purine-2,6-dione",
    "(2S,3R)-2-amino-3-hydroxybutanoic acid",
    "5,6-Dihydroxycyclohexa-1,3-diene-1-carboxylic acid",
]


def _spread(items: list[str], k: int) -> list[str]:
    if len(items) <= k:
        return items
    step = len(items) / k
    return [items[int(i * step)] for i in range(k)]


def main() -> None:
    path = sys.argv[1]
    kept, dropped = [], []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            name = json.loads(line).get("name", "")
            (kept if is_common_name(name) else dropped).append(name)

    total = len(kept) + len(dropped)
    print(
        f"total={total}  kept={len(kept)} ({100 * len(kept) / total:.1f}%)  "
        f"dropped={len(dropped)} ({100 * len(dropped) / total:.1f}%)\n"
    )

    print("--- KEPT (spread sample) ---")
    for n in _spread(kept, 30):
        print(f"  {n}")
    print("\n--- DROPPED (spread sample) ---")
    for n in _spread(dropped, 30):
        print(f"  {n}")

    print("\n--- sanity: should KEEP ---")
    for n in _SANITY_KEEP:
        print(f"  {'OK ' if is_common_name(n) else 'MISS'}  {n}")
    print("--- sanity: should DROP ---")
    for n in _SANITY_DROP:
        print(f"  {'OK ' if not is_common_name(n) else 'MISS'}  {n}")


if __name__ == "__main__":
    main()
