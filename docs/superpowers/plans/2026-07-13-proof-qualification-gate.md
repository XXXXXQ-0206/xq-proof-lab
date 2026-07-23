# Proof Qualification Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish reproducible development-set evidence that verified proof moves improve on identical-parameter local-only play, then freeze a holdout gate before expanding Elo matches.

**Architecture:** A corpus builder deterministically splits valid natural-game positions into versioned development and untouched holdout sets without consulting a live opponent. A qualification runner starts two local `proof_uci` processes with the same proof/local-search budgets, accepts only independently reverified proof artifacts, and emits an auditable A/B development report; a later explicit configuration freezes holdout thresholds from that development evidence.

**Tech Stack:** Python standard library, existing Xiangqi core, `ProofStore`/`ProofVerifier`, local UCI harnesses, SQLite.

---

### Task 1: Add a true closed local-only UCI mode

**Files:**
- Modify: `tools/proof_uci.py`
- Test: `tests/test_proof_uci.py`

- [ ] Add a failing CLI test proving `--closed --local-only` returns a legal `self_fallback` move without opening or querying a proof store.
- [ ] Run the focused test and confirm it fails because the flag is unavailable.
- [ ] Add `--local-only`; reject proof-store and online-proof options in this mode, and construct the engine with proof search disabled while preserving local-search configuration.
- [ ] Re-run the focused test and the existing UCI tests.

### Task 2: Freeze deterministic development and holdout natural tactical corpora

**Files:**
- Create: `tools/build_proof_qualification_corpus.py`
- Create: `tests/test_build_proof_qualification_corpus.py`
- Create: `configs/proof_qualification_sources_20260713.json`

- [ ] Add failing tests for accepting only valid natural-game records, detecting local capture/check candidates, de-duplicating exact replay positions, and assigning equal red/black development and holdout quotas by SHA-256 position key.
- [ ] Run the focused test and confirm the missing builder fails.
- [ ] Implement the builder with source-report hashes, explicit selection rules, serialized replay position/FEN/history data, tactical candidate moves, and a content digest.
- [ ] Add the source manifest and run the builder to write separately hashed development and holdout corpora; fail rather than silently producing fewer positions.
- [ ] Re-run the builder tests and validate all frozen entries replay through `GameState`.

### Task 3: Run and verify closed proof versus local-only development A/B

**Files:**
- Create: `tools/run_proof_qualification.py`
- Create: `tests/test_run_proof_qualification.py`
- Create: `scripts/run_proof_qualification.cmd`

- [ ] Add failing tests with fake UCI engines for provenance parsing, same-parameter run configuration, proof-store re-verification, different-local-move counting, and explicit non-acceptance before a holdout threshold is frozen.
- [ ] Run the focused test and confirm the runner is missing.
- [ ] Implement the runner to launch only local `proof_uci` commands with `--closed`; require `proof`/`proof_store` provenance plus `ProofVerifier` replay before counting a proof result.
- [ ] Emit a JSON report with corpus/config/store hashes, per-position moves and sources, verified proof data, development coverage/variance/cost metrics, and explicit non-acceptance reasons.
- [ ] Add a command wrapper that records the exact common UCI budget and refuses any external fallback argument.
- [ ] Re-run focused tests and all UCI/match/proof-store regressions.

### Task 4: Historical plan for development evidence and a holdout gate

**Files:**
- Create: `configs/proof_qualification_development_20260713.json`
- Deferred: a holdout configuration must not be created until natural development
  evidence supports a pre-registered threshold.
- Create: `artifacts/proof_qualification_development_ab_20260713.json`
- Modify: `docs/GOALS.md`

- [ ] Historical only: run rule/perft/search preflights for the frozen development corpus and save their reports.
- [ ] Run the closed development A/B command with the frozen settings and inspect every claimed proof against its verifier result.
- [ ] Use the observed coverage, variance and cost to write a holdout threshold before running the holdout report; do not claim qualification from development evidence alone.
- [ ] Select the next dependency-ready bottleneck from the report, then continue without expanding Elo samples.
