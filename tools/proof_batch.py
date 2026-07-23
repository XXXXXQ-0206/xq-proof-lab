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
    parser = argparse.ArgumentParser(description="Run proof-search cycles from a JSON batch file.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--base-dir", default=str(ROOT))
    parser.add_argument("--max-jobs", type=int)
    parser.add_argument("--time-limit-seconds", type=float)
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--report")
    args = parser.parse_args()
    batch_started_at = _utc_now()
    batch_started = perf_counter()

    config_path = Path(args.config)
    base_dir = Path(args.base_dir)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    defaults = dict(config.get("defaults", {}))
    jobs = config.get("jobs", [])
    if not isinstance(jobs, list) or not jobs:
        return _emit_result(
            {"valid": False, "error": "config must contain a non-empty jobs list"},
            args.report,
            2,
        )

    results = []
    attempted = 0
    had_error = False
    for index, job in enumerate(jobs, start=1):
        name = _job_name(index, job)
        job_started_at = _utc_now()
        job_started = perf_counter()
        if job.get("enabled", True) is False:
            results.append(
                _finish_timed(
                    {"name": name, "valid": True, "status": "skipped", "reason": "disabled"},
                    job_started_at,
                    job_started,
                )
            )
            continue
        if _time_limit_reached(args.time_limit_seconds, batch_started):
            results.append(
                _finish_timed(
                    {"name": name, "valid": True, "status": "skipped", "reason": "time_limit"},
                    job_started_at,
                    job_started,
                )
            )
            continue
        if args.max_jobs is not None and attempted >= args.max_jobs:
            results.append(
                _finish_timed(
                    {"name": name, "valid": True, "status": "skipped", "reason": "max_jobs"},
                    job_started_at,
                    job_started,
                )
            )
            continue
        attempted += 1
        try:
            results.append(
                _run_job(index, job, defaults, config, base_dir, job_started_at, job_started)
            )
        except ProofCycleError as exc:
            results.append(
                _finish_timed(
                    {"name": name, "valid": False, "errors": exc.errors},
                    job_started_at,
                    job_started,
                )
            )
            had_error = True
            if not args.continue_on_error:
                return _emit_result(
                    _finish_timed(
                        {"valid": False, "jobs": results},
                        batch_started_at,
                        batch_started,
                    ),
                    args.report,
                    2,
                )
        except Exception as exc:
            results.append(
                _finish_timed(
                    {"name": name, "valid": False, "error": str(exc)},
                    job_started_at,
                    job_started,
                )
            )
            had_error = True
            if not args.continue_on_error:
                return _emit_result(
                    _finish_timed(
                        {"valid": False, "jobs": results},
                        batch_started_at,
                        batch_started,
                    ),
                    args.report,
                    2,
                )

    return _emit_result(
        _finish_timed({"valid": not had_error, "jobs": results}, batch_started_at, batch_started),
        args.report,
        1 if had_error else 0,
    )


def _run_job(
    index: int,
    job: dict[str, Any],
    defaults: dict[str, Any],
    config: dict[str, Any],
    base_dir: Path,
    started_at: str,
    started: float,
) -> dict[str, Any]:
    name = _job_name(index, job)
    resume_artifact = bool(_setting(job, defaults, config, "resume_artifact", False))
    root_state = _root_state(job, resume_artifact)
    store_path = _path_setting(job, defaults, config, "store", base_dir)
    artifact_path = _path_setting(
        job,
        defaults,
        config,
        "artifact",
        base_dir,
        fallback=Path("artifacts") / f"{name}.json",
    )
    move_orderer = _move_orderer(job, defaults, config)
    try:
        output = run_proof_cycle(
            root_state,
            target=str(_setting(job, defaults, config, "target", "red")),
            store_path=store_path,
            artifact_path=artifact_path,
            initial_ply=int(_setting(job, defaults, config, "initial_ply", 0)),
            extra_ply=int(_setting(job, defaults, config, "extra_ply", 1)),
            cycles=int(_setting(job, defaults, config, "cycles", 1)),
            frontier_limit=int(_setting(job, defaults, config, "frontier_limit", 10)),
            frontier_reasons=_movelist_setting(_setting(job, defaults, config, "frontier_reasons", ())),
            frontier_max_attempts=_optional_int(
                _setting(job, defaults, config, "frontier_max_attempts", None)
            ),
            frontier_min_remaining_ply=_optional_int(
                _setting(job, defaults, config, "frontier_min_remaining_ply", None)
            ),
            frontier_max_remaining_ply=_optional_int(
                _setting(job, defaults, config, "frontier_max_remaining_ply", None)
            ),
            frontier_max_proof=_optional_int(
                _setting(job, defaults, config, "frontier_max_proof", None)
            ),
            frontier_max_disproof=_optional_int(
                _setting(job, defaults, config, "frontier_max_disproof", None)
            ),
            node_limit=int(_setting(job, defaults, config, "node_limit", 100_000)),
            resume_artifact=resume_artifact,
            searcher=str(_setting(job, defaults, config, "searcher", "bounded")),
            reuse_store=bool(_setting(job, defaults, config, "reuse_store", False)),
            proof_threshold=int(_setting(job, defaults, config, "proof_threshold", 10**12)),
            disproof_threshold=int(_setting(job, defaults, config, "disproof_threshold", 10**12)),
            dfpn_iterative=bool(_setting(job, defaults, config, "dfpn_iterative", False)),
            dfpn_iterations=int(_setting(job, defaults, config, "dfpn_iterations", 4)),
            threshold_growth=int(_setting(job, defaults, config, "threshold_growth", 2)),
            move_orderer=move_orderer,
            time_limit_seconds=_optional_float(_setting(job, defaults, config, "time_limit_seconds", None)),
            node_budget=_optional_int(_setting(job, defaults, config, "node_budget", None)),
            reset_running_max_age_seconds=_optional_float(
                _setting(job, defaults, config, "reset_running_max_age_seconds", None)
            ),
        )
    finally:
        _close_move_orderer(move_orderer)
    return _finish_timed(
        {
            "name": name,
            "valid": True,
            "store": str(store_path),
            "artifact": str(artifact_path),
            **output,
        },
        started_at,
        started,
    )


