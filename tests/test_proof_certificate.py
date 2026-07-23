from __future__ import annotations

from dataclasses import replace
import unittest

import context  # noqa: F401
from fixtures import RED_WIN_IN_ONE_FEN
from xiangqi_core import Position
from xiangqi_solver import BoundedProofSearch, ProofStatus, ProofVerifier, compact_proven_certificate
from xiangqi_solver.pns import INF, NodeKind


class ProofCertificateTests(unittest.TestCase):
    def test_compacts_redundant_proven_or_branch(self) -> None:
        root_position = Position.from_fen(RED_WIN_IN_ONE_FEN)
        root = BoundedProofSearch("red", max_ply=1).search(root_position).artifact
        winning_child = next(child for child in root.children if child.status is ProofStatus.PROVEN)
        alternate_move = next(
            move for move in root_position.legal_moves() if move.to_uci() != winning_child.move
        )
        unknown_child = BoundedProofSearch("red", max_ply=0).search(
            root_position.make_move(alternate_move)
        ).artifact
        expanded = replace(
            root,
            children=(unknown_child, winning_child),
            proof=0,
            disproof=INF,
        )

        certificate = compact_proven_certificate(expanded)

        self.assertEqual(certificate.children, (winning_child,))
        self.assertEqual(certificate.position_command, expanded.position_command)
        self.assertTrue(ProofVerifier().verify(certificate).valid)

    def test_keeps_every_proven_and_child(self) -> None:
        root = BoundedProofSearch("red", max_ply=1).search(
            Position.from_fen(RED_WIN_IN_ONE_FEN)
        ).artifact
        winning_child = next(child for child in root.children if child.status is ProofStatus.PROVEN)
        and_node = replace(
            root,
            node_kind=NodeKind.AND,
            children=(winning_child, winning_child),
            proof=0,
            disproof=INF,
        )

        from xiangqi_solver.certificate import compact_proven_certificate

        certificate = compact_proven_certificate(and_node)

        self.assertEqual(certificate.children, and_node.children)

    def test_rejects_non_proven_artifact(self) -> None:
        artifact = BoundedProofSearch("red", max_ply=0).search(
            Position.from_fen(RED_WIN_IN_ONE_FEN)
        ).artifact

        from xiangqi_solver.certificate import compact_proven_certificate

        with self.assertRaisesRegex(ValueError, "proven"):
            compact_proven_certificate(artifact)


if __name__ == "__main__":
    unittest.main()
