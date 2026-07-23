from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from xiangqi_core import GameState
from xiangqi_evaluators import ChessDbAction, ChessDbClient, build_rule_query_from_state


def _movelist(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    moves: list[str] = []
    for value in values:
        moves.extend(move for move in value.split("|") if move)
    return tuple(moves)


def main() -> int:
    parser = argparse.ArgumentParser(description="Query chessdb.cn cloud book.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fen")
    source.add_argument("--position")
    parser.add_argument(
        "--action",
        choices=[action.value for action in ChessDbAction],
        default=ChessDbAction.QUERY_BEST.value,
    )
    parser.add_argument("--show-all", action="store_true")
    parser.add_argument("--movelist", nargs="*", default=())
    parser.add_argument("--reptimes", type=int)
    parser.add_argument("--egtbmetric", choices=("dtc", "dtm"))
    parser.add_argument("--ban", nargs="*", default=())
    parser.add_argument("--learn", action="store_true")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    client = ChessDbClient(timeout=args.timeout, learn=args.learn)
    action = ChessDbAction(args.action)
    try:
        fen, movelist, reptimes = _query_inputs(args, action)
    except ValueError as exc:
        parser.error(str(exc))
    banned_moves = _movelist(args.ban)

    if args.dry_run:
        print(
            json.dumps(
                {
                    "status": "dry_run",
                    "action": action.value,
                    "fen": fen,
                    "movelist": movelist,
                    "reptimes": reptimes,
                    "url": client.build_url(
                        action,
                        fen,
                        showall=int(args.show_all) if action is ChessDbAction.QUERY_ALL else None,
                        egtbmetric=args.egtbmetric
                        if action is not ChessDbAction.QUERY_RULE
                        else None,
                        ban="|".join(banned_moves)
                        if action in {ChessDbAction.QUERY_ALL, ChessDbAction.QUERY_BEST}
                        and banned_moves
                        else None,
                        movelist="|".join(movelist) if movelist else None,
                        reptimes=reptimes,
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if action is ChessDbAction.QUERY_ALL:
        response = client.query_all(
            fen,
            showall=args.show_all,
            egtbmetric=args.egtbmetric,
            ban=banned_moves,
        )
    elif action is ChessDbAction.QUERY_BEST:
        response = client.query_best(fen, egtbmetric=args.egtbmetric, ban=banned_moves)
    elif action is ChessDbAction.QUERY_SCORE:
        response = client.query_score(fen, egtbmetric=args.egtbmetric)
    elif action is ChessDbAction.QUERY_PV:
        response = client.query_pv(fen, egtbmetric=args.egtbmetric)
    else:
        response = client.query_rule(fen, movelist=movelist, reptimes=reptimes)

    print(
        json.dumps(
            {
                "status": response.status.value,
                "best_move": response.best_move,
                "best_move_source": response.best_move_source,
                "score": response.score,
                "pv": response.pv,
                "rule": response.rule,
                "rule_results": [asdict(result) for result in response.rule_results],
                "moves": [asdict(move) for move in response.moves],
                "raw_text": response.raw_text,
                "error": response.error,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if response.error is None else 1


def _query_inputs(args, action: ChessDbAction) -> tuple[str, tuple[str, ...], int | None]:
    if args.position:
        state = GameState.from_uci_position(args.position)
        if action is ChessDbAction.QUERY_RULE:
            if args.movelist:
                raise ValueError("--movelist cannot be combined with --position for queryrule")
            query = build_rule_query_from_state(state)
            return query.fen, query.movelist, args.reptimes or query.reptimes
        return state.to_fen(), _movelist(args.movelist), args.reptimes
    return args.fen, _movelist(args.movelist), args.reptimes


if __name__ == "__main__":
    raise SystemExit(main())
