from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Color(str, Enum):
    RED = "w"
    BLACK = "b"

    @property
    def opponent(self) -> "Color":
        return Color.BLACK if self is Color.RED else Color.RED

    @classmethod
    def from_fen(cls, value: str) -> "Color":
        if value == "w":
            return cls.RED
        if value == "b":
            return cls.BLACK
        raise ValueError(f"invalid active color: {value!r}")


class PieceType(str, Enum):
    ROOK = "r"
    ADVISOR = "a"
    CANNON = "c"
    PAWN = "p"
    KNIGHT = "n"
    BISHOP = "b"
    KING = "k"


FEN_TO_KIND = {
    "r": PieceType.ROOK,
    "a": PieceType.ADVISOR,
    "c": PieceType.CANNON,
    "p": PieceType.PAWN,
    "n": PieceType.KNIGHT,
    "b": PieceType.BISHOP,
    "k": PieceType.KING,
}


@dataclass(frozen=True, slots=True)
class Piece:
    color: Color
    kind: PieceType

    @classmethod
    def from_fen(cls, char: str) -> "Piece":
        lower = char.lower()
        if lower not in FEN_TO_KIND:
            raise ValueError(f"invalid FEN piece: {char!r}")
        return cls(Color.RED if char.isupper() else Color.BLACK, FEN_TO_KIND[lower])

    def to_fen(self) -> str:
        char = self.kind.value
        return char.upper() if self.color is Color.RED else char
