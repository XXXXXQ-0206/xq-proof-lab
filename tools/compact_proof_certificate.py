from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from xiangqi_core import GameState
from xiangqi_solver import ProofStore, ProofTarget, ProofVerifier
from xiangqi_solver.certificate import compact_proven_certificate


REPORT_SCHEMA_VERSION = 1


def compact_store_certificate(
    source_store_path: Path,
    output_store_path: Path,
    *,
    position: str,
    target: str | ProofTarget,
    node_limit: int,
    max_ply: int | None = None,
    verification_time_limit_seconds: float = 10.0,
) -> dict[str, Any]:
    if node_limit <= 0:
        raise ValueError("node_limit must be positive")
    if max_ply is not None and max_ply < 0:
        raise ValueError("max_ply must be non-negative")
    if verification_time_limit_seconds < 0:
        raise ValueError("verification_time_limit_seconds must be non-negative")
    source_path = source_store_path.resolve()
    output_path = output_store_path.resolve()
    if source_path == output_path:
        raise ValueError("output store must differ from source store")
    if output_path.exists():
        raise ValueError("output store already exists")
    if not source_path.is_file():
        raise ValueError("source store does not exist")

    state = GameState.from_uci_position(position)
    proof_target = ProofTarget.parse(target)
    if proof_target.color is not state.side_to_move:
        raise ValueError("target must match the side to move")
    source_store_sha256 = _sha256_file(source_path)
    source_store = ProofStore(source_path)
    artifact = source_store.resolve_proven(
        state.to_fen(),
        proof_target,
        max_ply=max_ply,
        history_signature=state.history_signature(),
    )
    if artifact is None:
        raise ValueError("source store has no matching proven artifact")
    source_artifact_sha256 = _canonical_sha256(artifact.to_dict())
    certificate = compact_proven_certificate(artifact)
    verification = ProofVerifier().verify(
        certificate,
        time_limit_seconds=verification_time_limit_seconds,
    )
    if not verification.valid:
        raise ValueError("certificate verification failed: " + "; ".join(verification.errors))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, output_path)
    output_store_initial_sha256 = _sha256_file(output_path)
    if output_store_initial_sha256 != source_store_sha256:
        raise ValueError("copied store hash does not match source store")
    output_store = ProofStore(output_path)
    output_store.save(certificate, node_limit=node_limit, verify=False)
    saved = output_store.resolve_proven(
        state.to_fen(),
        proof_target,
        max_ply=certificate.max_ply,
        history_signature=state.history_signature(),
    )
    if saved is None or _canonical_sha256(saved.to_dict()) != _canonical_sha256(certificate.to_dict()):
        raise ValueError("saved certificate does not match verified certificate")

    return {
        "report_type": "proof_certificate_compaction",
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "valid": True,
        "position": state.to_uci_position(),
        "target": proof_target.value,
        "max_ply": certificate.max_ply,
        "node_limit": node_limit,
        "verification_time_limit_seconds": verification_time_limit_seconds,
        "source_store": str(source_store_path),
        "output_store": str(output_store_path),
        "source_store_sha256": source_store_sha256,
        "output_store_initial_sha256": output_store_initial_sha256,
        "output_store_sha256": _sha256_file(output_path),
        "source_artifact_sha256": source_artifact_sha256,
        "certificate_sha256": _canonical_sha256(certificate.to_dict()),
        "source_nodes": _node_count(artifact),
        "certificate_nodes": _node_count(certificate),
        "source_bytes": len(_canonical_bytes(artifact.to_dict())),
        "certificate_bytes": len(_canonical_bytes(certificate.to_dict())),
    }


def _node_count(artifact) -> int:
    return 1 + sum(_node_count(child) for child in artifact.children)


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Copy one locally proven store artifact as a verified compact certificate."
    )
    parser.add_argument("--source-store", required=True)
    parser.add_argument("--output-store", required=True)
    parser.add_argument("--position", required=True)
    parser.add_argument("--target", required=True, choices=("red", "black"))
    parser.add_argument("--node-limit", type=int, required=True)
    parser.add_argument("--max-ply", type=int)
    parser.add_argument("--verification-time-limit-seconds", type=float, default=10.0)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    output = compact_store_certificate(
        Path(args.source_store),
        Path(args.output_store),
        position=args.position,
        target=args.target,
        node_limit=args.node_limit,
        max_ply=args.max_ply,
        verification_time_limit_seconds=args.verification_time_limit_seconds,
    )
    text = json.dumps(output, ensure_ascii=False, indent=2)
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(text + "\n", encoding="utf-8")
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
