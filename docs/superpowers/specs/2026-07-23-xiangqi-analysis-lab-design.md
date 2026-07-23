# Xiangqi Analysis Lab Design

## Decision

The project will ship as **XQ Proof Lab**: a local Python toolkit
for Xiangqi rule replay, bounded proof analysis, certificate verification,
UCI integration, and reproducible diagnostic reports. It will not present an
engine-strength claim or a goal of exceeding Pikafish.

## Considered Directions

1. Keep the engine-versus-Pikafish framing. This retains the current research
   narrative, but makes ordinary diagnostic tools look like incomplete Elo
   infrastructure and overstates what the repository can currently support.
2. Archive all external-engine tooling. This reduces surface area, but throws
   away useful compatibility, perft, and move-legality diagnostics.
3. Keep compatibility tools as opt-in diagnostics and ship the rules/proof
   tooling as the product. This is the selected direction because it preserves
   useful work while making the evidence boundary honest and easy to operate.

## Product Boundary

The supported workflows are:

- replay a FEN or UCI position history through the local rules core;
- run a bounded local proof or DFPN analysis and independently verify its
  certificate;
- use the UCI shell as a deterministic local analysis adapter;
- compare legal-move/perft behavior with an explicitly external diagnostic
  engine; and
- generate, compact, inspect, and resume local proof-store artifacts.

Match runners and Pikafish scripts remain diagnostic utilities. Their reports
must identify themselves as diagnostics and cannot be described as strength,
ranking, or release acceptance evidence.

## Reliability Design

- The local fallback treats draws as neutral and scores terminal wins from the
  perspective of the side being evaluated.
- A UCI proof-store hit is limited by the current `max_ply`; stored proofs from
  a larger bound cannot bypass a shallower `go depth` request.
- Every `go` request runs through the same cancellable lifecycle. `stop`,
  `ponderhit`, and `quit` remain responsive even while the local orderer is
  working.
- Match reports store file provenance when command tokens resolve to local
  artifacts. Resumed batches validate a canonical digest of game payloads and
  recompute summaries before aggregation.

## Non-goals

- No claim of superior Elo, no formal closed-Elo acceptance workflow, and no
  bundled Pikafish or NNUE asset.
- No network access, tournament execution, mining, training, or long proof
  search is required for release validation.
- Existing local artifacts, SQLite stores, and NNUE files remain user data;
  they are ignored rather than deleted.

## Acceptance Criteria

- Rule, proof, UCI, match-report, resume, and CLI-focused tests pass.
- The UCI process returns one legal bestmove after `stop` and after
  `ponderhit` in the provided regression cases.
- The restored project documentation names XQ Proof Lab, explains
  the supported local workflows, and labels external-engine tools diagnostic.
- Every project source, test, script, example configuration, and document is
  either staged for version control or listed as a reproducible/local artifact
  with an ignore rule and retention note.
