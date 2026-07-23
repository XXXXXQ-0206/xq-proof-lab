from __future__ import annotations

import tempfile
import unittest
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import context  # noqa: F401
from fixtures import RED_WIN_IN_ONE_FEN, TERMINAL_RED_WIN_FEN
from xiangqi_core import GameState, Position

from xiangqi_solver import BoundedProofSearch, ProofStatus, ProofStore


class ProofStoreTests(unittest.TestCase):
    def test_save_and_load_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProofStore(Path(tmp) / "proofs.sqlite")
            artifact = BoundedProofSearch("red", max_ply=0).search(
                Position.from_fen(TERMINAL_RED_WIN_FEN)
            ).artifact

            position_key = store.save(artifact, node_limit=100)
            loaded = store.load(TERMINAL_RED_WIN_FEN, "red", max_ply=0)
            proofs = store.iter_proofs()
            summary = store.database_summary()

        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.position_key, position_key)
        self.assertEqual(loaded.artifact.status, ProofStatus.PROVEN)
        self.assertEqual(len(proofs), 1)
        self.assertEqual(summary["proof_results"]["proven"], 1)

    def test_rejects_corrupt_stored_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "proofs.sqlite"
            store = ProofStore(path)
            artifact = BoundedProofSearch("red", max_ply=0).search(
                Position.from_fen(TERMINAL_RED_WIN_FEN)
            ).artifact
            store.save(artifact, node_limit=100)
            with closing(sqlite3.connect(path)) as con:
                con.execute("UPDATE proof_results SET artifact_json = ?", ("{}",))
                con.commit()

            with self.assertRaises(ValueError):
                store.load(TERMINAL_RED_WIN_FEN, "red", max_ply=0)

    def test_rejects_invalid_artifact_on_save(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProofStore(Path(tmp) / "proofs.sqlite")
            invalid = replace(
                BoundedProofSearch("red", max_ply=0).search(
                    Position.from_fen(RED_WIN_IN_ONE_FEN)
                ).artifact,
                status=ProofStatus.PROVEN,
            )

            with self.assertRaisesRegex(ValueError, "failed verification"):
                store.save(invalid, node_limit=100)

    def test_delete_proof(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProofStore(Path(tmp) / "proofs.sqlite")
            artifact = BoundedProofSearch("red", max_ply=0).search(
                Position.from_fen(TERMINAL_RED_WIN_FEN)
            ).artifact
            position_key = store.save(artifact, node_limit=100)

            deleted = store.delete_proof(position_key, artifact.target, artifact.max_ply)

            self.assertEqual(deleted, 1)
            self.assertEqual(store.iter_proofs(), [])

    def test_history_signature_distinguishes_stored_positions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProofStore(Path(tmp) / "proofs.sqlite")
            state = GameState.from_uci_position(
                "position fen 4k4/9/9/9/9/9/4P4/9/R8/4K4 w - - 0 1 "
                "moves a1a2 e9e8 a2a1 e8e9 a1a2 e9e8 a2a1 e8e9"
            )
            artifact = BoundedProofSearch("red", max_ply=0).search(state).artifact

            position_key = store.save(artifact, node_limit=100)
            loaded_without_history = store.load(artifact.fen, artifact.target, artifact.max_ply)
            loaded_with_history = store.load_with_history(
                artifact.fen,
                artifact.history_signature,
                artifact.target,
                artifact.max_ply,
            )

        self.assertTrue(position_key.startswith("history:"))
        self.assertIsNone(loaded_without_history)
        self.assertIsNotNone(loaded_with_history)
        assert loaded_with_history is not None
        self.assertEqual(loaded_with_history.artifact.history_signature, artifact.history_signature)
        self.assertEqual(loaded_with_history.artifact.position_command, state.to_uci_position())

    def test_resolve_for_search_reuses_proven_within_requested_depth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProofStore(Path(tmp) / "proofs.sqlite")
            artifact = BoundedProofSearch("red", max_ply=1).search(
                Position.from_fen(RED_WIN_IN_ONE_FEN)
            ).artifact
            store.save(artifact, node_limit=100)

            too_shallow = store.resolve_for_search(RED_WIN_IN_ONE_FEN, artifact.target, max_ply=0)
            reusable = store.resolve_for_search(RED_WIN_IN_ONE_FEN, artifact.target, max_ply=2)

        self.assertIsNone(too_shallow)
        self.assertIsNotNone(reusable)
        assert reusable is not None
        self.assertEqual(reusable.status, ProofStatus.PROVEN)

    def test_resolve_proven_returns_shallowest_proof_without_online_depth_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProofStore(Path(tmp) / "proofs.sqlite")
            shallow = BoundedProofSearch("red", max_ply=1).search(
                Position.from_fen(RED_WIN_IN_ONE_FEN)
            ).artifact
            deeper = BoundedProofSearch("red", max_ply=2).search(
                Position.from_fen(RED_WIN_IN_ONE_FEN)
            ).artifact
            store.save(deeper, node_limit=200)
            store.save(shallow, node_limit=100)

            resolved = store.resolve_proven(RED_WIN_IN_ONE_FEN, shallow.target)
            too_deep_for_bound = store.resolve_proven(
                RED_WIN_IN_ONE_FEN,
                shallow.target,
                max_ply=0,
            )

        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.max_ply, 1)
        self.assertIsNone(too_deep_for_bound)


if __name__ == "__main__":
    unittest.main()
