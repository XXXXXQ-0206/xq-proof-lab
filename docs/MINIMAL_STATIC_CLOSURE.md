# Minimal Static Closure

## Status

This document records the 2026-07-19 static closeout state. The current project
is a phase-1 proof-search, rules-verifier, UCI, and qualification-evaluation
framework. It is not a completed Xiangqi engine, a training or distributed
system, or evidence that it beats Pikafish.

## 2026-07-23 Maintenance Update

The repository is now documented as XQ Proof Lab: a local rules,
bounded-proof, certificate-verification, SQLite, and UCI analysis toolkit.
It does not ship an engine-strength claim. `docs/MAINTENANCE.md`,
`docs/FILE_MANIFEST.md`, and `docs/PROJECT_STATUS.md` define the maintained
compatibility, local-data, and research-resumption boundary.

Report schema 11 adds command-file provenance, proof-artifact hash telemetry,
and a canonical game-list digest. Batch resume now rejects stale schemas,
changed game payloads, mismatched summaries, and mismatched validity flags.
This is diagnostic-report integrity work, not playing-strength evidence.

The final research claim remains sealed: a completely closed candidate must
obtain a strictly positive 95% Elo lower bound against the fixed full-strength
Pikafish baseline on each of two independent, frozen, color-balanced sets of at
least 400 valid games. Nothing in this closeout satisfies that condition.

## Completed Static Work

- The rules core does not use the former 60-move counter as a terminal result.
- A black-to-move cycle regression now protects moving-target perpetual-chase
  continuity across the cycle boundary.
- External move ordering cannot label a resulting move as local `proof`; it is
  reported as assisted external fallback with `unknown` proof status.
- Acceptance rejects a candidate command containing Pikafish, fallback settings,
  or direct external-UCI ordering before it starts any engine.
- A/B proof telemetry must match the configured `max_ply`; compact certificate
  support is exported from `xiangqi_solver`.

## Qualification Boundary

The frozen 64-position natural development corpus is a valid negative coverage
result: it recorded zero proof/store hits and zero proof-driven decision changes.
The 2/3/5-position `proven` configurations were selected from already proved
mate candidates and are only `proof_pipeline_smoke`; they are not natural
coverage, holdout, Elo, or proof-strength evidence.

Old A/B reports are not fair timing evidence. They measure `ucinewgame`,
`position`, possible proof-store work, and `go` together. A future rerun must
record `process_startup_ms`, `position_setup_ms`, `readyok_ms`, `go_elapsed_ms`,
and verifier/compact work separately. No proof search or proof-store validation
may become free work during `position` or `readyok`.

The current A/B runner writes newly verified online proof artifacts only to an
ephemeral copy of the frozen proof store. Report schema 2 records the frozen
input SHA separately from the runtime-copy initial/final SHAs and checks that
the frozen source did not change. Schema 1 reports may have modified their
input store and must not be used as frozen-store or fair-performance evidence.

`scripts/run_proof_qualification.cmd` is intentionally disabled: its historical
64-position corpus does not pin the proof-store SHA now required by the runner.
It must not be repointed at an arbitrary local SQLite store.

## Worktree Classification

