from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from collections.abc import Sequence
from time import perf_counter
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from xiangqi_core import Color, GameState, Move, Position
from xiangqi_evaluators import (
    UciEngine,
    extract_go_searchmoves,
    split_engine_command,
    starts_with_uci_token,
)


_Z_95 = 1.959963984540054
REPORT_SCHEMA_VERSION = 11
_PROOF_INFO_SOURCES = {
    "proof",
    "proof_store",
    "self_fallback",
    "external_fallback",
    "fallback",
    "emergency",
    "none",
}


@dataclass(frozen=True, slots=True)
class PlyRecord:
    ply: int
    side: str
    engine: str
    position: str
    go_command: str
    bestmove: str
    source: str | None
    status: str | None
    reason: str | None
    nodes: int | None
    max_ply: int | None
    node_limit: int | None
    elapsed_ms: int | None
    external_ordering_elapsed_ms: int | None
    proof_search_elapsed_ms: int | None
    total_search_elapsed_ms: int | None
    red_time_before_ms: int | None
    black_time_before_ms: int | None
    red_time_after_ms: int | None
    black_time_after_ms: int | None
    time_limit_ms: int | None
    time_limit_reached: bool
    resolved_store_hits: int | None
    resolved_store_misses: int | None
    proof_store_saved: bool
    proof_store_save_error: str | None
    proof_artifact_sha256: str | None
    engine_lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MatchStart:
    name: str
    state: GameState
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class UciOption:
    name: str
    value: str


@dataclass(frozen=True, slots=True)
class ClockConfig:
    red_time_ms: int
    black_time_ms: int
    red_increment_ms: int = 0
    black_increment_ms: int = 0
    movestogo: int | None = None


@dataclass(frozen=True, slots=True)
class GameReport:
    game: int
    red_engine: str
    black_engine: str
    start_name: str
    start_fen: str
    start_position: str
    start_moves: tuple[str, ...]
    start_tags: tuple[str, ...]
    result: str
    valid: bool
    reason: str
    plies: int
    moves: tuple[str, ...]
    illegal: dict[str, Any] | None
    records: tuple[PlyRecord, ...]
    red_options: tuple[UciOption, ...] = ()
    black_options: tuple[UciOption, ...] = ()
    forfeit: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class BestmoveValidation:
    move: Move | None
    error: str | None
    legal_move_count: int
    legal_moves_sample: tuple[str, ...]
    searchmoves_error: str | None = None
    searchmoves: tuple[str, ...] = ()
    searchmoves_legal_move_count: int = 0
    searchmoves_legal_moves_sample: tuple[str, ...] = ()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a local-rule-validated UCI Xiangqi match.")
    parser.add_argument("--red", required=True, help="Red UCI engine command")
    parser.add_argument("--black", required=True, help="Black UCI engine command")
    parser.add_argument("--red-name", help="Stable report label for the initial Red engine")
    parser.add_argument("--black-name", help="Stable report label for the initial Black engine")
    parser.add_argument(
        "--red-option",
        action="append",
        default=None,
        metavar="NAME=VALUE",
        help="UCI option forwarded to the initial Red engine; repeatable.",
    )
    parser.add_argument(
        "--black-option",
        action="append",
        default=None,
        metavar="NAME=VALUE",
        help="UCI option forwarded to the initial Black engine; repeatable.",
    )
    parser.add_argument("--games", type=int, default=1)
    parser.add_argument("--alternate-colors", action="store_true")
    parser.add_argument("--fen", default=Position.START_FEN)
    parser.add_argument("--position", help="UCI start position, e.g. 'position startpos moves ...'")
    parser.add_argument("--suite", help="JSON suite of FEN or UCI start positions")
    parser.add_argument(
        "--suite-tag",
        action="append",
        default=None,
        help="Only run suite starts carrying this tag; repeat to allow multiple tags.",
    )
    parser.add_argument("--go", default="go depth 1", help="UCI go command sent each ply")
    parser.add_argument("--red-time-ms", type=int, help="Initial Red/white clock in milliseconds")
    parser.add_argument("--black-time-ms", type=int, help="Initial Black clock in milliseconds")
    parser.add_argument("--red-increment-ms", type=int, default=0)
    parser.add_argument("--black-increment-ms", type=int, default=0)
    parser.add_argument("--movestogo", type=int, help="Optional UCI movestogo sent with clock commands")
    parser.add_argument("--max-plies", type=int, default=200)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--report")
    parser.add_argument(
        "--accept-candidate",
        help="Optional historical diagnostic candidate label for report eligibility fields.",
    )
    parser.add_argument(
        "--accept-baseline",
        help="Optional historical diagnostic baseline label for report eligibility fields.",
    )
    parser.add_argument("--accept-min-games", type=int, default=1)
    parser.add_argument("--accept-min-elo-lower", type=float, default=0.0)
    args = parser.parse_args()

    if args.games <= 0:
        raise ValueError("--games must be positive")
    if args.max_plies <= 0:
        raise ValueError("--max-plies must be positive")
    if not starts_with_uci_token(args.go, "go"):
        raise ValueError("--go must start with 'go'")
    if args.timeout <= 0:
        raise ValueError("--timeout must be positive")
    clock = _clock_config_from_args(args)
    if args.accept_min_games < 0:
        raise ValueError("--accept-min-games must be non-negative")
    if bool(args.accept_candidate) != bool(args.accept_baseline):
        raise ValueError("--accept-candidate and --accept-baseline must be provided together")

    starts = _match_starts(args)
    reports = []
    initial_red_label = args.red_name or args.red
    initial_black_label = args.black_name or args.black
    initial_red_options = _parse_uci_options(args.red_option)
    initial_black_options = _parse_uci_options(args.black_option)
    _validate_closed_acceptance_candidate(
        args.accept_candidate,
        ((initial_red_label, args.red), (initial_black_label, args.black)),
    )
    config = _run_config(
        args,
        starts,
        initial_red_label,
        initial_black_label,
        initial_red_options,
        initial_black_options,
        clock,
    )
    game_number = 1
    for start_index, start in enumerate(starts):
        for index in range(args.games):
            global_pair_index = start_index * args.games + index
            swap = args.alternate_colors and global_pair_index % 2 == 1
            red_command = args.black if swap else args.red
            black_command = args.red if swap else args.black
            red_label = initial_black_label if swap else initial_red_label
            black_label = initial_red_label if swap else initial_black_label
            red_options = initial_black_options if swap else initial_red_options
            black_options = initial_red_options if swap else initial_black_options
            reports.append(
                run_game(
                    game=game_number,
                    red_command=red_command,
                    black_command=black_command,
                    red_label=red_label,
                    black_label=black_label,
                    start=start,
                    go_command=args.go,
                    clock=clock,
                    max_plies=args.max_plies,
                    timeout=args.timeout,
                    red_options=red_options,
                    black_options=black_options,
                )
            )
            game_number += 1

    summary = _summary(reports)
    acceptance = (
        _acceptance_summary(
            summary,
            str(args.accept_candidate),
            str(args.accept_baseline),
            args.accept_min_games,
            args.accept_min_elo_lower,
        )
        if args.accept_candidate
        else None
    )
    output = {
        "report_type": "uci_match",
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "config": config,
        "config_digest": _config_digest(config),
        "valid": all(report.valid for report in reports),
        "accepted": acceptance["accepted"] if acceptance is not None else None,
        "acceptance": acceptance,
        "summary": summary,
        "games": [_game_dict(report) for report in reports],
    }
    output["games_digest"] = _games_digest(output["games"])
    text = json.dumps(output, ensure_ascii=False, indent=2)
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if output["valid"] and (acceptance is None or acceptance["accepted"]) else 1


