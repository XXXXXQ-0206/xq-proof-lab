from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from xiangqi_core import Color, Position

from .pns import INF, NodeKind


class ProofStatus(str, Enum):
    PROVEN = "proven"
    DISPROVEN = "disproven"
    UNKNOWN = "unknown"
    DRAW = "draw"
    ILLEGAL = "illegal"


class ProofTarget(str, Enum):
    RED = "red"
    BLACK = "black"

    @property
    def color(self) -> Color:
        return Color.RED if self is ProofTarget.RED else Color.BLACK

    @classmethod
    def parse(cls, value: str | "ProofTarget") -> "ProofTarget":
        if isinstance(value, ProofTarget):
            return value
        normalized = value.lower()
        if normalized in {"red", "w", "white"}:
            return cls.RED
        if normalized in {"black", "b"}:
            return cls.BLACK
        raise ValueError(f"invalid proof target: {value!r}")


@dataclass(frozen=True, slots=True)
class ProofArtifact:
    fen: str
    target: ProofTarget
    max_ply: int
    node_kind: NodeKind
    status: ProofStatus
    proof: int
    disproof: int
    move: str | None = None
    reason: str = ""
    history_signature: str = ""
    position_command: str = ""
    children: tuple["ProofArtifact", ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fen": self.fen,
            "target": self.target.value,
            "max_ply": self.max_ply,
            "node_kind": self.node_kind.value,
            "status": self.status.value,
            "proof": self.proof,
            "disproof": self.disproof,
            "move": self.move,
            "reason": self.reason,
            "history_signature": self.history_signature,
            "position_command": self.position_command,
            "children": [child.to_dict() for child in self.children],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProofArtifact":
        return cls(
            fen=str(data["fen"]),
            target=ProofTarget.parse(str(data["target"])),
            max_ply=int(data["max_ply"]),
            node_kind=NodeKind(str(data["node_kind"])),
            status=ProofStatus(str(data["status"])),
            proof=int(data["proof"]),
            disproof=int(data["disproof"]),
            move=data.get("move"),
            reason=str(data.get("reason", "")),
            history_signature=str(data.get("history_signature", "")),
            position_command=str(data.get("position_command", "")),
            children=tuple(cls.from_dict(child) for child in data.get("children", [])),
        )

    @classmethod
    def terminal(
        cls,
        position: Position,
        target: ProofTarget,
        max_ply: int,
        move: str | None,
        result: str,
        reason: str | None = None,
    ) -> "ProofArtifact":
        status = status_from_game_result(result, target)
        if status is ProofStatus.PROVEN:
            proof, disproof = 0, INF
        elif status in {ProofStatus.DISPROVEN, ProofStatus.DRAW}:
            proof, disproof = INF, 0
        else:
            proof, disproof = 1, 1
        return cls(
            fen=position.to_fen(),
            target=target,
            max_ply=max_ply,
            node_kind=node_kind_for(position, target),
            status=status,
            proof=proof,
            disproof=disproof,
            move=move,
            reason=reason or result,
            history_signature=_history_signature(position),
            position_command=_position_command(position),
        )


def node_kind_for(position: Position, target: ProofTarget) -> NodeKind:
    return NodeKind.OR if position.side_to_move is target.color else NodeKind.AND


def status_from_game_result(result: str | None, target: ProofTarget) -> ProofStatus:
    if result is None:
        return ProofStatus.UNKNOWN
    if result == "red_win":
        return ProofStatus.PROVEN if target is ProofTarget.RED else ProofStatus.DISPROVEN
    if result == "black_win":
        return ProofStatus.PROVEN if target is ProofTarget.BLACK else ProofStatus.DISPROVEN
    if result == "draw":
        return ProofStatus.DRAW
    if result == "unknown_rule_state":
        return ProofStatus.UNKNOWN
    raise ValueError(f"unknown game result: {result!r}")


def status_refutes_target(status: ProofStatus) -> bool:
    return status in {ProofStatus.DISPROVEN, ProofStatus.DRAW}


def game_result_and_reason(
    position,
    legal_moves=None,
) -> tuple[str | None, str | None]:
    judgement = getattr(position, "rule_judgement", None)
    if callable(judgement):
        result = judgement(legal_moves=legal_moves)
        return result.result, result.reason
    if legal_moves is None:
        result = position.game_result()
    else:
        result = position.game_result(legal_moves=legal_moves)
    return result, result


def _history_signature(position) -> str:
    signature = getattr(position, "history_signature", None)
    return signature() if callable(signature) else ""


def _position_command(position) -> str:
    command = getattr(position, "to_uci_position", None)
    return command() if callable(command) else ""
