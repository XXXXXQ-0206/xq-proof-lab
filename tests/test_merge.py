from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import context  # noqa: F401
from context import ROOT
from fixtures import RED_WIN_IN_ONE_FEN
from xiangqi_core import GameState, Move, Position
from xiangqi_solver import (
    BoundedProofSearch,
    DfpnLimits,
    DfpnSearch,
    ProofArtifact,
    ProofStatus,
    ProofStore,
    ProofTarget,
    ProofVerifier,
    merge_resolved_frontier,
)
from xiangqi_solver.proof import node_kind_for


class MergeTests(unittest.TestCase):
    def test_merge_replaces_unknown_root_from_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProofStore(Path(tmp) / "proofs.sqlite")
            unknown = BoundedProofSearch("red", max_ply=0).search(
                Position.from_fen(RED_WIN_IN_ONE_FEN)
            ).artifact
            proven = BoundedProofSearch("red", max_ply=1).search(
                Position.from_fen(RED_WIN_IN_ONE_FEN)
            ).artifact
            store.save(proven, node_limit=100)

            merged = merge_resolved_frontier(unknown, store)

        self.assertEqual(merged.status, ProofStatus.PROVEN)
        self.assertEqual(merged.max_ply, 1)
        self.assertTrue(ProofVerifier().verify(merged).valid)

    def test_merge_grafts_expanded_unknown_root_from_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProofStore(Path(tmp) / "proofs.sqlite")
            root = Position.start()
            unknown = BoundedProofSearch("red", max_ply=0).search(root).artifact
            expanded = DfpnSearch(
                "red",
                max_ply=1,
                limits=DfpnLimits(proof_threshold=1),
            ).search(root).artifact
            store.save(expanded, node_limit=100)

            ordinary_resolve = store.resolve(unknown.fen, unknown.target, unknown.max_ply)
            merge_resolve = store.resolve_for_merge(unknown.fen, unknown.target, unknown.max_ply)
            merged = merge_resolved_frontier(unknown, store)

        self.assertIsNone(ordinary_resolve)
        self.assertIsNotNone(merge_resolve)
        self.assertEqual(merged.status, ProofStatus.UNKNOWN)
        self.assertEqual(merged.max_ply, 1)
        self.assertTrue(merged.children)
        self.assertTrue(ProofVerifier().verify(merged).valid)

    def test_merge_frontier_cli_grafts_expanded_unknown_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            store_path = tmp_path / "proofs.sqlite"
            artifact_path = tmp_path / "root.json"
            output_path = tmp_path / "merged.json"
            store = ProofStore(store_path)
            root = Position.start()
            unknown = BoundedProofSearch("red", max_ply=0).search(root).artifact
            expanded = DfpnSearch(
                "red",
                max_ply=1,
                limits=DfpnLimits(proof_threshold=1),
            ).search(root).artifact
            store.save(expanded, node_limit=100)
            artifact_path.write_text(
                json.dumps(unknown.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "merge_frontier.py"),
                    "--store",
                    str(store_path),
                    "--artifact",
                    str(artifact_path),
                    "--output",
                    str(output_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            output = json.loads(result.stdout)
            merged = ProofArtifact.from_dict(json.loads(output_path.read_text(encoding="utf-8")))

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(output["children"], 1)
        self.assertEqual(merged.status, ProofStatus.UNKNOWN)
        self.assertTrue(merged.children)
        self.assertTrue(ProofVerifier().verify(merged).valid)

    def test_merge_replaces_non_root_unknown_leaf_from_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProofStore(Path(tmp) / "proofs.sqlite")
            root_position = Position.from_fen(RED_WIN_IN_ONE_FEN)
            winning_move = Move.from_uci("a8a0")
            child_position = root_position.make_move(winning_move)
            child_state = GameState.from_uci_position(f"position fen {child_position.to_fen()}")
            unknown_child = ProofArtifact(
                fen=child_position.to_fen(),
                target=ProofTarget.RED,
                max_ply=0,
                node_kind=node_kind_for(child_position, ProofTarget.RED),
                status=ProofStatus.UNKNOWN,
                proof=1,
                disproof=1,
                move=winning_move.to_uci(),
                reason="queued_child",
                history_signature=child_state.history_signature(),
                position_command=child_state.to_uci_position(),
            )
            parent = ProofArtifact(
                fen=root_position.to_fen(),
                target=ProofTarget.RED,
                max_ply=1,
                node_kind=node_kind_for(root_position, ProofTarget.RED),
                status=ProofStatus.UNKNOWN,
                proof=1,
                disproof=1,
                reason="partial_parent",
                children=(unknown_child,),
            )
            resolved_child = replace(
                BoundedProofSearch("red", max_ply=0).search(child_position).artifact,
                history_signature=child_state.history_signature(),
            )
            store.save(resolved_child, node_limit=100)

            merged = merge_resolved_frontier(parent, store)

        self.assertEqual(merged.status, ProofStatus.PROVEN)
        self.assertEqual(merged.children[0].move, winning_move.to_uci())
        self.assertEqual(merged.children[0].status, ProofStatus.PROVEN)
        self.assertEqual(merged.children[0].history_signature, child_state.history_signature())
        self.assertEqual(merged.children[0].position_command, child_state.to_uci_position())
        self.assertTrue(ProofVerifier().verify(merged).valid)


if __name__ == "__main__":
    unittest.main()