def _validate_closed_acceptance_candidate(
    candidate: str | None,
    engines: Sequence[tuple[str, str]],
) -> None:
    if candidate is None:
        return
    candidate_commands = [command for label, command in engines if label == candidate]
    if not candidate_commands:
        raise ValueError("--accept-candidate must match a red or black engine label")
    for command in candidate_commands:
        forbidden = _closed_candidate_command_forbidden_reason(command)
        if forbidden is not None:
            raise ValueError(
                "acceptance candidate command must be closed: "
                f"{forbidden} is forbidden"
            )


def _closed_candidate_command_forbidden_reason(command: str) -> str | None:
    tokens = [token.casefold() for token in split_engine_command(command)]
    if any("pikafish" in token for token in tokens):
        return "Pikafish"
    for token in tokens:
        option = token.split("=", 1)[0]
        if option.startswith("--fallback"):
            return "fallback configuration"
        if option in {"--uci-engine", "--persistent-uci-ordering"}:
            return "external UCI configuration"
    return None


def run_game(
    game: int,
    red_command: str,
    black_command: str,
    red_label: str | None,
    black_label: str | None,
    start: MatchStart,
    go_command: str,
    max_plies: int,
    clock: ClockConfig | None = None,
    timeout: float = 5.0,
    red_options: Sequence[tuple[str, str]] = (),
    black_options: Sequence[tuple[str, str]] = (),
) -> GameReport:
    red_label = red_label or red_command
    black_label = black_label or black_command
    state = start.state
    start_move_count = len(start.state.moves)
    records: list[PlyRecord] = []
    red_time_ms = clock.red_time_ms if clock is not None else None
    black_time_ms = clock.black_time_ms if clock is not None else None

    initial_judgement = state.rule_judgement()
    if initial_judgement.result is not None:
        return _report(
            game,
            red_label,
            black_label,
            start,
            start_move_count,
            True,
            initial_judgement.result,
            initial_judgement.reason,
            state,
            records,
            None,
            red_options,
            black_options,
        )

    red_engine = UciEngine(_split_command(red_command), timeout=timeout)
    black_engine = UciEngine(_split_command(black_command), timeout=timeout)
    try:
        setup_error = _setup_engine(red_engine, Color.RED, red_label, red_options)
        if setup_error is not None:
            return _setup_error_report(
                game,
                red_label,
                black_label,
                start,
                start_move_count,
                state,
                setup_error,
                red_options,
                black_options,
            )
        setup_error = _setup_engine(black_engine, Color.BLACK, black_label, black_options)
        if setup_error is not None:
            return _setup_error_report(
                game,
                red_label,
                black_label,
                start,
                start_move_count,
                state,
                setup_error,
                red_options,
                black_options,
            )

        for ply in range(1, max_plies + 1):
            judgement = state.rule_judgement()
            if judgement.result is not None:
                return _report(
                    game,
                    red_label,
                    black_label,
                    start,
                    start_move_count,
                    True,
                    judgement.result,
                    judgement.reason,
                    state,
                    records,
                    None,
                    red_options,
                    black_options,
                )

            side = state.side_to_move
            engine = red_engine if side is Color.RED else black_engine
            engine_label = red_label if side is Color.RED else black_label
            position_command = state.to_uci_position()
            effective_go = _go_command_for_clock(go_command, clock, red_time_ms, black_time_ms)
            red_time_before = red_time_ms
            black_time_before = black_time_ms
            try:
                engine.set_position_command(position_command)
                engine.wait_ready()
            except Exception as exc:
                elapsed_ms = 0
                red_time_ms, black_time_ms, forfeit = _advance_clock(
                    clock,
                    side,
                    red_time_before,
                    black_time_before,
                    elapsed_ms,
                    engine_label,
                )
                records.append(
                    _failure_record(
                        ply,
                        side,
                        engine_label,
                        position_command,
                        effective_go,
                        elapsed_ms,
                        red_time_before,
                        black_time_before,
                        red_time_ms,
                        black_time_ms,
                        exc,
                    )
                )
                if forfeit is not None:
                    return _report(
                        game,
                        red_label,
                        black_label,
                        start,
                        start_move_count,
                        True,
                        "black_win" if side is Color.RED else "red_win",
                        "time_forfeit",
                        state,
                        records,
                        None,
                        red_options,
                        black_options,
                        forfeit=forfeit,
                    )
                return _report(
                    game,
                    red_label,
                    black_label,
                    start,
                    start_move_count,
                    False,
                    "black_win" if side is Color.RED else "red_win",
                    "engine_error",
                    state,
                    records,
                    {
                        "side": side.value,
                        "engine": engine_label,
                        "bestmove": None,
                        "go_command": effective_go,
                        "error": str(exc),
                    },
                    red_options,
                    black_options,
                )
            started = perf_counter()
            try:
                bestmove, lines = engine.go(effective_go)
            except Exception as exc:
                elapsed_ms = _elapsed_ms(started)
                red_time_ms, black_time_ms, forfeit = _advance_clock(
                    clock,
                    side,
                    red_time_before,
                    black_time_before,
                    elapsed_ms,
                    engine_label,
                )
                records.append(
                    _failure_record(
                        ply,
                        side,
                        engine_label,
                        position_command,
                        effective_go,
                        elapsed_ms,
                        red_time_before,
                        black_time_before,
                        red_time_ms,
                        black_time_ms,
                        exc,
                    )
                )
                if forfeit is not None:
                    return _report(
                        game,
                        red_label,
                        black_label,
                        start,
                        start_move_count,
                        True,
                        "black_win" if side is Color.RED else "red_win",
                        "time_forfeit",
                        state,
                        records,
                        None,
                        red_options,
                        black_options,
                        forfeit=forfeit,
                    )
                return _report(
                    game,
                    red_label,
                    black_label,
                    start,
                    start_move_count,
                    False,
                    "black_win" if side is Color.RED else "red_win",
                    "engine_error",
                    state,
                    records,
                    {
                        "side": side.value,
                        "engine": engine_label,
                        "bestmove": None,
                        "go_command": effective_go,
                        "error": str(exc),
                    },
                    red_options,
                    black_options,
                )
            elapsed_ms = _elapsed_ms(started)
            red_time_ms, black_time_ms, forfeit = _advance_clock(
                clock,
                side,
                red_time_before,
                black_time_before,
                elapsed_ms,
                engine_label,
            )
            info = _parse_proof_info(lines)
            records.append(
                PlyRecord(
                    ply=ply,
                    side=side.value,
                    engine=engine_label,
                    position=position_command,
                    go_command=effective_go,
                    bestmove=bestmove,
                    source=info.get("source"),
                    status=info.get("status"),
                    reason=info.get("reason"),
                    nodes=_int_or_none(info.get("nodes")),
                    max_ply=_int_or_none(info.get("max_ply")),
                    node_limit=_int_or_none(info.get("node_limit")),
                    elapsed_ms=elapsed_ms,
                    external_ordering_elapsed_ms=_int_or_none(
                        info.get("external_ordering_elapsed_ms")
                    ),
                    proof_search_elapsed_ms=_int_or_none(info.get("proof_search_elapsed_ms")),
                    total_search_elapsed_ms=_int_or_none(info.get("total_search_elapsed_ms")),
                    red_time_before_ms=red_time_before,
                    black_time_before_ms=black_time_before,
                    red_time_after_ms=red_time_ms,
                    black_time_after_ms=black_time_ms,
                    time_limit_ms=_int_or_none(info.get("time_limit_ms")),
                    time_limit_reached=_bool_flag(info.get("time_limit_reached")),
                    resolved_store_hits=_int_or_none(info.get("resolved_store_hits")),
                    resolved_store_misses=_int_or_none(info.get("resolved_store_misses")),
                    proof_store_saved=_bool_flag(info.get("proof_store_saved")),
                    proof_store_save_error=_none_info_value(
                        info.get("proof_store_save_error")
                    ),
                    proof_artifact_sha256=_none_info_value(
                        info.get("proof_artifact_sha256")
                    ),
                    engine_lines=tuple(lines),
                )
            )

            if forfeit is not None:
                return _report(
                    game,
                    red_label,
                    black_label,
                    start,
                    start_move_count,
                    True,
                    "black_win" if side is Color.RED else "red_win",
                    "time_forfeit",
                    state,
                    records,
                    None,
                    red_options,
                    black_options,
                    forfeit=forfeit,
                )

            validation = _validate_bestmove(
                state,
                bestmove,
                searchmoves=extract_go_searchmoves(effective_go),
            )
            if validation.error is not None:
                return _report(
                    game,
                    red_label,
                    black_label,
                    start,
                    start_move_count,
                    False,
                    "black_win" if side is Color.RED else "red_win",
                    "illegal_bestmove",
                    state,
                    records,
                    {
                        "side": side.value,
                        "engine": engine_label,
                        "bestmove": bestmove,
                        "error": validation.error,
                        "validation_error": validation.error,
                        "legal_move_count": validation.legal_move_count,
                        "legal_moves_sample": validation.legal_moves_sample,
                        "searchmoves": validation.searchmoves,
                        "searchmoves_error": validation.searchmoves_error,
                        "searchmoves_legal_move_count": validation.searchmoves_legal_move_count,
                        "searchmoves_legal_moves_sample": validation.searchmoves_legal_moves_sample,
                    },
                    red_options,
                    black_options,
                )

            assert validation.move is not None
            state = state.make_move(validation.move)
    finally:
        red_engine.close()
        black_engine.close()

    judgement = state.rule_judgement()
    if judgement.result is not None:
        return _report(
            game,
            red_label,
            black_label,
            start,
            start_move_count,
            True,
            judgement.result,
            judgement.reason,
            state,
            records,
            None,
            red_options,
            black_options,
        )
    return _report(
        game,
        red_label,
        black_label,
        start,
        start_move_count,
        True,
        "unfinished",
        "max_plies",
        state,
        records,
        None,
        red_options,
        black_options,
    )


