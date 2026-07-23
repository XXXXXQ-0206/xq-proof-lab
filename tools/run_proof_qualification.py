from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sys
import tempfile
from pathlib import Path
from time import perf_counter
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from xiangqi_core import Color, GameState
from xiangqi_evaluators import UciEngine
from xiangqi_solver import ProofStatus, ProofStore, ProofTarget, ProofVerifier


REPORT_SCHEMA_VERSION = 2
_PROOF_SOURCES = {"proof", "proof_store"}


def run_development_ab(
    corpus_path: Path,
    *,
    proof_store_path: Path,
    max_ply: int = 2,
    node_limit: int = 100_000,
    local_search_depth: int = 2,
    local_search_node_limit: int = 5_000,
    go_command: str = "go movetime 500",
    timeout: float = 10.0,
) -> dict[str, Any]:
    if max_ply < 0:
        raise ValueError("max_ply must be non-negative")
    if node_limit <= 0:
        raise ValueError("node_limit must be positive")
    if local_search_depth <= 0:
        raise ValueError("local_search_depth must be positive")
    if local_search_node_limit <= 0:
        raise ValueError("local_search_node_limit must be positive")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if not go_command.startswith("go"):
        raise ValueError("go_command must start with go")

    corpus_bytes = corpus_path.read_bytes()
    corpus = json.loads(corpus_bytes)
    if corpus.get("report_type") != "proof_qualification_corpus":
        raise ValueError("corpus must be a proof qualification corpus")
    if corpus.get("split") != "development":
        raise ValueError("run_development_ab requires a development corpus")
    positions = corpus.get("positions")
    if not isinstance(positions, list) or not positions:
        raise ValueError("development corpus must contain positions")
    frozen_proof_store_sha256 = _frozen_proof_store_sha256(corpus)
    proof_store_initial_sha256 = _sha256_file(proof_store_path)
    if proof_store_initial_sha256 != frozen_proof_store_sha256:
        raise ValueError("proof store does not match frozen proof-store hash")

    common_args = [
        "--closed",
        "--max-ply",
        str(max_ply),
        "--node-limit",
        str(node_limit),
        "--local-search-depth",
        str(local_search_depth),
        "--local-search-node-limit",
        str(local_search_node_limit),
    ]
    local_command = [
        sys.executable,
        str(ROOT / "tools" / "proof_uci.py"),
        *common_args,
        "--local-only",
    ]
    proof_command_template = [
        sys.executable,
        str(ROOT / "tools" / "proof_uci.py"),
        *common_args,
        "--proof-store",
        "<ephemeral-proof-store>",
        "--save-online-proofs",
    ]
    config = {
        "closed": True,
        "external_inputs_allowed": False,
        "corpus_path": str(corpus_path),
        "corpus_sha256": _sha256_bytes(corpus_bytes),
        "corpus_digest": corpus.get("corpus_digest"),
        "common_budget": {
            "max_ply": max_ply,
            "node_limit": node_limit,
            "local_search_depth": local_search_depth,
            "local_search_node_limit": local_search_node_limit,
            "go_command": go_command,
            "timeout": timeout,
        },
        "proof_command": proof_command_template,
        "local_only_command": local_command,
        "proof_store": str(proof_store_path),
        "proof_store_runtime": "ephemeral_copy",
        "proof_store_initial_sha256": proof_store_initial_sha256,
        "frozen_proof_store_sha256": frozen_proof_store_sha256,
    }
    config_digest = _canonical_sha256(config)
    verifier = ProofVerifier()
    entries: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="xiangqi-proof-qualification-") as temporary_directory:
        runtime_proof_store_path = Path(temporary_directory) / proof_store_path.name
        shutil.copy2(proof_store_path, runtime_proof_store_path)
        runtime_proof_store_initial_sha256 = _sha256_file(runtime_proof_store_path)
        if runtime_proof_store_initial_sha256 != frozen_proof_store_sha256:
            raise ValueError("runtime proof-store copy does not match frozen proof-store hash")
        proof_command = [
            *proof_command_template[: proof_command_template.index("<ephemeral-proof-store>")],
            str(runtime_proof_store_path),
            *proof_command_template[proof_command_template.index("<ephemeral-proof-store>") + 1 :],
        ]
        store = ProofStore(runtime_proof_store_path)
        with UciEngine(proof_command, timeout=timeout) as proof_engine, UciEngine(
            local_command,
            timeout=timeout,
        ) as local_engine:
            proof_engine.initialize()
            local_engine.initialize()
            for index, corpus_entry in enumerate(positions):
                entries.append(
                    _run_position(
                        corpus_entry,
                        proof_engine=proof_engine,
                        local_engine=local_engine,
                        store=store,
                        verifier=verifier,
                        max_ply=max_ply,
                        go_command=go_command,
                        proof_first=index % 2 == 0,
                    )
                )
        runtime_proof_store_sha256 = _sha256_file(runtime_proof_store_path)

    frozen_proof_store_final_sha256 = _sha256_file(proof_store_path)
    if frozen_proof_store_final_sha256 != frozen_proof_store_sha256:
        raise ValueError("frozen proof store changed during A/B run")
    summary = _summary(entries)
    return {
        "report_type": "proof_qualification_development_ab",
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "valid": summary["invalid_entries"] == 0,
        "accepted": False,
        "acceptance": {
            "accepted": False,
            "reasons": ["development_measurement_only", "holdout_threshold_not_frozen"],
        },
        "config": config,
        "config_digest": config_digest,
        "artifacts": {
            "proof_store_sha256": runtime_proof_store_sha256,
            "proof_store_initial_sha256": proof_store_initial_sha256,
            "frozen_proof_store_sha256": frozen_proof_store_final_sha256,
            "runtime_proof_store_initial_sha256": runtime_proof_store_initial_sha256,
            "runtime_proof_store_sha256": runtime_proof_store_sha256,
            "proof_uci_sha256": _sha256_file(ROOT / "tools" / "proof_uci.py"),
            "core_game_sha256": _sha256_file(ROOT / "src" / "xiangqi_core" / "game.py"),
            "uci_loop_sha256": _sha256_file(ROOT / "src" / "xiangqi_solver" / "uci_loop.py"),
        },
        "summary": summary,
        "entries": entries,
    }


