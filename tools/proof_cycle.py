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
from xiangqi_evaluators import (
    ChessDbClient,
    ChessDbMoveOrderer,
    PersistentUciBestMoveOrderer,
    UciBestMoveOrderer,
    parse_uci_options,
    split_engine_command,
)
from xiangqi_solver import (
    ProofCycleError,
    run_proof_cycle,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a restartable proof-search cycle.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--fen")
    source.add_argument("--position")
    parser.add_argument("--target", choices=("red", "black"), default="red")
    parser.add_argument("--store", required=True)
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--resume-artifact", action="store_true")
    parser.add_argument("--initial-ply", type=int, default=0)
    parser.add_argument("--extra-ply", type=int, default=1)
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--frontier-limit", type=int, default=10)
    parser.add_argument("--frontier-reason", action="append")
    parser.add_argument("--frontier-max-attempts", type=int)
    parser.add_argument("--frontier-min-remaining-ply", type=int)
    parser.add_argument("--frontier-max-remaining-ply", type=int)
    parser.add_argument("--frontier-max-proof", type=int)
    parser.add_argument("--frontier-max-disproof", type=int)
    parser.add_argument("--node-limit", type=int, default=100_000)
    parser.add_argument("--node-budget", type=int)
    parser.add_argument("--searcher", choices=("bounded", "dfpn"), default="bounded")
    parser.add_argument("--reuse-store", action="store_true")
    parser.add_argument("--proof-threshold", type=int, default=10**12)
    parser.add_argument("--disproof-threshold", type=int, default=10**12)
    parser.add_argument("--dfpn-iterative", action="store_true")
    parser.add_argument("--dfpn-iterations", type=int, default=4)
    parser.add_argument("--threshold-growth", type=int, default=2)
    parser.add_argument("--chessdb-ordering", action="store_true")
    parser.add_argument("--chessdb-egtbmetric", choices=("dtc", "dtm"))
    parser.add_argument("--chessdb-ban", nargs="*", default=())
    parser.add_argument("--uci-engine", help="UCI engine command used only for candidate ordering.")
    parser.add_argument("--uci-depth", type=int, default=4)
    parser.add_argument(
        "--uci-option",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="UCI option forwarded to the candidate ordering engine; repeatable.",
    )
    parser.add_argument(
        "--persistent-uci-ordering",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Keep the UCI ordering engine alive for the whole proof cycle.",
    )
    parser.add_argument("--time-limit-seconds", type=float)
    parser.add_argument("--reset-running-max-age-seconds", type=float)
    args = parser.parse_args()
    if args.chessdb_ordering and args.uci_engine:
        parser.error("choose only one candidate ordering source")

    if not args.resume_artifact and not (args.position or args.fen):
        parser.error("one of --fen/--position is required unless --resume-artifact is set")
    root_state = (
        None
        if args.resume_artifact
        else GameState.from_uci_position(args.position) if args.position else Position.from_fen(args.fen)
    )
    move_orderer = _move_orderer(args)
    try:
        output = run_proof_cycle(
            root_state,
            target=args.target,
            store_path=args.store,
            artifact_path=args.artifact,
            initial_ply=args.initial_ply,
            extra_ply=args.extra_ply,
            cycles=args.cycles,
            frontier_limit=args.frontier_limit,
            frontier_reasons=tuple(args.frontier_reason or ()),
            frontier_max_attempts=args.frontier_max_attempts,
            frontier_min_remaining_ply=args.frontier_min_remaining_ply,
            frontier_max_remaining_ply=args.frontier_max_remaining_ply,
            frontier_max_proof=args.frontier_max_proof,
            frontier_max_disproof=args.frontier_max_disproof,
            node_limit=args.node_limit,
            resume_artifact=args.resume_artifact,
            searcher=args.searcher,
            reuse_store=args.reuse_store,
            proof_threshold=args.proof_threshold,
            disproof_threshold=args.disproof_threshold,
            dfpn_iterative=args.dfpn_iterative,
            dfpn_iterations=args.dfpn_iterations,
            threshold_growth=args.threshold_growth,
            move_orderer=move_orderer,
            time_limit_seconds=args.time_limit_seconds,
            node_budget=args.node_budget,
            reset_running_max_age_seconds=args.reset_running_max_age_seconds,
        )
    except ProofCycleError as exc:
        print(json.dumps({"valid": False, "errors": exc.errors}, ensure_ascii=False, indent=2))
        return 2
    finally:
        _close_move_orderer(move_orderer)

    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def _move_orderer(args):
    if args.chessdb_ordering:
        return ChessDbMoveOrderer(
            ChessDbClient(),
            egtbmetric=args.chessdb_egtbmetric,
            ban=_movelist(args.chessdb_ban),
        )
    if args.uci_engine:
        orderer_type = (
            PersistentUciBestMoveOrderer
            if args.persistent_uci_ordering
            else UciBestMoveOrderer
        )
        return orderer_type(
            _split_command(args.uci_engine),
            depth=args.uci_depth,
            options=parse_uci_options(args.uci_option),
        )
    return None


def _close_move_orderer(move_orderer) -> None:
    close = getattr(move_orderer, "close", None)
    if callable(close):
        close()


def _movelist(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    moves: list[str] = []
    for value in values:
        moves.extend(move for move in value.split("|") if move)
    return tuple(moves)


def _split_command(command: str) -> list[str]:
    return split_engine_command(command)


if __name__ == "__main__":
    raise SystemExit(main())
