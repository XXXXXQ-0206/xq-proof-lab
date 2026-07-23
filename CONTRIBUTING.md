# Contributing to XQ Proof Lab

Thank you for helping improve XQ Proof Lab. The project values small,
reviewable changes with reproducible evidence.

## Development Workflow

1. Fork the repository or create a feature branch from `dev`.
2. Keep a change focused and explain its rule, proof, UCI, or documentation
   impact.
3. Run the lightweight checks below.
4. Open a pull request into `dev`. The `main` branch is reserved for reviewed
   release changes.

## Local Checks

Use CPython 3.14 on Windows for the currently verified environment:

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m compileall -q src tools tests
python -m unittest discover -s tests -v
python .\tools\perft.py --depth 1
python .\tools\proof_uci.py --help
git diff --check
```

Do not describe these checks as a performance benchmark or a strength result.
Long proof workers, bulk corpus creation, match batches, external-engine
benchmarks, downloads, and training require a separate research plan.

## Evidence Boundary

Local proof artifacts must be replayable by `ProofVerifier` before they are
described as proof. External UCI output, NNUE values, ChessDB responses, and
network data are diagnostic inputs only. Reports must distinguish `proof`,
`proof_store`, `self_fallback`, `external_fallback`, emergency, unknown, and
unfinished outcomes.

## Files and Secrets

Do not commit credentials, `.env` files, personal paths, editor state, local
SQLite databases, profiles, logs, match artifacts, Pikafish binaries, NNUE
files, or reference-engine checkouts. Use redacted example configuration and
the existing ignore rules. Do not add a third-party asset without recording
its source and license boundary.

## Pull Requests

The pull request description should state:

- what changed and why;
- the exact lightweight commands run;
- any known platform or timing limitation;
- whether the change affects report schemas or reproducibility.

Please keep generated output out of the diff and avoid unrelated formatting
churn.
