from __future__ import annotations

from dataclasses import dataclass

from .coordinates import square_from_name, square_name


@dataclass(frozen=True, order=True, slots=True)
class Move:
    from_square: int
    to_square: int

    @classmethod
    def from_uci(cls, text: str) -> "Move":
        if len(text) != 4:
            raise ValueError(f"invalid UCI move: {text!r}")
        return cls(square_from_name(text[:2]), square_from_name(text[2:]))

    def to_uci(self) -> str:
        return f"{square_name(self.from_square)}{square_name(self.to_square)}"

    def __str__(self) -> str:
        return self.to_uci()
