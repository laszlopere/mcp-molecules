# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 László Pere

"""Tier-3 online fallback: per-record lookups against the live databases.

Single-record lookups over the live database APIs, used only when the bundled
subset and the writable cache both miss. Two sources are wired so far -- PubChem
(NCBI/NLM, US public domain; TODO 2.1) and Wikidata (CC0 1.0, public domain;
TODO 2.2) -- both freely cacheable and redistributable. They are exposed as a
:class:`Fetcher` registry (:data:`FETCHERS`) that the query layer
(``RemoteSource`` in :mod:`mcp_molecules.names`) walks in order, returning the
first source with a hit.

Network is on by default but can be disabled (:func:`online_enabled`), and every
call fails soft: on a timeout, HTTP error, or malformed response the functions
return ``[]`` so an offline or flaky network degrades to "not found" rather than
raising. A descriptive ``User-Agent`` is sent per the Wikimedia user-agent policy.

Returned records use the fetcher shape ``{"ref", "name", "aliases", "formulas"}``
so :func:`mcp_molecules.cache.store` can cache them directly. Formulae are
normalized through :func:`mcp_molecules.naming.hill_formula` (Wikidata's come from
property P274; PubChem's from ``MolecularFormula``).
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import NamedTuple

from . import __version__
from .naming import FormulaError, hill_formula, normalize_name

_API = "https://www.wikidata.org/w/api.php"
_SPARQL = "https://query.wikidata.org/sparql"
_PUBCHEM = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
_UA = (
    f"mcp-molecules/{__version__} "
    "(https://github.com/laszlopere/mcp-molecules; laszlopere@gmail.com)"
)
_TIMEOUT = 15
_P274 = "P274"  # chemical formula

WIKIDATA_SOURCE = "wikidata"
WIKIDATA_LICENSE = "CC0-1.0"
PUBCHEM_SOURCE = "pubchem"
PUBCHEM_LICENSE = "public-domain"

# Online is the default; set $MCP_MOLECULES_ONLINE to a falsy value to opt out.
_FALSY = {"0", "false", "no", "off"}


def online_enabled() -> bool:
    """True unless the online fallback is opted out via ``$MCP_MOLECULES_ONLINE``.

    The Tier-3 online fallback is on by default; set the variable to a falsy
    value (``0`` / ``false`` / ``no`` / ``off``) to disable all network access and
    keep lookups purely local (bundled subset + cache).
    """
    return os.environ.get("MCP_MOLECULES_ONLINE", "").strip().casefold() not in _FALSY


def _get_json(url: str) -> dict | None:
    """GET ``url`` and parse JSON; return ``None`` on any failure (fail soft)."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            # strict=False: some Wikidata literals carry stray control characters.
            return json.loads(resp.read().decode("utf-8"), strict=False)
    except Exception:  # noqa: BLE001 -- any network/parse error degrades to "not found"
        return None


def _hill_or_raw(raw: str) -> str:
    """Hill-canonicalize ``raw``, falling back to its stripped form (or '')."""
    if not isinstance(raw, str) or not raw:
        return ""
    try:
        return hill_formula(raw)
    except FormulaError:
        return raw.strip()


