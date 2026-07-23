# Roadmap

## Maintained Baseline

The maintained baseline is a local Xiangqi rules, proof, certificate, store,
and UCI analysis toolkit. Current work prioritizes rule correctness, bounded
search behavior, reproducible artifacts, and stable tool interfaces.

Near-term maintenance work:

- Extend rule regressions for supported repetition, long-check, and
  single-target long-chase paths.
- Keep UCI cancellation, per-search depth limits, and one-bestmove emission
  covered by regression tests.
- Keep report schemas, command provenance, artifact hashes, and resume checks
  internally consistent.
- Improve local proof-search efficiency only when a bounded regression proves
  correctness and preserves the evidence boundary.

## Optional Diagnostics

External UCI perft/search comparison and local-rule-validated match tools stay
available as opt-in diagnostics. They are useful for finding rule drift and
adapter failures, but they are not release tests and do not alter proof status.
Pikafish and NNUE remain external, unbundled assets.

## Sealed Research Backlog

Do not start large matches, proof mining, frontier expansion, worker loops,
network fetches, training, or benchmark batches as part of ordinary project
maintenance.

Before future proof-qualification research resumes, first repair A/B timing
semantics: report process startup, position setup, ready confirmation, `go`
elapsed time, and verifier/certificate work separately. Position setup and
proof-store verification must not become free computation. Only after that
change has focused tests and new frozen natural data may broader search or
qualification work be reconsidered.

## Explicitly Deferred

- Full WXF/CXA adjudication for ambiguous multi-target, identity-changing, or
  capture-including perpetual-chase histories.
- Native high-performance implementation for large tactical proof trees.
- Any claim derived from playing strength, rating, or external-engine matches.
