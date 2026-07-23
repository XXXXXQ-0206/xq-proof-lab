# Proof-Assisted Xiangqi UCI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Current authority:** `docs/GOALS.md` supersedes any assisted-candidate wording below. Pikafish fallback/orderer matches are diagnostic only; formal Elo evidence must use the closed launcher and zero live Pikafish input. This is a historical implementation plan: its unchecked create steps are not evidence that a file exists or should be created during static closeout.

**Goal:** Build a reproducible proof-assisted Xiangqi UCI engine whose only completion criterion is a positive 95% Elo lower bound against a fixed full-strength Pikafish baseline.

**Architecture:** Keep local legal-move generation and independently verifiable proof artifacts as the proof boundary. The formal candidate uses local proof/store results plus a self-contained fallback search; Pikafish is restricted to the opposing baseline and offline compatibility or diagnostic work. Treat UCI launch settings, corpora, match controls, telemetry, and acceptance reports as versioned evidence rather than informal local commands.

**Tech Stack:** Python standard library, local Xiangqi rules/proof modules, SQLite proof store, UCI subprocess protocol, Pikafish/NNUE, JSON reports, unittest.

---

## Long-Term Objective

The active objective is: with a frozen, auditable full-strength Pikafish baseline and enough valid games, the candidate's score produces a 95% Elo confidence lower bound greater than zero. A unit-test pass, a legal UCI `bestmove`, a proof hit, a short match, an unbounded match, a mismatch-free perft sample, or a point estimate above zero is not completion.

The 60-move counter remains preserved in FEN/state compatibility but is not a natural-draw terminal result. Repetition and chase/check adjudication are evidence-sensitive: unadjudicated states invalidate Elo evidence rather than being silently scored.

## Revised Execution Order

The original acceptance scaffolding is now present in the working tree. The
next implementation cycle is deliberately ordered by measured strength risk:

1. Keep schema-9 closed-source gating and the closed launcher green.
2. Implement a self-contained alpha-beta/negamax fallback with static evaluation.
3. Add real-history complex long-chase differential tests and preserve unknown states.
4. Re-run balanced natural pilots and reduce zero-proof / `max_plies` rates.
5. Freeze independent holdout suites only after clean closed pilots.

Do not widen the match sample while the dominant proof candidate still ends as
`unknown/time_limit`; otherwise the resulting fallback games cannot tell us
whether proof search or match infrastructure is the limiting factor.

## Evidence Rules

- A Pikafish compatibility corpus contains only `startpos` and legal replay histories from it, or positions explicitly accepted by the frozen binary. Local proof fixtures with historically unreachable material placement remain in local rule/proof corpora.
- A fair game gives candidate fallback/orderer and baseline the same fixed Pikafish binary, NNUE, `Threads`, `Hash`, and per-move UCI time control. Candidate proof work must use a bounded portion of the same move budget.
- A scored game must have no UCI protocol error, illegal move, time forfeit, emergency fallback, missing or inconsistent proof telemetry, or unknown rule state.
- A report records source and NNUE hashes, commands, UCI options, suite digest, engine-clock settings, UCI timeout, and candidate proof parameters. Resume may only aggregate reports with the same compatibility-relevant configuration.

### Task 1: Freeze the External Reference Boundary

**Files:**
- Create: `scripts/run_pikafish_baseline.cmd`
- Create: `configs/pikafish_perft_corpus.example.json`
- Create: `configs/pikafish_match_suite.example.json`
- Modify: `docs/PIKAFISH_COMPATIBILITY.md`
- Test: `tests/test_compare_perft.py`

- [ ] **Step 1: Add a failing launcher/corpus contract test.**

Assert that the launcher points at `external/pikafish-baseline/src/pikafish.exe` and the compatibility corpus has only named `position startpos` cases or the canonical start FEN.

- [ ] **Step 2: Run the focused test.**

Run: `python -m unittest discover -s tests -p test_compare_perft.py -v`
Expected: FAIL because the baseline launcher/corpus contract does not exist.

- [ ] **Step 3: Create the launcher and compatible corpora.**

The launcher changes directory to its bundled baseline `src` directory before executing `pikafish.exe`; the corpus uses startpos plus deterministic opening histories. The match suite uses the same historical starts only.

- [ ] **Step 4: Re-run the focused test and compatibility preflight.**

Run: `python -m unittest discover -s tests -p test_compare_perft.py -v`
Expected: PASS.

Run: `python tools/compare_perft.py --engine "cmd.exe /d /s /c scripts\\run_pikafish_baseline.cmd" --config configs/pikafish_perft_corpus.example.json --report artifacts/pikafish_perft_preflight.json --require-root-divide`
Expected: every corpus case matches total nodes and root divide.

### Task 2: Bound Proof Work by UCI Time

**Files:**
- Modify: `src/xiangqi_solver/uci_loop.py`
- Modify: `tools/proof_uci.py`
- Test: `tests/test_proof_uci.py`
- Test: `tests/test_proof_uci_setoption.py`

- [ ] **Step 1: Add failing tests for explicit `go movetime`.**

Test that a UCI `go movetime 1000` emits a legal `bestmove`, reports a finite `time_limit_ms`, and returns before its outer timeout even when `MaxPly=2` and `NodeLimit=100000`.

