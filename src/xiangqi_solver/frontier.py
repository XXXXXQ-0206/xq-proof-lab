from __future__ import annotations

from dataclasses import dataclass

from .proof import ProofArtifact, ProofStatus, ProofTarget


@dataclass(frozen=True, slots=True)
class FrontierNode:
    fen: str
    target: ProofTarget
    remaining_ply: int
    reason: str
    history_signature: str = ""
    position_command: str = ""
    proof: int = 1
    disproof: int = 1


def collect_frontier(artifact: ProofArtifact) -> tuple[FrontierNode, ...]:
    nodes: list[FrontierNode] = []
    _collect(artifact, nodes)
    return tuple(nodes)


def _collect(artifact: ProofArtifact, nodes: list[FrontierNode]) -> None:
    if artifact.status is ProofStatus.UNKNOWN and not artifact.children:
        nodes.append(
            FrontierNode(
                fen=artifact.fen,
                target=artifact.target,
                remaining_ply=artifact.max_ply,
                reason=artifact.reason,
                history_signature=artifact.history_signature,
                position_command=artifact.position_command,
                proof=artifact.proof,
                disproof=artifact.disproof,
            )
        )
        return
    for child in artifact.children:
        _collect(child, nodes)
