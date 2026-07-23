# XQ Proof Lab

English | [简体中文](README.zh-CN.md)

XQ Proof Lab is a local, dependency-free Python toolkit for replaying Xiangqi
positions, running bounded AND/OR proof searches, verifying proof
certificates, storing verified artifacts in SQLite, and exposing local analysis
through UCI.

It is a rules-and-analysis project, not a claim of playing strength. It does
not bundle Pikafish, NNUE weights, online data, or training infrastructure.

## Project Overview

The project keeps proof claims separate from ordinary analysis. A result marked
`proof` or `proof_store` must have a locally replayable artifact accepted by
the independent verifier. A `self_fallback` move is local analysis only;
external engines, NNUE, and network services are opt-in diagnostics and never
prove a node.

## Features

- Xiangqi FEN parsing, UCI `position` history replay, legal move generation,
  check detection, and conservative repetition adjudication.
- Bounded proof-number, DFPN, and AND/OR search components.
- Independent certificate verification and compact proof certificates.
- Local SQLite proof storage with artifact hashes and history-sensitive keys.
- A UCI loop that returns one legal `bestmove` for legal positions.
- Rule probes, perft, report validators, and diagnostic UCI match tooling.

## Screenshots

XQ Proof Lab is a command-line and library project. There is no graphical UI
or screenshot set.

## Installation

The recorded lightweight validation environment is CPython 3.14 on Windows.
The source syntax targets Python 3.10 or later, but other Python versions and
platforms have not been release-validated.

```powershell
git clone https://github.com/XXXXXQ-0206/xq-proof-lab.git
cd xq-proof-lab
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install --editable .
```

The supported local workflows use only the Python standard library. No engine,
network service, or downloaded model is needed for the rules, proof,
certificate, store, or local UCI workflows.

## Usage

Run the local perft check:

```powershell
python .\tools\perft.py --depth 1
```

The standard initial position returns `44` nodes at depth 1.

Start the closed local UCI adapter:

```powershell
python .\tools\proof_uci.py --closed
```

Then send a standard UCI session, for example:

```text
uci
isready
position startpos
go depth 1
quit
```

Use `--help` on each tool before running a search. Long proof runs, match
batches, external-engine comparisons, downloads, and network queries are
optional research or diagnostic workflows, not release validation.

## Build Instructions

There is no native build step. `pyproject.toml` packages the three Python
libraries from `src/`; `pip install --editable .` is the development install.
Command-line tools remain in `tools/` so their invocation and generated-file
paths stay explicit.

## Validation

Run these lightweight checks from the repository root:

```powershell
python -m compileall -q src tools tests
python -m unittest discover -s tests -v
python .\tools\perft.py --depth 1
python .\tools\proof_uci.py --help
git diff --check
```

They validate functional behavior and tool contracts. They are not a benchmark
or a playing-strength evaluation.

## Project Structure

```text
src/xiangqi_core/        Rules, positions, move generation, and game history
src/xiangqi_solver/      Proof search, certificates, verifier, and SQLite store
src/xiangqi_evaluators/  Local and optional diagnostic move-ordering adapters
tools/                   Explicit command-line workflows
tests/                   unittest regression suite
configs/                 Versioned example and reproducibility manifests
docs/                    Rules, maintenance, evidence, and project boundaries
```

## Roadmap

Near-term maintenance focuses on rule regressions, UCI lifecycle coverage,
artifact/report consistency, and bounded-search correctness. Optional research
work is documented separately and must first repair historical A/B timing
semantics before producing new qualification data. See
[docs/ROADMAP.md](docs/ROADMAP.md) and
[docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md).

## Contributing

Please read [CONTRIBUTING.md](CONTRIBUTING.md). Contributions go through pull
requests; never commit external binaries, NNUE weights, local SQLite stores,
artifacts, credentials, or user-specific settings.

## License

XQ Proof Lab is released under the [MIT License](LICENSE). Third-party tools
and assets are not bundled and retain their own terms.

## FAQ

**Does this repository include Pikafish or an NNUE file?**

No. They are optional local diagnostic assets and are ignored by Git.

**Does a legal `bestmove` mean a position is proven?**

No. Only a locally verified proof artifact supports a proof claim.

**Does this project claim to outperform another engine?**

No. It makes no playing-strength or Elo claim.

## Acknowledgements

Pikafish compatibility notes and optional diagnostic scripts refer to the
upstream [official-pikafish/Pikafish](https://github.com/official-pikafish/Pikafish)
project. XQ Proof Lab does not redistribute Pikafish, NNUE files, ChessDB
responses, or other external assets. See
[docs/THIRD_PARTY_AND_EVIDENCE.md](docs/THIRD_PARTY_AND_EVIDENCE.md).

## Disclaimer

THIS SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED. THE AUTHORS AND CONTRIBUTORS ARE NOT LIABLE FOR ANY DIRECT, INDIRECT,
INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES ARISING FROM ITS USE.
You use the software and any generated analysis at your own risk.

Do not use this project for unlawful, infringing, deceptive, cheating,
unauthorized-access, or rule-evasion activity. You are responsible for
obtaining permission and complying with all applicable laws, platform rules,
and third-party licenses.
