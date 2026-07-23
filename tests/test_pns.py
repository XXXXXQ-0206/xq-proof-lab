from __future__ import annotations

import unittest

import context  # noqa: F401
from xiangqi_solver import NodeKind, ProofNumbers, ProofOutcome, combine_proof_numbers


class ProofNumberTests(unittest.TestCase):
    def test_or_node_uses_min_proof_and_sum_disproof(self) -> None:
        combined = combine_proof_numbers(
            NodeKind.OR,
            [ProofNumbers(3, 5), ProofNumbers(1, 7), ProofNumbers(4, 11)],
        )
        self.assertEqual(combined.proof, 1)
        self.assertEqual(combined.disproof, 23)

    def test_and_node_uses_sum_proof_and_min_disproof(self) -> None:
        combined = combine_proof_numbers(
            NodeKind.AND,
            [ProofNumbers(3, 5), ProofNumbers(1, 7), ProofNumbers(4, 11)],
        )
        self.assertEqual(combined.proof, 8)
        self.assertEqual(combined.disproof, 5)

    def test_proven_child_proves_or_node(self) -> None:
        combined = combine_proof_numbers(NodeKind.OR, [ProofNumbers.unknown(), ProofNumbers.proven()])
        self.assertEqual(combined.outcome, ProofOutcome.PROVEN)


if __name__ == "__main__":
    unittest.main()
