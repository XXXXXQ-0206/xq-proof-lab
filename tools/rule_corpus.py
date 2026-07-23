from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from xiangqi_core import GameState, Position
from xiangqi_evaluators import ChessDbClient, build_rule_query_from_state
from rule_probe import (
    _chessdb_rule_summary,
    _not_applicable_rule_comparison,
    _repetition_dict,
    _rule_comparison,
)


REPORT_SCHEMA_VERSION = 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch local and optional ChessDB rule probes.")
    parser.add_argument("--config", required=True)
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

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    positions = _positions(config)
    client = ChessDbClient(timeout=args.timeout) if args.chessdb else None
    entries = [_probe_entry(item, client) for item in positions]
    run_config = _run_config(args, positions)
    comparison_summary = _comparison_summary(entries)
    gate = _gate_summary(args, comparison_summary)
    output = {
        "report_type": "rule_corpus",
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "config": run_config,
        "config_digest": _config_digest(run_config),
        "valid": gate["valid"],
        "gate": gate,
        "count": len(entries),
        "chessdb": args.chessdb,
        "comparison_summary": comparison_summary,
        "entries": entries,
    }

    text = json.dumps(output, ensure_ascii=False, indent=2)
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if gate["valid"] else 1


def _positions(config: Any) -> list[dict[str, Any]]:
    positions = config.get("positions") if isinstance(config, dict) else config
    if not isinstance(positions, list) or not positions:
        raise ValueError("config must be a non-empty list or contain a non-empty positions list")
    return [dict(item) for item in positions]


def _probe_entry(item: dict[str, Any], client: ChessDbClient | None) -> dict[str, Any]:
    name = str(item.get("name", f"case_{item.get('index', '')}")).rstrip("_")
    state = _state_from_item(item)
    judgement = state.rule_judgement()
    query = build_rule_query_from_state(state)
    legal_moves = tuple(sorted(move.to_uci() for move in state.legal_moves()))
    entry = {
        "name": name,
        "fen": state.to_fen(),
        "position_command": state.to_uci_position(),
        "local_result": state.game_result(),
        "rule_judgement": {
            "result": judgement.result,
            "reason": judgement.reason,
            "adjudicated": judgement.adjudicated,
        },
        "repetition": _repetition_dict(state.repetition_info()),
        "chessdb_rule_query": {
            "fen": query.fen,
            "movelist": query.movelist,
            "reptimes": query.reptimes,
        },
        "legal_moves": legal_moves,
        "chessdb": None,
        "rule_comparison": None,
    }
    if client is not None and len(query.movelist) < 4:
        entry["chessdb"] = {
            "status": "not_applicable",
            "rule": None,
            "rule_summary": None,
            "rule_results": [],
            "raw_text": "",
            "error": "queryrule requires at least four historical moves",
        }
        entry["rule_comparison"] = _not_applicable_rule_comparison(judgement, legal_moves)
    elif client is not None:
        response = client.query_rule(query.fen, movelist=query.movelist, reptimes=query.reptimes)
        entry["chessdb"] = {
            "status": response.status.value,
            "rule": response.rule,
            "rule_summary": _chessdb_rule_summary(response),
            "rule_results": [asdict(result) for result in response.rule_results],
            "raw_text": response.raw_text,
            "error": response.error,
        }
        entry["rule_comparison"] = _rule_comparison(state, response, legal_moves)
    return entry


def _state_from_item(item: dict[str, Any]):
    has_position = "position" in item
    has_fen = "fen" in item
    if has_position == has_fen:
        raise ValueError("each corpus item must contain exactly one of position or fen")
    if has_position:
        return GameState.from_uci_position(str(item["position"]))
    return GameState.from_position(Position.from_fen(str(item["fen"])))


def _comparison_summary(entries: list[dict[str, Any]]) -> dict[str, int]:
    summary = {
        "not_run": 0,
        "queryrule_not_applicable": 0,
        "chessdb_unavailable": 0,
        "candidate_flags_agree": 0,
        "candidate_flags_disagree": 0,
        "candidate_flags_unverified": 0,
        "legal_move_rule_conflicts": 0,
    }
    for entry in entries:
        comparison = entry.get("rule_comparison")
        if comparison is None:
            summary["not_run"] += 1
            continue
        category = str(comparison.get("category", "chessdb_unavailable"))
        summary[category] = summary.get(category, 0) + 1
        if comparison.get("legal_move_rule_conflict"):
            summary["legal_move_rule_conflicts"] += 1
    return summary


def _run_config(args: argparse.Namespace, positions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "config": args.config,
        "chessdb": bool(args.chessdb),
        "timeout": args.timeout,
        "fail_on_legal_move_rule_conflict": bool(args.fail_on_legal_move_rule_conflict),
        "fail_on_flag_disagreement": bool(args.fail_on_flag_disagreement),
        "fail_on_chessdb_unavailable": bool(args.fail_on_chessdb_unavailable),
        "positions": [_position_config(index, item) for index, item in enumerate(positions, start=1)],
    }


def _gate_summary(args: argparse.Namespace, summary: dict[str, int]) -> dict[str, Any]:
    reasons: list[str] = []
    legal_conflicts = int(summary.get("legal_move_rule_conflicts", 0))
    flag_disagreements = int(summary.get("candidate_flags_disagree", 0)) + int(
        summary.get("candidate_flags_unverified", 0)
    )
    chessdb_unavailable = int(summary.get("chessdb_unavailable", 0))
    if getattr(args, "fail_on_legal_move_rule_conflict", False) and legal_conflicts:
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
        "legal_move_rule_conflicts": legal_conflicts,
        "flag_disagreements": flag_disagreements,
        "chessdb_unavailable": chessdb_unavailable,
        "reasons": reasons,
    }


def _position_config(index: int, item: dict[str, Any]) -> dict[str, Any]:
    state = _state_from_item(item)
    return {
        "name": str(item.get("name", f"case_{index}")),
        "fen": state.to_fen(),
        "position": state.to_uci_position(),
    }


def _config_digest(config: dict[str, Any]) -> dict[str, str]:
    canonical = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "algorithm": "sha256",
        "scope": "full",
        "value": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


if __name__ == "__main__":
    raise SystemExit(main())
