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

from xiangqi_core import GameState, Move, Position
from xiangqi_evaluators import (
    UciEngine,
    extract_go_searchmoves,
    extract_pv_moves,
    split_engine_command,
    starts_with_uci_token,
)


REPORT_SCHEMA_VERSION = 2


@dataclass(frozen=True, slots=True)
class ProbeCase:
    name: str
    state: GameState
    position_command: str


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe UCI search output against the local Xiangqi rules core."
    )
    parser.add_argument("--engine", required=True, help="UCI engine command, e.g. Pikafish")
    parser.add_argument("--config", help="JSON corpus with positions to probe")
    parser.add_argument("--position", help="UCI position command, e.g. 'position startpos moves h2e2'")
    parser.add_argument("--fen", default=Position.START_FEN)
    parser.add_argument("--go", default="go depth 1", help="UCI go command sent for each case")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--option",
        action="append",
        default=None,
        metavar="NAME=VALUE",
        help="Optional UCI option forwarded before probing; repeatable.",
    )
    parser.add_argument(
        "--require-pv",
        action="store_true",
        help="Fail when the engine does not emit any PV root move before bestmove.",
    )
    parser.add_argument("--report")
    args = parser.parse_args()
    if args.config and args.position:
        raise ValueError("--config cannot be combined with --position")
    if not starts_with_uci_token(args.go, "go"):
        raise ValueError("--go must start with 'go'")
    if args.timeout <= 0:
        raise ValueError("--timeout must be positive")

    cases = (
        _cases_from_config(args.config)
        if args.config
        else [_single_case(args.fen, args.position)]
    )
    options = tuple(_parse_uci_option(item) for item in (args.option or ()))
    config = _run_config(args, cases, options)

    try:
        with UciEngine(split_engine_command(args.engine), timeout=args.timeout) as engine:
            engine.initialize()
            for name, value in options:
                engine.set_option(name, value)
            engine.new_game()
            entries = []
            for case in cases:
                try:
                    entries.append(
                        _probe_case(
                            engine,
                            case,
                            go_command=args.go,
                            require_pv=args.require_pv,
                        )
                    )
                except Exception as exc:
                    entries.append(
                        _engine_error_case(
                            case,
                            exc,
                            require_pv=args.require_pv,
                        )
                    )
        output = _probe_output(config, entries, phase="probe", completed=len(entries))
    except Exception as exc:
        entries = [
            _engine_error_case(case, exc, phase="initialize", require_pv=args.require_pv)
            for case in cases
        ]
        output = _probe_output(config, entries, phase="initialize", completed=0, error=str(exc))

    if len(entries) == 1:
        output.update(entries[0])

    _emit(output, args.report)
    return 0 if output["valid"] else 1


def _parse_uci_option(text: str) -> tuple[str, str]:
    if "=" not in text:
        raise ValueError("--option must use NAME=VALUE")
    name, value = text.split("=", 1)
    name = name.strip()
    value = value.strip()
    if not name or not value:
        raise ValueError("--option name and value must be non-empty")
    return name, value


def _single_case(fen: str, position_command: str | None) -> ProbeCase:
    if position_command:
        state = GameState.from_uci_position(position_command)
        return ProbeCase("single", state, state.to_uci_position())
    state = GameState.from_position(Position.from_fen(fen, strict=False))
    return ProbeCase("single", state, f"position fen {state.to_fen()}")


def _cases_from_config(config_path: str) -> list[ProbeCase]:
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    positions = config.get("positions") if isinstance(config, dict) else config
    if not isinstance(positions, list) or not positions:
        raise ValueError("config must be a non-empty list or contain a non-empty positions list")
    return [_case_from_item(index, item) for index, item in enumerate(positions, start=1)]


def _case_from_item(index: int, item: Any) -> ProbeCase:
    if not isinstance(item, dict):
        raise ValueError("each search probe corpus item must be an object")
    has_position = "position" in item
    has_fen = "fen" in item
    if has_position == has_fen:
        raise ValueError("each search probe corpus item must contain exactly one of position or fen")
    name = str(item.get("name", f"case_{index}"))
    state = (
        GameState.from_uci_position(str(item["position"]))
        if has_position
        else GameState.from_position(Position.from_fen(str(item["fen"]), strict=False))
    )
    return ProbeCase(name=name, state=state, position_command=state.to_uci_position())


