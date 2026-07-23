from __future__ import annotations

import unittest

import context  # noqa: F401
from fixtures import RED_WIN_IN_ONE_FEN
from xiangqi_core import GameState, Position
from xiangqi_solver import BoundedProofSearch, ProofArtifact, ProofStatus, ProofVerifier


PERPETUAL_CHECK_POSITION = (
    "position fen 4k4/3R5/9/9/9/9/9/9/4A4/4K4 w - - 0 1 "
    "moves d8e8 e9d9 e8d8 d9e9 d8e8 e9d9 e8d8 d9e9"
)


class ProofVerifierTests(unittest.TestCase):
    def test_time_limit_rejects_unverified_artifact(self) -> None:
        artifact = BoundedProofSearch("red", max_ply=1).search(
            Position.from_fen(RED_WIN_IN_ONE_FEN)
        ).artifact

        result = ProofVerifier().verify(artifact, time_limit_seconds=0)

        self.assertFalse(result.valid)
        self.assertIn("verification_time_limit", result.errors)

    def test_rejects_tampered_child_fen(self) -> None:
        artifact = BoundedProofSearch("red", max_ply=1).search(
            Position.from_fen(RED_WIN_IN_ONE_FEN)
        ).artifact
        data = artifact.to_dict()
        data["children"][0]["fen"] = Position.START_FEN
        tampered = ProofArtifact.from_dict(data)

        result = ProofVerifier().verify(tampered)

        self.assertFalse(result.valid)
        self.assertTrue(any("child FEN" in error for error in result.errors))

    def test_rejects_false_proven_unknown_node(self) -> None:
        artifact = BoundedProofSearch("red", max_ply=0).search(
            Position.from_fen(RED_WIN_IN_ONE_FEN)
        ).artifact
        data = artifact.to_dict()
        data["status"] = ProofStatus.PROVEN.value
        tampered = ProofArtifact.from_dict(data)

        result = ProofVerifier().verify(tampered)

        self.assertFalse(result.valid)

    def test_rejects_tampered_proof_numbers(self) -> None:
        artifact = BoundedProofSearch("red", max_ply=1).search(
            Position.from_fen(RED_WIN_IN_ONE_FEN)
        ).artifact
        data = artifact.to_dict()
        data["proof"] = 7
        tampered = ProofArtifact.from_dict(data)

        result = ProofVerifier().verify(tampered)

        self.assertFalse(result.valid)
        self.assertTrue(any("proof/disproof" in error for error in result.errors))

    def test_disproven_child_can_refute_and_node(self) -> None:
        parent = BoundedProofSearch("black", max_ply=1).search(
            Position.from_fen(RED_WIN_IN_ONE_FEN)
        ).artifact

        result = ProofVerifier().verify(parent)

        self.assertEqual(parent.status, ProofStatus.DISPROVEN)
        self.assertTrue(any(child.status is ProofStatus.DISPROVEN for child in parent.children))
        self.assertTrue(result.valid, result.errors)

    def test_rejects_tampered_position_command(self) -> None:
        state = GameState.from_uci_position(f"position fen {RED_WIN_IN_ONE_FEN}")
        artifact = BoundedProofSearch("red", max_ply=0).search(state).artifact
        data = artifact.to_dict()
        data["position_command"] = "position startpos"
        tampered = ProofArtifact.from_dict(data)

        result = ProofVerifier().verify(tampered)

        self.assertFalse(result.valid)
        self.assertTrue(any("position command FEN" in error for error in result.errors))

    def test_verifies_history_sensitive_terminal_rule_result(self) -> None:
        state = GameState.from_uci_position(PERPETUAL_CHECK_POSITION)
        artifact = BoundedProofSearch("black", max_ply=0).search(state).artifact

        result = ProofVerifier().verify(artifact)

        self.assertEqual(artifact.status, ProofStatus.PROVEN)
        self.assertEqual(artifact.reason, "perpetual_check_loss")
        self.assertTrue(result.valid, result.errors)

    def test_rejects_wrong_status_for_history_sensitive_terminal(self) -> None:
        state = GameState.from_uci_position(PERPETUAL_CHECK_POSITION)
        artifact = BoundedProofSearch("black", max_ply=0).search(state).artifact
        data = artifact.to_dict()
        data["status"] = ProofStatus.DISPROVEN.value
        tampered = ProofArtifact.from_dict(data)

        result = ProofVerifier().verify(tampered)

        self.assertFalse(result.valid)
        self.assertTrue(any("terminal status" in error for error in result.errors))


if __name__ == "__main__":
    unittest.main()