def _run_position(
    corpus_entry: Any,
    *,
    proof_engine: UciEngine,
    local_engine: UciEngine,
    store: ProofStore,
    verifier: ProofVerifier,
    max_ply: int,
    go_command: str,
    proof_first: bool,
) -> dict[str, Any]:
    if not isinstance(corpus_entry, dict) or not isinstance(corpus_entry.get("position"), str):
        return {"valid": False, "failure_reasons": ["invalid_corpus_entry"]}
    try:
        state = GameState.from_uci_position(corpus_entry["position"])
    except ValueError as exc:
        return {
            "selection_key": corpus_entry.get("selection_key"),
            "valid": False,
            "failure_reasons": ["invalid_corpus_position"],
            "error": str(exc),
        }
    expected_position = state.to_uci_position()
    legal_moves = {move.to_uci() for move in state.legal_moves()}
    if not legal_moves:
        return {
            "selection_key": corpus_entry.get("selection_key"),
            "position": expected_position,
            "valid": False,
            "failure_reasons": ["terminal_corpus_position"],
        }

    execution_order = ["proof", "local_only"] if proof_first else ["local_only", "proof"]
    searches: dict[str, dict[str, Any]] = {}
    for engine_name in execution_order:
        engine = proof_engine if engine_name == "proof" else local_engine
        searches[engine_name] = _search_engine(engine, expected_position, go_command, legal_moves)
    proof = searches["proof"]
    local_only = searches["local_only"]
    proof_verification = _verify_proof_result(
        proof,
        state=state,
        store=store,
        verifier=verifier,
        max_ply=max_ply,
    )
    failure_reasons: list[str] = []
    if proof["error"] is not None:
        failure_reasons.append("proof_engine_error")
    if local_only["error"] is not None:
        failure_reasons.append("local_only_engine_error")
    if proof["bestmove"] not in legal_moves:
        failure_reasons.append("proof_illegal_move")
    if local_only["bestmove"] not in legal_moves:
        failure_reasons.append("local_only_illegal_move")
    if local_only["source"] != "self_fallback":
        failure_reasons.append("local_only_nonlocal_source")
    if proof["source"] in _PROOF_SOURCES and not proof_verification["valid"]:
        failure_reasons.append("unverified_proof_source")

    return {
        "selection_key": corpus_entry.get("selection_key"),
        "execution_order": execution_order,
        "position": expected_position,
        "fen": state.to_fen(),
        "history_signature": state.history_signature(),
        "engine_history_signature": _engine_history_signature(state),
        "side_to_move": "red" if state.side_to_move is Color.RED else "black",
        "proof": {**proof, "artifact_verification": proof_verification},
        "local_only": local_only,
        "different_local_move": (
            proof_verification["valid"]
            and proof["bestmove"] != local_only["bestmove"]
        ),
        "valid": not failure_reasons,
        "failure_reasons": failure_reasons,
    }


