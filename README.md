# mcp-molecules

[![CI](https://github.com/laszlopere/mcp-molecules/actions/workflows/ci.yml/badge.svg)](https://github.com/laszlopere/mcp-molecules/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/mcp-molecules.svg)](https://pypi.org/project/mcp-molecules/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: GPLv3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Sponsor](https://img.shields.io/badge/Sponsor-%E2%9D%A4-db61a2.svg)](https://github.com/sponsors/laszlopere)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://img.shields.io/badge/mypy-checked-2a6db2.svg)](https://mypy-lang.org/)
[![Last commit](https://img.shields.io/github/last-commit/laszlopere/mcp-molecules.svg)](https://github.com/laszlopere/mcp-molecules/commits)

**Atoms and molecules for the artificial minds — trustworthy chemistry tools,
computed for real and backed by authoritative data.**

Atoms combine into molecules, and a language model asked about them should not
have to recall facts from memory; it should *compute* answers it can trust.
mcp-molecules is a growing toolbox for working with chemical elements and
molecules — offline, deterministic, and backed by authoritative sources such as
the NIST Atomic Weights and Isotopic Compositions database.

For example, it can already take a chemical formula and return its molecular
weight: ask "what does C₆H₁₂O₆ weigh?" and it parses the formula, looks every
element up in NIST data, and computes the molar mass rather than guessing it.
More molecule-oriented tools are planned.

You can ask things like:

- *"What does a mole of glucose weigh?"* — resolves the name to C₆H₁₂O₆ and
  computes the molar mass.
- *"How much of Fe₂(SO₄)₃'s mass is iron?"* — per-element percent composition.
- *"What's the molar mass of caffeine, with uncertainty?"* — propagates the NIST
  standard uncertainties.
- *"What does the mass spectrum of chloroform look like?"* — the natural chlorine
  isotope pattern (the M, M+2, M+4 … peaks).
- *"What's the [M+H]⁺ m/z for caffeine?"* — the protonated-ion mass.
- *"Which compound has the formula C₉H₈O₄?"* — formula → name (aspirin, among its
  isomers).
- *"What are the isomers of C₂H₆O?"* — one formula, several names (ethanol and
  dimethyl ether).

## What it gives you

- **`molecular_weight_calculator`** — *(one example of what's here today)*
  compute the molecular weight (molar mass) of a chemical formula. Parameters:
  - `formula` — element symbols, integer multipliers, arbitrarily nested
    parentheses, and the isotope labels `D` (deuterium) and `T` (tritium).
    Examples: `H2O`, `C6H12O6`, `Ca(OH)2`, `Fe2(SO4)3`, `((CH3)2CH)2`, `D2O`, `Tc`.
  - `unit` — `g/mol` *(default)*, `kg/mol`, `Da`, `u`, or `kDa`.
  - `uncertainty` — propagate the per-element NIST standard uncertainties in
    quadrature and report `value ± sigma`.
  - `monoisotopic` — use the most abundant isotope of each element
    (mass-spectrometry monoisotopic mass) instead of the standard atomic weight.
  - `composition` — return the per-element percent composition by mass.
- **`isotope_distribution`** — compute the natural isotopic pattern (the peaks a
  mass spectrometer would see) for a formula, with each peak's mass, m/z, and
  relative intensity, plus the monoisotopic and average masses. Parameters:
  - `formula` — same syntax as `molecular_weight_calculator`.
  - `charge` — `0` *(default)* reports neutral masses; a non-zero `n` reports m/z
    for the `[M+nH]`/`[M-nH]` ion.
  - `threshold` — drop peaks below this percent of the base peak *(default 0.1)*.
  - `limit` — maximum peaks to return, most intense first *(default 10)*.
  - `grouping` — `unit` *(default)* collapses to nominal integer masses; `exact`
    keeps every resolved isotopologue.
- **`find_chemical_compound`** — look up a compound by name or molecular
  formula against a bundled offline database (a PubChem subset). Parameters:
  - `query` — a name (`aspirin`, `acetylsalicylic acid`) or a formula
    (`H2O`, `C9H8O4`); formulae are matched in the Hill system.
  - `by` — `auto` *(default)* guesses name vs. formula and falls back to the
    other direction on a miss; `name` or `formula` pin the direction.
  - `limit` — maximum compounds to return for a formula lookup (isomers share
    a formula), preferred name first.
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
uv run ruff format .        # format
uv run ruff check .         # lint
uv run mypy                 # type-check
```

A pre-commit hook in `.githooks/` auto-formats and lints staged Python files
so the CI format gate can't be missed. Enable it once per clone:

```sh
git config core.hooksPath .githooks
```

## Data

Element masses come from the NIST Atomic Weights and Isotopic Compositions
database (<https://physics.nist.gov/cgi-bin/Compositions/stand_alone.pl>), which
is in the public domain. The data is bundled in the package as
`mcp_molecules/data/nist_atomic_weights.json`.

## Sponsoring

Sponsoring this project will keep it alive. If it is useful to you, please
consider [sponsoring](https://github.com/sponsors/laszlopere).

## Credits

The idea and the inspiration came from Mátyás Mayer. The idea was excellent,
the inspiration priceless.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE). The bundled NIST data is public domain.
