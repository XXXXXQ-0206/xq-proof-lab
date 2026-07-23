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
from xiangqi_solver import ProofStatus, ProofStore, ProofTarget, ProofVerifier


REPORT_SCHEMA_VERSION = 1
SELECTION_RULE_VERSION = "locally_verified_natural_mate_candidate_v1"
PREFLIGHT_REPORT_SCHEMA_VERSION = 1


def build_proven_corpus(
    candidates_path: Path,
    store_path: Path,
    *,
    verification_time_limit_seconds: float = 10.0,
) -> dict[str, Any]:
    if verification_time_limit_seconds < 0:
        raise ValueError("verification_time_limit_seconds must be non-negative")
    candidate_bytes = candidates_path.read_bytes()
    candidate_report = json.loads(candidate_bytes)
    if candidate_report.get("report_type") != "pikafish_mate_mining":
        raise ValueError("candidates must be a Pikafish mate-mining report")
    candidates = candidate_report.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("candidate report must contain candidates")

    store = ProofStore(store_path)
    verifier = ProofVerifier()
    positions: dict[str, dict[str, Any]] = {}
    excluded: dict[str, int] = {}
    source_reports: dict[str, str] = {}
    for candidate in candidates:
        entry, reason = _verified_entry(
            candidate,
            store=store,
            verifier=verifier,
            verification_time_limit_seconds=verification_time_limit_seconds,
        )
        if entry is None:
            excluded[reason] = excluded.get(reason, 0) + 1
            continue
        source_reports[entry["source_report"]] = entry["source_report_sha256"]
        existing = positions.get(entry["position"])
        if existing is None or _source_key(entry) < _source_key(existing):
            positions[entry["position"]] = entry

    selected = sorted(positions.values(), key=lambda entry: entry["selection_key"])
    config = {
        "split": "development",
        "selection_rule_version": SELECTION_RULE_VERSION,
        "candidate_report": {
            "path": str(candidates_path),
            "sha256": _sha256_bytes(candidate_bytes),
        },
        "proof_store": {
            "path": str(store_path),
            "sha256": _sha256_file(store_path),
        },
        "verification_time_limit_seconds": verification_time_limit_seconds,
        "source_reports": [
            {"path": path, "sha256": source_reports[path]}
            for path in sorted(source_reports)
        ],
    }
    return {
        "report_type": "proof_qualification_corpus",
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "split": "development",
        "config": config,
        "corpus_digest": _canonical_sha256({"config": config, "positions": selected}),
        "summary": {
            "candidates": len(candidates),
            "verified_positions": len(selected),
            "excluded": dict(sorted(excluded.items())),
        },
        "positions": selected,
    }


def build_preflight_config(
    corpus: dict[str, Any],
    *,
    corpus_sha256: str,
) -> dict[str, Any]:
    if corpus.get("report_type") != "proof_qualification_corpus":
        raise ValueError("corpus must be a proof qualification corpus")
    if corpus.get("split") != "development":
        raise ValueError("preflight corpus must use the development split")
    corpus_digest = corpus.get("corpus_digest")
    if not isinstance(corpus_digest, str) or not corpus_digest:
        raise ValueError("corpus must contain a digest")
    if not isinstance(corpus_sha256, str) or not corpus_sha256:
        raise ValueError("corpus_sha256 must be non-empty")
    source_positions = corpus.get("positions")
    if not isinstance(source_positions, list) or not source_positions:
        raise ValueError("corpus must contain positions")

    positions: list[dict[str, str]] = []
    seen_keys: set[str] = set()
    seen_positions: set[str] = set()
    for index, entry in enumerate(source_positions, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"corpus position {index} must be an object")
        selection_key = entry.get("selection_key")
        position_text = entry.get("position")
        if not isinstance(selection_key, str) or not selection_key:
            raise ValueError(f"corpus position {index} must contain a selection_key")
        if not isinstance(position_text, str) or not position_text:
            raise ValueError(f"corpus position {index} must contain a position")
        if selection_key in seen_keys:
            raise ValueError("corpus contains duplicate selection_key values")
        if position_text in seen_positions:
            raise ValueError("corpus contains duplicate positions")
        try:
            canonical_position = GameState.from_uci_position(position_text).to_uci_position()
        except ValueError as exc:
            raise ValueError(f"corpus position {index} is not replayable") from exc
        if canonical_position != position_text:
            raise ValueError(f"corpus position {index} is not canonical")
        seen_keys.add(selection_key)
        seen_positions.add(position_text)
        positions.append({"name": selection_key, "position": position_text})

    positions.sort(key=lambda entry: (entry["name"], entry["position"]))
    source_corpus = {
        "corpus_digest": corpus_digest,
        "corpus_sha256": corpus_sha256,
    }
    output = {
        "report_type": "proof_qualification_preflight",
        "report_schema_version": PREFLIGHT_REPORT_SCHEMA_VERSION,
        "source_corpus": source_corpus,
        "positions": positions,
    }
    output["preflight_digest"] = _canonical_sha256(output)
    return output


