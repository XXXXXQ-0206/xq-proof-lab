from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from xiangqi_core import GameState, Position
from xiangqi_solver import FrontierJob, ProofStore, ProofVerifier


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify all proof artifacts in a SQLite store.")
    parser.add_argument("--store", required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--frontier-limit", type=int)
    parser.add_argument("--skip-frontier", action="store_true")
    parser.add_argument("--delete-invalid", action="store_true")
    args = parser.parse_args()

    store = ProofStore(args.store)
    verifier = ProofVerifier()
    errors = []
    frontier_errors = []
    deleted = 0
    checked = 0
    frontier_checked = 0

    try:
        proofs = store.iter_proofs(args.limit)
    except Exception as exc:
        print(json.dumps({"valid": False, "load_error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    for proof in proofs:
        checked += 1
        result = verifier.verify(proof.artifact)
        if not result.valid:
            if args.delete_invalid:
                deleted += store.delete_proof(proof.position_key, proof.target, proof.max_ply)
            errors.append(
                {
                    "position_key": proof.position_key,
                    "fen": proof.fen,
                    "target": proof.target.value,
                    "max_ply": proof.max_ply,
                    "errors": result.errors,
                }
            )

    if not args.skip_frontier:
        try:
            frontier_jobs = store.iter_frontier(args.frontier_limit)
        except Exception as exc:
            print(
                json.dumps(
                    {"valid": False, "frontier_load_error": str(exc)},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
        for job in frontier_jobs:
            frontier_checked += 1
            job_errors = _verify_frontier_job(store, job)
            if job_errors:
                frontier_errors.append(
                    {
                        "id": job.id,
                        "position_key": job.position_key,
                        "fen": job.fen,
                        "target": job.target.value,
                        "remaining_ply": job.remaining_ply,
                        "errors": job_errors,
                    }
                )

    print(
        json.dumps(
            {
                "valid": not errors and not frontier_errors,
                "checked": checked,
                "frontier_checked": frontier_checked,
                "deleted": deleted,
                "errors": errors,
                "frontier_errors": frontier_errors,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not errors and not frontier_errors else 1


def _verify_frontier_job(store: ProofStore, job: FrontierJob) -> list[str]:
    errors: list[str] = []
    if job.remaining_ply < 0:
        errors.append("remaining_ply is negative")
    if job.proof < 0:
        errors.append("proof is negative")
    if job.disproof < 0:
        errors.append("disproof is negative")
    if job.position_key != store.state_key(job.fen, job.history_signature):
        errors.append("position_key does not match FEN/history signature")

    if job.position_command:
        try:
            state = GameState.from_uci_position(job.position_command)
        except Exception as exc:
            errors.append(f"invalid position command: {exc}")
        else:
            if state.to_fen() != job.fen:
                errors.append("position command FEN does not match frontier FEN")
            if job.history_signature and state.history_signature() != job.history_signature:
                errors.append("position command history does not match frontier signature")
    else:
        try:
            Position.from_fen(job.fen)
        except Exception as exc:
            errors.append(f"invalid FEN: {exc}")
    return errors


if __name__ == "__main__":
    raise SystemExit(main())
