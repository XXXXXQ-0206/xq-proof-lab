# Xiangqi Proof Tree Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first verifiable slice of a Chinese chess proof-tree engine: rules core, PNS primitives, Pikafish compatibility notes, and executable tests.

**Architecture:** Keep the proof verifier independent from external engines. Use Pikafish for UCI/FEN compatibility and later move ordering, but store only proof results that the local verifier can replay.

**Tech Stack:** Python standard library for phase-0 verifier and tests; Pikafish source as local GPL-3.0 reference; future native C++/Rust performance layer after rule behavior is locked.

---

### Task 1: Project Structure

**Files:**
- Create: `README.md`
- Create: `.gitignore`
- Create: `docs/ARCHITECTURE.md`
- Create: `docs/RULES.md`
- Create: `docs/PIKAFISH_COMPATIBILITY.md`
- Create: `docs/ROADMAP.md`

- [x] **Step 1: Create directories**

Run:

```powershell
New-Item -ItemType Directory -Force -Path docs,external,src,tests,tools,database,scripts
```

Expected: the root contains the documented project folders.

- [x] **Step 2: Clone Pikafish as reference**

Run:

```powershell
git clone --depth 1 https://github.com/official-pikafish/Pikafish.git external/pikafish
```

Expected: `external/pikafish/src/types.h` and `external/pikafish/src/uci.cpp` exist.

### Task 2: Rules Core

**Files:**
- Create: `src/xiangqi_core/coordinates.py`
- Create: `src/xiangqi_core/model.py`
- Create: `src/xiangqi_core/move.py`
- Create: `src/xiangqi_core/position.py`
- Create: `src/xiangqi_core/zobrist.py`
- Create: `src/xiangqi_core/__init__.py`

- [x] **Step 1: Implement coordinate conversion**

Use `a0` through `i9`, matching Pikafish UCI examples.

- [x] **Step 2: Implement FEN model**

Parse and emit:

```text
rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1
```

- [x] **Step 3: Implement legal move generation**

Include all phase-0 piece rules: rook, knight, bishop, advisor, king, cannon, pawn, river, palace, blockers, cannon screen, and flying-general legality.

### Task 3: Proof-Number Foundation

**Files:**
- Create: `src/xiangqi_solver/pns.py`
- Create: `src/xiangqi_solver/__init__.py`

- [x] **Step 1: Implement proof/disproof values**

Define `ProofNumbers`, `ProofOutcome`, and `NodeKind`.

- [x] **Step 2: Implement OR/AND combine rules**

OR node: proof is minimum child proof; disproof is sum of child disproofs.

AND node: proof is sum of child proofs; disproof is minimum child disproof.

### Task 4: Tests and Perft

**Files:**
- Create: `tools/perft.py`
- Create: `tests/context.py`
- Create: `tests/test_coordinates.py`
- Create: `tests/test_fen.py`
- Create: `tests/test_movegen.py`
- Create: `tests/test_perft.py`
- Create: `tests/test_pns.py`
- Create: `tests/test_zobrist.py`

- [x] **Step 1: Add tests**

Run:

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m unittest discover -s tests -v
```

Expected: all tests pass.

- [x] **Step 2: Verify initial perft depth 1**

Run:

```powershell
python .\tools\perft.py --depth 1
```

Expected: `44`.
