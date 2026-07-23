from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from xiangqi_core import Color, GameState

REPORT_SCHEMA_VERSION = 1
_MATE_LINE = re.compile(r"\bscore mate (-?\d+).*?\btime (\d+)(?:\s+pv\s+(\S+))?")


def mine_reports(
    report_paths: list[Path],
    *,
    min_search_ms: int,
    max_mate_score: int,
    engine_name: str = "pikafish",
) -> dict[str, Any]:
    if min_search_ms < 0:
        raise ValueError("min_search_ms must be non-negative")
    if max_mate_score <= 0:
        raise ValueError("max_mate_score must be positive")

    records = 0
    candidates: list[dict[str, Any]] = []
    source_reports: list[dict[str, str]] = []
    for report_path in report_paths:
        raw_report = report_path.read_bytes()
        report = json.loads(raw_report)
        if report.get("valid") is not True:
            raise ValueError(f"source report is invalid: {report_path}")
        source_report_sha256 = hashlib.sha256(raw_report).hexdigest()
        source_reports.append(
            {
                "path": str(report_path),
                "sha256": source_report_sha256,
                "report_type": str(report.get("report_type", "")),
            }
        )
        for game in report.get("games", ()):
            if not isinstance(game, dict):
                continue
            if game.get("valid") is not True:
                raise ValueError(f"source report contains an invalid game: {report_path}")
            for record in game.get("records", ()):
                if not isinstance(record, dict):
                    continue
                records += 1
                if record.get("engine") != engine_name:
                    continue
                candidate = _candidate_from_record(
                    report_path,
                    game,
                    record,
                    source_report_sha256=source_report_sha256,
                    min_search_ms=min_search_ms,
                    max_mate_score=max_mate_score,
                )
                if candidate is not None:
                    candidates.append(candidate)

    return {
        "report_type": "pikafish_mate_mining",
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "config": {
            "reports": [str(path) for path in report_paths],
            "source_reports": source_reports,
            "engine_name": engine_name,
            "min_search_ms": min_search_ms,
            "max_mate_score": max_mate_score,
        },
        "summary": {
            "reports": len(report_paths),
            "records": records,
            "candidates": len(candidates),
        },
        "candidates": candidates,
    }


def _candidate_from_record(
    report_path: Path,
    game: dict[str, Any],
    record: dict[str, Any],
    *,
    source_report_sha256: str,
    min_search_ms: int,
    max_mate_score: int,
) -> dict[str, Any] | None:
    position_text = record.get("position")
    if not isinstance(position_text, str):
        return None
    for line in record.get("engine_lines", ()):
        if not isinstance(line, str):
            continue
        match = _MATE_LINE.search(line)
        if match is None:
            continue
        mate_score = int(match.group(1))
        search_ms = int(match.group(2))
        if abs(mate_score) > max_mate_score or search_ms < min_search_ms:
            continue
        state = GameState.from_uci_position(position_text)
        target = "red" if state.side_to_move is Color.RED else "black"
        return {
            "source_report": str(report_path),
            "source_report_sha256": source_report_sha256,
            "source_game": game.get("game"),
            "source_start": game.get("start_name"),
            "source_ply": record.get("ply"),
            "engine": record.get("engine"),
            "target": target,
            "mate_score": mate_score,
            "first_mate_time_ms": search_ms,
            "pv_root_move": match.group(3),
            "position": state.to_uci_position(),
            "fen": state.to_fen(),
            "source_line": line,
        }
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract replayable short-mate proof candidates from Pikafish match reports."
    )
    parser.add_argument("--report", action="append", required=True, metavar="PATH")
    parser.add_argument("--output")
    parser.add_argument("--engine-name", default="pikafish")
    parser.add_argument("--min-search-ms", type=int, default=100)
    parser.add_argument("--max-mate-score", type=int, default=5)
    args = parser.parse_args()

    output = mine_reports(
        [Path(path) for path in args.report],
        min_search_ms=args.min_search_ms,
        max_mate_score=args.max_mate_score,
        engine_name=args.engine_name,
    )
    text = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text + "\n", encoding="utf-8")
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
