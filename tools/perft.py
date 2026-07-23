from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from xiangqi_core import Position


def perft(position: Position, depth: int) -> int:
    if depth < 0:
        raise ValueError("depth must be non-negative")
    if depth == 0:
        return 1
    total = 0
    for move in position.legal_moves():
        total += perft(position.make_move(move), depth - 1)
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Xiangqi perft on the local rules core.")
    parser.add_argument("--fen", default=Position.START_FEN, help="FEN position")
    parser.add_argument("--depth", type=int, default=1, help="search depth")
    args = parser.parse_args()

    position = Position.from_fen(args.fen)
    print(perft(position, args.depth))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
