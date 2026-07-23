from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from xiangqi_solver import ProofArtifact, ProofVerifier


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]
    if len(args) != 1:
        print("usage: verify_proof.py ARTIFACT_JSON", file=sys.stderr)
        return 2

    artifact = ProofArtifact.from_dict(json.loads(Path(args[0]).read_text(encoding="utf-8")))
    result = ProofVerifier().verify(artifact)
    if result.valid:
        print("valid")
        return 0
    print(json.dumps({"valid": False, "errors": result.errors}, ensure_ascii=False, indent=2))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