| Class | Paths | Retention rule |
| --- | --- | --- |
| Final tracked core | `src/xiangqi_core/game.py`, `src/xiangqi_core/position.py`; `src/xiangqi_evaluators/__init__.py`, `src/xiangqi_evaluators/chessdb.py`, `src/xiangqi_evaluators/move_ordering.py`, `src/xiangqi_evaluators/uci_engine.py`; `src/xiangqi_solver/__init__.py`, `src/xiangqi_solver/cycle.py`, `src/xiangqi_solver/dfpn.py`, `src/xiangqi_solver/proof.py`, `src/xiangqi_solver/search.py`, `src/xiangqi_solver/store.py`, `src/xiangqi_solver/verifier.py`; `tools/dfpn.py`, `tools/proof_batch.py`, `tools/proof_cycle.py`, `tools/proof_worker.py`, `tools/prove.py`, `tools/rule_corpus.py`, `tools/rule_probe.py`, `tools/run_frontier.py` | Keep as final phase-1 rules, proof, local-search, persistence, and diagnostic implementation. |
| Final tracked tests | `tests/context.py`, `tests/fixtures.py`, `tests/test_chessdb.py`, `tests/test_dfpn.py`, `tests/test_frontier.py`, `tests/test_game_state.py`, `tests/test_move_ordering.py`, `tests/test_movegen.py`, `tests/test_proof_batch.py`, `tests/test_proof_cycle.py`, `tests/test_proof_search.py`, `tests/test_proof_store.py`, `tests/test_proof_verifier.py`, `tests/test_proof_worker.py`, `tests/test_rule_probe.py`, `tests/test_uci_engine.py` | Keep as paired unit/regression coverage for the tracked implementation. |
| Final tracked docs/config | `.gitignore`, `README.md`, `configs/proof_batch.example.json`, `configs/proof_worker.example.json`, `configs/rule_corpus.example.json`, `docs/PIKAFISH_COMPATIBILITY.md`, `docs/ROADMAP.md`, `docs/RULES.md` | Keep as current project contract; `.gitignore` protects local NNUE, temporary profiling data, artifacts, and SQLite stores. |
| Final source | `src/xiangqi_evaluators/local_search.py`; `src/xiangqi_solver/certificate.py`, `src/xiangqi_solver/uci_loop.py`; `tools/build_proof_qualification_corpus.py`, `tools/build_proof_qualification_proven_corpus.py`, `tools/compact_proof_certificate.py`, `tools/compare_perft.py`, `tools/mine_pikafish_mates.py`, `tools/play_uci_match.py`, `tools/proof_uci.py`, `tools/run_match_batch.py`, `tools/run_proof_qualification.py`, `tools/uci_search_probe.py` | Keep as project implementation with their paired tests. |
| Final tests | `tests/test_build_proof_qualification_corpus.py`, `tests/test_build_proof_qualification_proven_corpus.py`, `tests/test_compact_proof_certificate.py`, `tests/test_compare_perft.py`, `tests/test_local_search.py`, `tests/test_mine_pikafish_mates.py`, `tests/test_persistent_proof_ordering.py`, `tests/test_play_uci_match.py`, `tests/test_proof_certificate.py`, `tests/test_proof_uci.py`, `tests/test_proof_uci_setoption.py`, `tests/test_prove.py`, `tests/test_run_match_batch.py`, `tests/test_run_proof_qualification.py`, `tests/test_uci_search_probe.py` | Keep and run only focused lightweight subsets during closeout. |
| Final scripts/docs | `scripts/bootstrap_pikafish_baseline.ps1`, `scripts/run_pikafish_baseline.cmd`, `scripts/run_proof_assisted_pikafish.cmd`, `scripts/run_proof_closed.cmd`, `scripts/run_proof_qualification.cmd`; `docs/GOALS.md`, `docs/MAINTENANCE.md`, `docs/FILE_MANIFEST.md`, `docs/PROJECT_STATUS.md`, `docs/MINIMAL_STATIC_CLOSURE.md`, `docs/THIRD_PARTY_AND_EVIDENCE.md`; `docs/superpowers/plans/2026-07-11-proof-assisted-uci.md`; `docs/superpowers/plans/2026-07-13-proof-qualification-gate.md` | Keep as documented entrypoints or historical plans; the qualification launcher remains disabled. |
| Final example configs | `configs/match_suite.example.json`, `configs/perft_corpus.example.json`, `configs/pikafish_baseline.json`, `configs/pikafish_match_suite.example.json`, `configs/pikafish_perft_corpus.example.json`, `configs/pikafish_proof_store_batch.example.json`, `configs/pikafish_tactical_perft_corpus.example.json`, `configs/pikafish_tactical_proof_suite.example.json`, `configs/rule_adjudication_corpus.example.json`, `configs/rule_chase_gap.example.json` | Keep as schemas/examples; launching external tools is opt-in research work. |
| Historical reproducibility inputs | `configs/pikafish_balanced_shorttc_perft.json`, `configs/pikafish_balanced_shorttc_suite.json`, `configs/proof_qualification_development_20260713.json`, `configs/proof_qualification_development_preflight_20260713.json`, `configs/proof_qualification_development_proven_20260713.json`, `configs/proof_qualification_development_proven_preflight_20260713.json`, `configs/proof_qualification_development_proven_compact_20260713.json`, `configs/proof_qualification_development_proven_compact_preflight_20260713.json`, `configs/proof_qualification_development_proven_expanded_20260713.json`, `configs/proof_qualification_development_proven_expanded_preflight_20260713.json`, `configs/proof_qualification_sources_20260713.json` | Keep as provenance manifests only; their referenced `artifacts/` and SQLite stores are local ignored companions. |
| Invalid local experiment manifest | `configs/proof_qualification_development_proven_expanded_hashed_20260713.json` | Preserve for audit, but do not reference it as a corpus: it records zero candidates and zero verified positions. |
| Local temporary data | `pikafish.nnue`; every file under `tmp/`; ignored `artifacts/`; ignored `database/*.sqlite*` | Do not delete user data. `.gitignore` prevents accidental inclusion; retain source/download metadata in the files above. |

## Development Contract

The verified runtime is CPython 3.14.0 on Windows on 2026-07-19. No other Python
version, operating system, processor configuration, or Pikafish binary is
claimed as validated by this closeout.

Allowed lightweight checks:

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m compileall src tools
python -m unittest discover -s tests -p test_game_state.py -v
python -m unittest discover -s tests -p test_proof_uci.py -v
python -m unittest discover -s tests -p test_play_uci_match.py -v
python .\tools\proof_uci.py --help
python .\tools\play_uci_match.py --help
git diff --check
```

## Sealed Research and Resumption

Do not run large matches, batch Elo, Pikafish benchmarks, proof mining, long
DFPN/PNS, proof workers/frontier expansion, corpus construction, network fetch,
or training while this static closeout is active.

The first dependency when research resumes is to repair the A/B time-accounting
semantics described above, add its focused regression tests, and only then
rebuild an expanded natural development corpus from frozen source reports. Do
not reopen Elo batching or freeze holdout thresholds before natural proof
coverage and independently verified proof decision differences are nonzero.