def _probe_case(
    engine: UciEngine,
    case: ProbeCase,
    *,
    go_command: str,
    require_pv: bool,
) -> dict[str, Any]:
    engine.set_position_command(case.position_command)
    bestmove, lines = engine.go(go_command)
    searchmoves = extract_go_searchmoves(go_command)
    bestmove_validation = _validate_move(case.state, bestmove, searchmoves=searchmoves)
    pv_root_moves = extract_pv_moves(lines)
    pv_lines = _extract_pv_lines(lines)
    illegal_pv_roots = [
        _pv_illegal_detail(case.state, move)
        for move in pv_root_moves
        if _validate_move(case.state, move)["error"] is not None
    ]
    searchmoves_pv_root_violations = [
        _pv_searchmoves_detail(case.state, move, searchmoves)
        for move in pv_root_moves
        if _pv_searchmoves_detail(case.state, move, searchmoves) is not None
    ]
    illegal_pv_lines = [
        illegal
        for illegal in (_pv_line_illegal_detail(case.state, pv_line) for pv_line in pv_lines)
        if illegal is not None
    ]
    pv_available = bool(pv_root_moves)
    failure_reasons: list[str] = []
    if bestmove_validation["error"] is not None:
        if bestmove_validation["searchmoves_error"] is not None:
            failure_reasons.append("bestmove_searchmoves_violation")
        else:
            failure_reasons.append("bestmove_illegal")
    if illegal_pv_roots:
        failure_reasons.append("pv_root_illegal")
    if searchmoves_pv_root_violations:
        failure_reasons.append("pv_root_searchmoves_violation")
    if illegal_pv_lines:
        failure_reasons.append("pv_line_illegal")
    if require_pv and not pv_available:
        failure_reasons.append("pv_missing")
    judgement = case.state.rule_judgement()
    return {
        "valid": not failure_reasons,
        "name": case.name,
        "fen": case.state.to_fen(),
        "position_command": case.position_command,
        "go_command": go_command,
        "local_result": case.state.game_result(),
        "rule_judgement": {
            "result": judgement.result,
            "reason": judgement.reason,
            "adjudicated": judgement.adjudicated,
        },
        "bestmove": bestmove,
        "bestmove_valid": bestmove_validation["error"] is None,
        "bestmove_error": bestmove_validation["error"],
        "bestmove_searchmoves_valid": bestmove_validation["searchmoves_error"] is None,
        "bestmove_searchmoves_error": bestmove_validation["searchmoves_error"],
        "legal_move_count": bestmove_validation["legal_move_count"],
        "legal_moves_sample": bestmove_validation["legal_moves_sample"],
        "searchmoves": searchmoves,
        "searchmoves_legal_move_count": bestmove_validation["searchmoves_legal_move_count"],
        "searchmoves_legal_moves_sample": bestmove_validation["searchmoves_legal_moves_sample"],
        "pv_root_moves": pv_root_moves,
        "pv_lines": pv_lines,
        "pv_available": pv_available,
        "require_pv": require_pv,
        "illegal_pv_roots": illegal_pv_roots,
        "searchmoves_pv_root_violations": searchmoves_pv_root_violations,
        "illegal_pv_lines": illegal_pv_lines,
        "failure_reasons": failure_reasons,
        "engine_lines": tuple(lines),
    }


