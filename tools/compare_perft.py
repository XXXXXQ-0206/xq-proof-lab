from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from xiangqi_core import GameState, Position
from xiangqi_evaluators import UciEngine, split_engine_command
from perft import perft


@dataclass(frozen=True, slots=True)
class PerftCase:
    name: str
    position: Position
    position_command: str
    depth: int


REPORT_SCHEMA_VERSION = 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare local perft with a UCI Xiangqi engine.")
    parser.add_argument("--engine", required=True, help="UCI engine command, e.g. path to Pikafish")
    parser.add_argument("--config", help="JSON corpus with positions to compare")
    parser.add_argument("--position", help="UCI position command, e.g. 'position startpos moves h2e2'")
    parser.add_argument("--fen", default=Position.START_FEN)
    parser.add_argument("--depth", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--require-root-divide",
        action="store_true",
        help="Fail when the external UCI engine does not emit root divide lines before Nodes searched.",
    )
    parser.add_argument("--report")
    args = parser.parse_args()
    if args.config and args.position:
        raise ValueError("--config cannot be combined with --position")
    _parse_depth(args.depth, "--depth")
    if args.timeout <= 0:
        raise ValueError("--timeout must be positive")

    cases = (
        _cases_from_config(args.config, args.depth)
        if args.config
        else [_single_case(args.fen, args.depth, args.position)]
    )
    command = split_engine_command(args.engine)
    config = _run_config(args, cases)
    try:
        with UciEngine(command, timeout=args.timeout) as engine:
            engine.initialize()
            entries = []
            for case in cases:
                try:
                    entries.append(
                        _compare_case(
                            engine,
                            case,
                            require_root_divide=args.require_root_divide,
                        )
                    )
                except Exception as exc:
                    entries.append(
                        _engine_error_case(
                            case,
                            exc,
                            require_root_divide=args.require_root_divide,
                        )
                    )
        output = _compare_output(config, entries, phase="compare", completed=len(entries))
    except Exception as exc:
        entries = [
            _engine_error_case(
                case,
                exc,
                phase="initialize",
                require_root_divide=args.require_root_divide,
            )
            for case in cases
        ]
        output = _compare_output(config, entries, phase="initialize", completed=0, error=str(exc))

    if len(entries) == 1:
        output.update(entries[0])

    _emit(output, args.report)
    return 0 if output["valid"] else 1


def _single_case(fen: str, depth: int, position_command: str | None = None) -> PerftCase:
    if position_command:
        state = GameState.from_uci_position(position_command)
        return PerftCase(
            name="single",
            position=state.position,
            position_command=state.to_uci_position(),
            depth=depth,
        )
    position = Position.from_fen(fen)
    return PerftCase(
        name="single",
        position=position,
        position_command=f"position fen {position.to_fen()}",
        depth=depth,
    )


def _cases_from_config(config_path: str, cli_depth: int) -> list[PerftCase]:
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    defaults = config.get("defaults", {}) if isinstance(config, dict) else {}
    positions = config.get("positions") if isinstance(config, dict) else config
    if not isinstance(positions, list) or not positions:
        raise ValueError("config must be a non-empty list or contain a non-empty positions list")
    default_depth = _parse_depth(defaults.get("depth", cli_depth), "defaults.depth")
    return [_case_from_item(index, item, default_depth) for index, item in enumerate(positions, start=1)]


def _case_from_item(index: int, item: Any, default_depth: int) -> PerftCase:
    if not isinstance(item, dict):
        raise ValueError("each perft corpus item must be an object")
    has_position = "position" in item
    has_fen = "fen" in item
    if has_position == has_fen:
        raise ValueError("each perft corpus item must contain exactly one of position or fen")
    name = str(item.get("name", f"case_{index}"))
    depth = _parse_depth(item.get("depth", default_depth), f"perft corpus item {name!r} depth")
    if has_position:
        state = GameState.from_uci_position(str(item["position"]))
        return PerftCase(
            name=name,
            position=state.position,
            position_command=state.to_uci_position(),
            depth=depth,
        )
    position = Position.from_fen(str(item["fen"]))
    return PerftCase(
        name=name,
        position=position,
        position_command=f"position fen {position.to_fen()}",
        depth=depth,
    )


def _parse_depth(value: Any, label: str) -> int:
    try:
        depth = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer") from exc
    if depth < 0:
        raise ValueError(f"{label} must be non-negative")
    return depth


def _compare_case(
    engine: UciEngine,
    case: PerftCase,
    *,
    require_root_divide: bool = False,
) -> dict[str, Any]:
    local_nodes = perft(case.position, case.depth)
    local_root_nodes = _local_root_nodes(case.position, case.depth)
    engine.set_position_command(case.position_command)
    external = engine.go_perft(case.depth)
    root_mismatches = _root_mismatches(local_root_nodes, external.divide)
    root_divide_applicable = bool(local_root_nodes)
    root_divide_available = bool(external.divide)
    root_divide_valid = None if not root_divide_available else not root_mismatches
    if require_root_divide and root_divide_applicable and not root_divide_available:
        root_divide_valid = False
    total_valid = local_nodes == external.nodes
    failure_reasons = _failure_reasons(
        total_valid,
        root_divide_valid,
        root_divide_applicable=root_divide_applicable,
        root_divide_available=root_divide_available,
        require_root_divide=require_root_divide,
    )
    return {
        "valid": total_valid and (root_divide_valid is not False),
        "name": case.name,
        "fen": case.position.to_fen(),
        "position_command": case.position_command,
        "depth": case.depth,
        "local_nodes": local_nodes,
        "engine_nodes": external.nodes,
        "local_root_nodes": local_root_nodes,
        "engine_root_nodes": external.divide,
        "root_divide_applicable": root_divide_applicable,
        "root_divide_available": root_divide_available,
        "require_root_divide": require_root_divide,
        "root_divide_valid": root_divide_valid,
        "root_mismatches": root_mismatches,
        "failure_reasons": failure_reasons,
        "engine_raw_lines": external.raw_lines,
    }


