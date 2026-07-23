from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from xiangqi_evaluators import (
    CachedMoveOrderer,
    LocalSearchMoveOrderer,
    PersistentUciBestMoveOrderer,
    split_engine_command,
)
from xiangqi_solver import ProofStore
from xiangqi_solver.uci_loop import ProofAssistedUciEngine, run_uci_loop


def _split_command(command: str) -> list[str]:
    return split_engine_command(command)


def _parse_uci_option(text: str) -> tuple[str, str]:
    if "=" not in text:
        raise ValueError("--fallback-uci-option must use NAME=VALUE")
    name, value = text.split("=", 1)
    name = name.strip()
    value = value.strip()
    if not name or not value:
        raise ValueError("--fallback-uci-option name and value must be non-empty")
    return name, value


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the proof-assisted Xiangqi UCI engine.")
    parser.add_argument(
        "--closed",
        action="store_true",
        help="Forbid external UCI ordering and fallback for closed-candidate play.",
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Disable proof-store lookup and online proof search for a closed local baseline.",
    )
    parser.add_argument("--max-ply", type=int, default=1)
    parser.add_argument("--node-limit", type=int, default=10_000)
    parser.add_argument("--local-search-depth", type=int, default=2)
    parser.add_argument("--local-search-node-limit", type=int, default=5_000)
    parser.add_argument(
        "--proof-store",
        help="Optional SQLite proof store; verified proven artifacts may choose bestmove directly.",
    )
    parser.add_argument(
        "--save-online-proofs",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Persist verified PROVEN artifacts found by online proof search into --proof-store.",
    )
    parser.add_argument(
        "--fallback-uci-engine",
        help="Optional UCI engine command used only to order fallback/proof candidate moves.",
    )
    parser.add_argument("--fallback-uci-depth", type=int, default=1)
    parser.add_argument("--fallback-uci-multipv", type=int, default=1)
    parser.add_argument(
        "--fallback-uci-option",
        action="append",
        default=None,
        metavar="NAME=VALUE",
        help="Optional UCI option forwarded to the fallback engine; repeatable.",
    )
    parser.add_argument("--fallback-uci-timeout", type=float, default=5.0)
    parser.add_argument(
        "--direct-fallback-uci",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Use the fallback UCI engine's original go command after a proof-store miss.",
    )
    parser.add_argument(
        "--ponder",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable UCI ponder mode before any runtime setoption commands arrive.",
    )
    args = parser.parse_args()

    if args.closed and args.fallback_uci_engine:
        parser.error("--closed forbids --fallback-uci-engine")
    if args.closed and args.direct_fallback_uci:
        parser.error("--closed forbids --direct-fallback-uci")
    if args.closed and args.fallback_uci_option:
        parser.error("--closed forbids --fallback-uci-option")
    if args.local_only and not args.closed:
        parser.error("--local-only requires --closed")
    if args.local_only and args.proof_store:
        parser.error("--local-only forbids --proof-store")
    if args.local_only and args.save_online_proofs:
        parser.error("--local-only forbids --save-online-proofs")
    if args.fallback_uci_multipv <= 0:
        raise ValueError("--fallback-uci-multipv must be positive")
    if args.local_search_depth <= 0:
        raise ValueError("--local-search-depth must be positive")
    if args.local_search_node_limit <= 0:
        raise ValueError("--local-search-node-limit must be positive")
    fallback_options = tuple(
        _parse_uci_option(option) for option in (args.fallback_uci_option or ())
    )
    proof_store = ProofStore(args.proof_store) if args.proof_store else None
    if args.save_online_proofs and proof_store is None:
        raise ValueError("--save-online-proofs requires --proof-store")

    local_orderer = LocalSearchMoveOrderer(
        depth=args.local_search_depth,
        node_limit=args.local_search_node_limit,
    )
    move_orderer = local_orderer
    if args.fallback_uci_engine:
        move_orderer = CachedMoveOrderer(
            PersistentUciBestMoveOrderer(
                _split_command(args.fallback_uci_engine),
                depth=args.fallback_uci_depth,
                multipv=args.fallback_uci_multipv,
                timeout=args.fallback_uci_timeout,
                fallback=local_orderer,
                options=fallback_options,
            )
        )

    return run_uci_loop(
        ProofAssistedUciEngine(
            args.max_ply,
            args.node_limit,
            move_orderer=move_orderer,
            proof_store=proof_store,
            ponder_enabled=args.ponder,
            save_online_proofs=args.save_online_proofs,
            prefer_external_fallback=args.direct_fallback_uci,
            external_move_ordering=bool(args.fallback_uci_engine),
            proof_enabled=not args.local_only,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