def _formulae_from_claims(entity: dict) -> list[str]:
    """Extract Hill-canonical P274 formula strings from an entity's claims."""
    out: list[str] = []
    for claim in entity.get("claims", {}).get(_P274, []):
        snak = claim.get("mainsnak", {})
        if snak.get("snaktype") != "value":
            continue
        value = _hill_or_raw(snak.get("datavalue", {}).get("value"))
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

    search_url = (
        _API
        + "?"
        + urllib.parse.urlencode(
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
    )
    search = _get_json(search_url)
    if not search:
        return []
    qids = [hit["id"] for hit in search.get("search", []) if hit.get("id")]
    if not qids:
        return []

    entities_url = (
        _API
        + "?"
        + urllib.parse.urlencode(
            {
                "action": "wbgetentities",
                "ids": "|".join(qids),
                "props": "claims|labels|aliases",
                "languages": "en",
                "format": "json",
            }
        )
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
        records.append({"ref": qid, "name": label, "aliases": aliases, "formulas": formulas})
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


# --- PubChem (TODO 2.1) ----------------------------------------------------


def _pubchem_properties(path: str, limit: int) -> list[dict]:
    """Fetch ``MolecularFormula,Title`` for a PUG-REST ``path`` -> fetcher records.

    ``path`` is the portion after ``/compound/`` (e.g. ``name/aspirin`` or
    ``cid/2244,5793``). The PUG-REST property table is turned into the shared
    ``{"ref", "name", "aliases", "formulas"}`` shape, capped at ``limit`` rows.
    """
    url = f"{_PUBCHEM}/compound/{path}/property/MolecularFormula,Title/JSON"
    data = _get_json(url)
    if not data:
        return []
    props = data.get("PropertyTable", {}).get("Properties", [])
    records: list[dict] = []
    for prop in props[:limit]:
        cid = prop.get("CID")
        title = (prop.get("Title") or "").strip()
        formula = _hill_or_raw(prop.get("MolecularFormula"))
        if cid is None or not title or not formula:
            continue
        records.append({"ref": f"CID:{cid}", "name": title, "aliases": [], "formulas": [formula]})
    return records


def pubchem_by_name(name: str, limit: int = 5) -> list[dict]:
    """Resolve a compound ``name`` to PubChem records via PUG-REST (TODO 2.1).

    Looks the name up at ``/compound/name/{name}/property/MolecularFormula,Title``,
    which resolves trivial names, systematic names, and CAS numbers alike. The
    queried name is carried as an alias when it differs from PubChem's preferred
    ``Title``, so a later cache lookup by the original query still hits. Returns
    ``[]`` when disabled, offline, or unmatched (PubChem 404s an unknown name,
    which :func:`_get_json` degrades to ``None``).
    """
    if not online_enabled():
        return []
    query = name.strip()
    if not query:
        return []
    records = _pubchem_properties(f"name/{urllib.parse.quote(query, safe='')}", limit)
    key = normalize_name(query)
    for rec in records:
        if normalize_name(rec["name"]) != key:
            rec["aliases"] = [query]
    return records


def pubchem_by_formula(formula: str, limit: int = 5) -> list[dict]:
    """Resolve a molecular ``formula`` to named PubChem records (TODO 2.1).

    Uses ``/compound/fastformula/{hill}/cids`` to find matching CIDs, then a
    single property fetch for their ``Title`` + ``MolecularFormula``. Returns
    ``[]`` when disabled, offline, on an unparseable formula, or with no match.
    """
    if not online_enabled():
        return []
    try:
        hill = hill_formula(formula)
    except FormulaError:
        return []
    if not hill:
        return []
    cids_url = f"{_PUBCHEM}/compound/fastformula/{urllib.parse.quote(hill, safe='')}/cids/JSON"
    data = _get_json(cids_url)
    if not data:
        return []
    cids = data.get("IdentifierList", {}).get("CID", [])[:limit]
    if not cids:
        return []
    joined = ",".join(str(c) for c in cids)
    return _pubchem_properties(f"cid/{joined}", limit)


class Fetcher(NamedTuple):
    """One Tier-3 online source: its provenance plus bidirectional fetchers.

    ``by_name`` / ``by_formula`` take ``(query, limit)`` and emit the shared
    fetcher record shape. ``source`` / ``license`` tag whatever they return so the
    per-source cache (TODO 2.0) records the right provenance.
    """

    source: str
    license: str
    by_name: Callable[..., list[dict]]
    by_formula: Callable[..., list[dict]]


# Query order: PubChem first (largest public-domain set, resolves trivial +
# systematic names + CAS), then Wikidata. The query layer returns the first hit.
FETCHERS: list[Fetcher] = [
    Fetcher(PUBCHEM_SOURCE, PUBCHEM_LICENSE, pubchem_by_name, pubchem_by_formula),
    Fetcher(WIKIDATA_SOURCE, WIKIDATA_LICENSE, wikidata_by_name, wikidata_by_formula),
]
