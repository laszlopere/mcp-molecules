# Changelog

All notable changes to **mcp-molecules** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0.dev0] - 2026-06-15

Initial scaffold.

### Added
- Project skeleton modelled on the other `mcp-*` servers: src-layout,
  hatchling build, FastMCP server, CI + PyPI Trusted Publishing workflows,
  GitHub Sponsors and Glama configuration.
- `info` tool — server availability / version / environment health check.
- `molecular_weight_calculator` tool — molar-mass calculation ported from the C
  `mwc` tool (byte-for-byte parity): recursive-descent formula parser with nested
  groups and isotope labels (`D`, `T`), unit selection (g/mol, kg/mol, Da, u,
  kDa), propagated NIST uncertainties, monoisotopic masses, and percent
  composition by mass.
- Bundled NIST Atomic Weights and Isotopic Compositions data
  (`nist_atomic_weights.json`) as package data for the forthcoming
  implementation.
