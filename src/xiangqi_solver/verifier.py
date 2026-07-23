from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter

from xiangqi_core import GameState

from .artifact_state import state_from_artifact
from .pns import INF, ProofNumbers, combine_proof_numbers
from .proof import (
    ProofArtifact,
    ProofStatus,
    game_result_and_reason,
    node_kind_for,
    status_from_game_result,
    status_refutes_target,
)
from .search import _proof_outcome_name


@dataclass(frozen=True, slots=True)
class VerificationResult:
    valid: bool
    errors: tuple[str, ...] = field(default_factory=tuple)


class ProofVerifier:
    def verify(
        self,
        artifact: ProofArtifact,
        *,
        time_limit_seconds: float | None = None,
    ) -> VerificationResult:
        if time_limit_seconds is not None and time_limit_seconds < 0:
            raise ValueError("time_limit_seconds must be non-negative")
        errors: list[str] = []
        deadline = (
            perf_counter() + time_limit_seconds
            if time_limit_seconds is not None
            else None
        )
        self._verify_node(artifact, parent=None, errors=errors, deadline=deadline)
        return VerificationResult(not errors, tuple(errors))

    def _verify_node(
        self,
        artifact: ProofArtifact,
        parent: tuple[object, ProofArtifact] | None,
        errors: list[str],
        deadline: float | None,
    ) -> None:
        if _verification_time_limit_reached(deadline, errors):
            return
        try:
            position = state_from_artifact(artifact)
        except Exception as exc:
            errors.append(f"{artifact.move or '<root>'}: invalid position: {exc}")
            return

        expected_kind = node_kind_for(position, artifact.target)
        if artifact.node_kind is not expected_kind:
            errors.append(
                f"{artifact.move or '<root>'}: node kind {artifact.node_kind.value} "
                f"does not match side to move"
            )
        if artifact.position_command:
            self._verify_position_command(artifact, errors)

        if parent is not None:
            parent_position, parent_artifact = parent
            if artifact.move is None:
                errors.append("child node is missing move")
            else:
                self._verify_child_position(parent_position, parent_artifact, artifact, errors)

        result, _reason = game_result_and_reason(position)
        if result is not None:
            expected_status = status_from_game_result(result, artifact.target)
            if artifact.status is not expected_status:
                errors.append(
                    f"{artifact.move or '<root>'}: terminal status {artifact.status.value} "
                    f"should be {expected_status.value}"
                )
            if artifact.children:
                errors.append(f"{artifact.move or '<root>'}: terminal node must not have children")
            self._verify_terminal_numbers(artifact, expected_status, errors)
            return

        for child in artifact.children:
            self._verify_node(
                child,
                parent=(position, artifact),
                errors=errors,
                deadline=deadline,
            )
            if _verification_time_limit_reached(deadline, errors):
                return

        if _verification_time_limit_reached(deadline, errors):
            return
        self._verify_nonterminal_numbers(artifact, errors)

        if artifact.status is ProofStatus.UNKNOWN:
            return

        legal_moves = {move.to_uci(): move for move in position.legal_moves()}
        children_by_move = {child.move: child for child in artifact.children}

        if artifact.status is ProofStatus.PROVEN:
            if artifact.node_kind.value == "or":
                proven_children = [
                    child for child in artifact.children if child.status is ProofStatus.PROVEN
                ]
                if not proven_children:
                    errors.append(f"{artifact.move or '<root>'}: proven OR node has no proven child")
            else:
                missing = sorted(set(legal_moves) - set(children_by_move))
                if missing:
                    errors.append(
                        f"{artifact.move or '<root>'}: proven AND node does not cover moves {missing}"
                    )
                for child in artifact.children:
                    if _verification_time_limit_reached(deadline, errors):
                        return
                    if child.status is not ProofStatus.PROVEN:
                        errors.append(
                            f"{artifact.move or '<root>'}: proven AND child {child.move} "
                            f"is {child.status.value}"
                        )

        if artifact.status in {ProofStatus.DISPROVEN, ProofStatus.DRAW}:
            if artifact.node_kind.value == "or":
                missing = sorted(set(legal_moves) - set(children_by_move))
                if missing:
                    errors.append(
                        f"{artifact.move or '<root>'}: disproven OR node does not cover moves {missing}"
                    )
                for child in artifact.children:
                    if not status_refutes_target(child.status):
                        errors.append(
                            f"{artifact.move or '<root>'}: disproven OR child {child.move} "
                            f"is {child.status.value}"
                        )
            else:
                if not any(status_refutes_target(child.status) for child in artifact.children):
                    errors.append(
                        f"{artifact.move or '<root>'}: disproven AND node has no disproven child"
                    )

    def _verify_terminal_numbers(
        self,
        artifact: ProofArtifact,
        expected_status: ProofStatus,
        errors: list[str],
    ) -> None:
        if expected_status is ProofStatus.PROVEN:
            expected = (0, INF)
        elif expected_status in {ProofStatus.DISPROVEN, ProofStatus.DRAW}:
            expected = (INF, 0)
        else:
            expected = (1, 1)
        self._verify_numbers(artifact, expected, errors)

    def _verify_nonterminal_numbers(
        self,
        artifact: ProofArtifact,
        errors: list[str],
    ) -> None:
        if not artifact.children:
            if artifact.status is ProofStatus.UNKNOWN:
                self._verify_numbers(artifact, (1, 1), errors)
            return
        combined = combine_proof_numbers(
            artifact.node_kind,
            [
                ProofNumbers(child.proof, child.disproof, _proof_outcome_name(child.status))
                for child in artifact.children
            ],
        )
        self._verify_numbers(artifact, (combined.proof, combined.disproof), errors)

    def _verify_numbers(
        self,
        artifact: ProofArtifact,
        expected: tuple[int, int],
        errors: list[str],
    ) -> None:
        if (artifact.proof, artifact.disproof) != expected:
            errors.append(
                f"{artifact.move or '<root>'}: proof/disproof "
                f"{artifact.proof}/{artifact.disproof} should be {expected[0]}/{expected[1]}"
            )

    def _verify_child_position(
        self,
        parent_position,
        parent_artifact: ProofArtifact,
        child: ProofArtifact,
        errors: list[str],
    ) -> None:
        assert child.move is not None
        legal_moves = {move.to_uci(): move for move in parent_position.legal_moves()}
        if child.move not in legal_moves:
            errors.append(f"{child.move}: child move is not legal")
            return
        expected = parent_position.make_move(legal_moves[child.move]).to_fen()
        if child.fen != expected:
            errors.append(f"{child.move}: child FEN does not match make_move result")
        if child.target is not parent_artifact.target:
            errors.append(f"{child.move}: child target differs from parent")
        if child.max_ply > parent_artifact.max_ply - 1:
            errors.append(f"{child.move}: child max_ply exceeds parent budget")

    def _verify_position_command(self, artifact: ProofArtifact, errors: list[str]) -> None:
        try:
            state = GameState.from_uci_position(artifact.position_command)
        except Exception as exc:
            errors.append(f"{artifact.move or '<root>'}: invalid position command: {exc}")
            return
        if state.to_fen() != artifact.fen:
            errors.append(
                f"{artifact.move or '<root>'}: position command FEN does not match artifact FEN"
            )
        if artifact.history_signature and state.history_signature() != artifact.history_signature:
            errors.append(
                f"{artifact.move or '<root>'}: position command history does not match signature"
            )


def _verification_time_limit_reached(deadline: float | None, errors: list[str]) -> bool:
    if deadline is None or perf_counter() < deadline:
        return False
    if "verification_time_limit" not in errors:
        errors.append("verification_time_limit")
    return True
