from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import context  # noqa: F401
from fixtures import RED_WIN_IN_ONE_FEN, TERMINAL_RED_WIN_FEN
from xiangqi_core import GameState, Move, Position
from xiangqi_solver import BoundedProofSearch, ProofStatus, ProofStore, ProofVerifier


class ProofSearchTests(unittest.TestCase):
    def test_terminal_red_win_is_proven_for_red(self) -> None:
        result = BoundedProofSearch("red", max_ply=0).search(Position.from_fen(TERMINAL_RED_WIN_FEN))
        self.assertEqual(result.artifact.status, ProofStatus.PROVEN)
        self.assertTrue(ProofVerifier().verify(result.artifact).valid)

    def test_terminal_red_win_is_disproven_for_black(self) -> None:
        result = BoundedProofSearch("black", max_ply=0).search(Position.from_fen(TERMINAL_RED_WIN_FEN))
        self.assertEqual(result.artifact.status, ProofStatus.DISPROVEN)
        self.assertTrue(ProofVerifier().verify(result.artifact).valid)

    def test_red_win_in_one_is_proven_with_one_ply(self) -> None:
        result = BoundedProofSearch("red", max_ply=1).search(Position.from_fen(RED_WIN_IN_ONE_FEN))
        self.assertEqual(result.artifact.status, ProofStatus.PROVEN)
        self.assertTrue(any(child.status is ProofStatus.PROVEN for child in result.artifact.children))
        self.assertTrue(ProofVerifier().verify(result.artifact).valid)

    def test_red_win_in_one_is_unknown_at_zero_ply(self) -> None:
        result = BoundedProofSearch("red", max_ply=0).search(Position.from_fen(RED_WIN_IN_ONE_FEN))
        self.assertEqual(result.artifact.status, ProofStatus.UNKNOWN)
        self.assertTrue(ProofVerifier().verify(result.artifact).valid)

    def test_halfmove_120_is_not_a_terminal_draw(self) -> None:
        result = BoundedProofSearch("red", max_ply=0).search(
            Position.from_fen("4k4/9/9/9/9/9/4P4/9/9/4K4 w - - 120 1")
        )
        self.assertEqual(result.artifact.status, ProofStatus.UNKNOWN)
        self.assertEqual(result.artifact.reason, "ply_bound")
        self.assertTrue(ProofVerifier().verify(result.artifact).valid)

    def test_time_limit_returns_unknown_without_overrunning_budget(self) -> None:
        result = BoundedProofSearch(
            "red",
            max_ply=1,
            node_limit=1000,
            time_limit_seconds=0,
        ).search(Position.from_fen(RED_WIN_IN_ONE_FEN))

        self.assertEqual(result.artifact.status, ProofStatus.UNKNOWN)
        self.assertEqual(result.artifact.reason, "time_limit")
        self.assertTrue(result.time_limit_reached)
        self.assertEqual(result.nodes_searched, 0)
        self.assertTrue(ProofVerifier().verify(result.artifact).valid)

    def test_time_limit_does_not_retain_partially_expanded_children(self) -> None:
        with patch("xiangqi_solver.search.perf_counter", side_effect=(0.0, 0.0, *([2.0] * 100))):
            result = BoundedProofSearch(
                "red",
                max_ply=1,
                node_limit=1_000,
                time_limit_seconds=1,
            ).search(Position.from_fen(RED_WIN_IN_ONE_FEN))

        self.assertEqual(result.artifact.status, ProofStatus.UNKNOWN)
        self.assertEqual(result.artifact.reason, "time_limit")
        self.assertEqual(result.artifact.children, ())
        self.assertTrue(result.time_limit_reached)
        self.assertTrue(ProofVerifier().verify(result.artifact).valid)

    def test_bounded_search_defers_non_repetition_rule_metadata(self) -> None:
        state = GameState.from_position(Position.from_fen(RED_WIN_IN_ONE_FEN))

        with patch("xiangqi_core.game._attacked_opponent_details", return_value=()) as details:
            result = BoundedProofSearch("red", max_ply=1).search(state)

        self.assertEqual(result.artifact.status, ProofStatus.PROVEN)
        self.assertEqual(details.call_count, 0)

    def test_reuses_resolved_store_proof_without_searching(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProofStore(Path(tmp) / "proofs.sqlite")
            proven = BoundedProofSearch("red", max_ply=1).search(
                Position.from_fen(RED_WIN_IN_ONE_FEN)
            ).artifact
            store.save(proven, node_limit=100)

            result = BoundedProofSearch(
                "red",
                max_ply=2,
                node_limit=1,
                resolver=store,
            ).search(Position.from_fen(RED_WIN_IN_ONE_FEN))

        self.assertEqual(result.artifact.status, ProofStatus.PROVEN)
        self.assertEqual(result.nodes_searched, 0)
        self.assertEqual(result.resolved_store_hits, 1)
        self.assertEqual(result.resolved_store_misses, 0)
        self.assertTrue(ProofVerifier().verify(result.artifact).valid)

    def test_resolved_store_proof_orders_winning_child_first(self) -> None:
        fen = "4k4/1N6R/9/3N5/4P4/9/9/9/9/4KR3 w - - 0 1"
        winning_move = Move.from_uci("i8i0")
        root = Position.from_fen(fen)
        child = root.make_move(winning_move)

        with tempfile.TemporaryDirectory() as tmp:
            store = ProofStore(Path(tmp) / "proofs.sqlite")
            proven_child = BoundedProofSearch("red", max_ply=0).search(child).artifact
            store.save(proven_child, node_limit=100)

            result = BoundedProofSearch(
                "red",
                max_ply=1,
                node_limit=1,
                resolver=store,
            ).search(root)

        self.assertEqual(result.artifact.status, ProofStatus.PROVEN)
        self.assertEqual(result.artifact.children[0].move, winning_move.to_uci())
        self.assertEqual(result.nodes_searched, 1)
        self.assertEqual(result.resolved_store_hits, 1)
        self.assertTrue(ProofVerifier().verify(result.artifact).valid)

    def test_rejects_invalid_resolved_store_proof(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProofStore(Path(tmp) / "proofs.sqlite")
            invalid = replace(
                BoundedProofSearch("red", max_ply=0).search(
                    Position.from_fen(RED_WIN_IN_ONE_FEN)
                ).artifact,
                status=ProofStatus.PROVEN,
            )
            store.save(invalid, node_limit=100, verify=False)

            with self.assertRaisesRegex(ValueError, "failed verification"):
                BoundedProofSearch("red", max_ply=0, resolver=store).search(
                    Position.from_fen(RED_WIN_IN_ONE_FEN)
                )


if __name__ == "__main__":
    unittest.main()
