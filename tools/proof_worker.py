from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from xiangqi_core import GameState, Position
from xiangqi_evaluators import (
    ChessDbClient,
    ChessDbMoveOrderer,
    PersistentUciBestMoveOrderer,
    UciBestMoveOrderer,
    parse_uci_options,
    split_engine_command,
)
from xiangqi_solver import ProofCycleError, run_proof_cycle


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one proof target in repeatable slices.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--base-dir", default=str(ROOT))
    parser.add_argument("--max-rounds", type=int)
    parser.add_argument("--wall-time-seconds", type=float)
    parser.add_argument("--resume-existing", action="store_true")
    parser.add_argument("--report")
    args = parser.parse_args()

    started_at = _utc_now()
    started = perf_counter()
    config_path = Path(args.config)
    base_dir = Path(args.base_dir)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    max_rounds = args.max_rounds or int(config.get("max_rounds", 1))
    wall_time_seconds = (
        args.wall_time_seconds
        if args.wall_time_seconds is not None
        else _optional_float(config.get("wall_time_seconds"))
    )
    if max_rounds <= 0:
        return _emit(
            _finish(
                {"valid": False, "error": "max_rounds must be positive"},
                started_at,
                started,
            ),
            args.report,
            2,
        )

    artifact_path = _path_setting(config, "artifact", base_dir)
    rounds: list[dict[str, Any]] = []
    stop_reason = "max_rounds"
    move_orderer = _move_orderer(config)

    try:
        for index in range(1, max_rounds + 1):
            if _time_limit_reached(wall_time_seconds, started):
                stop_reason = "wall_time"
                break
            round_started_at = _utc_now()
            round_started = perf_counter()
            resume_artifact = _should_resume(config, artifact_path, index, args.resume_existing)
            try:
                output = run_proof_cycle(
                    _root_state(config, resume_artifact),
                    target=str(config.get("target", "red")),
                    store_path=_path_setting(config, "store", base_dir),
                    artifact_path=artifact_path,
                    initial_ply=int(config.get("initial_ply", 0)),
                    extra_ply=int(config.get("extra_ply", 1)),
                    cycles=int(config.get("cycles", 1)),
                    frontier_limit=int(config.get("frontier_limit", 10)),
                    frontier_reasons=_movelist_setting(config.get("frontier_reasons", ())),
                    frontier_max_attempts=_optional_int(config.get("frontier_max_attempts")),
                    frontier_min_remaining_ply=_optional_int(
                        config.get("frontier_min_remaining_ply")
                    ),
                    frontier_max_remaining_ply=_optional_int(
                        config.get("frontier_max_remaining_ply")
                    ),
                    frontier_max_proof=_optional_int(config.get("frontier_max_proof")),
                    frontier_max_disproof=_optional_int(config.get("frontier_max_disproof")),
                    node_limit=int(config.get("node_limit", 100_000)),
                    resume_artifact=resume_artifact,
                    searcher=str(config.get("searcher", "bounded")),
                    reuse_store=bool(config.get("reuse_store", False)),
                    proof_threshold=int(config.get("proof_threshold", 10**12)),
                    disproof_threshold=int(config.get("disproof_threshold", 10**12)),
                    dfpn_iterative=bool(config.get("dfpn_iterative", False)),
                    dfpn_iterations=int(config.get("dfpn_iterations", 4)),
                    threshold_growth=int(config.get("threshold_growth", 2)),
                    move_orderer=move_orderer,
                    time_limit_seconds=_optional_float(config.get("time_limit_seconds")),
                    node_budget=_optional_int(config.get("node_budget")),
                    reset_running_max_age_seconds=_optional_float(
                        config.get("reset_running_max_age_seconds")
                    ),
                )
            except ProofCycleError as exc:
                rounds.append(
                    _finish(
                        {
                            "index": index,
                            "valid": False,
                            "resumed": resume_artifact,
                            "errors": exc.errors,
                        },
                        round_started_at,
                        round_started,
                    )
                )
                stop_reason = "error"
                break

            round_output = _finish(
                {"index": index, "valid": True, **output},
                round_started_at,
                round_started,
            )
            rounds.append(round_output)

            stop_reason = _stop_reason(round_output)
            if stop_reason != "continue":
                break
    finally:
        _close_move_orderer(move_orderer)

    final_round = rounds[-1] if rounds else {}
    result = _finish(
        {
            "valid": all(round.get("valid") for round in rounds),
            "wall_time_seconds": wall_time_seconds,
            "stop_reason": stop_reason,
            "rounds": rounds,
            "final_status": final_round.get("status"),
            "final_database": final_round.get("database"),
            "final_frontier_metrics": final_round.get("frontier_metrics"),
        },
        started_at,
        started,
    )
    return _emit(result, args.report, 0 if result["valid"] else 2)