def _search_engine(
    engine: UciEngine,
    position: str,
    go_command: str,
    legal_moves: set[str],
) -> dict[str, Any]:
    started = perf_counter()
    try:
        engine.new_game()
        engine.set_position_command(position)
        bestmove, lines = engine.go(go_command)
        info = _parse_proof_info(lines)
        return {
            "bestmove": bestmove,
            "legal": bestmove in legal_moves,
            "source": info.get("source"),
            "status": info.get("status"),
            "reason": info.get("reason"),
            "nodes": _int_info(info, "nodes"),
            "max_ply": _int_info(info, "max_ply"),
            "node_limit": _int_info(info, "node_limit"),
            "elapsed_ms": _elapsed_ms(started),
            "info": info,
            "engine_lines": list(lines),
            "error": None,
        }
    except Exception as exc:
        return {
            "bestmove": "0000",
            "legal": False,
            "source": None,
            "status": None,
            "reason": None,
            "nodes": None,
            "max_ply": None,
            "node_limit": None,
            "elapsed_ms": _elapsed_ms(started),
            "info": {},
            "engine_lines": [],
            "error": str(exc),
        }


def _verify_proof_result(
    result: dict[str, Any],
    *,
    state: GameState,
    store: ProofStore,
    verifier: ProofVerifier,
    max_ply: int,
) -> dict[str, Any]:
    if result["source"] not in _PROOF_SOURCES:
        return _invalid_verification("not_a_proof_source")
    if result["status"] != ProofStatus.PROVEN.value:
        return _invalid_verification("proof_source_not_proven")
    if result["source"] == "proof" and result["info"].get("proof_store_saved") != "1":
        return _invalid_verification("proof_artifact_not_saved")
    if result["source"] == "proof_store" and _int_info(result["info"], "resolved_store_hits") != 1:
        return _invalid_verification("proof_store_without_store_hit")
    telemetry_max_ply = _int_info(result["info"], "max_ply")
    if telemetry_max_ply is None:
        return _invalid_verification("proof_telemetry_missing_max_ply")
    if telemetry_max_ply != max_ply:
        return _invalid_verification("proof_telemetry_max_ply_mismatch")
    target = ProofTarget.RED if state.side_to_move is Color.RED else ProofTarget.BLACK
    proof_position = _engine_proof_position(state)
    history_signature = _position_history_signature(proof_position)
    artifact = store.resolve_proven(
        state.to_fen(),
        target,
        max_ply=telemetry_max_ply,
        history_signature=history_signature,
    )
    if artifact is None:
        return _invalid_verification("proof_artifact_missing")
    verification = verifier.verify(artifact)
    history_errors = _artifact_history_errors(artifact, proof_position)
    proven_moves = {
        child.move
        for child in artifact.children
        if child.status is ProofStatus.PROVEN and child.move is not None
    }
    result_move = result["bestmove"]
    move_matches = result_move in proven_moves
    valid = verification.valid and not history_errors and move_matches
    errors = list(verification.errors)
    errors.extend(history_errors)
    if not move_matches:
        errors.append("UCI proof move is not a proven root child")
    return {
        "valid": valid,
        "reason": "verified" if valid else "artifact_or_move_mismatch",
        "errors": errors,
        "artifact_status": artifact.status.value,
        "artifact_max_ply": artifact.max_ply,
        "artifact_history_signature": artifact.history_signature,
        "engine_history_signature": history_signature,
        "history_continuity_valid": not history_errors,
        "artifact_sha256": _canonical_sha256(artifact.to_dict()),
        "proven_root_moves": sorted(proven_moves),
    }


def _frozen_proof_store_sha256(corpus: dict[str, Any]) -> str:
    corpus_config = corpus.get("config")
    if not isinstance(corpus_config, dict):
        raise ValueError("development corpus must contain a config")
    proof_store = corpus_config.get("proof_store")
    if not isinstance(proof_store, dict):
        raise ValueError("development corpus must pin a frozen proof store")
    proof_store_sha256 = proof_store.get("sha256")
    if not isinstance(proof_store_sha256, str) or not proof_store_sha256:
        raise ValueError("development corpus must pin a frozen proof-store hash")
    return proof_store_sha256


def _invalid_verification(reason: str) -> dict[str, Any]:
    return {
        "valid": False,
        "reason": reason,
        "errors": [],
        "history_continuity_valid": False,
        "artifact_sha256": None,
    }


