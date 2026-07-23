# Maintenance Contract

## Supported Environment

The current lightweight verification record is CPython 3.14 on Windows. The
repository has not been certified on other Python versions, operating systems,
or hardware. The code uses the Python standard library and local SQLite; no
network access is required for the supported workflows.

Set the source path before invoking tools directly:

```powershell
$env:PYTHONPATH = "$PWD\src"
```

## Required Lightweight Checks

```powershell
python -m compileall src tools
python -m unittest discover -s tests -p test_game_state.py -v
python -m unittest discover -s tests -p test_local_search.py -v
python -m unittest discover -s tests -p test_proof_uci.py -v
python -m unittest discover -s tests -p test_play_uci_match.py -v
python -m unittest discover -s tests -p test_run_match_batch.py -v
python .\tools\proof_uci.py --help
python .\tools\play_uci_match.py --help
python .\tools\run_match_batch.py --help
git diff --check
```

These commands deliberately exclude match batches, reference-engine benchmarks,
proof workers, frontier expansion, mining, network calls, and training.

## UCI Contract

`tools/proof_uci.py` accepts normal UCI lifecycle commands and returns one
legal `bestmove` for a legal position. Ordinary, infinite, and ponder searches
use the same background lifecycle; `stop` asks the local orderer and bounded
proof search to stop, and an active search emits at most one `bestmove`.

`go depth` is a per-request proof depth limit. Proof-store cache keys and store
lookups include this limit; a proof found at a larger depth cannot satisfy a
shallower request. `self_fallback` is local analysis and is intentionally not
reported as proof.

## Diagnostic Report Contract

`tools/play_uci_match.py` and `tools/run_match_batch.py` currently write
schema `11`. The schema records a config digest, local command-file provenance
where resolvable, per-game records, and a canonical SHA-256 digest of the game
list. Batch resume rejects stale schemas, changed game content, mismatched
summaries, mismatched validity flags, or changed resume-relevant configuration.

Diagnostic `proof` and `proof_store` telemetry requires a proof-artifact SHA-256
in the UCI info line. This allows report consumers to link the claim to a local
artifact; it does not replace independent verification with `ProofVerifier`.

## Known Timing Boundary

Historical proof-qualification A/B reports did not cleanly separate process
startup, `position` setup, proof-store work, `readyok`, `go`, and certificate
verification. They must not be used for a fair performance conclusion. Do not
reuse them as benchmarks or qualification evidence. A future research restart
must fix the timing schema before creating a new natural corpus or match set.

## Local Data

Never delete a user's `artifacts/`, `tmp/`, SQLite stores, profile files,
reference-engine checkout, or NNUE file during maintenance. Preserve the input
configuration and hashes beside any reproducibility artifact that must survive
outside the ignored directories.
