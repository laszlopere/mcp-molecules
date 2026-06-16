# Changelog

All notable changes to **mcp-molecules** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-06-16

### Added
- `find_chemical_compound` online fallback now also queries PubChem PUG-REST
  (NCBI/NLM, US public domain), alongside the existing Wikidata source, when the
  bundled offline subset and the local cache both miss. The online fallback stays
  on by default; opt out with `MCP_MOLECULES_ONLINE=0`.

### Changed
- `parse_formula` trims surrounding whitespace and rejects explicit zero atom
  counts (e.g. `C0`) instead of silently accepting them.
- The bundled name database no longer indexes bare formulae as name aliases, so
  a formula query is resolved as a formula rather than as a coincidental name.
- Refreshed the PyPI summary to describe the calculator, isotope, and
  compound-lookup tools rather than molecular weight alone.

## [0.1.0] - 2026-06-16

First published release.

### Added
- Project skeleton modelled on the other `mcp-*` servers: src-layout,
  hatchling build, FastMCP server, CI + PyPI Trusted Publishing workflows,
  GitHub Sponsors and Glama configuration.
- `info` tool — server availability / version / environment health check.
- `molecular_weight_calculator` tool — molar-mass calculation ported from the C
  `mwc` tool (byte-for-byte parity): recursive-descent formula parser with nested
  groups and isotope labels (`D`, `T`), unit selection (g/mol, kg/mol, Da, u,
  kDa), propagated NIST uncertainties, monoisotopic masses, and percent
  composition by mass. Reports all mass flavors (nominal / average /
  monoisotopic) per call.
- `isotope_distribution` tool — natural isotopic pattern and m/z peaks for a
  formula, with charge, intensity threshold, peak limit, and grouping options.
- `find_chemical_compound` tool — resolve a compound by name or molecular
  formula (Hill system) against a bundled offline PubChem subset, with an
  on-by-default Wikidata online fallback (CC0) and a per-source, concurrency-safe
  SQLite cache (WAL + busy-timeout, fail-soft on lock contention).
- Bundled NIST Atomic Weights and Isotopic Compositions data
  (`nist_atomic_weights.json`) and the curated PubChem name subset as package
  data.