def _validate_bestmove(
    state: GameState,
    bestmove: str,
    *,
    searchmoves: Sequence[str] = (),
) -> BestmoveValidation:
    legal_by_uci = {move.to_uci(): move for move in state.legal_moves()}
    legal_moves_sample = tuple(sorted(legal_by_uci)[:8])
    normalized_searchmoves = tuple(searchmoves)
    searchmove_set = set(normalized_searchmoves)
    legal_searchmoves = tuple(sorted(move for move in searchmove_set if move in legal_by_uci))
    if bestmove == "0000":
        if not legal_by_uci:
            return BestmoveValidation(
                None,
                None,
                0,
                (),
                None,
                normalized_searchmoves,
                0,
                (),
            )
        searchmoves_error = (
            "null move returned while legal searchmoves exist"
            if normalized_searchmoves and legal_searchmoves
            else None
        )
        return BestmoveValidation(
            None,
            searchmoves_error or "null move returned while legal moves exist",
            len(legal_by_uci),
            legal_moves_sample,
            searchmoves_error,
            normalized_searchmoves,
            len(legal_searchmoves),
            legal_searchmoves[:8],
        )
    try:
        move = Move.from_uci(bestmove)
    except Exception as exc:
        return BestmoveValidation(
            None,
            str(exc),
            len(legal_by_uci),
            legal_moves_sample,
            None,
            normalized_searchmoves,
            len(legal_searchmoves),
            legal_searchmoves[:8],
        )
    if move.to_uci() not in legal_by_uci:
        return BestmoveValidation(
            None,
            "move is not legal in the local rules core",
            len(legal_by_uci),
            legal_moves_sample,
            None,
            normalized_searchmoves,
            len(legal_searchmoves),
            legal_searchmoves[:8],
        )
    searchmoves_error = (
        "move is outside go searchmoves"
        if normalized_searchmoves and move.to_uci() not in searchmove_set
        else None
    )
    return BestmoveValidation(
        legal_by_uci[move.to_uci()],
        searchmoves_error,
        len(legal_by_uci),
        legal_moves_sample,
        searchmoves_error,
        normalized_searchmoves,
        len(legal_searchmoves),
        legal_searchmoves[:8],
    )


@dataclass(frozen=True, slots=True)
class EngineSetupError:
    side: Color
    engine: str
    phase: str
    error: str


def _setup_engine(
    engine: UciEngine,
    side: Color,
    engine_label: str,
    options: Sequence[tuple[str, str]],
) -> EngineSetupError | None:
    try:
        engine.initialize()
        _configure_engine_options(engine, options)
        engine.new_game()
    except Exception as exc:
        return EngineSetupError(
            side=side,
            engine=engine_label,
            phase="initialize",
            error=str(exc),
        )
    return None


def _setup_error_report(
    game: int,
    red_label: str,
    black_label: str,
    start: MatchStart,
    start_move_count: int,
    state: GameState,
    setup_error: EngineSetupError,
    red_options: Sequence[tuple[str, str]],
    black_options: Sequence[tuple[str, str]],
) -> GameReport:
    return _report(
        game,
        red_label,
        black_label,
        start,
        start_move_count,
        False,
        "black_win" if setup_error.side is Color.RED else "red_win",
        "engine_error",
        state,
        [],
        {
            "side": setup_error.side.value,
            "engine": setup_error.engine,
            "phase": setup_error.phase,
            "bestmove": None,
            "go_command": None,
            "error": setup_error.error,
        },
        red_options,
        black_options,
    )


