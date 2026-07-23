from __future__ import annotations

from dataclasses import replace

from .proof import ProofArtifact, ProofStatus


def compact_proven_certificate(artifact: ProofArtifact) -> ProofArtifact:
    if artifact.status is not ProofStatus.PROVEN:
        raise ValueError("only proven artifacts can produce certificates")
    return _compact(artifact)


def _compact(artifact: ProofArtifact) -> ProofArtifact:
    if artifact.status is not ProofStatus.PROVEN:
        raise ValueError("proven certificate contains a non-proven node")
    if not artifact.children:
        return artifact
    if artifact.node_kind.value == "or":
        proven_children = [
            child for child in artifact.children if child.status is ProofStatus.PROVEN
        ]
        if not proven_children:
            raise ValueError("proven OR node has no proven child")
        selected = min(proven_children, key=_certificate_child_key)
        return replace(artifact, children=(_compact(selected),))
    if any(child.status is not ProofStatus.PROVEN for child in artifact.children):
        raise ValueError("proven AND node contains a non-proven child")
    return replace(artifact, children=tuple(_compact(child) for child in artifact.children))


def _certificate_child_key(child: ProofArtifact) -> tuple[str, str, str]:
    return child.move or "", child.fen, child.history_signature
