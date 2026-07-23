from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from xiangqi_solver import ProofArtifact, ProofStore, ProofVerifier, merge_resolved_frontier


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge resolved frontier proofs into an artifact.")
    parser.add_argument("--store", required=True)
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    artifact = ProofArtifact.from_dict(json.loads(Path(args.artifact).read_text(encoding="utf-8")))
    merged = merge_resolved_frontier(artifact, ProofStore(args.store))
    verification = ProofVerifier().verify(merged)
    if not verification.valid:
        print(json.dumps({"valid": False, "errors": verification.errors}, ensure_ascii=False, indent=2))
        return 2

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(merged.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": merged.status.value,
                "proof": merged.proof,
                "disproof": merged.disproof,
                "max_ply": merged.max_ply,
                "children": len(merged.children),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
