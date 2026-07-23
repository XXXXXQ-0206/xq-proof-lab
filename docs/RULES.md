# Rules Specification

This document fixes the phase-0 rules understood by the local verifier.

## Coordinates

- Files are `a` through `i`.
- Ranks are `0` through `9`.
- `a0` is Red's left corner from Red's point of view.
- The standard initial FEN is:

```text
rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1
```

This matches the coordinate and FEN style used by Pikafish UCI examples.

## Side Names

The code uses `Color.RED` and `Color.BLACK`. For UCI/FEN compatibility:

- Red is encoded as `w`.
- Black is encoded as `b`.
- Red pieces use uppercase FEN letters.
- Black pieces use lowercase FEN letters.

## Phase-0 Rule Scope

Implemented now:

- Strict six-field FEN parsing for proof inputs.
- UCI-style historical inputs: `position startpos moves ...` and `position fen ... moves ...`.
- Piece count and piece-area validation.
- Legal moves for rook, knight, bishop, advisor, king, cannon, and pawn.
- Palace restrictions for king and advisor.
- River restriction for bishop.
- River crossing behavior for pawns.
- Horse-leg and elephant-eye blockers.
- Cannon screen capture rules.
- Flying-general check and legality filtering.
- Stalemate/no-legal-move as a loss for the side to move.
- Halfmove counter parsing and capture-only updates for FEN/UCI compatibility; the counter is not used as a terminal draw result.
- Repeated positions are detected from historical position hashes and currently become `unknown_rule_state`.
- `GameState` records rule events for each historical move: mover, UCI move, capture flag, whether the move gives check, which opponent pieces are attacked after the move, whether those attacked pieces are protected by their owner, which attackers could legally chase-capture them, and whether the owner has an immediate legal recapture.
- `GameState.repetition_info()` summarizes a repeated cycle: repetition count, cycle length, cycle start/end plies, cycle moves, capture flag, sides that checked on every move in the cycle, per-side repeated attack patterns and moves, common attacked target metadata, possible long-chase sides after attacker/target/recapture filtering, and strict single-target perpetual-chase sides when locally adjudicable.
- `GameState.rule_judgement()` exposes a structured local rule result: result, reason, adjudicated flag, and repetition metadata when available.
- Threefold repetition with exactly one side checking on every move in the repeated cycle is adjudicated as a loss for the checking side. If both sides check throughout the repeated cycle, the result is a draw.
- Threefold repetition with no captures or checks, where exactly one side attacks on every move and the repeated attack has exactly one common locally chase-relevant target, is adjudicated as a perpetual-chase loss for that side. If both sides satisfy the same strict single-target chase pattern, the result is treated as a draw.

Compatibility notes:

- The halfmove counter is reset by captures only, matching the Pikafish rule60 counter interpretation.
- Pawn moves do not reset the halfmove counter.
- `strict=False` parsing exists only for low-level rule tests and deliberately constructed internal positions; proof entry points use strict FEN.
- The history-aware `GameState` path is required for any proof that depends on repetition state.
- Rule events and repeated-cycle move ranges are stored as inputs for future long-check and broader long-chase adjudication; attacked-piece type/protection, legal chase attackers, legal recapture metadata, and possible long-chase sides are diagnostic inputs unless the stricter single-target perpetual-chase pattern is also satisfied.
- `possible_chase_unadjudicated` deliberately remains `unknown_rule_state` for proof search when the local pattern is broader than the strict single-target chase case, such as multiple repeated targets or incomplete chase classification.
- Proof artifacts generated from `GameState` include a history signature and a replayable UCI `position ... moves ...` command. This preserves repetition context for storage and merging, but does not by itself prove a repetition result.

Initial-position perft checks used by the local test suite and manual verification:

| Depth | Nodes |
|---:|---:|
| 1 | 44 |
| 2 | 1920 |
| 3 | 79666 |

Not final yet:

- Full WXF/CXA repetition adjudication.
- Broader perpetual chase classification for multi-target, non-continuous, and capture-bearing cycles beyond the supported single-target identity chain.
- Natural move-limit adjudication aligned with an external rules reference; the current verifier deliberately does not declare a draw from the FEN halfmove counter alone.
- Further proof-database schema evolution beyond the current verified SQLite
  store and artifact format.

Repetition rules must be modeled explicitly before any large proof database is considered authoritative.
Use `tools/rule_probe.py` to inspect the local repetition summary for a UCI historical position; pass `--chessdb` only when an external ChessDB comparison is desired. The ChessDB output includes both raw per-move rule results and a compact rule-count summary. Use `tools/rule_corpus.py --config configs/rule_corpus.example.json` to run the same local or ChessDB-backed rule probe across a JSON corpus of positions while developing long-check and long-chase cases. When ChessDB is enabled, the corpus output includes a comparison summary that highlights local-only, ChessDB-only, and jointly flagged rule cases for review. For a fully adjudicated, single-sided identity chase, `GameState.candidate_rule_judgement()` preserves that result only when the current mover restarts the recorded cycle with its first move; the probe maps that continuation to `ban` and evaluates other legal moves normally. Ambiguous identity histories remain `unknown_rule_state`.