def _failure_record(
    ply: int,
    side: Color,
    engine_label: str,
    position_command: str,
    go_command: str,
    elapsed_ms: int,
    red_time_before_ms: int | None,
    black_time_before_ms: int | None,
    red_time_after_ms: int | None,
    black_time_after_ms: int | None,
    exc: Exception,
) -> PlyRecord:
    return PlyRecord(
        ply=ply,
        side=side.value,
        engine=engine_label,
        position=position_command,
        go_command=go_command,
        bestmove="0000",
        source=None,
        status=None,
        reason="engine_error",
        nodes=None,
        max_ply=None,
        node_limit=None,
        elapsed_ms=elapsed_ms,
        external_ordering_elapsed_ms=None,
        proof_search_elapsed_ms=None,
        total_search_elapsed_ms=None,
        red_time_before_ms=red_time_before_ms,
        black_time_before_ms=black_time_before_ms,
        red_time_after_ms=red_time_after_ms,
        black_time_after_ms=black_time_after_ms,
        time_limit_ms=None,
        time_limit_reached=False,
        resolved_store_hits=None,
        resolved_store_misses=None,
        proof_store_saved=False,
        proof_store_save_error=None,
        proof_artifact_sha256=None,
        engine_lines=(f"error: {exc}",),
    )


def _clock_config_from_args(args: argparse.Namespace) -> ClockConfig | None:
    has_clock = args.red_time_ms is not None or args.black_time_ms is not None
    has_clock_setting = (
        args.red_increment_ms != 0
        or args.black_increment_ms != 0
        or args.movestogo is not None
    )
    if not has_clock and not has_clock_setting:
        return None
    if args.red_time_ms is None or args.black_time_ms is None:
        raise ValueError("--red-time-ms and --black-time-ms must be provided together")
    if args.red_time_ms < 0 or args.black_time_ms < 0:
        raise ValueError("clock times must be non-negative")
    if args.red_increment_ms < 0 or args.black_increment_ms < 0:
        raise ValueError("clock increments must be non-negative")
    if args.movestogo is not None and args.movestogo <= 0:
        raise ValueError("--movestogo must be positive")
    if _go_contains_clock_fields(args.go):
        raise ValueError("clock CLI options cannot be combined with clock fields in --go")
    return ClockConfig(
        red_time_ms=args.red_time_ms,
        black_time_ms=args.black_time_ms,
        red_increment_ms=args.red_increment_ms,
        black_increment_ms=args.black_increment_ms,
        movestogo=args.movestogo,
    )


def _go_command_for_clock(
    go_command: str,
    clock: ClockConfig | None,
    red_time_ms: int | None,
    black_time_ms: int | None,
) -> str:
    if clock is None:
        return go_command
    return (
        f"{go_command} wtime {max(0, int(red_time_ms or 0))} "
        f"btime {max(0, int(black_time_ms or 0))} "
        f"winc {clock.red_increment_ms} binc {clock.black_increment_ms}"
        + (f" movestogo {clock.movestogo}" if clock.movestogo is not None else "")
    )


def _advance_clock(
    clock: ClockConfig | None,
    side: Color,
    red_time_before_ms: int | None,
    black_time_before_ms: int | None,
    elapsed_ms: int,
    engine_label: str,
) -> tuple[int | None, int | None, dict[str, Any] | None]:
    if clock is None:
        return red_time_before_ms, black_time_before_ms, None
    if side is Color.RED:
        before = int(red_time_before_ms or 0)
        remaining = before - elapsed_ms
        after = max(0, remaining)
        forfeit = _time_forfeit(side, engine_label, before, elapsed_ms) if remaining < 0 else None
        if forfeit is None:
            after += clock.red_increment_ms
        return after, black_time_before_ms, forfeit

    before = int(black_time_before_ms or 0)
    remaining = before - elapsed_ms
    after = max(0, remaining)
    forfeit = _time_forfeit(side, engine_label, before, elapsed_ms) if remaining < 0 else None
    if forfeit is None:
        after += clock.black_increment_ms
    return red_time_before_ms, after, forfeit


def _time_forfeit(side: Color, engine: str, time_before_ms: int, elapsed_ms: int) -> dict[str, Any]:
    return {
        "side": side.value,
        "engine": engine,
        "time_before_ms": time_before_ms,
        "elapsed_ms": elapsed_ms,
        "overrun_ms": elapsed_ms - time_before_ms,
    }


def _elapsed_ms(started: float) -> int:
    return max(0, int((perf_counter() - started) * 1000))


def _go_contains_clock_fields(go_command: str) -> bool:
    clock_fields = {"wtime", "btime", "winc", "binc", "movestogo"}
    return any(token.lower() in clock_fields for token in go_command.split()[1:])


def _report(
    game: int,
    red_engine: str,
    black_engine: str,
    start: MatchStart,
    start_move_count: int,
    valid: bool,
    result: str,
    reason: str,
    state: GameState,
    records: list[PlyRecord],
    illegal: dict[str, Any] | None,
    red_options: Sequence[tuple[str, str]] = (),
    black_options: Sequence[tuple[str, str]] = (),
    forfeit: dict[str, Any] | None = None,
) -> GameReport:
    return GameReport(
        game=game,
        red_engine=red_engine,
        black_engine=black_engine,
        start_name=start.name,
        start_fen=start.state.to_fen(),
        start_position=start.state.to_uci_position(),
        start_moves=tuple(move.to_uci() for move in start.state.moves),
        start_tags=start.tags,
        valid=valid,
        result=result,
        reason=reason,
        plies=len(state.moves) - start_move_count,
        moves=tuple(move.to_uci() for move in state.moves[start_move_count:]),
        illegal=illegal,
        records=tuple(records),
        red_options=_option_records(red_options),
        black_options=_option_records(black_options),
        forfeit=forfeit,
    )


