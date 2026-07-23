from __future__ import annotations

import json
import subprocess
import sys
import unittest
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import context  # noqa: F401
from context import ROOT
from fixtures import RED_WIN_IN_ONE_FEN
from xiangqi_core import GameState, Move, Position
from xiangqi_solver import (
    BoundedProofSearch,
    DfpnLimits,
    DfpnSearch,
    ProofStatus,
    ProofStore,
    ProofVerifier,
    run_iterative_dfpn,
)
from xiangqi_solver.dfpn import _child_thresholds, _next_iteration_thresholds
from xiangqi_solver.pns import INF, NodeKind


class DfpnTests(unittest.TestCase):
    def test_time_limit_returns_verifiable_unknown_without_expanding(self) -> None:
        result = DfpnSearch(
            "red",
            max_ply=1,
            limits=DfpnLimits(node_limit=1_000, time_limit_seconds=0),
        ).search(Position.from_fen(RED_WIN_IN_ONE_FEN))

        self.assertEqual(result.artifact.status, ProofStatus.UNKNOWN)
        self.assertEqual(result.artifact.reason, "time_limit")
        self.assertTrue(result.time_limit_reached)
        self.assertEqual(result.nodes_searched, 0)
        self.assertTrue(ProofVerifier().verify(result.artifact).valid)

    def test_child_time_limit_discards_partial_dfpn_tree(self) -> None:
        with patch("xiangqi_solver.dfpn.perf_counter", side_effect=(0.0, 0.0, *([2.0] * 100))):
            result = DfpnSearch(
                "red",
                max_ply=1,
                limits=DfpnLimits(node_limit=1_000, time_limit_seconds=1),
            ).search(Position.from_fen(RED_WIN_IN_ONE_FEN))

        self.assertEqual(result.artifact.status, ProofStatus.UNKNOWN)
        self.assertEqual(result.artifact.reason, "time_limit")
        self.assertEqual(result.artifact.children, ())
        self.assertTrue(result.time_limit_reached)
        self.assertTrue(ProofVerifier().verify(result.artifact).valid)

    def test_partial_dfpn_aggregation_does_not_recompute_legal_coverage(self) -> None:
        with patch("xiangqi_solver.dfpn._all_legal_moves_covered", wraps=lambda *_args: True) as covered:
            DfpnSearch(
                "red",
                max_ply=1,
                limits=DfpnLimits(node_limit=1_000),
            ).search(Position.start())

        self.assertLessEqual(covered.call_count, 1)

    def test_dfpn_child_thresholds_for_or_nodes(self) -> None:
        proof, disproof = _child_thresholds(
            NodeKind.OR,
            proof_threshold=10,
            disproof_threshold=10,
            searched_siblings=[],
            unexpanded_siblings=2,
        )

        self.assertEqual((proof, disproof), (2, 8))

    def test_dfpn_child_thresholds_for_and_nodes(self) -> None:
        proof, disproof = _child_thresholds(
            NodeKind.AND,
            proof_threshold=10,
            disproof_threshold=10,
            searched_siblings=[],
            unexpanded_siblings=2,
        )

        self.assertEqual((proof, disproof), (8, 2))

    def test_dfpn_child_thresholds_keep_default_infinite_budget(self) -> None:
        proof, disproof = _child_thresholds(
            NodeKind.OR,
            proof_threshold=INF,
            disproof_threshold=INF,
            searched_siblings=[],
            unexpanded_siblings=30,
        )

        self.assertEqual((proof, disproof), (INF, INF))

    def test_dfpn_iteration_thresholds_advance_reached_side_only(self) -> None:
        self.assertEqual(
            _next_iteration_thresholds(
                proof_threshold=5,
                disproof_threshold=20,
                proof=9,
                disproof=4,
                growth=2,
            ),
            (10, 20),
        )
        self.assertEqual(
            _next_iteration_thresholds(
                proof_threshold=5,
                disproof_threshold=20,
                proof=2,
                disproof=30,
                growth=2,
            ),
            (5, 40),
        )

    def test_dfpn_iteration_thresholds_never_exceed_inf(self) -> None:
        self.assertEqual(
            _next_iteration_thresholds(
                proof_threshold=INF,
                disproof_threshold=INF - 1,
                proof=INF,
                disproof=INF,
                growth=2,
            ),
            (INF, INF),
        )

    def test_dfpn_proves_win_in_one(self) -> None:
        result = DfpnSearch("red", max_ply=1).search(Position.from_fen(RED_WIN_IN_ONE_FEN))

        self.assertEqual(result.artifact.status, ProofStatus.PROVEN)
        self.assertTrue(ProofVerifier().verify(result.artifact).valid)

    def test_dfpn_reuses_legal_moves_for_terminal_check_and_ordering(self) -> None:
        root = Position.from_fen(RED_WIN_IN_ONE_FEN)
        expected_calls = 2
        original_legal_moves = Position.legal_moves
        calls = 0

        def counted_legal_moves(position, color=None):
            nonlocal calls
            calls += 1
            return original_legal_moves(position, color)

        with patch.object(Position, "legal_moves", counted_legal_moves):
            result = DfpnSearch("red", max_ply=1).search(root)

        self.assertEqual(result.artifact.status, ProofStatus.PROVEN)
        self.assertEqual(calls, expected_calls)

    def test_dfpn_defers_non_repetition_rule_metadata(self) -> None:
        state = GameState.from_position(Position.from_fen(RED_WIN_IN_ONE_FEN))

        with patch("xiangqi_core.game._attacked_opponent_details", return_value=()) as details:
            result = DfpnSearch("red", max_ply=1).search(state)

        self.assertEqual(result.artifact.status, ProofStatus.PROVEN)
        self.assertEqual(details.call_count, 0)

    def test_dfpn_infinite_thresholds_do_not_stop_on_infinite_numbers(self) -> None:
        state = GameState.from_uci_position(
            "position startpos moves b2b9 a9b9 h2h9 i9h9 a3a4 b7e7 "
            "b0c2 h7h5 c3c4 b9b2 c2d4 e7e3 d4c6 h5h4 g3g4"
        )

        class CapturesFirst:
            def order_moves(self, position, moves):
                board = getattr(position, "position", position)
                return sorted(
                    moves,
                    key=lambda move: (
                        board.piece_at(move.to_square) is None,
                        move.to_uci(),
                    ),
                )

        result = DfpnSearch(
            "black",
            max_ply=7,
            limits=DfpnLimits(node_limit=100_000, time_limit_seconds=2),
            move_orderer=CapturesFirst(),
        ).search(state)

        self.assertEqual(result.artifact.reason, "time_limit")
        self.assertFalse(result.threshold_reached)

    def test_dfpn_default_ordering_prioritizes_captures(self) -> None:
        state = GameState.from_uci_position(
            "position startpos moves b2b9 a9b9 h2h9 i9h9 a3a4 b7e7 "
            "b0c2 h7h5 c3c4 b9b2 c2d4 e7e3 d4c6 h5h4 g3g4"
        )
        search = DfpnSearch("black", max_ply=7)

        ordered = search._order_moves(  # noqa: SLF001 - default ordering regression.
            state,
            max_ply=7,
            legal_moves=state.legal_moves(),
        )

        self.assertEqual(ordered[0].to_uci(), "h4c4")

    def test_dfpn_node_limit_returns_unknown(self) -> None:
        result = DfpnSearch(
            "red",
            max_ply=1,
            limits=DfpnLimits(node_limit=1),
        ).search(Position.from_fen(RED_WIN_IN_ONE_FEN))

        self.assertEqual(result.artifact.status, ProofStatus.UNKNOWN)
        self.assertTrue(result.node_limit_reached)
        self.assertTrue(ProofVerifier().verify(result.artifact).valid)

    def test_dfpn_result_reports_threshold_reached(self) -> None:
        result = DfpnSearch(
            "red",
            max_ply=1,
            limits=DfpnLimits(proof_threshold=1),
        ).search(Position.start())

        self.assertEqual(result.artifact.reason, "threshold")
        self.assertTrue(result.threshold_reached)
        self.assertTrue(ProofVerifier().verify(result.artifact).valid)

    def test_dfpn_cache_distinguishes_thresholds(self) -> None:
        search = DfpnSearch("red", max_ply=1)
        position = Position.start()

        threshold = search._search(  # noqa: SLF001 - regression coverage for threshold cache keys.
            position,
            max_ply=1,
            move=None,
            proof_threshold=1,
            disproof_threshold=10**12,
        )
        complete = search._search(  # noqa: SLF001 - regression coverage for threshold cache keys.
            position,
            max_ply=1,
            move=None,
            proof_threshold=10**12,
            disproof_threshold=10**12,
        )

        self.assertEqual(threshold.reason, "threshold")
        self.assertEqual(complete.reason, "dfpn_complete")
        self.assertTrue(ProofVerifier().verify(complete).valid)

    def test_iterative_dfpn_grows_threshold_until_complete(self) -> None:
        result = run_iterative_dfpn(
            Position.start(),
            "red",
            max_ply=1,
            initial_limits=DfpnLimits(
                proof_threshold=1,
                disproof_threshold=INF,
                node_limit=1000,
            ),
            max_iterations=3,
        )

        self.assertEqual(len(result.iterations), 2)
        self.assertEqual(result.iterations[0].proof_threshold, 1)
        self.assertTrue(result.iterations[0].threshold_reached)
        self.assertEqual(result.iterations[1].proof_threshold, 2)
        self.assertFalse(result.result.threshold_reached)
        self.assertEqual(result.result.artifact.reason, "dfpn_complete")
        self.assertGreater(result.total_nodes_searched, result.result.nodes_searched)
        self.assertTrue(ProofVerifier().verify(result.result.artifact).valid)

    def test_dfpn_preserves_history_signature(self) -> None:
        state = GameState.from_uci_position(
            "position fen 4k4/9/9/9/9/9/4P4/9/R8/4K4 w - - 0 1 "
            "moves a1a2 e9e8 a2a1 e8e9 a1a2 e9e8 a2a1 e8e9"
        )

        result = DfpnSearch("red", max_ply=1).search(state)

        self.assertEqual(result.artifact.history_signature, state.history_signature())
        self.assertEqual(result.artifact.position_command, state.to_uci_position())
        self.assertTrue(ProofVerifier().verify(result.artifact).valid)

    def test_dfpn_reuses_resolved_store_proof(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProofStore(Path(tmp) / "proofs.sqlite")
            proven = BoundedProofSearch("red", max_ply=1).search(
                Position.from_fen(RED_WIN_IN_ONE_FEN)
            ).artifact
            store.save(proven, node_limit=100)

            result = DfpnSearch(
                "red",
                max_ply=2,
                limits=DfpnLimits(node_limit=1),
                resolver=store,
            ).search(Position.from_fen(RED_WIN_IN_ONE_FEN))

        self.assertEqual(result.artifact.status, ProofStatus.PROVEN)
        self.assertEqual(result.nodes_searched, 0)
        self.assertEqual(result.resolved_store_hits, 1)
        self.assertEqual(result.resolved_store_misses, 0)
        self.assertTrue(ProofVerifier().verify(result.artifact).valid)

    def test_dfpn_orders_resolved_proven_or_child_first(self) -> None:
        fen = "4k4/1N6R/9/3N5/4P4/9/9/9/9/4KR3 w - - 0 1"
        late_winning_move = Move.from_uci("i8i0")
        root = Position.from_fen(fen)
        child = root.make_move(late_winning_move)

        with tempfile.TemporaryDirectory() as tmp:
            store = ProofStore(Path(tmp) / "proofs.sqlite")
            proven_child = BoundedProofSearch("red", max_ply=0).search(child).artifact
            store.save(proven_child, node_limit=100)

            result = DfpnSearch("red", max_ply=1, resolver=store).search(root)

        self.assertEqual(result.artifact.status, ProofStatus.PROVEN)
        self.assertEqual(result.artifact.children[0].move, late_winning_move.to_uci())
        self.assertTrue(ProofVerifier().verify(result.artifact).valid)

    def test_dfpn_rejects_invalid_resolved_store_proof(self) -> None:
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
                DfpnSearch("red", max_ply=0, resolver=store).search(
                    Position.from_fen(RED_WIN_IN_ONE_FEN)
                )

    def test_dfpn_cli_reuses_store_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "proofs.sqlite"
            store = ProofStore(store_path)
            proven = BoundedProofSearch("red", max_ply=1).search(
                Position.from_fen(RED_WIN_IN_ONE_FEN)
            ).artifact
            store.save(proven, node_limit=100)

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "dfpn.py"),
                    "--fen",
                    RED_WIN_IN_ONE_FEN,
                    "--target",
                    "red",
                    "--max-ply",
                    "2",
                    "--node-limit",
                    "1",
                    "--store",
                    str(store_path),
                    "--reuse-store",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        output = json.loads(result.stdout)
        self.assertEqual(output["status"], ProofStatus.PROVEN.value)
        self.assertEqual(output["nodes_searched"], 0)
        self.assertEqual(output["resolved_store_hits"], 1)
        self.assertEqual(output["resolved_store_misses"], 0)

    def test_dfpn_cli_can_run_iterative_threshold_growth(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "dfpn.py"),
                "--fen",
                Position.START_FEN,
                "--target",
                "red",
                "--max-ply",
                "1",
                "--proof-threshold",
                "1",
                "--disproof-threshold",
                str(INF),
                "--node-limit",
                "1000",
                "--iterative",
                "--iterations",
                "3",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        output = json.loads(result.stdout)
        self.assertTrue(output["iterative"])
        self.assertEqual(len(output["iterations"]), 2)
        self.assertTrue(output["iterations"][0]["threshold_reached"])
        self.assertEqual(output["iterations"][1]["reason"], "dfpn_complete")
        self.assertGreater(output["total_nodes_searched"], output["nodes_searched"])
        self.assertIn("cache_hits", output)
        self.assertIn("resolved_store_hits", output["iterations"][0])


if __name__ == "__main__":
    unittest.main()