def _verified_entry(
    candidate: Any,
    *,
    store: ProofStore,
    verifier: ProofVerifier,
    verification_time_limit_seconds: float,
) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(candidate, dict):
        return None, "invalid_candidate"
    position_text = candidate.get("position")
    target_text = candidate.get("target")
    if not isinstance(position_text, str) or not isinstance(target_text, str):
        return None, "invalid_candidate"
    try:
        state = GameState.from_uci_position(position_text)
        target = ProofTarget.parse(target_text)
    except ValueError:
        return None, "invalid_candidate"
    expected_target = ProofTarget.RED if state.side_to_move is Color.RED else ProofTarget.BLACK
    if target is not expected_target:
        return None, "candidate_target_mismatch"
    artifact = store.resolve_proven(
        state.to_fen(),
        target,
        history_signature=state.history_signature(),
    )
    if artifact is None:
        return None, "proof_artifact_missing"
    verification = verifier.verify(
        artifact,
        time_limit_seconds=verification_time_limit_seconds,
    )
    if not verification.valid:
        reason = (
            "proof_verification_time_limit"
            if "verification_time_limit" in verification.errors
            else "proof_artifact_invalid"
        )
        return None, reason
    source_report = candidate.get("source_report")
    source_report_sha256 = candidate.get("source_report_sha256")
    if not isinstance(source_report, str) or not isinstance(source_report_sha256, str):
        return None, "invalid_candidate_source"
    source_report_path = Path(source_report)
    if not source_report_path.is_absolute():
        source_report_path = ROOT / source_report_path
    if not source_report_path.is_file() or _sha256_file(source_report_path) != source_report_sha256:
        return None, "source_report_hash_mismatch"
    proven_root_moves = sorted(
        child.move
        for child in artifact.children
        if child.status is ProofStatus.PROVEN and child.move is not None
    )
    if not proven_root_moves:
        return None, "proof_without_proven_root_move"
    position = state.to_uci_position()
    return (
        {
            "selection_key": _sha256_text(position),
            "position": position,
            "fen": state.to_fen(),
            "history_signature": state.history_signature(),
            "side_to_move": target.value,
            "source_report": source_report,
            "source_report_sha256": source_report_sha256,
            "source_game": candidate.get("source_game"),
            "source_ply": candidate.get("source_ply"),
            "offline_candidate_move": candidate.get("pv_root_move"),
            "local_proof": {
                "artifact_sha256": _canonical_sha256(artifact.to_dict()),
                "max_ply": artifact.max_ply,
                "proven_root_moves": proven_root_moves,
            },
        },
        "",
    )


def _source_key(entry: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(entry.get("source_report", "")),
        str(entry.get("source_game", "")),
        str(entry.get("source_ply", "")),
    )


def _canonical_sha256(value: Any) -> str:
    return _sha256_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a development corpus from locally verified natural proof candidates."
    )
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--proof-store", required=True)
    parser.add_argument("--verification-time-limit-seconds", type=float, default=10.0)
    parser.add_argument("--output", required=True)
    parser.add_argument("--preflight-output")
    args = parser.parse_args()

    output = build_proven_corpus(
        Path(args.candidates),
        Path(args.proof_store),
        verification_time_limit_seconds=args.verification_time_limit_seconds,
    )
    text = json.dumps(output, ensure_ascii=False, indent=2)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text + "\n", encoding="utf-8")
    if args.preflight_output:
        preflight = build_preflight_config(
            output,
            corpus_sha256=_sha256_bytes((text + "\n").encode("utf-8")),
        )
        preflight_path = Path(args.preflight_output)
        preflight_path.parent.mkdir(parents=True, exist_ok=True)
        preflight_path.write_text(
            json.dumps(preflight, ensure_ascii=False, indent=2) + "\n",
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