def _engine_error_case(
    case: PerftCase,
    exc: Exception,
    *,
    phase: str = "compare",
    require_root_divide: bool = False,
) -> dict[str, Any]:
    local_nodes = perft(case.position, case.depth)
    local_root_nodes = _local_root_nodes(case.position, case.depth)
    root_divide_applicable = bool(local_root_nodes)
    return {
        "valid": False,
        "name": case.name,
        "fen": case.position.to_fen(),
        "position_command": case.position_command,
        "depth": case.depth,
        "local_nodes": local_nodes,
        "engine_nodes": None,
        "local_root_nodes": local_root_nodes,
        "engine_root_nodes": {},
        "root_divide_applicable": root_divide_applicable,
        "root_divide_available": False,
        "require_root_divide": require_root_divide,
        "root_divide_valid": None,
        "root_mismatches": [],
        "failure_reasons": ["engine_error"],
        "phase": phase,
        "engine_error": str(exc),
        "engine_raw_lines": [],
    }


def _compare_output(
    config: dict[str, Any],
    entries: list[dict[str, Any]],
    *,
    phase: str,
    completed: int,
    error: str | None = None,
) -> dict[str, Any]:
    output = {
        "report_type": "perft_compare",
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "config": config,
        "config_digest": _config_digest(config),
        "valid": all(entry["valid"] for entry in entries),
        "count": len(entries),
        "completed": completed,
        "failures": sum(1 for entry in entries if not entry["valid"]),
        "phase": phase,
        "summary": _comparison_summary(entries),
        "entries": entries,
        "error": error,
    }
    return output


def _local_root_nodes(position: Position, depth: int) -> dict[str, int]:
    if depth <= 0:
        return {}
    return {
        move.to_uci(): perft(position.make_move(move), depth - 1)
        for move in sorted(position.legal_moves(), key=lambda candidate: candidate.to_uci())
    }


def _root_mismatches(
    local_root_nodes: dict[str, int],
    engine_root_nodes: dict[str, int],
) -> list[dict[str, Any]]:
    if not engine_root_nodes:
        return []
    mismatches: list[dict[str, Any]] = []
    for move in sorted(set(local_root_nodes) | set(engine_root_nodes)):
        local = local_root_nodes.get(move)
        external = engine_root_nodes.get(move)
        if local == external:
            continue
        mismatches.append(
            {
                "move": move,
                "local_nodes": local,
                "engine_nodes": external,
            }
        )
    return mismatches


def _failure_reasons(
    total_valid: bool,
    root_divide_valid: bool | None,
    *,
    root_divide_applicable: bool,
    root_divide_available: bool,
    require_root_divide: bool,
) -> list[str]:
    reasons: list[str] = []
    if not total_valid:
        reasons.append("node_mismatch")
    if require_root_divide and root_divide_applicable and not root_divide_available:
        reasons.append("root_divide_unavailable")
    if root_divide_available and root_divide_valid is False:
        reasons.append("root_divide_mismatch")
    return reasons


def _comparison_summary(entries: list[dict[str, Any]]) -> dict[str, Any]:
    invalid_cases = [entry["name"] for entry in entries if not entry["valid"]]
    return {
        "node_mismatches": sum(
            1 for entry in entries if "node_mismatch" in entry.get("failure_reasons", [])
        ),
        "root_divide_mismatches": sum(
            1
            for entry in entries
            if "root_divide_mismatch" in entry.get("failure_reasons", [])
        ),
        "root_divide_unavailable": sum(
            1
            for entry in entries
            if entry.get("root_divide_applicable", True)
            and not entry.get("root_divide_available", False)
            and "engine_error" not in entry.get("failure_reasons", [])
        ),
        "engine_errors": sum(
            1 for entry in entries if "engine_error" in entry.get("failure_reasons", [])
        ),
        "invalid_cases": invalid_cases,
    }


def _emit(output: dict[str, Any], report: str | None) -> None:
    text = json.dumps(output, ensure_ascii=False, indent=2)
    if report:
        report_path = Path(report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(text + "\n", encoding="utf-8")
    print(text)


def _run_config(args: argparse.Namespace, cases: list[PerftCase]) -> dict[str, Any]:
    return {
        "engine": args.engine,
        "config": args.config,
        "position": args.position,
        "fen": None if args.position or args.config else args.fen,
        "depth": args.depth,
        "timeout": args.timeout,
        "require_root_divide": bool(args.require_root_divide),
        "starts": [_case_config(case) for case in cases],
    }


def _case_config(case: PerftCase) -> dict[str, Any]:
    return {
        "name": case.name,
        "fen": case.position.to_fen(),
        "position": case.position_command,
        "depth": case.depth,
    }


def _config_digest(config: dict[str, Any]) -> dict[str, str]:
    canonical = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "algorithm": "sha256",
        "scope": "full",
        "value": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def _cli_main() -> int:
    try:
        return main()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_cli_main())
