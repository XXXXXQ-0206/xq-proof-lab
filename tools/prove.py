from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from xiangqi_core import GameState, Position
from xiangqi_evaluators import ChessDbClient, ChessDbMoveOrderer, UciBestMoveOrderer, split_engine_command
from xiangqi_solver import BoundedProofSearch, ProofStatus, ProofStore, ProofVerifier, collect_frontier
from xiangqi_solver.certificate import compact_proven_certificate


def main() -> int:
    parser = argparse.ArgumentParser(description="Run bounded Xiangqi proof search.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fen", help="Position FEN")
    source.add_argument("--position", help="UCI position command, e.g. 'position startpos moves ...'")
    parser.add_argument("--target", choices=("red", "black"), default="red")
    parser.add_argument("--max-ply", type=int, required=True)
    parser.add_argument("--node-limit", type=int, default=100_000)
    parser.add_argument("--time-limit-seconds", type=float)
    parser.add_argument("--verification-reserve-seconds", type=float, default=0.0)
    parser.add_argument("--compact-proven-certificate", action="store_true")
    parser.add_argument("--artifact", help="Optional JSON artifact output path")
    parser.add_argument("--store", help="Optional SQLite proof store path")
    parser.add_argument(
        "--enqueue-frontier",
        action="store_true",
        help="When a store is provided, enqueue unknown leaf nodes for later continuation.",
    )
    parser.add_argument(
        "--chessdb-ordering",
        action="store_true",
        help="Use chessdb.cn only for candidate move ordering; never as proof.",
    )
    parser.add_argument("--uci-engine", help="UCI engine command used only for candidate ordering.")
    parser.add_argument("--uci-depth", type=int, default=4)
    args = parser.parse_args()
    if args.verification_reserve_seconds < 0:
        parser.error("verification reserve must be non-negative")
    if args.verification_reserve_seconds and args.time_limit_seconds is None:
        parser.error("verification reserve requires a total time limit")
    if (
        args.time_limit_seconds is not None
        and args.verification_reserve_seconds > args.time_limit_seconds
    ):
        parser.error("verification reserve cannot exceed the total time limit")

    position = GameState.from_uci_position(args.position) if args.position else Position.from_fen(args.fen)
    move_orderer = None
    if args.chessdb_ordering:
        move_orderer = ChessDbMoveOrderer(ChessDbClient())
    elif args.uci_engine:
        move_orderer = UciBestMoveOrderer(
            split_engine_command(args.uci_engine),
            depth=args.uci_depth,
        )
    search_time_limit = args.time_limit_seconds
    if search_time_limit is not None:
        search_time_limit -= args.verification_reserve_seconds
    searcher = BoundedProofSearch(
        args.target,
        args.max_ply,
        args.node_limit,
        move_orderer=move_orderer,
        time_limit_seconds=search_time_limit,
    )
    search_started = perf_counter()
    result = searcher.search(position)

    artifact = result.artifact
    certificate_compacted = False
    if args.compact_proven_certificate and artifact.status is ProofStatus.PROVEN:
        artifact = compact_proven_certificate(artifact)
        certificate_compacted = True
    verification_performed = artifact.status is not ProofStatus.UNKNOWN
    verification_errors: tuple[str, ...] = ()
    if verification_performed:
        verification_time_limit = (
            max(0.0, args.time_limit_seconds - (perf_counter() - search_started))
            if args.time_limit_seconds is not None
            else None
        )
        if verification_time_limit is not None and args.verification_reserve_seconds:
            verification_time_limit = min(
                verification_time_limit,
                args.verification_reserve_seconds,
            )
        verification = ProofVerifier().verify(
            artifact,
            time_limit_seconds=verification_time_limit,
        )
        if not verification.valid:
            print(
                json.dumps(
                    {
                        "valid": False,
                        "status": artifact.status.value,
                        "proof": artifact.proof,
                        "disproof": artifact.disproof,
                        "nodes_searched": result.nodes_searched,
                        "node_limit_reached": result.node_limit_reached,
                        "time_limit_reached": result.time_limit_reached,
                        "verification_performed": True,
                        "verification_reserve_seconds": args.verification_reserve_seconds,
                        "certificate_compacted": certificate_compacted,
                        "verification_errors": verification.errors,
                        "artifact_written": False,
                        "proof_saved": False,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2

    position_key = None
    frontier_count = 0
    proof_saved = False
    if args.store and verification_performed:
        store = ProofStore(args.store)
        position_key = store.save(artifact, args.node_limit, verify=False)
        proof_saved = True
        if args.enqueue_frontier:
            frontier_count = store.enqueue_frontier(collect_frontier(result.artifact))

    artifact_written = False
    if args.artifact and verification_performed:
        artifact_path = Path(args.artifact)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            json.dumps(artifact.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        artifact_written = True

    print(
        json.dumps(
            {
                "status": artifact.status.value,
                "valid": True,
                "proof": result.artifact.proof,
                "disproof": result.artifact.disproof,
                "nodes_searched": result.nodes_searched,
                "node_limit_reached": result.node_limit_reached,
                "time_limit_reached": result.time_limit_reached,
                "verification_performed": verification_performed,
                "verification_reserve_seconds": args.verification_reserve_seconds,
                "certificate_compacted": certificate_compacted,
                "artifact_written": artifact_written,
                "proof_saved": proof_saved,
                "verification_errors": verification_errors,
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
