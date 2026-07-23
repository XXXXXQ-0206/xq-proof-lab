from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from xiangqi_core import Color, GameState, Move, Position
from xiangqi_evaluators import ChessDbClient, build_rule_query_from_state


REPORT_SCHEMA_VERSION = 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect local and optional ChessDB rule state.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--position")
    source.add_argument("--fen")
    parser.add_argument("--chessdb", action="store_true")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--report")
    parser.add_argument(
        "--fail-on-legal-move-rule-conflict",
        action="store_true",
        help="Exit non-zero when ChessDB marks a move that is locally legal as non-none.",
    )
    parser.add_argument(
        "--fail-on-flag-disagreement",
        action="store_true",
        help="Exit non-zero when local and ChessDB rule flags disagree.",
    )
    parser.add_argument(
        "--fail-on-chessdb-unavailable",
        action="store_true",
        help="Exit non-zero when ChessDB-backed rule comparison is unavailable.",
    )
    args = parser.parse_args()

    state = GameState.from_uci_position(args.position) if args.position else GameState.from_position(Position.from_fen(args.fen))
    legal_moves = tuple(move.to_uci() for move in state.legal_moves())
    repetition = state.repetition_info()
    judgement = state.rule_judgement()
    chessdb_rule_query = build_rule_query_from_state(state)
    run_config = _run_config(args, state)
    output = {
        "report_type": "rule_probe",
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "config": run_config,
        "config_digest": _config_digest(run_config),
        "fen": state.to_fen(),
        "position_command": state.to_uci_position(),
        "local_result": state.game_result(),
        "rule_judgement": {
            "result": judgement.result,
            "reason": judgement.reason,
            "adjudicated": judgement.adjudicated,
        },
        "repetition": _repetition_dict(repetition),
        "rule_events": [_rule_event_dict(event) for event in state.rule_events],
        "legal_moves": legal_moves,
        "chessdb_rule_query": {
            "fen": chessdb_rule_query.fen,
            "movelist": chessdb_rule_query.movelist,
            "reptimes": chessdb_rule_query.reptimes,
        },
        "chessdb": None,
        "rule_comparison": None,
        "valid": True,
        "gate": _gate_summary(args, None),
    }

    if args.chessdb:
        if len(chessdb_rule_query.movelist) < 4:
            output["chessdb"] = {
                "status": "not_applicable",
                "rule": None,
                "rule_summary": None,
                "rule_results": [],
                "raw_text": "",
                "error": "queryrule requires at least four historical moves",
            }
            output["rule_comparison"] = _not_applicable_rule_comparison(
                judgement, legal_moves
            )
        else:
            client = ChessDbClient(timeout=args.timeout)
            response = client.query_rule(
                chessdb_rule_query.fen,
                movelist=chessdb_rule_query.movelist,
                reptimes=chessdb_rule_query.reptimes,
            )
            output["chessdb"] = {
                "status": response.status.value,
                "rule": response.rule,
                "rule_summary": _chessdb_rule_summary(response),
                "rule_results": [asdict(result) for result in response.rule_results],
                "raw_text": response.raw_text,
                "error": response.error,
            }
            output["rule_comparison"] = _rule_comparison(state, response, legal_moves)
        output["gate"] = _gate_summary(args, output["rule_comparison"])
        output["valid"] = output["gate"]["valid"]

    text = json.dumps(output, ensure_ascii=False, indent=2)
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if output["valid"] else 1


def _repetition_dict(repetition) -> dict | None:
    if repetition is None:
        return None
    return {
        "count": repetition.count,
        "cycle_plies": repetition.cycle_plies,
        "cycle_start_ply": repetition.cycle_start_ply,
        "cycle_end_ply": repetition.cycle_end_ply,
        "cycle_moves": repetition.cycle_moves,
        "has_capture": repetition.has_capture,
        "checking_sides": [color.name.lower() for color in repetition.checking_sides],
        "possible_chasing_sides": [
            color.name.lower() for color in repetition.possible_chasing_sides
        ],
        "perpetual_chasing_sides": [
            color.name.lower() for color in repetition.perpetual_chasing_sides
        ],
        "attack_patterns": [
            {
                "color": pattern.color.name.lower(),
                "moves": pattern.moves,
                "attacks_every_move": pattern.attacks_every_move,
                "common_attacked_squares": pattern.common_attacked_squares,
                "common_targets": [
                    {
                        "square": target.square,
                        "color": target.color.name.lower(),
                        "kind": target.kind.name.lower(),
                        "always_protected_by_owner": target.always_protected_by_owner,
                        "ever_unprotected": target.ever_unprotected,
                        "always_recapturable_by_owner": target.always_recapturable_by_owner,
                        "ever_recapturable_by_owner": target.ever_recapturable_by_owner,
                        "always_chase_relevant": target.always_chase_relevant,
                    }
                    for target in pattern.common_targets
                ],
            }
            for pattern in repetition.attack_patterns
        ],
    }


