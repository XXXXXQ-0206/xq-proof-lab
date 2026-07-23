from __future__ import annotations

from xiangqi_core import GameState, Position

from .proof import ProofArtifact


def state_from_artifact(artifact: ProofArtifact):
    if artifact.position_command:
        return GameState.from_uci_position(artifact.position_command)
    return Position.from_fen(artifact.fen)