def _summary(reports: list[GameReport]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "games": len(reports),
        "valid_games": sum(1 for report in reports if report.valid),
        "invalid_games": sum(1 for report in reports if not report.valid),
        "proof_moves": 0,
        "proof_store_moves": 0,
        "fallback_moves": 0,
        "self_fallback_moves": 0,
        "external_fallback_moves": 0,
        "legacy_fallback_moves": 0,
        "emergency_moves": 0,
        "none_moves": 0,
        "unclassified_moves": 0,
        "invalid_proof_telemetry_moves": 0,
        "invalid_proof_telemetry_reasons": {},
        "time_limited_moves": 0,
        "time_forfeits": 0,
        "engine_errors": 0,
        "resolved_store_hits": 0,
        "resolved_store_misses": 0,
        "proof_store_saves": 0,
        "proof_store_save_errors": 0,
        "reasons": {},
        "red_win": 0,
        "black_win": 0,
        "draw": 0,
        "unfinished": 0,
        "unknown_rule_state": 0,
    }
    for report in reports:
        summary[report.result] = summary.get(report.result, 0) + 1
        _increment_reason(summary, report.reason)
        if report.reason == "time_forfeit":
            summary["time_forfeits"] += 1
        elif report.reason == "engine_error":
            summary["engine_errors"] += 1
        for record in report.records:
            if record.source == "proof":
                summary["proof_moves"] += 1
            elif record.source == "proof_store":
                summary["proof_moves"] += 1
                summary["proof_store_moves"] += 1
            elif record.source in {"self_fallback", "external_fallback", "fallback"}:
                summary["fallback_moves"] += 1
                summary[f"{record.source}_moves" if record.source != "fallback" else "legacy_fallback_moves"] += 1
            elif record.source == "emergency":
                summary["emergency_moves"] += 1
            elif record.source == "none":
                summary["none_moves"] += 1
            else:
                summary["unclassified_moves"] += 1
            invalid_telemetry_reason = _invalid_proof_telemetry_reason(record)
            if invalid_telemetry_reason is not None:
                summary["invalid_proof_telemetry_moves"] += 1
                _increment_counter(
                    summary,
                    "invalid_proof_telemetry_reasons",
                    invalid_telemetry_reason,
                )
            if record.time_limit_reached:
                summary["time_limited_moves"] += 1
            if record.resolved_store_hits is not None:
                summary["resolved_store_hits"] += record.resolved_store_hits
            if record.resolved_store_misses is not None:
                summary["resolved_store_misses"] += record.resolved_store_misses
            if record.proof_store_saved:
                summary["proof_store_saves"] += 1
            if record.proof_store_save_error is not None:
                summary["proof_store_save_errors"] += 1
    summary["engines"] = _engine_summary(reports)
    summary["starts"] = _start_summary(reports)
    return summary


def _game_dict(report: GameReport) -> dict[str, Any]:
    data = asdict(report)
    data["records"] = [asdict(record) for record in report.records]
    return data


def _split_command(command: str) -> list[str]:
    return split_engine_command(command)


def _parse_uci_options(values: Sequence[str] | None) -> tuple[tuple[str, str], ...]:
    return tuple(_parse_uci_option(value) for value in (values or ()))


def _parse_uci_option(text: str) -> tuple[str, str]:
    if "=" not in text:
        raise ValueError("UCI option must use NAME=VALUE")
    name, value = text.split("=", 1)
    name = name.strip()
    value = value.strip()
    if not name or not value:
        raise ValueError("UCI option name and value must be non-empty")
    return name, value


def _configure_engine_options(
    engine: UciEngine,
    options: Sequence[tuple[str, str]],
) -> None:
    if not options:
        return
    for name, value in options:
        engine.set_option(name, value)
    engine.wait_ready()


def _option_records(options: Sequence[tuple[str, str]]) -> tuple[UciOption, ...]:
    return tuple(UciOption(name=name, value=value) for name, value in options)


def _option_config(options: Sequence[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"name": name, "value": value} for name, value in options]


