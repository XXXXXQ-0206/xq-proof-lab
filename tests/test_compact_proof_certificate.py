from __future__ import annotations

from dataclasses import replace
import hashlib
import tempfile
import unittest
from pathlib import Path

import context  # noqa: F401
from fixtures import RED_WIN_IN_ONE_FEN
from xiangqi_core import GameState
from xiangqi_solver import BoundedProofSearch, ProofStatus, ProofStore, ProofTarget
from xiangqi_solver.pns import INF


class CompactProofCertificateTests(unittest.TestCase):
    def test_writes_verified_compact_copy_without_mutating_source_store(self) -> None:
        state = GameState.from_uci_position(f"position fen {RED_WIN_IN_ONE_FEN}")
        root = BoundedProofSearch("red", max_ply=1).search(state).artifact
        winning_child = next(child for child in root.children if child.status is ProofStatus.PROVEN)
        alternate_move = next(
            move for move in state.legal_moves() if move.to_uci() != winning_child.move
        )
        unknown_child = replace(
            BoundedProofSearch("red", max_ply=0).search(
                state.make_move(alternate_move)
            ).artifact,
            move=alternate_move.to_uci(),
        )
        expanded = replace(
            root,
            children=(unknown_child, winning_child),
            proof=0,
            disproof=INF,
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_store_path = tmp_path / "source.sqlite"
            output_store_path = tmp_path / "compact.sqlite"
            source_store = ProofStore(source_store_path)
            source_store.save(expanded, node_limit=100)
            source_store_sha256 = hashlib.sha256(source_store_path.read_bytes()).hexdigest()

            from tools.compact_proof_certificate import compact_store_certificate

            result = compact_store_certificate(
                source_store_path,
                output_store_path,
                position=state.to_uci_position(),
                target=ProofTarget.RED,
                node_limit=100,
                verification_time_limit_seconds=5.0,
            )

            source = source_store.resolve_proven(
                state.to_fen(),
                ProofTarget.RED,
                history_signature=state.history_signature(),
            )
            compact = ProofStore(output_store_path).resolve_proven(
                state.to_fen(),
                ProofTarget.RED,
                history_signature=state.history_signature(),
            )

        self.assertIsNotNone(source)
        self.assertIsNotNone(compact)
        assert source is not None
        assert compact is not None
        self.assertEqual(len(source.children), 2)
        self.assertEqual(len(compact.children), 1)
        self.assertEqual(result["source_store_sha256"], source_store_sha256)
        self.assertNotEqual(result["source_artifact_sha256"], result["certificate_sha256"])
        self.assertLess(result["certificate_nodes"], result["source_nodes"])


if __name__ == "__main__":
    unittest.main()
