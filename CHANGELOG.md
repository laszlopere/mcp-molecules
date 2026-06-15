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
- `molecular_weight_calculator` tool — interface only (formula, unit,
  uncertainty, monoisotopic, composition). Calculation is not yet implemented.
- Bundled NIST Atomic Weights and Isotopic Compositions data
  (`nist_atomic_weights.json`) as package data for the forthcoming
  implementation.