def _rule_event_dict(event) -> dict:
    return {
        "move": event.move,
        "moved_color": event.moved_color.name.lower() if event.moved_color is not None else None,
        "captured": event.captured,
        "gives_check": event.gives_check,
        "attacked_opponent_pieces": event.attacked_opponent_pieces,
        "attacked_opponent_details": [
            {
                "square": detail.square,
                "color": detail.color.name.lower(),
                "kind": detail.kind.name.lower(),
                "protected_by_owner": detail.protected_by_owner,
                "defenders": detail.defenders,
                "attackers": detail.attackers,
                "legal_chase_attackers": detail.legal_chase_attackers,
                "recapture_defenders": detail.recapture_defenders,
                "recapturable_by_owner": detail.recapturable_by_owner,
                "chase_relevant_attackers": detail.chase_relevant_attackers,
                "chase_relevant": detail.chase_relevant,
            }
            for detail in event.attacked_opponent_details
        ],
    }


def _chessdb_rule_summary(response) -> dict:
    counts = Counter(result.rule for result in response.rule_results)
    non_none = [result for result in response.rule_results if result.rule != "none"]
    return {
        "result_count": len(response.rule_results),
        "rule_counts": dict(sorted(counts.items())),
        "non_none_rule_results": [asdict(result) for result in non_none],
    }


def _rule_comparison(state, response, legal_moves: tuple[str, ...]) -> dict[str, object]:
    judgement = state.rule_judgement()
    local_adjudicated = bool(judgement.adjudicated)
    if response.status.value != "ok":
        return {
            "comparable": False,
            "category": "chessdb_unavailable",
            "local_adjudicated": local_adjudicated,
            "chessdb_has_non_none": False,
            "local_reason": judgement.reason,
            "chessdb_rules": [],
            "local_legal_moves": list(legal_moves),
            "flagged_locally_legal_moves": [],
            "flagged_locally_illegal_moves": [],
            "legal_move_rule_conflict": False,
            "flag_agreement": False,
            "candidate_rule_mismatches": [],
            "unverified_local_candidates": [],
        }

    legal_move_set = set(legal_moves)
    external_rules = {result.move: result.rule for result in response.rule_results}
    flagged_results = [result for result in response.rule_results if result.rule != "none"]
    chessdb_rules = sorted({result.rule for result in flagged_results})
    flagged_locally_legal_moves = [
        {"move": result.move, "rule": result.rule}
        for result in flagged_results
        if result.move in legal_move_set
    ]
    flagged_locally_illegal_moves = [
        {"move": result.move, "rule": result.rule}
        for result in flagged_results
        if result.move not in legal_move_set
    ]
    local_rules = {move: _local_candidate_rule(state, move) for move in legal_moves}
    mismatches = [
        {
            "move": move,
            "local_rule": local_rule,
            "chessdb_rule": external_rules.get(move, "none"),
        }
        for move, local_rule in local_rules.items()
        if local_rule is not None and local_rule != external_rules.get(move, "none")
    ]
    unverified = [move for move, local_rule in local_rules.items() if local_rule is None]
    if mismatches or flagged_locally_illegal_moves:
        category = "candidate_flags_disagree"
    elif unverified:
        category = "candidate_flags_unverified"
    else:
        category = "candidate_flags_agree"
    chessdb_has_non_none = bool(chessdb_rules)
    return {
        "comparable": True,
        "category": category,
        "local_adjudicated": local_adjudicated,
        "chessdb_has_non_none": chessdb_has_non_none,
        "local_reason": judgement.reason,
        "chessdb_rules": chessdb_rules,
        "flag_agreement": category == "candidate_flags_agree",
        "local_legal_moves": list(legal_moves),
        "local_non_none_rule_results": [
            {"move": move, "rule": rule}
            for move, rule in local_rules.items()
            if rule not in (None, "none")
        ],
        "flagged_locally_legal_moves": flagged_locally_legal_moves,
        "flagged_locally_illegal_moves": flagged_locally_illegal_moves,
        "candidate_rule_mismatches": mismatches,
        "unverified_local_candidates": unverified,
        "legal_move_rule_conflict": bool(mismatches or flagged_locally_illegal_moves),
    }


