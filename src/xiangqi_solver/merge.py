from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from .artifact_state import state_from_artifact
from .pns import ProofNumbers, combine_proof_numbers
from .proof import ProofArtifact, ProofStatus, ProofTarget, node_kind_for, status_refutes_target
from .search import _proof_outcome_name


class ProofResolver(Protocol):
    def resolve(
        self,
        fen: str,
        target: ProofTarget,
        min_ply: int,
        history_signature: str = "",
    ) -> ProofArtifact | None:
        ...


def merge_resolved_frontier(artifact: ProofArtifact, resolver: ProofResolver) -> ProofArtifact:
    if artifact.status is ProofStatus.UNKNOWN and not artifact.children:
        resolved = _resolve_for_merge(
            resolver,
            artifact.fen,
            artifact.target,
            artifact.max_ply,
            artifact.history_signature,
        )
        if resolved is not None:
            merged = _with_move(
                resolved,
                artifact.move,
                artifact.history_signature,
                artifact.position_command,
            )
            if merged.status is not ProofStatus.UNKNOWN:
                return merged
            if merged.children:
                return merge_resolved_frontier(merged, resolver)
        return artifact

    merged_children = tuple(merge_resolved_frontier(child, resolver) for child in artifact.children)
    if merged_children == artifact.children:
        return artifact
    return _recompute(artifact, merged_children)


def _resolve_for_merge(
    resolver: ProofResolver,
    fen: str,
    target: ProofTarget,
    min_ply: int,
    history_signature: str,
) -> ProofArtifact | None:
    resolve_for_merge = getattr(resolver, "resolve_for_merge", None)
    if callable(resolve_for_merge):
        return resolve_for_merge(fen, target, min_ply, history_signature)
    return resolver.resolve(fen, target, min_ply, history_signature)


def _recompute(artifact: ProofArtifact, children: tuple[ProofArtifact, ...]) -> ProofArtifact:
    position = state_from_artifact(artifact)
    node_kind = node_kind_for(position, artifact.target)
    max_ply = max([artifact.max_ply] + [child.max_ply + 1 for child in children])
    if not children:
        return replace(artifact, max_ply=max_ply)

    combined = combine_proof_numbers(
        node_kind,
        [ProofNumbers(child.proof, child.disproof, _proof_outcome_name(child.status)) for child in children],
    )
    legal_moves = {move.to_uci() for move in position.legal_moves()}
    covered_moves = {child.move for child in children}

    if node_kind.value == "or":
        if any(child.status is ProofStatus.PROVEN for child in children):
            status = ProofStatus.PROVEN
            reason = "merged_or_proven"
        elif legal_moves == covered_moves and all(status_refutes_target(child.status) for child in children):
            status = ProofStatus.DISPROVEN
            reason = "merged_or_disproven"
        else:
            status = ProofStatus.UNKNOWN
            reason = "merged_partial"
    else:
        if any(status_refutes_target(child.status) for child in children):
            status = ProofStatus.DISPROVEN
            reason = "merged_and_disproven"
        elif legal_moves == covered_moves and all(child.status is ProofStatus.PROVEN for child in children):
            status = ProofStatus.PROVEN
            reason = "merged_and_proven"
        else:
            status = ProofStatus.UNKNOWN
            reason = "merged_partial"

    return ProofArtifact(
        fen=artifact.fen,
        target=artifact.target,
        max_ply=max_ply,
        node_kind=node_kind,
        status=status,
        proof=combined.proof,
        disproof=combined.disproof,
        move=artifact.move,
        reason=reason,
        history_signature=artifact.history_signature,
        position_command=artifact.position_command,
        children=children,
    )


def _with_move(
    artifact: ProofArtifact,
    move: str | None,
    fallback_history_signature: str = "",
    fallback_position_command: str = "",
) -> ProofArtifact:
    history_signature = artifact.history_signature or fallback_history_signature
    position_command = artifact.position_command or fallback_position_command
    if (
        artifact.move == move
        and artifact.history_signature == history_signature
        and artifact.position_command == position_command
    ):
        return artifact
    return ProofArtifact(
        fen=artifact.fen,
        target=artifact.target,
        max_ply=artifact.max_ply,
        node_kind=artifact.node_kind,
        status=artifact.status,
        proof=artifact.proof,
        disproof=artifact.disproof,
        move=move,
        reason=artifact.reason,
        history_signature=history_signature,
        position_command=position_command,
        children=artifact.children,
    )
