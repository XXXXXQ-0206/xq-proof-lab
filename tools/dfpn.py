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
from xiangqi_evaluators import UciBestMoveOrderer, split_engine_command
from xiangqi_solver import (
    DfpnLimits,
    DfpnSearch,
    ProofStore,
    ProofVerifier,
    collect_frontier,
    run_iterative_dfpn,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run threshold-controlled DFPN-style proof search.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fen")
    source.add_argument("--position")
    parser.add_argument("--target", choices=("red", "black"), default="red")
    parser.add_argument("--max-ply", type=int, required=True)
    parser.add_argument("--proof-threshold", type=int, default=10**12)
    parser.add_argument("--disproof-threshold", type=int, default=10**12)
    parser.add_argument("--node-limit", type=int, default=100_000)
    parser.add_argument("--time-limit-seconds", type=float)
    parser.add_argument("--store")
    parser.add_argument("--reuse-store", action="store_true")
    parser.add_argument("--artifact")
    parser.add_argument("--enqueue-frontier", action="store_true")
    parser.add_argument("--uci-engine", help="UCI engine command used only for candidate ordering.")
    parser.add_argument("--uci-depth", type=int, default=4)
    parser.add_argument("--iterative", action="store_true")
    parser.add_argument("--iterations", type=int, default=4)
    parser.add_argument("--threshold-growth", type=int, default=2)
    args = parser.parse_args()

    limits = DfpnLimits(
        proof_threshold=args.proof_threshold,
        disproof_threshold=args.disproof_threshold,
        node_limit=args.node_limit,
        time_limit_seconds=args.time_limit_seconds,
    )
    move_orderer = (
        UciBestMoveOrderer(
            split_engine_command(args.uci_engine),
            depth=args.uci_depth,
        )
        if args.uci_engine
        else None
    )
    resolver = ProofStore(args.store) if args.store and args.reuse_store else None
    position = GameState.from_uci_position(args.position) if args.position else Position.from_fen(args.fen)
    iterations = None
    total_nodes_searched = None
    if args.iterative:
        iterative = run_iterative_dfpn(
            position,
            args.target,
            args.max_ply,
            initial_limits=limits,
            max_iterations=args.iterations,
            threshold_growth=args.threshold_growth,
            move_orderer=move_orderer,
            resolver=resolver,
        )
        result = iterative.result
        total_nodes_searched = iterative.total_nodes_searched
        iterations = [
            {
                "index": iteration.index,
                "proof_threshold": iteration.proof_threshold,
                "disproof_threshold": iteration.disproof_threshold,
                "status": iteration.status.value,
                "proof": iteration.proof,
                "disproof": iteration.disproof,
                "reason": iteration.reason,
                "nodes_searched": iteration.nodes_searched,
                "node_limit_reached": iteration.node_limit_reached,
                "threshold_reached": iteration.threshold_reached,
                "cache_hits": iteration.cache_hits,
                "resolved_cache_hits": iteration.resolved_cache_hits,
                "resolved_store_hits": iteration.resolved_store_hits,
                "resolved_store_misses": iteration.resolved_store_misses,
            }
            for iteration in iterative.iterations
        ]
    else:
        searcher = DfpnSearch(
            args.target,
            args.max_ply,
            limits,
            move_orderer=move_orderer,
            resolver=resolver,
        )
        result = searcher.search(position)
    verification = ProofVerifier().verify(result.artifact)
    if not verification.valid:
        print(json.dumps({"valid": False, "errors": verification.errors}, ensure_ascii=False, indent=2))
        return 2

    frontier_count = 0
    position_key = None
    if args.store:
        store = ProofStore(args.store)
        position_key = store.save(result.artifact, args.node_limit)
        if args.enqueue_frontier:
            frontier_count = store.enqueue_frontier(collect_frontier(result.artifact))

    if args.artifact:
        artifact_path = Path(args.artifact)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            json.dumps(result.artifact.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    print(
        json.dumps(
            {
                "status": result.artifact.status.value,
                "proof": result.artifact.proof,
                "disproof": result.artifact.disproof,
                "nodes_searched": result.nodes_searched,
                "total_nodes_searched": total_nodes_searched,
                "cache_hits": result.cache_hits,
                "total_cache_hits": iterative.total_cache_hits if args.iterative else None,
                "resolved_cache_hits": result.resolved_cache_hits,
                "total_resolved_cache_hits": (
                    iterative.total_resolved_cache_hits if args.iterative else None
                ),
                "resolved_store_hits": result.resolved_store_hits,
                "total_resolved_store_hits": (
                    iterative.total_resolved_store_hits if args.iterative else None
                ),
                "resolved_store_misses": result.resolved_store_misses,
                "total_resolved_store_misses": (
                    iterative.total_resolved_store_misses if args.iterative else None
                ),
                "node_limit_reached": result.node_limit_reached,
                "time_limit_reached": result.time_limit_reached,
                "threshold_reached": result.threshold_reached,
                "iterative": args.iterative,
                "iterations": iterations,
                "position_key": position_key,
                "frontier_enqueued": frontier_count,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
