from __future__ import annotations

from hashlib import blake2b

from .model import Color, PieceType
from .position import Position


class Zobrist:
    def __init__(self, seed: str = "xiangqi-proof-tree-v1") -> None:
        self.seed = seed

    def hash_position(self, position: Position, include_rule_state: bool = True) -> int:
        value = 0
        for square, piece in enumerate(position.board):
            if piece is None:
                continue
            value ^= self._key(f"piece:{piece.color.value}:{piece.kind.value}:{square}")
        value ^= self._key(f"side:{position.side_to_move.value}")
        if include_rule_state:
            value ^= self._key(f"halfmove:{position.halfmove_clock}")
            value ^= self._key(f"fullmove:{position.fullmove_number}")
        return value

    def _key(self, label: str) -> int:
        digest = blake2b(
            f"{self.seed}:{label}".encode("ascii"),
            digest_size=8,
            person=b"xiangqip",
        ).digest()
        return int.from_bytes(digest, "little")


__all__ = ["Color", "PieceType", "Zobrist"]
