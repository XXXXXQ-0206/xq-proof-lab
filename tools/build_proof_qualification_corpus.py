from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from xiangqi_core import Color, GameState


REPORT_SCHEMA_VERSION = 1
SELECTION_RULE_VERSION = "natural_tactical_capture_or_check_v1"


def build_corpus(
    report_paths: list[Path],
    *,
    split: str,
    count: int,
) -> dict[str, Any]:
    if split not in {"development", "holdout"}:
        raise ValueError("split must be development or holdout")
    if count <= 0 or count % 2:
        raise ValueError("count must be positive and even")
    if not report_paths:
        raise ValueError("at least one report path is required")

    source_reports: list[dict[str, str]] = []
    candidates: dict[str, dict[str, Any]] = {}
    records_seen = 0
    natural_records = 0
    for report_path in sorted(set(report_paths), key=lambda path: str(path)):
        raw_report = report_path.read_bytes()
        report = json.loads(raw_report)
        source_hash = _sha256_bytes(raw_report)
        source_reports.append(
            {
                "path": str(report_path),
                "sha256": source_hash,
                "report_type": str(report.get("report_type", "")),
            }
        )
        for game_index, game in enumerate(report.get("games", ())):
            if not _is_natural_valid_game(game):
                continue
            for record_index, record in enumerate(game.get("records", ())):
                records_seen += 1
                candidate = _candidate_from_record(
                    record,
                    report_path=report_path,
                    source_hash=source_hash,
                    game=game,
                    game_index=game_index,
                    record_index=record_index,
                )
                if candidate is None:
                    continue
                natural_records += 1
                previous = candidates.get(candidate["position"])
                if previous is None or _source_sort_key(candidate) < _source_sort_key(previous):
                    candidates[candidate["position"]] = candidate

    per_side = count // 2
    selected_by_side: dict[str, list[dict[str, Any]]] = {"red": [], "black": []}
    for side in ("red", "black"):
        eligible = sorted(
            (candidate for candidate in candidates.values() if candidate["side_to_move"] == side),
            key=lambda candidate: candidate["selection_key"],
        )
        if len(eligible) < per_side:
            raise ValueError(
                f"insufficient {side} natural tactical positions: "
                f"need {per_side}, found {len(eligible)}"
            )
        selected_by_side[side] = eligible[:per_side]

    positions = sorted(
        [*selected_by_side["red"], *selected_by_side["black"]],
        key=lambda candidate: candidate["selection_key"],
    )
    config = {
        "split": split,
        "count": count,
        "per_side": per_side,
        "selection_rule_version": SELECTION_RULE_VERSION,
        "source_reports": source_reports,
    }
    corpus_digest = _canonical_sha256({"config": config, "positions": positions})
    return {
        "report_type": "proof_qualification_corpus",
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "split": split,
        "config": config,
        "corpus_digest": corpus_digest,
        "summary": {
            "source_reports": len(source_reports),
            "records_seen": records_seen,
            "natural_tactical_records": natural_records,
            "unique_natural_tactical_positions": len(candidates),
            "selected": len(positions),
            "selected_by_side": {
                side: len(entries) for side, entries in selected_by_side.items()
            },
        },
        "positions": positions,
    }


def build_preflight_config(corpus: dict[str, Any]) -> dict[str, Any]:
    if corpus.get("report_type") != "proof_qualification_corpus":
        raise ValueError("corpus must be a proof qualification corpus")
    positions = corpus.get("positions")
    if not isinstance(positions, list) or not positions:
        raise ValueError("corpus must contain positions")
    preflight_positions: list[dict[str, str]] = []
    for entry in positions:
        if not isinstance(entry, dict):
            raise ValueError("corpus positions must be objects")
        selection_key = entry.get("selection_key")
        position = entry.get("position")
        if not isinstance(selection_key, str) or not isinstance(position, str):
            raise ValueError("corpus positions require selection_key and position")
        preflight_positions.append({"name": selection_key, "position": position})
    return {
        "report_type": "proof_qualification_preflight",
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "source_corpus_digest": corpus.get("corpus_digest"),
        "source_split": corpus.get("split"),
        "positions": preflight_positions,
    }


def _is_natural_valid_game(game: Any) -> bool:
    return (
        isinstance(game, dict)
        and game.get("valid") is True
        and "natural" in game.get("start_tags", ())
        and isinstance(game.get("records"), list)
    )


def _candidate_from_record(
    record: Any,
    *,
    report_path: Path,
    source_hash: str,
    game: dict[str, Any],
    game_index: int,
    record_index: int,
) -> dict[str, Any] | None:
    if not isinstance(record, dict) or not isinstance(record.get("position"), str):
        return None
    try:
        state = GameState.from_uci_position(record["position"])
    except ValueError:
        return None
    legal_moves = state.legal_moves()
    if state.game_result(legal_moves) is not None:
        return None
    tactical_moves = _tactical_moves(state, legal_moves)
    if not tactical_moves:
        return None

    position = state.to_uci_position()
    return {
        "selection_key": _sha256_text(position),
        "position": position,
        "fen": state.to_fen(),
        "history_signature": state.history_signature(),
        "side_to_move": "red" if state.side_to_move is Color.RED else "black",
        "tactical_moves": tactical_moves,
        "source_report": str(report_path),
        "source_report_sha256": source_hash,
        "source_game": game.get("game"),
        "source_game_index": game_index,
        "source_start": game.get("start_name"),
        "source_ply": record.get("ply"),
        "source_record_index": record_index,
    }


def _tactical_moves(state: GameState, legal_moves: list[Any]) -> list[dict[str, Any]]:
    tactical: list[dict[str, Any]] = []
    for move in legal_moves:
        capture = state.position.piece_at(move.to_square) is not None
        child = state.make_move(move, validate=False)
        check = child.position.is_in_check(child.side_to_move)
        if capture or check:
            tactical.append(
                {
                    "move": move.to_uci(),
                    "capture": capture,
                    "check": check,
                }
            )
    return sorted(tactical, key=lambda entry: entry["move"])


def _source_sort_key(candidate: dict[str, Any]) -> tuple[object, ...]:
    return (
        candidate["source_report_sha256"],
        candidate["source_report"],
        str(candidate["source_game"]),
        candidate["source_game_index"],
        str(candidate["source_ply"]),
        candidate["source_record_index"],
    )


def _canonical_sha256(value: Any) -> str:
    return _sha256_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Freeze deterministic natural tactical positions for proof qualification."
    )
    parser.add_argument("--report", action="append", required=True, metavar="PATH")
    parser.add_argument("--split", required=True, choices=("development", "holdout"))
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--preflight-output")
    args = parser.parse_args()

    output = build_corpus(
        [Path(path) for path in args.report],
        split=args.split,
        count=args.count,
    )
    text = json.dumps(output, ensure_ascii=False, indent=2)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text + "\n", encoding="utf-8")
    if args.preflight_output:
        preflight_path = Path(args.preflight_output)
        preflight_path.parent.mkdir(parents=True, exist_ok=True)
        preflight_path.write_text(
            json.dumps(build_preflight_config(output), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(text)
    return 0


def _cli_main() -> int:
    try:
        return main()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_cli_main())
