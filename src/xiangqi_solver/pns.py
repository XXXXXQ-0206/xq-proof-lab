from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

INF = 10**12


class ProofOutcome(str, Enum):
    PROVEN = "proven"
    DISPROVEN = "disproven"
    UNKNOWN = "unknown"
    DRAW = "draw"
    ILLEGAL = "illegal"


class NodeKind(str, Enum):
    OR = "or"
    AND = "and"


@dataclass(frozen=True, slots=True)
class ProofNumbers:
    proof: int
    disproof: int
    outcome: ProofOutcome = ProofOutcome.UNKNOWN

    @classmethod
    def unknown(cls) -> "ProofNumbers":
        return cls(1, 1, ProofOutcome.UNKNOWN)

    @classmethod
    def proven(cls) -> "ProofNumbers":
        return cls(0, INF, ProofOutcome.PROVEN)

    @classmethod
    def disproven(cls) -> "ProofNumbers":
        return cls(INF, 0, ProofOutcome.DISPROVEN)

    @classmethod
    def draw(cls) -> "ProofNumbers":
        return cls(INF, 0, ProofOutcome.DRAW)


def combine_proof_numbers(kind: NodeKind, children: list[ProofNumbers]) -> ProofNumbers:
    if not children:
        return ProofNumbers.disproven()

    if kind is NodeKind.OR:
        proof = min(child.proof for child in children)
        disproof = min(INF, sum(child.disproof for child in children))
    else:
        proof = min(INF, sum(child.proof for child in children))
        disproof = min(child.disproof for child in children)

    if proof == 0:
        outcome = ProofOutcome.PROVEN
    elif disproof == 0:
        outcome = ProofOutcome.DISPROVEN
    else:
        outcome = ProofOutcome.UNKNOWN
    return ProofNumbers(proof, disproof, outcome)
