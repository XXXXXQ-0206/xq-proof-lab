# Project Status

## 2026-07-23 Closeout

The repository is positioned as XQ Proof Lab: a local rules,
bounded-proof, certificate-verification, SQLite, and UCI analysis toolkit.
The project does not claim an Elo result or superiority over Pikafish.

This closeout completed the following static and lightweight work:

- Removed terminal-score confusion between draws and wins in local negamax.
- Derived ChessDB repetition queries from actual position repetition state.
- Bound proof-store reuse to the current UCI `max_ply` request and made local
  search cancellation available to `stop`.
- Unified ordinary, infinite, and ponder UCI search lifecycle handling and
  protected bestmove emission from duplication.
- Repaired time-forfeit engine-error reporting; added report provenance,
  proof-artifact hashes, game-list digests, and strict batch-resume summary
  validation.
- Rewrote product, evidence, maintenance, and file-retention documentation.

## Sealed Research Work

The following tasks are deliberately not run as part of maintenance: match or
Elo batches, Pikafish benchmarking, proof mining, long DFPN/PNS, proof workers,
frontier expansion, bulk corpus construction, downloading, and training.

Existing natural proof-qualification data is a negative coverage result, not a
strength result. Historical reports whose timing includes setup or proof-store
work outside `go` are not fair performance evidence.

## First Research Dependency

Before any new qualification or match activity, implement and test a timing
schema that separately captures process startup, position setup, ready
confirmation, `go` elapsed time, and verifier/certificate work. Ensure setup
cannot hide proof-store verification or proof search. Only then build fresh,
frozen natural development data and reassess whether larger research work is
justified.

## Preservation

No local experiment output, SQLite store, NNUE file, profile, or temporary
file was removed for this closeout. See `docs/FILE_MANIFEST.md` for retention
and ignore policy.
