from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from xiangqi_core import Position
from xiangqi_evaluators import UciEngine, split_engine_command, starts_with_uci_token

from compare_perft import (
    REPORT_SCHEMA_VERSION as PERFT_REPORT_SCHEMA_VERSION,
    PerftCase,
    _cases_from_config as _perft_cases_from_config,
    _compare_case as _compare_perft_case,
    _comparison_summary as _perft_comparison_summary,
    _config_digest as _perft_config_digest,
    _engine_error_case as _perft_engine_error_case,
)
from uci_search_probe import (
    REPORT_SCHEMA_VERSION as SEARCH_PROBE_REPORT_SCHEMA_VERSION,
    ProbeCase,
    _cases_from_config as _search_probe_cases_from_config,
    _config_digest as _search_probe_config_digest,
    _engine_error_case as _search_probe_engine_error_case,
    _probe_case as _search_probe_case,
    _probe_summary as _search_probe_summary,
)

from play_uci_match import (
    REPORT_SCHEMA_VERSION,
    GameReport,
    MatchStart,
    PlyRecord,
    UciOption,
    _acceptance_summary,
    _clock_config,
    _clock_config_from_args,
    _command_provenance,
    _config_digest,
    _game_dict,
    _games_digest,
    _match_starts,
    _option_config,
    _parse_uci_options,
    _start_config,
    _suite_tags_from_args,
    _summary,
    run_game,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a resumable batch of local-rule-validated UCI Xiangqi matches."
    )
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
    parser.add_argument("--batches", type=int, default=1)
    parser.add_argument("--games-per-batch", type=int, default=1)
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
    parser.add_argument("--batch-dir", default="artifacts/match_batches")
    parser.add_argument("--resume", action="store_true", help="Reuse existing batch report files")
    parser.add_argument("--report")
    parser.add_argument(
        "--preflight-perft-engine",
        help="Optional UCI engine command used to compare local perft before running match batches.",
    )
    parser.add_argument(
        "--preflight-perft-config",
        help="Optional perft corpus JSON. If omitted, the match starts are checked.",
    )
    parser.add_argument("--preflight-perft-depth", type=int, default=1)
    parser.add_argument("--preflight-perft-timeout", type=float, default=10.0)
    parser.add_argument(
        "--preflight-perft-require-root-divide",
        action="store_true",
        help="Fail perft preflight when the external engine does not emit root divide lines.",
    )
    parser.add_argument("--preflight-perft-report")
    parser.add_argument(
        "--preflight-search-engine",
        help="Optional UCI engine command used to probe searched bestmove/PV legality before match batches.",
    )
    parser.add_argument(
        "--preflight-search-config",
        help="Optional search-probe corpus JSON. If omitted, the match starts are checked.",
    )
    parser.add_argument("--preflight-search-go", default="go depth 1")
    parser.add_argument("--preflight-search-timeout", type=float, default=10.0)
    parser.add_argument(
        "--preflight-search-option",
        action="append",
        default=None,
        metavar="NAME=VALUE",
        help="UCI option forwarded to the search-probe engine; repeatable.",
    )
    parser.add_argument(
        "--preflight-search-require-pv",
        action="store_true",
        help="Fail search-probe preflight when the external engine does not emit a PV root move.",
    )
    parser.add_argument("--preflight-search-report")
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

    if args.batches <= 0:
        raise ValueError("--batches must be positive")
    if args.games_per_batch <= 0:
        raise ValueError("--games-per-batch must be positive")
    if args.max_plies <= 0:
        raise ValueError("--max-plies must be positive")
    if not starts_with_uci_token(args.go, "go"):
        raise ValueError("--go must start with 'go'")
    if args.timeout <= 0:
        raise ValueError("--timeout must be positive")
    clock = _clock_config_from_args(args)
    if args.preflight_perft_depth < 0:
        raise ValueError("--preflight-perft-depth must be non-negative")
    if args.preflight_perft_timeout <= 0:
        raise ValueError("--preflight-perft-timeout must be positive")
    if not args.preflight_perft_engine and (
        args.preflight_perft_config or args.preflight_perft_report
    ):
        raise ValueError("--preflight-perft-engine is required for perft preflight options")
    if not starts_with_uci_token(args.preflight_search_go, "go"):
        raise ValueError("--preflight-search-go must start with 'go'")
    if args.preflight_search_timeout <= 0:
        raise ValueError("--preflight-search-timeout must be positive")
    if not args.preflight_search_engine and (
        args.preflight_search_config
        or args.preflight_search_report
        or args.preflight_search_option
        or args.preflight_search_require_pv
    ):
        raise ValueError("--preflight-search-engine is required for search preflight options")
    if args.accept_min_games < 0:
        raise ValueError("--accept-min-games must be non-negative")
    if bool(args.accept_candidate) != bool(args.accept_baseline):
        raise ValueError("--accept-candidate and --accept-baseline must be provided together")

    starts = _match_starts(args)
    initial_red_label = args.red_name or args.red
    initial_black_label = args.black_name or args.black
    initial_red_options = _parse_uci_options(args.red_option)
    initial_black_options = _parse_uci_options(args.black_option)
    config = _run_config(
        args,
        starts,
        initial_red_label,
        initial_black_label,
        initial_red_options,
        initial_black_options,
        clock,
    )
    batch_dir = Path(args.batch_dir)
    batch_dir.mkdir(parents=True, exist_ok=True)

    if args.resume:
        _ensure_resume_compatible_batches(batch_dir, args.batches, config)

    preflight_perft = _run_perft_preflight(args, starts)
    preflight_search = _run_search_preflight(args, starts)
    preflight_failures = _preflight_failure_reasons(preflight_perft, preflight_search)
    if preflight_failures:
        output = {
            "report_type": "uci_match_batch_aggregate",
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "config": config,
            "config_digest": _config_digest(
                _resume_relevant_config(config),
                scope="resume_relevant",
            ),
            "valid": False,
            "accepted": False if args.accept_candidate else None,
            "acceptance": {
                "accepted": False,
                "candidate": args.accept_candidate,
                "baseline": args.accept_baseline,
                "min_games": args.accept_min_games,
                "min_elo_lower": args.accept_min_elo_lower,
                "candidate_scored_games": 0,
                "candidate_invalid_games": 0,
                "baseline_invalid_games": 0,
                "candidate_unfinished_games": 0,
                "baseline_unfinished_games": 0,
                "candidate_unknown_rule_state": 0,
                "baseline_unknown_rule_state": 0,
                "candidate_time_forfeit_losses": 0,
                "baseline_time_forfeit_losses": 0,
                "candidate_emergency_moves": 0,
                "baseline_emergency_moves": 0,
                "candidate_unclassified_moves": 0,
                "candidate_none_moves": 0,
                "candidate_external_fallback_moves": 0,
                "candidate_legacy_fallback_moves": 0,
                "candidate_invalid_proof_telemetry_moves": 0,
                "baseline_invalid_proof_telemetry_moves": 0,
                "candidate_score_rate": None,
                "candidate_score_rate_ci95": None,
                "candidate_elo_diff": None,
                "candidate_elo_diff_ci95": None,
                "evidence_class": "closed",
                "closed_elo_eligible": False,
                "closed_elo_diff": None,
                "closed_elo_diff_ci95": None,
                "baseline_score_rate": None,
                "baseline_elo_diff": None,
                "reasons": preflight_failures,
            }
            if args.accept_candidate
            else None,
            "preflight_perft": preflight_perft,
            "preflight_search": preflight_search,
            "summary": _summary([]),
            "batches": [],
            "games": [],
        }
        _emit(output, args.report)
        return 1

    reports: list[GameReport] = []
    batch_summaries: list[dict[str, Any]] = []
    for batch_number in range(1, args.batches + 1):
        batch_path = _batch_path(batch_dir, batch_number)
        if args.resume and batch_path.exists():
            batch_output = json.loads(batch_path.read_text(encoding="utf-8"))
            batch_reports = [_game_report_from_dict(game) for game in batch_output["games"]]
            status = "loaded"
        else:
            batch_reports = _run_batch(args, starts, batch_number, clock)
            batch_output = _batch_output(batch_number, batch_reports, config)
            batch_path.write_text(
                json.dumps(batch_output, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            status = "ran"

        reports.extend(batch_reports)
        batch_summaries.append(
            {
                "batch": batch_number,
                "status": status,
                "report": str(batch_path),
                "games": len(batch_reports),
                "valid": all(report.valid for report in batch_reports),
                "summary": batch_output.get("summary", _summary(batch_reports)),
            }
        )

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
        "report_type": "uci_match_batch_aggregate",
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "config": config,
        "config_digest": _config_digest(
            _resume_relevant_config(config),
            scope="resume_relevant",
        ),
        "valid": all(report.valid for report in reports),
        "accepted": acceptance["accepted"] if acceptance is not None else None,
        "acceptance": acceptance,
        "preflight_perft": preflight_perft,
        "preflight_search": preflight_search,
        "summary": summary,
        "batches": batch_summaries,
        "games": [_game_dict(report) for report in reports],
    }
    _emit(output, args.report)
    return 0 if output["valid"] and (acceptance is None or acceptance["accepted"]) else 1


def _run_batch(
    args: argparse.Namespace,
    starts: list[MatchStart],
    batch_number: int,
    clock,
) -> list[GameReport]:
    initial_red_label = args.red_name or args.red
    initial_black_label = args.black_name or args.black
    initial_red_options = _parse_uci_options(args.red_option)
    initial_black_options = _parse_uci_options(args.black_option)
    reports: list[GameReport] = []
    game_base = (batch_number - 1) * len(starts) * args.games_per_batch
    for start_index, start in enumerate(starts):
        start_base = game_base + start_index * args.games_per_batch
        for index in range(args.games_per_batch):
            global_pair_index = (
                (batch_number - 1) * len(starts) * args.games_per_batch
                + start_index * args.games_per_batch
                + index
            )
            swap = args.alternate_colors and global_pair_index % 2 == 1
            red_command = args.black if swap else args.red
            black_command = args.red if swap else args.black
            red_label = initial_black_label if swap else initial_red_label
            black_label = initial_red_label if swap else initial_black_label
            red_options = initial_black_options if swap else initial_red_options
            black_options = initial_red_options if swap else initial_black_options
            reports.append(
                run_game(
                    game=start_base + index + 1,
                    red_command=red_command,
                    black_command=black_command,
                    red_label=red_label,
                    black_label=black_label,
                    start=start,
                    go_command=args.go,
                    max_plies=args.max_plies,
                    clock=clock,
                    timeout=args.timeout,
                    red_options=red_options,
                    black_options=black_options,
                )
            )
    return reports


def _batch_output(
    batch_number: int,
    reports: list[GameReport],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    games = [_game_dict(report) for report in reports]
    return {
        "report_type": "uci_match_batch",
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "batch": batch_number,
        "config": config,
        "config_digest": _config_digest(
            _resume_relevant_config(config or {}),
            scope="resume_relevant",
        )
        if config is not None
        else None,
        "valid": all(report.valid for report in reports),
        "summary": _summary(reports),
        "games": games,
        "games_digest": _games_digest(games),
    }


def _batch_path(batch_dir: Path, batch_number: int) -> Path:
    return batch_dir / f"match_batch_{batch_number:04d}.json"


def _ensure_resume_compatible_batches(
    batch_dir: Path,
    batches: int,
    config: dict[str, Any],
) -> None:
    expected_games = int(config["games_per_batch"]) * len(config["starts"])
    for batch_number in range(1, batches + 1):
        batch_path = _batch_path(batch_dir, batch_number)
        if not batch_path.exists():
            continue
        batch_output = json.loads(batch_path.read_text(encoding="utf-8"))
        _ensure_resume_compatible(
            batch_path,
            batch_number,
            batch_output,
            config,
            expected_games,
        )


def _ensure_resume_compatible(
    batch_path: Path,
    batch_number: int,
    batch_output: dict[str, Any],
    config: dict[str, Any],
    expected_games: int,
) -> None:
    if batch_output.get("batch") != batch_number:
        raise ValueError(
            f"--resume refuses {batch_path}: stored batch number "
            f"{batch_output.get('batch')!r} does not match {batch_number}"
        )
    stored_games = batch_output.get("games")
    if not isinstance(stored_games, list):
        raise ValueError(f"--resume refuses {batch_path}: stored games must be a list")
    if len(stored_games) != expected_games:
        raise ValueError(
            f"--resume refuses {batch_path}: expected {expected_games} games, "
            f"found {len(stored_games)}"
        )
    expected_games_digest = _games_digest(stored_games)
    stored_games_digest = batch_output.get("games_digest")
    if stored_games_digest != expected_games_digest:
        raise ValueError(
            f"--resume refuses {batch_path}: stored games digest "
            f"{stored_games_digest!r} does not match {expected_games_digest!r}"
        )
    expected_game_numbers = list(
        range((batch_number - 1) * expected_games + 1, batch_number * expected_games + 1)
    )
    stored_game_numbers = [game.get("game") for game in stored_games]
    if stored_game_numbers != expected_game_numbers:
        raise ValueError(
            f"--resume refuses {batch_path}: stored game numbers "
            f"{stored_game_numbers!r} do not match {expected_game_numbers!r}"
        )
    summary = batch_output.get("summary")
    if not isinstance(summary, dict):
        raise ValueError(f"--resume refuses {batch_path}: stored summary must be an object")
    if summary.get("games") != expected_games:
        raise ValueError(
            f"--resume refuses {batch_path}: stored summary reports "
            f"{summary.get('games')!r} games, expected {expected_games}"
        )
    try:
        reports = [_game_report_from_dict(game) for game in stored_games]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"--resume refuses {batch_path}: stored game payload is invalid: {exc}"
        ) from exc
    if summary != _summary(reports):
        raise ValueError(
            f"--resume refuses {batch_path}: stored summary does not match game payload"
        )
    if batch_output.get("valid") != all(report.valid for report in reports):
        raise ValueError(
            f"--resume refuses {batch_path}: stored valid flag does not match game payload"
        )
    stored_config = batch_output.get("config")
    if not isinstance(stored_config, dict):
        raise ValueError(
            f"--resume refuses {batch_path}: stored batch is missing a config snapshot"
        )
    stored_report_type = batch_output.get("report_type")
    if stored_report_type != "uci_match_batch":
        raise ValueError(
            f"--resume refuses {batch_path}: stored report_type "
            f"{stored_report_type!r} is not 'uci_match_batch'"
        )
    stored_schema_version = batch_output.get("report_schema_version")
    if stored_schema_version != REPORT_SCHEMA_VERSION:
        raise ValueError(
            f"--resume refuses {batch_path}: stored schema version "
            f"{stored_schema_version!r} does not match {REPORT_SCHEMA_VERSION}"
        )
    mismatches = _resume_config_mismatches(stored_config, config)
    expected_digest = _config_digest(_resume_relevant_config(config), scope="resume_relevant")
    stored_digest = batch_output.get("config_digest")
    if not isinstance(stored_digest, dict):
        raise ValueError(
            f"--resume refuses {batch_path}: stored batch is missing a config digest"
        )
    if stored_digest != expected_digest:
        detail = ""
        if mismatches:
            detail = f"; differing config paths: {', '.join(mismatches[:8])}"
        raise ValueError(
            f"--resume refuses {batch_path}: stored config digest "
            f"{stored_digest!r} does not match {expected_digest!r}{detail}"
        )
    if mismatches:
        paths = ", ".join(mismatches[:8])
        if len(mismatches) > 8:
            paths += f", ... ({len(mismatches)} total)"
        raise ValueError(
            f"--resume refuses {batch_path}: stored batch config differs at {paths}; "
            "use a fresh --batch-dir or the original match configuration"
        )


def _resume_config_mismatches(
    stored_config: dict[str, Any],
    current_config: dict[str, Any],
) -> list[str]:
    return _value_mismatches(
        _resume_relevant_config(stored_config),
        _resume_relevant_config(current_config),
        "config",
    )


def _resume_relevant_config(config: dict[str, Any]) -> dict[str, Any]:
    ignored_top_level = {"acceptance", "batch_dir", "batches", "resume"}
    relevant: dict[str, Any] = {}
    for key, value in config.items():
        if key in ignored_top_level:
            continue
        if key in {"preflight_perft", "preflight_search"} and isinstance(value, dict):
            relevant[key] = {
                subkey: subvalue for subkey, subvalue in value.items() if subkey != "report"
            }
        else:
            relevant[key] = value
    return relevant


def _value_mismatches(left: Any, right: Any, path: str) -> list[str]:
    if isinstance(left, dict) and isinstance(right, dict):
        mismatches: list[str] = []
        for key in sorted(set(left) | set(right)):
            child_path = f"{path}.{key}"
            if key not in left or key not in right:
                mismatches.append(child_path)
                continue
            mismatches.extend(_value_mismatches(left[key], right[key], child_path))
        return mismatches
    if isinstance(left, list) and isinstance(right, list):
        mismatches = []
        common = min(len(left), len(right))
        for index in range(common):
            mismatches.extend(
                _value_mismatches(left[index], right[index], f"{path}[{index}]")
            )
        if len(left) != len(right):
            mismatches.append(f"{path}.length")
        return mismatches
    return [] if left == right else [path]


def _run_config(
    args: argparse.Namespace,
    starts: list[MatchStart],
    red_label: str,
    black_label: str,
    red_options: tuple[tuple[str, str], ...],
    black_options: tuple[tuple[str, str], ...],
    clock,
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
        "batches": args.batches,
        "games_per_batch": args.games_per_batch,
        "alternate_colors": bool(args.alternate_colors),
        "fen": args.fen if not args.position and not args.suite else None,
        "position": args.position,
        "suite": args.suite,
        "suite_tags": list(_suite_tags_from_args(args)),
        "go": args.go,
        "clock": _clock_config(clock),
        "max_plies": args.max_plies,
        "timeout": args.timeout,
        "batch_dir": args.batch_dir,
        "resume": bool(args.resume),
        "preflight_perft": {
            "engine": args.preflight_perft_engine,
            "config": args.preflight_perft_config,
            "depth": args.preflight_perft_depth,
            "timeout": args.preflight_perft_timeout,
            "require_root_divide": bool(args.preflight_perft_require_root_divide),
            "report": args.preflight_perft_report,
        },
        "preflight_search": {
            "engine": args.preflight_search_engine,
            "config": args.preflight_search_config,
            "go": args.preflight_search_go,
            "timeout": args.preflight_search_timeout,
            "options": _option_config(_parse_uci_options(args.preflight_search_option)),
            "require_pv": bool(args.preflight_search_require_pv),
            "report": args.preflight_search_report,
        },
        "acceptance": {
            "candidate": args.accept_candidate,
            "baseline": args.accept_baseline,
            "min_games": args.accept_min_games,
            "min_elo_lower": args.accept_min_elo_lower,
        },
        "starts": [_start_config(start) for start in starts],
    }


def _game_report_from_dict(data: dict[str, Any]) -> GameReport:
    records = tuple(
        PlyRecord(
            ply=int(record["ply"]),
            side=str(record["side"]),
            engine=str(record["engine"]),
            position=str(record["position"]),
            go_command=str(record.get("go_command", "")),
            bestmove=str(record["bestmove"]),
            source=record.get("source"),
            status=record.get("status"),
            reason=record.get("reason"),
            nodes=record.get("nodes"),
            max_ply=record.get("max_ply"),
            node_limit=record.get("node_limit"),
            elapsed_ms=record.get("elapsed_ms"),
            external_ordering_elapsed_ms=record.get("external_ordering_elapsed_ms"),
            proof_search_elapsed_ms=record.get("proof_search_elapsed_ms"),
            total_search_elapsed_ms=record.get("total_search_elapsed_ms"),
            red_time_before_ms=record.get("red_time_before_ms"),
            black_time_before_ms=record.get("black_time_before_ms"),
            red_time_after_ms=record.get("red_time_after_ms"),
            black_time_after_ms=record.get("black_time_after_ms"),
            time_limit_ms=record.get("time_limit_ms"),
            time_limit_reached=bool(record.get("time_limit_reached", False)),
            resolved_store_hits=record.get("resolved_store_hits"),
            resolved_store_misses=record.get("resolved_store_misses"),
            proof_store_saved=bool(record.get("proof_store_saved", False)),
            proof_store_save_error=record.get("proof_store_save_error"),
            proof_artifact_sha256=record.get("proof_artifact_sha256"),
            engine_lines=tuple(record.get("engine_lines", ())),
        )
        for record in data.get("records", ())
    )
    return GameReport(
        game=int(data["game"]),
        red_engine=str(data["red_engine"]),
        black_engine=str(data["black_engine"]),
        start_name=str(data["start_name"]),
        start_fen=str(data["start_fen"]),
        start_position=str(data["start_position"]),
        start_moves=tuple(data.get("start_moves", ())),
        start_tags=tuple(str(tag) for tag in data.get("start_tags", ())),
        valid=bool(data["valid"]),
        result=str(data["result"]),
        reason=str(data["reason"]),
        plies=int(data["plies"]),
        moves=tuple(data.get("moves", ())),
        illegal=data.get("illegal"),
        records=records,
        red_options=_uci_options_from_report(data.get("red_options")),
        black_options=_uci_options_from_report(data.get("black_options")),
        forfeit=data.get("forfeit"),
    )


def _uci_options_from_report(values: Any) -> tuple[UciOption, ...]:
    options: list[UciOption] = []
    for item in values or ():
        if isinstance(item, dict):
            options.append(UciOption(name=str(item["name"]), value=str(item["value"])))
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            options.append(UciOption(name=str(item[0]), value=str(item[1])))
        else:
            raise ValueError("stored UCI option must be an object or two-item list")
    return tuple(options)


def _run_perft_preflight(
    args: argparse.Namespace,
    starts: list[MatchStart],
) -> dict[str, Any] | None:
    if not args.preflight_perft_engine:
        return None
    cases = (
        _perft_cases_from_config(args.preflight_perft_config, args.preflight_perft_depth)
        if args.preflight_perft_config
        else _perft_cases_from_starts(starts, args.preflight_perft_depth)
    )
    entries: list[dict[str, Any]] = []
    output = {
        "report_type": "perft_compare",
        "report_schema_version": PERFT_REPORT_SCHEMA_VERSION,
        "valid": True,
        "engine": args.preflight_perft_engine,
        "config": args.preflight_perft_config,
        "depth": args.preflight_perft_depth,
        "report_config": _preflight_perft_report_config(args, cases),
        "config_digest": None,
        "count": len(cases),
        "completed": 0,
        "failures": 0,
        "entries": entries,
        "summary": None,
        "error": None,
        "phase": None,
    }
    output["config_digest"] = _perft_config_digest(output["report_config"])
    try:
        with UciEngine(
            split_engine_command(args.preflight_perft_engine),
            timeout=args.preflight_perft_timeout,
        ) as engine:
            try:
                engine.initialize()
            except Exception as exc:
                entries.extend(
                    _perft_engine_error_case(case, exc, phase="initialize")
                    for case in cases
                )
                output["valid"] = False
                output["completed"] = 0
                output["failures"] = len(cases)
                output["error"] = str(exc)
                output["phase"] = "initialize"
                output["summary"] = _preflight_perft_summary(entries)
                _write_report(output, args.preflight_perft_report)
                return output

            for case in cases:
                try:
                    entries.append(
                        _compare_perft_case(
                            engine,
                            case,
                            require_root_divide=args.preflight_perft_require_root_divide,
                        )
                    )
                except Exception as exc:
                    entries.append(
                        _perft_engine_error_case(
                            case,
                            exc,
                            phase="compare",
                            require_root_divide=args.preflight_perft_require_root_divide,
                        )
                    )
                    output["valid"] = False
                    output["error"] = str(exc)
                    output["phase"] = "compare"
                    break
    except Exception as exc:
        output["valid"] = False
        output["failures"] = max(1, len(cases))
        output["error"] = str(exc)
        output["phase"] = output["phase"] or "engine"
        output["summary"] = _preflight_perft_summary(entries)
        _write_report(output, args.preflight_perft_report)
        return output

    output["completed"] = len(entries)
    output["failures"] = sum(1 for entry in entries if not entry["valid"])
    output["valid"] = output["valid"] and all(entry["valid"] for entry in entries)
    output["summary"] = _preflight_perft_summary(entries)
    _write_report(output, args.preflight_perft_report)
    return output


def _run_search_preflight(
    args: argparse.Namespace,
    starts: list[MatchStart],
) -> dict[str, Any] | None:
    if not args.preflight_search_engine:
        return None
    cases = (
        _search_probe_cases_from_config(args.preflight_search_config)
        if args.preflight_search_config
        else _search_probe_cases_from_starts(starts)
    )
    options = _parse_uci_options(args.preflight_search_option)
    entries: list[dict[str, Any]] = []
    output = {
        "report_type": "uci_search_probe",
        "report_schema_version": SEARCH_PROBE_REPORT_SCHEMA_VERSION,
        "valid": True,
        "engine": args.preflight_search_engine,
        "config": args.preflight_search_config,
        "go": args.preflight_search_go,
        "report_config": _preflight_search_report_config(args, cases, options),
        "config_digest": None,
        "count": len(cases),
        "completed": 0,
        "failures": 0,
        "entries": entries,
        "summary": None,
        "error": None,
        "phase": None,
    }
    output["config_digest"] = _search_probe_config_digest(output["report_config"])
    try:
        with UciEngine(
            split_engine_command(args.preflight_search_engine),
            timeout=args.preflight_search_timeout,
        ) as engine:
            try:
                engine.initialize()
                for name, value in options:
                    engine.set_option(name, value)
                engine.new_game()
            except Exception as exc:
                entries.extend(
                    _search_probe_engine_error_case(
                        case,
                        exc,
                        phase="initialize",
                        require_pv=args.preflight_search_require_pv,
                    )
                    for case in cases
                )
                output["valid"] = False
                output["completed"] = 0
                output["failures"] = len(cases)
                output["error"] = str(exc)
                output["phase"] = "initialize"
                output["summary"] = _search_probe_summary(entries)
                _write_report(output, args.preflight_search_report)
                return output

            for case in cases:
                try:
                    entries.append(
                        _search_probe_case(
                            engine,
                            case,
                            go_command=args.preflight_search_go,
                            require_pv=args.preflight_search_require_pv,
                        )
                    )
                except Exception as exc:
                    entries.append(
                        _search_probe_engine_error_case(
                            case,
                            exc,
                            phase="probe",
                            require_pv=args.preflight_search_require_pv,
                        )
                    )
                    output["valid"] = False
                    output["error"] = str(exc)
                    output["phase"] = "probe"
                    break
    except Exception as exc:
        output["valid"] = False
        output["failures"] = max(1, len(cases))
        output["error"] = str(exc)
        output["phase"] = output["phase"] or "engine"
        output["summary"] = _search_probe_summary(entries)
        _write_report(output, args.preflight_search_report)
        return output

    output["completed"] = len(entries)
    output["failures"] = sum(1 for entry in entries if not entry["valid"])
    output["valid"] = output["valid"] and all(entry["valid"] for entry in entries)
    output["summary"] = _search_probe_summary(entries)
    _write_report(output, args.preflight_search_report)
    return output


def _preflight_perft_summary(entries: list[dict[str, Any]]) -> dict[str, Any]:
    summary = _perft_comparison_summary(entries)
    return {
        **summary,
        "engine_errors": sum(
            1 for entry in entries if "engine_error" in entry.get("failure_reasons", [])
        ),
    }


def _preflight_perft_report_config(
    args: argparse.Namespace,
    cases: list[PerftCase],
) -> dict[str, Any]:
    return {
        "engine": args.preflight_perft_engine,
        "config": args.preflight_perft_config,
        "position": None,
        "fen": None,
        "depth": args.preflight_perft_depth,
        "timeout": args.preflight_perft_timeout,
        "require_root_divide": bool(args.preflight_perft_require_root_divide),
        "starts": [
            {
                "name": case.name,
                "fen": case.position.to_fen(),
                "position": case.position_command,
                "depth": case.depth,
            }
            for case in cases
        ],
    }


def _preflight_search_report_config(
    args: argparse.Namespace,
    cases: list[ProbeCase],
    options: tuple[tuple[str, str], ...],
) -> dict[str, Any]:
    return {
        "engine": args.preflight_search_engine,
        "config": args.preflight_search_config,
        "position": None,
        "fen": None,
        "go": args.preflight_search_go,
        "timeout": args.preflight_search_timeout,
        "options": [{"name": name, "value": value} for name, value in options],
        "require_pv": bool(args.preflight_search_require_pv),
        "starts": [
            {
                "name": case.name,
                "fen": case.state.to_fen(),
                "position": case.position_command,
            }
            for case in cases
        ],
    }


def _perft_cases_from_starts(starts: list[MatchStart], depth: int) -> list[PerftCase]:
    return [
        PerftCase(
            name=start.name,
            position=start.state.position,
            position_command=start.state.to_uci_position(),
            depth=depth,
        )
        for start in starts
    ]


def _search_probe_cases_from_starts(starts: list[MatchStart]) -> list[ProbeCase]:
    return [
        ProbeCase(
            name=start.name,
            state=start.state,
            position_command=start.state.to_uci_position(),
        )
        for start in starts
    ]


def _preflight_failure_reasons(
    preflight_perft: dict[str, Any] | None,
    preflight_search: dict[str, Any] | None,
) -> list[str]:
    reasons: list[str] = []
    if preflight_perft is not None and not preflight_perft.get("valid", False):
        reasons.append("preflight_perft_failed")
    if preflight_search is not None and not preflight_search.get("valid", False):
        reasons.append("preflight_search_failed")
    return reasons


def _emit(output: dict[str, Any], report: str | None) -> None:
    text = json.dumps(output, ensure_ascii=False, indent=2)
    _write_text(text, report)
    print(text)


def _write_report(output: dict[str, Any], report: str | None) -> None:
    if report:
        _write_text(json.dumps(output, ensure_ascii=False, indent=2), report)


def _write_text(text: str, report: str | None) -> None:
    if not report:
        return
    report_path = Path(report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(text + "\n", encoding="utf-8")


def _cli_main() -> int:
    try:
        return main()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_cli_main())
