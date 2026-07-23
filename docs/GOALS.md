# Product Scope

XQ Proof Lab is a local analysis and verification toolkit. Its
release objective is reliable rule replay, bounded proof analysis, independent
certificate validation, deterministic UCI interaction, and auditable
diagnostic reports.

## In Scope

- Legal move generation, FEN/UCI history replay, repetition state, and covered
  long-check/long-chase adjudication in the local rules core.
- Bounded proof/DFPN/PNS components whose claimed artifacts are replayed by
  `ProofVerifier` before being treated as proof data.
- Local SQLite storage with artifact hashes and history-sensitive keys.
- A local UCI adapter with proof, proof-store, self-fallback, emergency, and
  external sources reported separately.
- Optional compatibility diagnostics: external UCI perft/search probes and
  local-rule-validated match reports.

## Non-Goals

- No claim that this repository is stronger than Pikafish or another engine.
- No bundled Pikafish binary, NNUE network, online database, training system,
  or distributed worker service.
- No use of external bestmoves, PVs, ordering, NNUE, or network data as proof.
- No interpretation of a local fallback, a matching external first move, a
  smoke test, or an incomplete match as playing-strength evidence.

## Evidence Policy

`proof` and `proof_store` are meaningful only when a local artifact is
verified and its SHA-256 is reported. `self_fallback` is a local analysis
result, not proof. `external_fallback`, legacy fallback, emergency moves,
unknown rule states, invalid telemetry, and unfinished games are diagnostic
outcomes.

Match and batch runners retain optional score/Elo calculations for historical
diagnostic compatibility. Those fields are not a project delivery claim and
must not be used to state engine superiority. See
`docs/THIRD_PARTY_AND_EVIDENCE.md` for source boundaries.

## Research Archive

Earlier proof-qualification and closed-Elo exploration remains preserved as
local reproducibility material. It is sealed from the product goal: existing
natural-corpus reports have zero verified proof/store coverage, and historical
A/B reports combine position/proof-store work with `go` timing. They are not
fair performance evidence. `docs/PROJECT_STATUS.md` gives the first required
step before any research activity is resumed.
