# Xiangqi Analysis Lab Closeout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the repository into a stable, reproducible Xiangqi rules and proof-analysis toolkit with no engine-strength product claim.

**Architecture:** Keep the local rules core, proof search, certificate verifier, UCI adapter, and diagnostic match tools. Correct budget, terminal scoring, cancellation, and report-integrity behavior at their ownership boundaries, then make documentation and file classification match those boundaries.

**Tech Stack:** CPython 3.14, standard library, SQLite, JSON, UCI text protocol, unittest.

---

### Task 1: Correct terminal and ChessDB rule semantics

**Files:**
- Modify: `src/xiangqi_evaluators/local_search.py`
- Modify: `src/xiangqi_evaluators/chessdb.py`
- Modify: `tests/test_local_search.py`
- Modify: `tests/test_chessdb.py`

- [x] Add a terminal-score helper that returns `0` for `draw`, positive mate
  for the evaluated winner, and negative mate for the evaluated loser.

```python
def _terminal_score_for(result: str, perspective: Color, ply: int = 0) -> int:
    if result == "draw":
        return 0
    winner = Color.RED if result == "red_win" else Color.BLACK
    return _MATE_SCORE - ply if winner is perspective else -_MATE_SCORE + ply
```

- [x] Use that helper in `_negamax()` and `_root_static_score()`.
- [x] Derive `ChessDbRuleQuery.reptimes` from actual repetition state rather
  than history length; omit it when the current position has not repeated.
- [x] Add tests for a neutral threefold draw and a non-repeating four-ply
  history.
- [x] Run `python -m unittest discover -s tests -p test_local_search.py -v`
  and `python -m unittest discover -s tests -p test_chessdb.py -v`.

### Task 2: Enforce UCI proof budget and cancellation semantics

**Files:**
- Modify: `src/xiangqi_solver/uci_loop.py`
- Modify: `src/xiangqi_evaluators/local_search.py`
- Modify: `tests/test_proof_uci.py`

- [x] Include `max_ply` in verified-store cache keys and call the store with
  the requested maximum depth.
- [x] Add cancellable local ordering with an optional `Event` parameter and
  check it inside the local search limit guard.
- [x] Start normal, infinite, and ponder `go` searches through one background
  lifecycle. Serialize bestmove emission so a timeout/stop fallback cannot
  emit twice.
- [x] Add regressions for a shallower `go depth` store lookup, normal `go`
  followed by `stop`, infinite `stop`, and `ponderhit`.
- [x] Run `python -m unittest discover -s tests -p test_proof_uci.py -v`.

### Task 3: Make diagnostic match reports internally reproducible

**Files:**
- Modify: `tools/play_uci_match.py`
- Modify: `tools/run_match_batch.py`
- Modify: `tests/test_play_uci_match.py`
- Modify: `tests/test_run_match_batch.py`

- [x] Correct the engine-error-plus-time-forfeit report call so it receives
  `start`, `start_move_count`, `valid=True`, `result`, and `reason` in that
  order.
- [x] Add canonical SHA-256 digests for serialized game lists, validate them
  when resuming, and recompute summaries from reloaded games.
- [x] Record command-token file provenance for executable/script artifacts
  that exist locally, including SHA-256, path, and size.
- [x] Document report-facing acceptance as historical diagnostics and reject a
  claimed proof source unless the diagnostic report carries an artifact hash.
- [x] Add tests for engine-error time forfeits, changed game payloads,
  provenance records, and fake proof telemetry.
- [x] Run the two match test modules independently.

### Task 4: Reposition documentation and examples

**Files:**
- Modify: `README.md`
- Modify: `docs/GOALS.md`
- Modify: `docs/ROADMAP.md`
- Modify: `docs/THIRD_PARTY_AND_EVIDENCE.md`
- Create: `docs/MAINTENANCE.md`

- [x] Describe the product as XQ Proof Lab and enumerate supported
  local workflows.
- [x] Label all Pikafish, NNUE, match, and network examples as optional
  diagnostics. Remove language that frames the repository as an Elo contender.
- [x] State the verified Python/Windows scope, lightweight validation commands,
  artifact retention policy, and non-goals.
- [x] Resolve the `self_fallback` evidence wording: it is local candidate
  analysis, never external fallback, but it is not proof evidence.

### Task 5: Classify and stage project-owned files

**Files:**
- Modify: `.gitignore`
- Modify: `docs/MINIMAL_STATIC_CLOSURE.md`
- Create: `docs/FILE_MANIFEST.md`

- [x] Separate project source/tests/docs/example configs from generated
  artifacts, SQLite stores, profiles, temporary data, and NNUE assets.
- [x] Keep generated/local files ignored and document their provenance; do not
  delete them.
- [x] Stage project-owned source, tests, scripts, configs, and documentation
  without creating a commit.
- [x] Validate with `git diff --check`, JSON parsing, `compileall`, focused
  unittest modules, and CLI help commands.