def _validate_move(
    state: GameState,
    move_text: str,
    *,
    searchmoves: tuple[str, ...] = (),
) -> dict[str, Any]:
    legal_by_uci = {move.to_uci(): move for move in state.legal_moves()}
    legal_moves_sample = tuple(sorted(legal_by_uci)[:8])
    searchmove_set = set(searchmoves)
    legal_searchmoves = tuple(sorted(move for move in searchmove_set if move in legal_by_uci))
    searchmoves_error: str | None = None
    if move_text == "0000":
        if not legal_by_uci:
            return {
                "move": move_text,
                "error": None,
                "searchmoves_error": None,
                "legal_move_count": 0,
                "legal_moves_sample": (),
                "searchmoves_legal_move_count": len(legal_searchmoves),
                "searchmoves_legal_moves_sample": legal_searchmoves[:8],
            }
        if searchmoves and not legal_searchmoves:
            return {
                "move": move_text,
                "error": None,
                "searchmoves_error": None,
                "legal_move_count": len(legal_by_uci),
                "legal_moves_sample": legal_moves_sample,
                "searchmoves_legal_move_count": 0,
                "searchmoves_legal_moves_sample": (),
            }
        if searchmoves:
            searchmoves_error = "null move returned while legal searchmoves exist"
        return {
            "move": move_text,
            "error": searchmoves_error or "null move returned while legal moves exist",
            "searchmoves_error": searchmoves_error,
            "legal_move_count": len(legal_by_uci),
            "legal_moves_sample": legal_moves_sample,
            "searchmoves_legal_move_count": len(legal_searchmoves),
            "searchmoves_legal_moves_sample": legal_searchmoves[:8],
        }
    try:
        move = Move.from_uci(move_text)
    except Exception as exc:
        return {
            "move": move_text,
            "error": str(exc),
            "searchmoves_error": None,
            "legal_move_count": len(legal_by_uci),
            "legal_moves_sample": legal_moves_sample,
            "searchmoves_legal_move_count": len(legal_searchmoves),
            "searchmoves_legal_moves_sample": legal_searchmoves[:8],
        }
    if move.to_uci() not in legal_by_uci:
        return {
            "move": move_text,
            "error": "move is not legal in the local rules core",
            "searchmoves_error": None,
            "legal_move_count": len(legal_by_uci),
            "legal_moves_sample": legal_moves_sample,
            "searchmoves_legal_move_count": len(legal_searchmoves),
            "searchmoves_legal_moves_sample": legal_searchmoves[:8],
        }
    if searchmoves and move.to_uci() not in searchmove_set:
        searchmoves_error = "move is outside go searchmoves"
    return {
        "move": move_text,
        "error": searchmoves_error,
        "searchmoves_error": searchmoves_error,
        "legal_move_count": len(legal_by_uci),
        "legal_moves_sample": legal_moves_sample,
        "searchmoves_legal_move_count": len(legal_searchmoves),
        "searchmoves_legal_moves_sample": legal_searchmoves[:8],
    }


def _pv_illegal_detail(state: GameState, move_text: str) -> dict[str, Any]:
    validation = _validate_move(state, move_text)
    return {
        "move": move_text,
        "error": validation["error"],
        "legal_move_count": validation["legal_move_count"],
        "legal_moves_sample": validation["legal_moves_sample"],
    }


def _pv_searchmoves_detail(
    state: GameState,
    move_text: str,
    searchmoves: tuple[str, ...],
) -> dict[str, Any] | None:
    if not searchmoves:
        return None
    validation = _validate_move(state, move_text, searchmoves=searchmoves)
    if validation["searchmoves_error"] is None:
        return None
    return {
        "move": move_text,
        "error": validation["searchmoves_error"],
        "searchmoves": searchmoves,
        "searchmoves_legal_move_count": validation["searchmoves_legal_move_count"],
        "searchmoves_legal_moves_sample": validation["searchmoves_legal_moves_sample"],
    }


def _extract_pv_lines(lines: tuple[str, ...]) -> tuple[dict[str, Any], ...]:
    latest_by_multipv: dict[int, tuple[str, ...]] = {}
    for line in lines:
        tokens = line.split()
        if not _is_search_info_pv_tokens(tokens):
            continue
        pv_index = tokens.index("pv")
        if pv_index + 1 >= len(tokens):
            continue
        multipv = 1
        if "multipv" in tokens:
            multipv_index = tokens.index("multipv")
            if multipv_index + 1 < len(tokens):
                try:
                    multipv = int(tokens[multipv_index + 1])
                except ValueError:
                    multipv = 1
        latest_by_multipv[multipv] = tuple(tokens[pv_index + 1 :])
    return tuple(
        {"multipv": multipv, "moves": latest_by_multipv[multipv]}
        for multipv in sorted(latest_by_multipv)
    )


def _is_search_info_pv_tokens(tokens: list[str]) -> bool:
    return bool(tokens) and tokens[0] == "info" and len(tokens) > 1 and tokens[1] != "string" and "pv" in tokens


def _pv_line_illegal_detail(
    state: GameState,
    pv_line: dict[str, Any],
) -> dict[str, Any] | None:
    current = state
    prefix: list[str] = []
    for ply, move_text in enumerate(pv_line["moves"], start=1):
        judgement = current.rule_judgement()
        if judgement.result is not None:
            if move_text == "0000":
                return None
            return {
                "multipv": pv_line["multipv"],
                "ply": ply,
                "move": move_text,
                "prefix": tuple(prefix),
                "error": "PV continues after local rule result",
                "local_result": judgement.result,
                "rule_reason": judgement.reason,
                "adjudicated": judgement.adjudicated,
                "legal_move_count": 0,
                "legal_moves_sample": (),
            }
        validation = _validate_move(current, move_text)
        if validation["error"] is not None:
            if ply == 1:
                return None
            return {
                "multipv": pv_line["multipv"],
                "ply": ply,
                "move": move_text,
                "prefix": tuple(prefix),
                "error": validation["error"],
                "legal_move_count": validation["legal_move_count"],
                "legal_moves_sample": validation["legal_moves_sample"],
            }
        if move_text == "0000":
            return None
        current = current.make_move(Move.from_uci(move_text))
        prefix.append(move_text)
    return None


