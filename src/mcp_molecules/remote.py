# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""Tier-3 online fallback: per-record lookups against Wikidata (TODO 2.2).

A single-record lookup over the live Wikidata API/SPARQL, used only when the
bundled subset and the writable cache both miss. Wikidata data is CC0 1.0 (public
domain, no attribution obligation), so anything fetched here may be cached and
redistributed freely.

Network is strictly opt-in (:func:`online_enabled`) and every call fails soft:
on a timeout, HTTP error, or malformed response the functions return ``[]`` so an
offline or flaky network degrades to "not found" rather than raising. A
descriptive ``User-Agent`` is sent per the Wikimedia user-agent policy.

Returned records use the fetcher shape ``{"ref", "name", "aliases", "formulas"}``
so :func:`mcp_molecules.cache.store` can cache them directly. Formulae come from
property P274 (chemical formula); the caller normalizes them through
:func:`mcp_molecules.naming.hill_formula`.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from . import __version__
from .naming import FormulaError, hill_formula, normalize_name

_API = "https://www.wikidata.org/w/api.php"
_SPARQL = "https://query.wikidata.org/sparql"
_UA = (
    f"mcp-molecules/{__version__} "
    "(https://github.com/laszlopere/mcp-molecules; laszlopere@gmail.com)"
)
_TIMEOUT = 15
_P274 = "P274"  # chemical formula

SOURCE = "wikidata"
LICENSE = "CC0-1.0"

_TRUTHY = {"1", "true", "yes", "on"}


def online_enabled() -> bool:
    """True if the online fallback is opted in via ``$MCP_MOLECULES_ONLINE``."""
    return os.environ.get("MCP_MOLECULES_ONLINE", "").strip().casefold() in _TRUTHY


def _get_json(url: str) -> dict | None:
    """GET ``url`` and parse JSON; return ``None`` on any failure (fail soft)."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            # strict=False: some Wikidata literals carry stray control characters.
            return json.loads(resp.read().decode("utf-8"), strict=False)
    except Exception:  # noqa: BLE001 -- any network/parse error degrades to "not found"
        return None


def _formulae_from_claims(entity: dict) -> list[str]:
    """Extract Hill-canonical P274 formula strings from an entity's claims."""
    out: list[str] = []
    for claim in entity.get("claims", {}).get(_P274, []):
        snak = claim.get("mainsnak", {})
        if snak.get("snaktype") != "value":
            continue
        raw = snak.get("datavalue", {}).get("value")
        if not isinstance(raw, str) or not raw:
            continue
        try:
            value = hill_formula(raw)
        except FormulaError:
            value = raw.strip()
        if value and value not in out:
            out.append(value)
    return out


def wikidata_by_name(name: str, limit: int = 7) -> list[dict]:
    """Resolve a compound ``name`` to Wikidata records carrying a P274 formula.

    Searches entities via ``wbsearchentities``, fetches the candidates' claims,
    labels, and aliases via ``wbgetentities``, and keeps only those whose English
    label or an alias matches ``name`` (after normalization) and that bear a
    chemical formula. Returns ``[]`` when disabled, offline, or unmatched.
    """
    if not online_enabled():
        return []
    key = normalize_name(name)
    if not key:
        return []

    search_url = _API + "?" + urllib.parse.urlencode(
        {
            "action": "wbsearchentities",
            "search": name,
            "language": "en",
            "uselang": "en",
            "type": "item",
            "limit": limit,
            "format": "json",
        }
    )
    search = _get_json(search_url)
    if not search:
        return []
    qids = [hit["id"] for hit in search.get("search", []) if hit.get("id")]
    if not qids:
        return []

    entities_url = _API + "?" + urllib.parse.urlencode(
        {
            "action": "wbgetentities",
            "ids": "|".join(qids),
            "props": "claims|labels|aliases",
            "languages": "en",
            "format": "json",
        }
    )
    data = _get_json(entities_url)
    if not data:
        return []

    records: list[dict] = []
    # Preserve wbsearchentities' relevance order rather than the dict order.
    for qid in qids:
        entity = data.get("entities", {}).get(qid)
        if not entity:
            continue
        label = entity.get("labels", {}).get("en", {}).get("value", "")
        aliases = [a.get("value", "") for a in entity.get("aliases", {}).get("en", [])]
        names = {normalize_name(n) for n in [label, *aliases]}
        if key not in names:
            continue
        formulas = _formulae_from_claims(entity)
        if not label or not formulas:
            continue
        records.append(
            {"ref": qid, "name": label, "aliases": aliases, "formulas": formulas}
        )
    return records


def wikidata_by_formula(formula: str, limit: int = 5) -> list[dict]:
    """Resolve a molecular ``formula`` to named Wikidata records via SPARQL.

    Queries items whose P274 equals the Hill-canonical ``formula`` and returns
    their English labels. Returns ``[]`` when disabled, offline, on an
    unparseable formula, or with no match.
    """
    if not online_enabled():
        return []
    try:
        hill = hill_formula(formula)
    except FormulaError:
        return []

    query = (
        "SELECT ?item ?itemLabel WHERE { "
        f'?item wdt:{_P274} "{hill}". '
        'SERVICE wikibase:label { bd:serviceParam wikibase:language "en". } '
        f"}} LIMIT {int(limit)}"
    )
    url = _SPARQL + "?" + urllib.parse.urlencode({"query": query, "format": "json"})
    data = _get_json(url)
    if not data:
        return []

    records: list[dict] = []
    for row in data.get("results", {}).get("bindings", []):
        qid = row.get("item", {}).get("value", "").rsplit("/", 1)[-1]
        label = row.get("itemLabel", {}).get("value", "")
        # The label service echoes the QID when no English label exists.
        if not qid or not label or label == qid:
            continue
        records.append({"ref": qid, "name": label, "aliases": [], "formulas": [hill]})
    return records