def _not_applicable_rule_comparison(judgement, legal_moves: tuple[str, ...]) -> dict[str, object]:
    return {
        "comparable": False,
        "category": "queryrule_not_applicable",
        "local_adjudicated": bool(judgement.adjudicated),
        "chessdb_has_non_none": False,
        "local_reason": judgement.reason,
        "chessdb_rules": [],
        "flag_agreement": None,
        "local_legal_moves": list(legal_moves),
        "local_non_none_rule_results": [],
        "flagged_locally_legal_moves": [],
        "flagged_locally_illegal_moves": [],
        "candidate_rule_mismatches": [],
        "unverified_local_candidates": [],
        "legal_move_rule_conflict": False,
    }


def _local_candidate_rule(state: GameState, move_text: str) -> str | None:
    judgement = state.candidate_rule_judgement(Move.from_uci(move_text))
    if judgement.result == "unknown_rule_state":
        return None
    if not judgement.adjudicated or judgement.repetition is None:
        return "none"
    if judgement.result == "draw":
        return "draw"
    mover = state.position.side_to_move
    mover_win = "red_win" if mover is Color.RED else "black_win"
    return "none" if judgement.result == mover_win else "ban"


def _gate_summary(args: argparse.Namespace, comparison: dict[str, object] | None) -> dict[str, object]:
    reasons: list[str] = []
    legal_move_rule_conflicts = int(bool(comparison and comparison.get("legal_move_rule_conflict")))
    flag_disagreements = int(
        bool(
            comparison
            and comparison.get("category")
            in {"candidate_flags_disagree", "candidate_flags_unverified"}
        )
    )
    chessdb_unavailable = int(bool(comparison and comparison.get("category") == "chessdb_unavailable"))
    if getattr(args, "fail_on_legal_move_rule_conflict", False) and legal_move_rule_conflicts:
        reasons.append("legal_move_rule_conflict")
    if getattr(args, "fail_on_flag_disagreement", False) and flag_disagreements:
        reasons.append("flag_disagreement")
    if getattr(args, "fail_on_chessdb_unavailable", False) and chessdb_unavailable:
        reasons.append("chessdb_unavailable")
    return {
        "valid": not reasons,
        "fail_on_legal_move_rule_conflict": bool(
            getattr(args, "fail_on_legal_move_rule_conflict", False)
        ),
        "fail_on_flag_disagreement": bool(getattr(args, "fail_on_flag_disagreement", False)),
        "fail_on_chessdb_unavailable": bool(
            getattr(args, "fail_on_chessdb_unavailable", False)
        ),
        "legal_move_rule_conflicts": legal_move_rule_conflicts,
        "flag_disagreements": flag_disagreements,
        "chessdb_unavailable": chessdb_unavailable,
        "reasons": reasons,
    }


def _run_config(args: argparse.Namespace, state: GameState) -> dict[str, object]:
    return {
        "position": args.position,
        "fen": None if args.position else args.fen,
        "chessdb": bool(args.chessdb),
        "timeout": args.timeout,
        "fail_on_legal_move_rule_conflict": bool(args.fail_on_legal_move_rule_conflict),
        "fail_on_flag_disagreement": bool(args.fail_on_flag_disagreement),
        "fail_on_chessdb_unavailable": bool(args.fail_on_chessdb_unavailable),
        "resolved_position": state.to_uci_position(),
        "resolved_fen": state.to_fen(),
    }


def _config_digest(config: dict[str, object]) -> dict[str, str]:
    canonical = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "algorithm": "sha256",
        "scope": "full",
        "value": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


if __name__ == "__main__":
    raise SystemExit(main())
