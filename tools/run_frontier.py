from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from xiangqi_core import GameState, Position
from xiangqi_solver import (
    BoundedProofSearch,
    DfpnLimits,
    DfpnSearch,
    ProofStore,
    ProofVerifier,
    collect_frontier,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Continue pending proof frontier jobs.")
    parser.add_argument("--store", required=True)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--extra-ply", type=int, default=1)
    parser.add_argument("--node-limit", type=int, default=100_000)
    parser.add_argument("--searcher", choices=("bounded", "dfpn"), default="bounded")
    parser.add_argument("--reuse-store", action="store_true")
    parser.add_argument("--reason", action="append")
    parser.add_argument("--max-attempts", type=int)
    parser.add_argument("--min-remaining-ply", type=int)
    parser.add_argument("--max-remaining-ply", type=int)
    parser.add_argument("--max-proof", type=int)
    parser.add_argument("--max-disproof", type=int)
    parser.add_argument("--node-budget", type=int)
    parser.add_argument("--reset-running-max-age-seconds", type=float)
    args = parser.parse_args()
    if args.node_budget is not None and args.node_budget < 0:
        parser.error("--node-budget must be non-negative")
    if (
        args.reset_running_max_age_seconds is not None
        and args.reset_running_max_age_seconds < 0
    ):
        parser.error("--reset-running-max-age-seconds must be non-negative")

    store = ProofStore(args.store)
    running_frontier_reset = store.reset_running_frontier(
        args.reset_running_max_age_seconds
    )
    processed = []
    nodes_consumed = 0
    node_budget_reached = False

    reasons = tuple(args.reason or ())
    for job in store.pending_frontier(
        args.limit,
        reasons=reasons,
        max_attempts=args.max_attempts,
        min_remaining_ply=args.min_remaining_ply,
        max_remaining_ply=args.max_remaining_ply,
        max_proof=args.max_proof,
        max_disproof=args.max_disproof,
    ):
        if _node_budget_reached(args.node_budget, nodes_consumed):
            node_budget_reached = True
            break
        store.mark_frontier_running(job.id)
        max_ply = max(0, job.remaining_ply + args.extra_ply)
        state = (
            GameState.from_uci_position(job.position_command)
            if job.position_command
            else Position.from_fen(job.fen)
        )
        result = _search(
            state,
            job.target,
            max_ply,
            args.node_limit,
            args.searcher,
            store,
            args.reuse_store,
        )
        nodes_consumed += result.nodes_searched
        artifact = (
            replace(
                result.artifact,
                history_signature=result.artifact.history_signature or job.history_signature,
                position_command=result.artifact.position_command or job.position_command,
            )
            if (
                job.history_signature
                and result.artifact.history_signature != job.history_signature
            )
            or (job.position_command and result.artifact.position_command != job.position_command)
            else result.artifact
        )
        verification = ProofVerifier().verify(artifact)
        if not verification.valid:
            print(
                json.dumps(
                    {"job_id": job.id, "valid": False, "errors": verification.errors},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2

        store.save(artifact, args.node_limit)
        frontier = collect_frontier(artifact)
        if artifact.status.value == "unknown":
            store.enqueue_frontier(frontier)
        split_frontier = artifact.status.value == "unknown" and bool(artifact.children)
        store.finish_frontier(
            job.id,
            artifact.status.value,
            proof=artifact.proof,
            disproof=artifact.disproof,
            reason=artifact.reason,
            split=split_frontier,
        )
        processed.append(
            {
                "job_id": job.id,
                "status": artifact.status.value,
                "split_frontier": split_frontier,
                "frontier_proof": job.proof,
                "frontier_disproof": job.disproof,
                "result_proof": artifact.proof,
                "result_disproof": artifact.disproof,
                "result_reason": artifact.reason,
                "nodes_searched": result.nodes_searched,
                "cache_hits": result.cache_hits,
                "resolved_cache_hits": result.resolved_cache_hits,
                "resolved_store_hits": result.resolved_store_hits,
                "resolved_store_misses": result.resolved_store_misses,
                "node_limit_reached": result.node_limit_reached,
                "threshold_reached": result.threshold_reached,
                "max_ply": max_ply,
                "nodes_consumed_after": nodes_consumed,
            }
        )
        if _node_budget_reached(args.node_budget, nodes_consumed):
            node_budget_reached = True

    print(
        json.dumps(
            {
                "searcher": args.searcher,
                "reuse_store": args.reuse_store,
                "node_budget": args.node_budget,
                "node_budget_reached": node_budget_reached,
                "nodes_consumed": nodes_consumed,
                "reset_running_max_age_seconds": args.reset_running_max_age_seconds,
                "running_frontier_reset": running_frontier_reset,
                "filters": {
                    "reason": list(reasons),
                    "max_attempts": args.max_attempts,
                    "min_remaining_ply": args.min_remaining_ply,
                    "max_remaining_ply": args.max_remaining_ply,
                    "max_proof": args.max_proof,
                    "max_disproof": args.max_disproof,
                },
                "processed": processed,
                "database": store.database_summary(),
                "frontier_metrics": store.frontier_metrics(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _node_budget_reached(node_budget: int | None, nodes_consumed: int) -> bool:
    return node_budget is not None and nodes_consumed >= node_budget


def _search(
    state,
    target,
    max_ply: int,
    node_limit: int,
    searcher: str,
    store: ProofStore,
    reuse_store: bool,
):
    if searcher == "bounded":
        resolver = store if reuse_store else None
        return BoundedProofSearch(
            target,
            max_ply=max_ply,
            node_limit=node_limit,
            resolver=resolver,
        ).search(state)
    if searcher == "dfpn":
        resolver = store if reuse_store else None
        return DfpnSearch(
            target,
            max_ply=max_ply,
            limits=DfpnLimits(node_limit=node_limit),
            resolver=resolver,
        ).search(state)
    raise ValueError(f"unsupported searcher: {searcher}")


if __name__ == "__main__":
    raise SystemExit(main())