def _root_state(job: dict[str, Any], resume_artifact: bool):
    has_fen = "fen" in job
    has_position = "position" in job
    if resume_artifact:
        if has_fen or has_position:
            raise ValueError("resume_artifact jobs must not also contain 'fen' or 'position'")
        return None
    if has_fen == has_position:
        raise ValueError("each job must contain exactly one of 'fen' or 'position'")
    if has_position:
        return GameState.from_uci_position(str(job["position"]))
    return Position.from_fen(str(job["fen"]))


def _setting(
    job: dict[str, Any],
    defaults: dict[str, Any],
    config: dict[str, Any],
    key: str,
    fallback: Any,
) -> Any:
    if key in job:
        return job[key]
    if key in defaults:
        return defaults[key]
    if key in config:
        return config[key]
    return fallback


def _path_setting(
    job: dict[str, Any],
    defaults: dict[str, Any],
    config: dict[str, Any],
    key: str,
    base_dir: Path,
    fallback: str | Path | None = None,
) -> Path:
    value = _setting(job, defaults, config, key, fallback)
    if value is None:
        raise ValueError(f"missing required path setting: {key}")
    path = Path(value)
    return path if path.is_absolute() else base_dir / path


def _job_name(index: int, job: dict[str, Any]) -> str:
    return str(job.get("name", f"job_{index}"))


def _move_orderer(job: dict[str, Any], defaults: dict[str, Any], config: dict[str, Any]):
    chessdb_ordering = bool(_setting(job, defaults, config, "chessdb_ordering", False))
    uci_engine = _setting(job, defaults, config, "uci_engine", None)
    if chessdb_ordering and uci_engine:
        raise ValueError("choose only one candidate ordering source")
    if chessdb_ordering:
        return ChessDbMoveOrderer(
            ChessDbClient(),
            egtbmetric=_optional_str(_setting(job, defaults, config, "chessdb_egtbmetric", None)),
            ban=_movelist_setting(_setting(job, defaults, config, "chessdb_ban", ())),
        )
    if uci_engine:
        orderer_type = (
            PersistentUciBestMoveOrderer
            if bool(_setting(job, defaults, config, "persistent_uci_ordering", False))
            else UciBestMoveOrderer
        )
        return orderer_type(
            _split_command(str(uci_engine)),
            depth=int(_setting(job, defaults, config, "uci_depth", 4)),
            options=parse_uci_options(
                _uci_option_values(_setting(job, defaults, config, "uci_options", ()))
            ),
        )
    return None


def _close_move_orderer(move_orderer) -> None:
    close = getattr(move_orderer, "close", None)
    if callable(close):
        close()


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


def _emit_result(payload: dict[str, Any], report: str | None, exit_code: int) -> int:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if report:
        report_path = Path(report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(text + "\n", encoding="utf-8")
    print(text)
    return exit_code


def _finish_timed(payload: dict[str, Any], started_at: str, started: float) -> dict[str, Any]:
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


def _split_command(command: str) -> list[str]:
    return split_engine_command(command)


if __name__ == "__main__":
    raise SystemExit(main())