def _root_state(config: dict[str, Any], resume_artifact: bool):
    if resume_artifact:
        return None
    has_fen = "fen" in config
    has_position = "position" in config
    if has_fen == has_position:
        raise ProofCycleError(("config must contain exactly one of 'fen' or 'position'",))
    if has_position:
        return GameState.from_uci_position(str(config["position"]))
    return Position.from_fen(str(config["fen"]))


def _should_resume(
    config: dict[str, Any],
    artifact_path: Path,
    round_index: int,
    resume_existing: bool,
) -> bool:
    if bool(config.get("resume_artifact", False)):
        return True
    if round_index > 1:
        return True
    return resume_existing and artifact_path.exists()


def _stop_reason(round_output: dict[str, Any]) -> str:
    if not round_output.get("valid", False):
        return "error"
    if round_output.get("status") != "unknown":
        return "resolved"
    frontier = round_output.get("database", {}).get("frontier_jobs", {})
    if frontier.get("pending", 0) <= 0:
        return "frontier_empty"
    if _made_progress(round_output):
        return "continue"
    if round_output.get("node_budget_reached"):
        return "node_budget_without_progress"
    if round_output.get("time_limit_reached"):
        return "time_limit_without_progress"
    return "no_progress"


def _made_progress(round_output: dict[str, Any]) -> bool:
    return (
        bool(round_output.get("processed"))
        or int(round_output.get("initial_total_nodes_searched", 0)) > 0
        or int(round_output.get("nodes_consumed", 0)) > 0
    )


def _path_setting(config: dict[str, Any], key: str, base_dir: Path) -> Path:
    value = config.get(key)
    if value is None:
        raise ProofCycleError((f"missing required path setting: {key}",))
    path = Path(str(value))
    return path if path.is_absolute() else base_dir / path


def _movelist_setting(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        values = value
    else:
        values = [str(value)]
    moves: list[str] = []
    for item in values:
        moves.extend(move for move in str(item).split("|") if move)
    return tuple(moves)


def _uci_option_values(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return (str(value),)


def _move_orderer(config: dict[str, Any]):
    chessdb_ordering = bool(config.get("chessdb_ordering", False))
    uci_engine = config.get("uci_engine")
    if chessdb_ordering and uci_engine:
        raise ProofCycleError(("choose only one candidate ordering source",))
    if chessdb_ordering:
        return ChessDbMoveOrderer(
            ChessDbClient(),
            egtbmetric=_optional_str(config.get("chessdb_egtbmetric")),
            ban=_movelist_setting(config.get("chessdb_ban", ())),
        )
    if uci_engine:
        orderer_type = (
            PersistentUciBestMoveOrderer
            if bool(config.get("persistent_uci_ordering", False))
            else UciBestMoveOrderer
        )
        return orderer_type(
            _split_command(str(uci_engine)),
            depth=int(config.get("uci_depth", 4)),
            options=parse_uci_options(_uci_option_values(config.get("uci_options", ()))),
        )
    return None


def _close_move_orderer(move_orderer) -> None:
    close = getattr(move_orderer, "close", None)
    if callable(close):
        close()


def _split_command(command: str) -> list[str]:
    return split_engine_command(command)


def _emit(payload: dict[str, Any], report: str | None, exit_code: int) -> int:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if report:
        report_path = Path(report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(text + "\n", encoding="utf-8")
    print(text)
    return exit_code


def _finish(payload: dict[str, Any], started_at: str, started: float) -> dict[str, Any]:
    return {
        **payload,
        "started_at": started_at,
        "finished_at": _utc_now(),
        "elapsed_seconds": round(perf_counter() - started, 6),
    }


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _time_limit_reached(limit_seconds: float | None, started: float) -> bool:
    return limit_seconds is not None and perf_counter() - started >= limit_seconds


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


if __name__ == "__main__":
    raise SystemExit(main())
