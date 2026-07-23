from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from xiangqi_solver import ProofStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Print proof database status.")
    parser.add_argument("--store", required=True)
    parser.add_argument("--frontier-limit", type=int, default=5)
    parser.add_argument("--proof-limit", type=int, default=5)
    args = parser.parse_args()

    store = ProofStore(args.store)
    output = store.database_summary()
    output["frontier_metrics"] = store.frontier_metrics()
    output["pending_frontier"] = [
        {
            "id": job.id,
            "position_key": job.position_key,
            "target": job.target.value,
            "remaining_ply": job.remaining_ply,
            "reason": job.reason,
            "attempts": job.attempts,
            "proof": job.proof,
            "disproof": job.disproof,
            "last_result_status": job.last_result_status,
        }
        for job in store.pending_frontier(args.frontier_limit)
    ]
    output["recent_proofs"] = [
        {
            "position_key": proof.position_key,
            "target": proof.target.value,
            "max_ply": proof.max_ply,
            "node_limit": proof.node_limit,
            "status": proof.artifact.status.value,
            "proof": proof.artifact.proof,
            "disproof": proof.artifact.disproof,
            "reason": proof.artifact.reason,
            "history_sensitive": bool(proof.artifact.history_signature),
        }
        for proof in store.iter_proofs(args.proof_limit)
    ]
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