def _artifact_history_errors(artifact: Any, position: Any) -> list[str]:
    errors: list[str] = []
    if artifact.fen != position.to_fen():
        errors.append("artifact FEN does not match replay position")
    expected_history = _position_history_signature(position)
    if artifact.history_signature != expected_history:
        errors.append("artifact history signature does not match replay position")
    legal_by_uci = {move.to_uci(): move for move in position.legal_moves()}
    for child in artifact.children:
        if child.move not in legal_by_uci:
            errors.append(f"artifact child {child.move!r} is not legal in replay position")
            continue
        child_position = position.make_move(legal_by_uci[child.move])
        errors.extend(_artifact_history_errors(child, child_position))
    return errors


def _summary(entries: list[dict[str, Any]]) -> dict[str, Any]:
    proof_entries = [entry for entry in entries if entry.get("proof", {}).get("source") in _PROOF_SOURCES]
    verified = [
        entry
        for entry in proof_entries
        if entry["proof"].get("artifact_verification", {}).get("valid")
    ]
    proof_elapsed = [entry["proof"]["elapsed_ms"] for entry in entries if "proof" in entry]
    local_elapsed = [entry["local_only"]["elapsed_ms"] for entry in entries if "local_only" in entry]
    total = len(entries)
    return {
        "positions": total,
        "valid_entries": sum(1 for entry in entries if entry.get("valid")),
        "invalid_entries": sum(1 for entry in entries if not entry.get("valid")),
        "proof_moves": len(proof_entries),
        "proof_store_moves": sum(
            1 for entry in proof_entries if entry["proof"]["source"] == "proof_store"
        ),
        "verified_proof_moves": len(verified),
        "different_local_moves": sum(
            1 for entry in verified if entry.get("different_local_move")
        ),
        "proof_coverage": _rate(len(verified), total),
        "proof_coverage_variance": _bernoulli_variance(len(verified), total),
        "different_local_move_rate": _rate(
            sum(1 for entry in verified if entry.get("different_local_move")), total
        ),
        "proof_elapsed_ms": _timing_summary(proof_elapsed),
        "local_only_elapsed_ms": _timing_summary(local_elapsed),
        "failure_reasons": _failure_reason_counts(entries),
    }


def _parse_proof_info(lines: tuple[str, ...]) -> dict[str, str]:
    for line in reversed(lines):
        if not line.startswith("info string "):
            continue
        parsed = _parse_info_tokens(line)
        if "source" in parsed:
            return parsed
    return {}


def _parse_info_tokens(line: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for token in line[len("info string ") :].split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        parsed[key] = value
    return parsed


def _int_info(info: dict[str, str], key: str) -> int | None:
    value = info.get(key)
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _failure_reason_counts(entries: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        for reason in entry.get("failure_reasons", []):
            counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def _timing_summary(values: list[int]) -> dict[str, float | int]:
    if not values:
        return {"count": 0, "mean": 0.0, "stddev": 0.0, "max": 0}
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return {
        "count": len(values),
        "mean": round(mean, 3),
        "stddev": round(math.sqrt(variance), 3),
        "max": max(values),
    }


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _bernoulli_variance(successes: int, total: int) -> float:
    rate = _rate(successes, total)
    return rate * (1.0 - rate)


def _elapsed_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))


def _engine_history_signature(state: GameState) -> str:
    return _position_history_signature(_engine_proof_position(state))


def _engine_proof_position(state: GameState) -> Any:
    return state.position if not state.moves else state


def _position_history_signature(position: Any) -> str:
    if hasattr(position, "moves") and not getattr(position, "moves"):
        return ""
    signature = getattr(position, "history_signature", None)
    return signature() if callable(signature) else ""


def _canonical_sha256(value: Any) -> str:
    return _sha256_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure closed proof versus local-only behavior on a development corpus."
    )
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--proof-store", required=True)
    parser.add_argument("--max-ply", type=int, default=2)
    parser.add_argument("--node-limit", type=int, default=100_000)
    parser.add_argument("--local-search-depth", type=int, default=2)
    parser.add_argument("--local-search-node-limit", type=int, default=5_000)
    parser.add_argument("--go", default="go movetime 500")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    output = run_development_ab(
        Path(args.corpus),
        proof_store_path=Path(args.proof_store),
        max_ply=args.max_ply,
        node_limit=args.node_limit,
        local_search_depth=args.local_search_depth,
        local_search_node_limit=args.local_search_node_limit,
        go_command=args.go,
        timeout=args.timeout,
    )
    text = json.dumps(output, ensure_ascii=False, indent=2)
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if output["valid"] else 1


def _cli_main() -> int:
    try:
        return main()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_cli_main())