- [ ] **Step 2: Run the focused UCI tests.**

Run: `python -m unittest discover -s tests -p "test_proof_uci*.py" -v`
Expected: the new time-bounded behavior fails before implementation, not from a test setup error.

- [ ] **Step 3: Implement minimal deadline propagation.**

Parse explicit move time and side clocks, reserve process/protocol margin, pass remaining time to the fallback orderer, stop proof expansion at the same deadline, and retain a legal fallback.

- [ ] **Step 4: Re-run focused UCI tests.**

Run: `python -m unittest discover -s tests -p "test_proof_uci*.py" -v`
Expected: PASS with telemetry identifying proof, store, fallback, or emergency source.

### Task 3: Make Fair Match Configuration First-Class

**Files:**
- Modify: `tools/play_uci_match.py`
- Modify: `tools/run_match_batch.py`
- Reference: `configs/pikafish_baseline.json`
- Test: `tests/test_play_uci_match.py`
- Test: `tests/test_run_match_batch.py`

- [ ] **Step 1: Add failing match tests.**

Test a two-game suite where both engines receive the same `go movetime` command and candidate proof telemetry shows a bounded time limit. Test rejection when the fallback and baseline commands or configured baseline options differ in a claimed fair run.

- [ ] **Step 2: Run focused match tests.**

Run: `python -m unittest discover -s tests -p test_play_uci_match.py -v`, then `python -m unittest discover -s tests -p test_run_match_batch.py -v`
Expected: FAIL only for the new fairness requirements.

- [ ] **Step 3: Implement fairness validation and report snapshots.**

Reject asymmetric fallback/baseline configurations when fairness mode is selected; save actual `go` commands, time budgets, command hashes, options, per-move elapsed time, and proof timeout counts in each batch report.

- [ ] **Step 4: Re-run focused match tests.**

Run: `python -m unittest discover -s tests -p test_play_uci_match.py -v`, then `python -m unittest discover -s tests -p test_run_match_batch.py -v`
Expected: PASS.

### Task 4: Execute a Valid Pilot Batch

**Files:**
- Create: `artifacts/pikafish_match_batch_fair_movetime_smoke.json`
- Modify: `docs/ROADMAP.md`

- [ ] **Step 1: Run rule/search preflight on the external-compatible corpus.**

Run: `python tools/uci_search_probe.py --engine "cmd.exe /d /s /c scripts\\run_pikafish_baseline.cmd" --config configs/pikafish_perft_corpus.example.json --report artifacts/pikafish_search_preflight.json`
Expected: every returned `bestmove` and PV root move is locally legal.

- [ ] **Step 2: Run two alternating-color time-controlled games.**

Run `tools/run_match_batch.py` with the frozen launcher for both baseline and candidate fallback, identical options, `go movetime`, a UCI timeout larger than the move budget, and the external-compatible match suite.
Expected: no engine timeout, illegal move, time forfeit, or unscored rule state.

- [ ] **Step 3: Classify the pilot report.**

Record W/D/L, valid-game count, proof/store/fallback usage, time-limit hits, and 95% score/Elo intervals. Mark the report `pilot_only`; never use two games as a strength conclusion.

### Task 5: Improve Candidate Strength From Valid Evidence

**Files:**
- Modify: `src/xiangqi_solver/search.py`
- Modify: `src/xiangqi_solver/dfpn.py`
- Modify: `src/xiangqi_solver/store.py`
- Modify: `src/xiangqi_evaluators/move_ordering.py`
- Test: `tests/test_proof_search.py`
- Test: `tests/test_proof_store.py`
- Test: `tests/test_move_ordering.py`

- [ ] **Step 1: Select one bottleneck from a valid report.**

Choose exactly one of: zero useful proof coverage, proof timeouts, invalid proof reuse, fallback weakness, rule mismatch, or unacceptable unscored-game rate. Do not optimize from invalid or asymmetric results.

- [ ] **Step 2: Write a failing focused regression test.**

Express the selected bottleneck as a replayable tactical position or stored artifact behavior with an explicit legal move, proof status, budget, and expected telemetry.

- [ ] **Step 3: Implement the smallest proof-safe improvement.**

Keep external evaluation outside proof verification; verify stored artifacts before reuse; preserve history-sensitive keys; and retain emergency legal fallback only as invalid-match telemetry.

- [ ] **Step 4: Run proof, rule, and UCI regressions.**

Run: `python -m unittest discover -s tests -v`
Expected: PASS.

### Task 6: Scale Only After Clean Pilots

**Files:**
- Reference: `configs/pikafish_match_suite.example.json`
- Create: `artifacts/pikafish_acceptance_*.json`
- Modify: `docs/ROADMAP.md`

- [ ] **Step 1: Expand opening starts and colors symmetrically.**

Use paired colors for every opening history and freeze a suite digest before starting a batch series.

- [ ] **Step 2: Run resumable valid batches.**

Use `tools/run_match_batch.py --resume` only with matching commands, options, time controls, corpus digests, and schema versions.

- [ ] **Step 3: Apply the final acceptance gate.**

Require sufficient valid games and a candidate-vs-baseline 95% Elo lower bound greater than zero. Continue iterating Task 5 until this condition holds; this is the only terminal condition.