def _engine_error_case(
    case: ProbeCase,
    exc: Exception,
    *,
    phase: str = "probe",
    require_pv: bool = False,
) -> dict[str, Any]:
    judgement = case.state.rule_judgement()
    validation = _validate_move(case.state, "0000")
    return {
        "valid": False,
        "name": case.name,
        "fen": case.state.to_fen(),
        "position_command": case.position_command,
        "go_command": None,
        "local_result": case.state.game_result(),
        "rule_judgement": {
            "result": judgement.result,
            "reason": judgement.reason,
            "adjudicated": judgement.adjudicated,
        },
        "bestmove": None,
        "bestmove_valid": False,
        "bestmove_error": None,
        "bestmove_searchmoves_valid": True,
        "bestmove_searchmoves_error": None,
        "legal_move_count": validation["legal_move_count"],
        "legal_moves_sample": validation["legal_moves_sample"],
        "searchmoves": (),
        "searchmoves_legal_move_count": 0,
        "searchmoves_legal_moves_sample": (),
        "pv_root_moves": (),
        "pv_lines": (),
        "pv_available": False,
        "require_pv": require_pv,
        "illegal_pv_roots": [],
        "searchmoves_pv_root_violations": [],
        "illegal_pv_lines": [],
        "failure_reasons": ["engine_error"],
        "phase": phase,
        "engine_error": str(exc),
        "engine_lines": (),
    }


def _probe_output(
    config: dict[str, Any],
    entries: list[dict[str, Any]],
    *,
    phase: str,
    completed: int,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "report_type": "uci_search_probe",
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "config": config,
        "config_digest": _config_digest(config),
        "valid": all(entry["valid"] for entry in entries),
        "count": len(entries),
        "completed": completed,
        "failures": sum(1 for entry in entries if not entry["valid"]),
        "phase": phase,
        "summary": _probe_summary(entries),
        "entries": entries,
        "error": error,
    }


def _probe_summary(entries: list[dict[str, Any]]) -> dict[str, Any]:
    invalid_cases = [entry["name"] for entry in entries if not entry["valid"]]
    return {
        "illegal_bestmoves": sum(
            1 for entry in entries if "bestmove_illegal" in entry.get("failure_reasons", ())
        ),
        "illegal_pv_roots": sum(
            1 for entry in entries if "pv_root_illegal" in entry.get("failure_reasons", ())
        ),
        "searchmoves_bestmove_violations": sum(
            1 for entry in entries if "bestmove_searchmoves_violation" in entry.get("failure_reasons", ())
        ),
        "searchmoves_pv_root_violations": sum(
            1 for entry in entries if "pv_root_searchmoves_violation" in entry.get("failure_reasons", ())
        ),
        "illegal_pv_lines": sum(
            1 for entry in entries if "pv_line_illegal" in entry.get("failure_reasons", ())
        ),
        "missing_pv": sum(
            1 for entry in entries if "pv_missing" in entry.get("failure_reasons", ())
        ),
        "engine_errors": sum(
            1 for entry in entries if "engine_error" in entry.get("failure_reasons", ())
        ),
        "invalid_cases": invalid_cases,
    }


def _run_config(
    args: argparse.Namespace,
    cases: list[ProbeCase],
    options: tuple[tuple[str, str], ...],
) -> dict[str, Any]:
    return {
        "engine": args.engine,
        "config": args.config,
        "position": args.position,
        "fen": None if args.position or args.config else args.fen,
        "go": args.go,
        "timeout": args.timeout,
        "options": [{"name": name, "value": value} for name, value in options],
        "require_pv": bool(args.require_pv),
        "starts": [_case_config(case) for case in cases],
    }


def _case_config(case: ProbeCase) -> dict[str, Any]:
    return {
        "name": case.name,
        "fen": case.state.to_fen(),
        "position": case.position_command,
    }


def _config_digest(config: dict[str, Any]) -> dict[str, str]:
    canonical = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "algorithm": "sha256",
        "scope": "full",
        "value": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def _emit(output: dict[str, Any], report: str | None) -> None:
    text = json.dumps(output, ensure_ascii=False, indent=2)
    if report:
        report_path = Path(report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(text + "\n", encoding="utf-8")
    print(text)


def _cli_main() -> int:
    try:
        return main()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_cli_main())
