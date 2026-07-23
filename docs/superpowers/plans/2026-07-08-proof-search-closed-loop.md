# Proof Search Closed Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a complete first proof-search loop that can prove bounded Xiangqi wins, export proof artifacts, independently verify them, and persist results in a restartable database.

**Architecture:** The local rules core remains the only proof authority. The solver builds AND/OR proof trees for a target side, the verifier replays exported artifacts against `xiangqi_core`, and the SQLite store keeps search results keyed by deterministic position hashes.

**Tech Stack:** Python standard library, `unittest`, `sqlite3`, JSON proof artifacts, existing `xiangqi_core` and `xiangqi_solver`.

---

### Task 1: Proof Target And Artifact Model

**Files:**
- Create: `src/xiangqi_solver/proof.py`
- Modify: `src/xiangqi_solver/__init__.py`
- Test: `tests/test_proof_search.py`

- [x] **Step 1: Define target-side outcomes**

Create `ProofStatus`, `ProofTarget`, and `ProofArtifact`. The artifact must include the FEN, target side, remaining ply bound, node kind, status, proof/disproof numbers, chosen move, and child artifacts.

- [x] **Step 2: Map terminal positions to proof status**

For target Red, `red_win` is proven, `black_win` is disproven. For target Black, the mapping is reversed. Unknown non-terminal nodes keep `UNKNOWN`.

### Task 2: Bounded AND/OR Proof Search

**Files:**
- Create: `src/xiangqi_solver/search.py`
- Modify: `src/xiangqi_solver/__init__.py`
- Test: `tests/test_proof_search.py`

- [x] **Step 1: Implement bounded search**

Search semantics:

- Target side to move: OR node, one proven child proves the node.
- Opponent to move: AND node, every legal child must be proven.
- Ply bound exhausted before terminal result: `UNKNOWN`.
- Node budget exhausted: `UNKNOWN`.

- [x] **Step 2: Add deterministic move ordering**

Sort legal moves by UCI string so artifacts are reproducible until engine-guided ordering is introduced.

### Task 3: Independent Verifier

**Files:**
- Create: `src/xiangqi_solver/verifier.py`
- Create: `tools/verify_proof.py`
- Test: `tests/test_proof_verifier.py`

- [x] **Step 1: Verify artifact structure**

The verifier checks FEN, target side, side-to-move node kind, legal child moves, terminal outcomes, and AND/OR proof coverage.

- [x] **Step 2: Add CLI**

`python tools/verify_proof.py artifact.json` exits with code `0` only for valid artifacts.

### Task 4: SQLite Proof Store

**Files:**
- Create: `src/xiangqi_solver/store.py`
- Create: `tools/prove.py`
- Test: `tests/test_proof_store.py`

- [x] **Step 1: Create schema**

Store position hash, FEN, target side, max ply, node budget, status, proof/disproof numbers, artifact JSON, and timestamps.

- [x] **Step 2: Add CLI**

`python tools/prove.py --fen FEN --target red --max-ply 1 --store database/proofs.sqlite --artifact artifacts/proof.json`.

### Task 5: Validation

**Files:**
- Modify: `docs/ROADMAP.md`
- Modify: `README.md`

- [x] **Step 1: Run tests**

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test.ps1
```

Expected: all tests pass.

- [x] **Step 2: Prove and verify a terminal win fixture**

Run:

```powershell
python .\tools\prove.py --fen "4k4/4R2N1/9/9/9/9/9/9/9/3RK4 b - - 0 1" --target red --max-ply 0 --artifact artifacts/red_terminal_win.json
python .\tools\verify_proof.py artifacts/red_terminal_win.json
```

Expected: proof status `proven`, verifier `valid`.
