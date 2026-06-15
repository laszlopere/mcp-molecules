# mcp-molecules

[![CI](https://github.com/laszlopere/mcp-molecules/actions/workflows/ci.yml/badge.svg)](https://github.com/laszlopere/mcp-molecules/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/mcp-molecules.svg)](https://pypi.org/project/mcp-molecules/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: GPLv3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Sponsor](https://img.shields.io/badge/Sponsor-%E2%9D%A4-db61a2.svg)](https://github.com/sponsors/laszlopere)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://img.shields.io/badge/mypy-checked-2a6db2.svg)](https://mypy-lang.org/)
[![Last commit](https://img.shields.io/github/last-commit/laszlopere/mcp-molecules.svg)](https://github.com/laszlopere/mcp-molecules/commits)

**Molecular weights for the artificial minds — molar mass from a chemical
formula, computed for real and backed by NIST data.**

A language model asked "what does C₆H₁₂O₆ weigh?" should not have to recall a
number; it should *compute* one it can trust. mcp-molecules parses a chemical
formula, looks every element up in the NIST Atomic Weights and Isotopic
Compositions database, and returns the molar mass — offline, deterministic, and
with the option to carry the measurement uncertainty along with the answer.

> **Status: scaffold.** The interface is defined and the NIST data is bundled,
> but the calculation itself is not implemented yet. `molecular_weight_calculator`
> currently raises "not implemented".

## What it gives you

- **`molecular_weight_calculator`** — compute the molecular weight (molar mass)
  of a chemical formula. Parameters:
  - `formula` — element symbols, integer multipliers, arbitrarily nested
    parentheses, and the isotope labels `D` (deuterium) and `T` (tritium).
    Examples: `H2O`, `C6H12O6`, `Ca(OH)2`, `Fe2(SO4)3`, `((CH3)2CH)2`, `D2O`, `Tc`.
  - `unit` — `g/mol` *(default)*, `kg/mol`, `Da`, `u`, or `kDa`.
  - `uncertainty` — propagate the per-element NIST standard uncertainties in
    quadrature and report `value ± sigma`.
  - `monoisotopic` — use the most abundant isotope of each element
    (mass-spectrometry monoisotopic mass) instead of the standard atomic weight.
  - `composition` — return the per-element percent composition by mass.
- **`info`** — server availability / version / environment health check.

## Install

```sh
uv tool install mcp-molecules
```

## Register with Claude Code

```sh
claude mcp add molecules -- mcp-molecules
```

## Development

```sh
uv sync --all-extras
uv run mcp-molecules        # run the server over stdio
uv run pytest               # tests
uv run ruff check .         # lint
uv run mypy                 # type-check
```

## Data

Element masses come from the NIST Atomic Weights and Isotopic Compositions
database (<https://physics.nist.gov/cgi-bin/Compositions/stand_alone.pl>), which
is in the public domain. The data is bundled in the package as
`mcp_molecules/data/nist_atomic_weights.json`.

## Sponsoring

If this is useful to you, please consider
[sponsoring](https://github.com/sponsors/laszlopere).

## License

GPL-3.0-or-later. See [LICENSE](LICENSE). The bundled NIST data is public domain.