def _stable_json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _config_digest(config: dict[str, Any], *, scope: str = "full") -> dict[str, str]:
    canonical = _stable_json_text(config)
    return {
        "algorithm": "sha256",
        "scope": scope,
        "value": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def _games_digest(games: Sequence[dict[str, Any]]) -> dict[str, str]:
    return _config_digest({"games": list(games)}, scope="games")


def _command_provenance(command: str) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for index, token in enumerate(split_engine_command(command)):
        for candidate in _command_token_paths(token):
            if not candidate.is_file():
                continue
            resolved = candidate.resolve()
            files.append(
                {
                    "token_index": index,
                    "token": token,
                    "path": str(resolved),
                    "bytes": resolved.stat().st_size,
                    "sha256": _file_sha256(resolved),
                }
            )
            break
    return {"command": command, "files": files}


def _command_token_paths(token: str) -> tuple[Path, ...]:
    values = [token]
    if token.startswith("-") and "=" in token:
        values.append(token.split("=", 1)[1])
    candidates: list[Path] = []
    for value in values:
        path = Path(value).expanduser()
        if path.is_absolute():
            candidates.append(path)
        else:
            candidates.extend((ROOT / path, Path.cwd() / path))
        resolved_command = shutil.which(value)
        if resolved_command:
            candidates.append(Path(resolved_command))
    return tuple(dict.fromkeys(candidates))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _start_config(start: MatchStart) -> dict[str, Any]:
    config = {
        "name": start.name,
        "fen": start.state.to_fen(),
        "position": start.state.to_uci_position(),
        "moves": [move.to_uci() for move in start.state.moves],
    }
    if start.tags:
        config["tags"] = list(start.tags)
    return config


def _run_config(
    args: argparse.Namespace,
    starts: Sequence[MatchStart],
    red_label: str,
    black_label: str,
    red_options: Sequence[tuple[str, str]],
    black_options: Sequence[tuple[str, str]],
    clock: ClockConfig | None,
) -> dict[str, Any]:
    return {
        "red_command": args.red,
        "black_command": args.black,
        "engine_provenance": {
            "red": _command_provenance(args.red),
            "black": _command_provenance(args.black),
        },
        "red_name": red_label,
        "black_name": black_label,
        "red_options": _option_config(red_options),
        "black_options": _option_config(black_options),
        "games": args.games,
        "alternate_colors": bool(args.alternate_colors),
        "fen": args.fen if not args.position and not args.suite else None,
        "position": args.position,
        "suite": args.suite,
        "suite_tags": list(_suite_tags_from_args(args)),
        "go": args.go,
        "clock": _clock_config(clock),
        "max_plies": args.max_plies,
        "timeout": args.timeout,
        "acceptance": {
            "candidate": args.accept_candidate,
            "baseline": args.accept_baseline,
            "min_games": args.accept_min_games,
            "min_elo_lower": args.accept_min_elo_lower,
        },
        "starts": [_start_config(start) for start in starts],
    }


def _clock_config(clock: ClockConfig | None) -> dict[str, Any] | None:
    if clock is None:
        return None
    return {
        "red_time_ms": clock.red_time_ms,
        "black_time_ms": clock.black_time_ms,
        "red_increment_ms": clock.red_increment_ms,
        "black_increment_ms": clock.black_increment_ms,
        "movestogo": clock.movestogo,
    }


def _match_starts(args) -> list[MatchStart]:
    suite_tags = _suite_tags_from_args(args)
    if suite_tags and not args.suite:
        raise ValueError("--suite-tag requires --suite")
    if args.suite:
        if args.position or args.fen != Position.START_FEN:
            raise ValueError("--suite cannot be combined with --fen or --position")
        return _starts_from_suite(args.suite, suite_tags)
    if args.position:
        return [MatchStart("single", GameState.from_uci_position(args.position))]
    return [MatchStart("single", GameState.from_position(Position.from_fen(args.fen, strict=False)))]


def _suite_tags_from_args(args) -> tuple[str, ...]:
    tags: list[str] = []
    for value in getattr(args, "suite_tag", None) or ():
        tag = str(value).strip()
        if not tag:
            raise ValueError("--suite-tag values must be non-empty")
        tags.append(tag)
    return tuple(tags)


def _starts_from_suite(path: str, include_tags: Sequence[str] = ()) -> list[MatchStart]:
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    positions = config.get("positions") if isinstance(config, dict) else config
    if not isinstance(positions, list) or not positions:
        raise ValueError("suite must be a non-empty list or contain a non-empty positions list")
    starts = [_start_from_item(index, item) for index, item in enumerate(positions, start=1)]
    if not include_tags:
        return starts
    tag_filter = set(include_tags)
    filtered = [start for start in starts if tag_filter.intersection(start.tags)]
    if not filtered:
        raise ValueError("suite tag filter matched no starts")
    return filtered


def _start_from_item(index: int, item: Any) -> MatchStart:
    if not isinstance(item, dict):
        raise ValueError("each suite item must be an object")
    has_position = "position" in item
    has_fen = "fen" in item
    if has_position == has_fen:
        raise ValueError("each suite item must contain exactly one of position or fen")
    name = str(item.get("name", f"start_{index}"))
    tags = _start_tags(item.get("tags"))
    if has_position:
        return MatchStart(name, GameState.from_uci_position(str(item["position"])), tags)
    return MatchStart(
        name,
        GameState.from_position(Position.from_fen(str(item["fen"]), strict=False)),
        tags,
    )


def _start_tags(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("suite item tags must be a list of strings")
    tags: list[str] = []
    for tag in value:
        if not isinstance(tag, str):
            raise ValueError("suite item tags must be a list of strings")
        tags.append(tag)
    return tuple(tags)


def _start_summary(reports: list[GameReport]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for report in reports:
        start = summary.setdefault(
            report.start_name,
            {
                "games": 0,
                "valid_games": 0,
                "invalid_games": 0,
                "proof_moves": 0,
                "proof_store_moves": 0,
                "fallback_moves": 0,
                "self_fallback_moves": 0,
                "external_fallback_moves": 0,
                "legacy_fallback_moves": 0,
                "emergency_moves": 0,
                "none_moves": 0,
                "unclassified_moves": 0,
                "invalid_proof_telemetry_moves": 0,
                "invalid_proof_telemetry_reasons": {},
                "time_limited_moves": 0,
                "time_forfeits": 0,
                "engine_errors": 0,
                "resolved_store_hits": 0,
                "resolved_store_misses": 0,
                "proof_store_saves": 0,
                "proof_store_save_errors": 0,
                "reasons": {},
                "red_win": 0,
                "black_win": 0,
                "draw": 0,
                "unfinished": 0,
                "unknown_rule_state": 0,
            },
        )
        start["games"] += 1
        if report.valid:
            start["valid_games"] += 1
        else:
            start["invalid_games"] += 1
        start[report.result] = start.get(report.result, 0) + 1
        _increment_reason(start, report.reason)
        if report.reason == "time_forfeit":
            start["time_forfeits"] += 1
        elif report.reason == "engine_error":
            start["engine_errors"] += 1
        for record in report.records:
            if record.source == "proof":
                start["proof_moves"] += 1
            elif record.source == "proof_store":
                start["proof_moves"] += 1
                start["proof_store_moves"] += 1
            elif record.source in {"self_fallback", "external_fallback", "fallback"}:
                start["fallback_moves"] += 1
                start[f"{record.source}_moves" if record.source != "fallback" else "legacy_fallback_moves"] += 1
            elif record.source == "emergency":
                start["emergency_moves"] += 1
            elif record.source == "none":
                start["none_moves"] += 1
            else:
                start["unclassified_moves"] += 1
            invalid_telemetry_reason = _invalid_proof_telemetry_reason(record)
            if invalid_telemetry_reason is not None:
                start["invalid_proof_telemetry_moves"] += 1
                _increment_counter(
                    start,
                    "invalid_proof_telemetry_reasons",
                    invalid_telemetry_reason,
                )
            if record.time_limit_reached:
                start["time_limited_moves"] += 1
            if record.resolved_store_hits is not None:
                start["resolved_store_hits"] += record.resolved_store_hits
            if record.resolved_store_misses is not None:
                start["resolved_store_misses"] += record.resolved_store_misses
            if record.proof_store_saved:
                start["proof_store_saves"] += 1
            if record.proof_store_save_error is not None:
                start["proof_store_save_errors"] += 1
    return summary


def _acceptance_summary(
    summary: dict[str, Any],
    candidate: str,
    baseline: str,
    min_games: int,
    min_elo_lower: float,
) -> dict[str, Any]:
    engines = summary.get("engines", {})
    candidate_stats = engines.get(candidate)
    baseline_stats = engines.get(baseline)
    reasons: list[str] = []

    if candidate == baseline:
        reasons.append("candidate_equals_baseline")
    if candidate_stats is None:
        reasons.append("candidate_missing")
    if baseline_stats is None:
        reasons.append("baseline_missing")

    candidate_scored_games = int(candidate_stats.get("scored_games", 0)) if candidate_stats else 0
    if candidate_scored_games < min_games:
        reasons.append("insufficient_scored_games")
    candidate_invalid_games = int(candidate_stats.get("invalid_games", 0)) if candidate_stats else 0
    baseline_invalid_games = int(baseline_stats.get("invalid_games", 0)) if baseline_stats else 0
    candidate_unfinished_games = int(candidate_stats.get("unfinished", 0)) if candidate_stats else 0
    baseline_unfinished_games = int(baseline_stats.get("unfinished", 0)) if baseline_stats else 0
    candidate_unknown_rule_state = (
        int(candidate_stats.get("unknown_rule_state", 0)) if candidate_stats else 0
    )
    baseline_unknown_rule_state = (
        int(baseline_stats.get("unknown_rule_state", 0)) if baseline_stats else 0
    )
    candidate_time_forfeit_losses = (
        int(candidate_stats.get("time_forfeit_losses", 0)) if candidate_stats else 0
    )
    baseline_time_forfeit_losses = (
        int(baseline_stats.get("time_forfeit_losses", 0)) if baseline_stats else 0
    )
    candidate_emergency_moves = int(candidate_stats.get("emergency_moves", 0)) if candidate_stats else 0
    baseline_emergency_moves = int(baseline_stats.get("emergency_moves", 0)) if baseline_stats else 0
    candidate_unclassified_moves = (
        int(candidate_stats.get("unclassified_moves", 0)) if candidate_stats else 0
    )
    candidate_none_moves = int(candidate_stats.get("none_moves", 0)) if candidate_stats else 0
    candidate_external_fallback_moves = (
        int(candidate_stats.get("external_fallback_moves", 0)) if candidate_stats else 0
    )
    candidate_legacy_fallback_moves = (
        int(candidate_stats.get("legacy_fallback_moves", 0)) if candidate_stats else 0
    )
    candidate_invalid_proof_telemetry_moves = (
        int(candidate_stats.get("invalid_proof_telemetry_moves", 0)) if candidate_stats else 0
    )
    baseline_invalid_proof_telemetry_moves = (
        int(baseline_stats.get("invalid_proof_telemetry_moves", 0)) if baseline_stats else 0
    )
    if candidate_invalid_games > 0:
        reasons.append("candidate_invalid_games")
    if baseline_invalid_games > 0:
        reasons.append("baseline_invalid_games")
    if candidate_unfinished_games > 0:
        reasons.append("candidate_unfinished_games")
    if baseline_unfinished_games > 0:
        reasons.append("baseline_unfinished_games")
    if candidate_unknown_rule_state > 0:
        reasons.append("candidate_unknown_rule_state")
    if baseline_unknown_rule_state > 0:
        reasons.append("baseline_unknown_rule_state")
    if candidate_time_forfeit_losses > 0:
        reasons.append("candidate_time_forfeits")
    if baseline_time_forfeit_losses > 0:
        reasons.append("baseline_time_forfeits")
    if candidate_emergency_moves > 0:
        reasons.append("candidate_emergency_moves")
    if baseline_emergency_moves > 0:
        reasons.append("baseline_emergency_moves")
    if candidate_unclassified_moves > 0:
        reasons.append("candidate_unclassified_moves")
    if candidate_none_moves > 0:
        reasons.append("candidate_none_moves")
    if candidate_external_fallback_moves > 0:
        reasons.append("candidate_external_fallback_moves")
    if candidate_legacy_fallback_moves > 0:
        reasons.append("candidate_legacy_fallback_moves")
    if candidate_invalid_proof_telemetry_moves > 0:
        reasons.append("candidate_invalid_proof_telemetry")
    if baseline_invalid_proof_telemetry_moves > 0:
        reasons.append("baseline_invalid_proof_telemetry")

    elo_ci = candidate_stats.get("elo_diff_ci95") if candidate_stats else None
    elo_lower = elo_ci.get("lower") if isinstance(elo_ci, dict) else None
    if elo_lower is None:
        reasons.append("no_finite_elo_lower_bound")
    elif float(elo_lower) < min_elo_lower:
        reasons.append("elo_lower_bound_below_threshold")

    evidence_class = (
        "assisted"
        if candidate_external_fallback_moves > 0 or candidate_legacy_fallback_moves > 0
        else "closed"
    )
    candidate_elo_diff = candidate_stats.get("elo_diff") if candidate_stats else None
    closed_elo_eligible = bool(
        candidate_stats is not None
        and candidate_scored_games > 0
        and evidence_class == "closed"
        and candidate_emergency_moves == 0
        and candidate_unclassified_moves == 0
        and candidate_none_moves == 0
        and candidate_invalid_proof_telemetry_moves == 0
        and candidate_invalid_games == 0
        and candidate_unfinished_games == 0
        and candidate_unknown_rule_state == 0
        and candidate_time_forfeit_losses == 0
    )

    return {
        "accepted": not reasons,
        "candidate": candidate,
        "baseline": baseline,
        "min_games": min_games,
        "min_elo_lower": min_elo_lower,
        "candidate_scored_games": candidate_scored_games,
        "candidate_invalid_games": candidate_invalid_games,
        "baseline_invalid_games": baseline_invalid_games,
        "candidate_unfinished_games": candidate_unfinished_games,
        "baseline_unfinished_games": baseline_unfinished_games,
        "candidate_unknown_rule_state": candidate_unknown_rule_state,
        "baseline_unknown_rule_state": baseline_unknown_rule_state,
        "candidate_time_forfeit_losses": candidate_time_forfeit_losses,
        "baseline_time_forfeit_losses": baseline_time_forfeit_losses,
        "candidate_emergency_moves": candidate_emergency_moves,
        "baseline_emergency_moves": baseline_emergency_moves,
        "candidate_unclassified_moves": candidate_unclassified_moves,
        "candidate_none_moves": candidate_none_moves,
        "candidate_external_fallback_moves": candidate_external_fallback_moves,
        "candidate_legacy_fallback_moves": candidate_legacy_fallback_moves,
        "candidate_invalid_proof_telemetry_moves": candidate_invalid_proof_telemetry_moves,
        "baseline_invalid_proof_telemetry_moves": baseline_invalid_proof_telemetry_moves,
        "candidate_score_rate": candidate_stats.get("score_rate") if candidate_stats else None,
        "candidate_score_rate_ci95": candidate_stats.get("score_rate_ci95") if candidate_stats else None,
        "candidate_elo_diff": candidate_elo_diff,
        "candidate_elo_diff_ci95": elo_ci,
        "evidence_class": evidence_class,
        "closed_elo_eligible": closed_elo_eligible,
        "closed_elo_diff": candidate_elo_diff if closed_elo_eligible else None,
        "closed_elo_diff_ci95": elo_ci if closed_elo_eligible else None,
        "baseline_score_rate": baseline_stats.get("score_rate") if baseline_stats else None,
        "baseline_elo_diff": baseline_stats.get("elo_diff") if baseline_stats else None,
        "reasons": reasons,
    }


def _engine_summary(reports: list[GameReport]) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for report in reports:
        for engine in (report.red_engine, report.black_engine):
            stats.setdefault(engine, _empty_engine_stats())

        red = stats[report.red_engine]
        black = stats[report.black_engine]
        red["games"] += 1
        red["red_games"] += 1
        black["games"] += 1
        black["black_games"] += 1
        _increment_reason(red, report.reason)
        _increment_reason(black, report.reason)

        if report.valid:
            if report.result == "red_win":
                _add_score(red, win=True)
                _add_score(black, loss=True)
            elif report.result == "black_win":
                _add_score(red, loss=True)
                _add_score(black, win=True)
            elif report.result == "draw":
                _add_score(red, draw=True)
                _add_score(black, draw=True)
            elif report.result == "unfinished":
                red["unfinished"] += 1
                black["unfinished"] += 1
            elif report.result == "unknown_rule_state":
                red["unknown_rule_state"] += 1
                black["unknown_rule_state"] += 1
        elif report.result == "unfinished":
            red["unfinished"] += 1
            black["unfinished"] += 1
        elif report.result == "unknown_rule_state":
            red["unknown_rule_state"] += 1
            black["unknown_rule_state"] += 1

        if report.reason == "time_forfeit" and report.forfeit:
            offender = stats.setdefault(str(report.forfeit["engine"]), _empty_engine_stats())
            offender["time_forfeit_losses"] += 1
        elif report.reason == "engine_error" and report.illegal:
            offender = stats.setdefault(str(report.illegal["engine"]), _empty_engine_stats())
            offender["engine_errors"] += 1

        if not report.valid:
            if report.reason == "engine_error" and report.illegal and report.illegal.get("engine") in stats:
                offender = stats[str(report.illegal["engine"])]
                offender["invalid_games"] += 1
            elif report.illegal and report.illegal.get("engine") in stats:
                offender = stats[str(report.illegal["engine"])]
                offender["illegal_losses"] += 1
                offender["invalid_games"] += 1
            else:
                red["invalid_games"] += 1
                black["invalid_games"] += 1

        for record in report.records:
            engine_stats = stats.setdefault(record.engine, _empty_engine_stats())
            engine_stats["moves"] += 1
            if record.source == "proof":
                engine_stats["proof_moves"] += 1
            elif record.source == "proof_store":
                engine_stats["proof_moves"] += 1
                engine_stats["proof_store_moves"] += 1
            elif record.source in {"self_fallback", "external_fallback", "fallback"}:
                engine_stats["fallback_moves"] += 1
                engine_stats[
                    f"{record.source}_moves"
                    if record.source != "fallback"
                    else "legacy_fallback_moves"
                ] += 1
            elif record.source == "emergency":
                engine_stats["emergency_moves"] += 1
            elif record.source == "none":
                engine_stats["none_moves"] += 1
            else:
                engine_stats["unclassified_moves"] += 1
            invalid_telemetry_reason = _invalid_proof_telemetry_reason(record)
            if invalid_telemetry_reason is not None:
                engine_stats["invalid_proof_telemetry_moves"] += 1
                _increment_counter(
                    engine_stats,
                    "invalid_proof_telemetry_reasons",
                    invalid_telemetry_reason,
                )
            if record.time_limit_reached:
                engine_stats["time_limited_moves"] += 1
            if record.resolved_store_hits is not None:
                engine_stats["resolved_store_hits"] += record.resolved_store_hits
            if record.resolved_store_misses is not None:
                engine_stats["resolved_store_misses"] += record.resolved_store_misses
            if record.proof_store_saved:
                engine_stats["proof_store_saves"] += 1
            if record.proof_store_save_error is not None:
                engine_stats["proof_store_save_errors"] += 1

    for engine_stats in stats.values():
        scored = engine_stats["scored_games"]
        if scored:
            score_rate = engine_stats["score"] / scored
            engine_stats["score_rate"] = round(score_rate, 6)
            score_rate_ci = _score_rate_ci95(engine_stats["score"], scored)
            engine_stats["score_rate_ci95"] = score_rate_ci
            elo = _elo_from_score_rate(score_rate)
            engine_stats["elo_diff"] = round(elo, 1) if elo is not None else None
            engine_stats["elo_diff_ci95"] = _elo_ci_from_score_ci(score_rate_ci)
        else:
            engine_stats["score_rate"] = None
            engine_stats["score_rate_ci95"] = None
            engine_stats["elo_diff"] = None
            engine_stats["elo_diff_ci95"] = None
        if engine_stats["score"] == int(engine_stats["score"]):
            engine_stats["score"] = int(engine_stats["score"])
    return stats


def _empty_engine_stats() -> dict[str, Any]:
    return {
        "games": 0,
        "red_games": 0,
        "black_games": 0,
        "moves": 0,
        "proof_moves": 0,
        "proof_store_moves": 0,
        "fallback_moves": 0,
        "self_fallback_moves": 0,
        "external_fallback_moves": 0,
        "legacy_fallback_moves": 0,
        "emergency_moves": 0,
        "none_moves": 0,
        "unclassified_moves": 0,
        "invalid_proof_telemetry_moves": 0,
        "invalid_proof_telemetry_reasons": {},
        "time_limited_moves": 0,
        "time_forfeit_losses": 0,
        "engine_errors": 0,
        "resolved_store_hits": 0,
        "resolved_store_misses": 0,
        "proof_store_saves": 0,
        "proof_store_save_errors": 0,
        "scored_games": 0,
        "wins": 0,
        "losses": 0,
        "draws": 0,
        "score": 0.0,
        "score_rate": None,
        "elo_diff": None,
        "unfinished": 0,
        "unknown_rule_state": 0,
        "illegal_losses": 0,
        "invalid_games": 0,
        "reasons": {},
    }


def _increment_reason(summary: dict[str, Any], reason: str) -> None:
    reasons = summary.setdefault("reasons", {})
    reasons[reason] = int(reasons.get(reason, 0)) + 1


def _increment_counter(summary: dict[str, Any], field: str, key: str) -> None:
    counters = summary.setdefault(field, {})
    counters[key] = int(counters.get(key, 0)) + 1


def _invalid_proof_telemetry_reason(record: PlyRecord) -> str | None:
    if record.source == "proof" and record.status != "proven":
        return "proof_without_proven_status"
    if record.source == "proof" and not _is_sha256(record.proof_artifact_sha256):
        return "proof_without_artifact_hash"
    if record.source == "proof_store":
        if record.status != "proven":
            return "proof_store_without_proven_status"
        if record.resolved_store_hits is None or record.resolved_store_hits <= 0:
            return "proof_store_without_store_hit"
        if not _is_sha256(record.proof_artifact_sha256):
            return "proof_store_without_artifact_hash"
    if record.source in {
        "self_fallback",
        "external_fallback",
        "fallback",
        "emergency",
        "none",
    } and record.status == "proven":
        return f"{record.source}_with_proven_status"
    return None


def _is_sha256(value: str | None) -> bool:
    if value is None or len(value) != 64:
        return False
    return all(character in "0123456789abcdef" for character in value.lower())


def _add_score(
    stats: dict[str, Any],
    *,
    win: bool = False,
    loss: bool = False,
    draw: bool = False,
) -> None:
    stats["scored_games"] += 1
    if win:
        stats["wins"] += 1
        stats["score"] += 1.0
    elif loss:
        stats["losses"] += 1
    elif draw:
        stats["draws"] += 1
        stats["score"] += 0.5


def _elo_from_score_rate(score_rate: float) -> float | None:
    if score_rate <= 0.0 or score_rate >= 1.0:
        return None
    return -400.0 * math.log10((1.0 / score_rate) - 1.0)


def _score_rate_ci95(score: float, games: int) -> dict[str, float]:
    p = score / games
    z2 = _Z_95 * _Z_95
    denominator = 1.0 + z2 / games
    center = (p + z2 / (2.0 * games)) / denominator
    half_width = (
        _Z_95
        * math.sqrt((p * (1.0 - p) + z2 / (4.0 * games)) / games)
        / denominator
    )
    lower = max(0.0, center - half_width)
    upper = min(1.0, center + half_width)
    return {"lower": round(lower, 6), "upper": round(upper, 6)}


def _elo_ci_from_score_ci(score_rate_ci: dict[str, float]) -> dict[str, float | None]:
    lower = _elo_from_score_rate(score_rate_ci["lower"])
    upper = _elo_from_score_rate(score_rate_ci["upper"])
    return {
        "lower": round(lower, 1) if lower is not None else None,
        "upper": round(upper, 1) if upper is not None else None,
    }


def _parse_proof_info(lines: tuple[str, ...]) -> dict[str, str]:
    info: dict[str, str] = {}
    for line in lines:
        if not line.startswith("info string "):
            continue
        parsed = _parse_info_string_tokens(line)
        if parsed.get("source") not in _PROOF_INFO_SOURCES:
            continue
        info = parsed
    return info


def _parse_info_string_tokens(line: str) -> dict[str, str]:
    info: dict[str, str] = {}
    for token in line[len("info string ") :].split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        info[key] = value
    return info


def _int_or_none(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _bool_flag(value: str | None) -> bool:
    return value in {"1", "true", "True", "yes"}


def _none_info_value(value: str | None) -> str | None:
    if value is None or value in {"", "none", "None"}:
        return None
    return value


def _cli_main() -> int:
    try:
        return main()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_cli_main())
