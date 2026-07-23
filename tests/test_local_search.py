from __future__ import annotations

import unittest
from threading import Event
from unittest.mock import patch

import context  # noqa: F401
from fixtures import IMMEDIATE_LOSS_FEN, RED_WIN_IN_ONE_FEN
from xiangqi_core import GameState, Position
from xiangqi_evaluators import LocalSearchMoveOrderer
from xiangqi_evaluators.local_search import _SearchInterrupted


class LocalSearchMoveOrdererTests(unittest.TestCase):
    def test_immediate_checkmate_is_ordered_first(self) -> None:
        position = Position.from_fen(RED_WIN_IN_ONE_FEN)

        ordered = LocalSearchMoveOrderer(depth=1).order_moves(
            position,
            position.legal_moves(),
        )

        self.assertEqual(ordered[0].to_uci(), "a8a0")
        self.assertEqual(
            position.make_move(ordered[0], validate=False).game_result(),
            "red_win",
        )

    def test_two_ply_search_demotes_move_allowing_checkmate(self) -> None:
        position = Position.from_fen(IMMEDIATE_LOSS_FEN)
        legal_by_uci = {move.to_uci(): move for move in position.legal_moves()}

        ordered = LocalSearchMoveOrderer(depth=2).order_moves(
            position,
            [legal_by_uci["a6b4"], legal_by_uci["d2e1"]],
        )

        self.assertEqual([move.to_uci() for move in ordered], ["d2e1", "a6b4"])

    def test_equal_scores_use_deterministic_uci_tie_break(self) -> None:
        position = Position.start()
        legal_by_uci = {move.to_uci(): move for move in position.legal_moves()}
        candidates = [legal_by_uci["i3i4"], legal_by_uci["a3a4"]]

        ordered = LocalSearchMoveOrderer(depth=1).order_moves(position, candidates)

        self.assertEqual([move.to_uci() for move in ordered], ["a3a4", "i3i4"])

    def test_static_evaluation_prefers_material_gain(self) -> None:
        position = Position.from_fen(
            "4k4/9/9/9/4p4/9/9/9/8r/4K3R w - - 0 1"
        )

        ordered = LocalSearchMoveOrderer(depth=1).order_moves(
            position,
            position.legal_moves(),
        )

        self.assertEqual(ordered[0].to_uci(), "i0i1")

    def test_static_evaluation_rewards_giving_check(self) -> None:
        position = Position.from_fen(
            "4k4/9/9/9/4P4/9/9/9/9/R3K4 w - - 0 1"
        )
        legal_by_uci = {move.to_uci(): move for move in position.legal_moves()}

        ordered = LocalSearchMoveOrderer(depth=1).order_moves(
            position,
            [legal_by_uci["a0a1"], legal_by_uci["a0a9"]],
        )

        self.assertEqual([move.to_uci() for move in ordered], ["a0a9", "a0a1"])

    def test_static_ordering_prefers_center_pawn_advance_to_knight_development(self) -> None:
        state = GameState.from_uci_position(
            "position startpos moves h2e2 h9g7 g3g4"
        )
        legal_by_uci = {move.to_uci(): move for move in state.legal_moves()}

        ordered = LocalSearchMoveOrderer(depth=1).order_moves(
            state,
            [legal_by_uci["c6c5"], legal_by_uci["b9c7"]],
        )

        self.assertEqual([move.to_uci() for move in ordered], ["c6c5", "b9c7"])

    def test_interrupted_order_keeps_cheap_static_order_over_uci_order(self) -> None:
        class InterruptingOrderer(LocalSearchMoveOrderer):
            def _check_search_limits(self) -> None:
                raise _SearchInterrupted

        state = GameState.from_uci_position(
            "position startpos moves h2e2 h9g7 e3e4 b7e7 d0e1 b9c7 c3c4 a9b9 b0c2 b9b3"
        )
        legal_by_uci = {move.to_uci(): move for move in state.legal_moves()}

        ordered = InterruptingOrderer(depth=1).order_moves(
            state,
            [legal_by_uci["a0a1"], legal_by_uci["h0g2"]],
        )

        self.assertEqual([move.to_uci() for move in ordered], ["h0g2", "a0a1"])

    def test_interrupted_order_demotes_recapturable_capture(self) -> None:
        class InterruptingOrderer(LocalSearchMoveOrderer):
            def _check_search_limits(self) -> None:
                raise _SearchInterrupted

        state = GameState.from_uci_position(
            "position startpos moves h2e2 h9g7 e3e4 b7e7 d0e1 b9c7 c3c4 a9b9 b0c2 b9b3"
        )
        legal_by_uci = {move.to_uci(): move for move in state.legal_moves()}

        ordered = InterruptingOrderer(depth=1).order_moves(
            state,
            [legal_by_uci["e2e6"], legal_by_uci["h0g2"]],
        )

        self.assertEqual([move.to_uci() for move in ordered], ["h0g2", "e2e6"])

    def test_static_root_guard_demotes_immediately_recaptured_capture(self) -> None:
        state = GameState.from_uci_position("position startpos moves h2e2 h9g7")
        legal_by_uci = {move.to_uci(): move for move in state.legal_moves()}
        candidates = [legal_by_uci["b2b9"], legal_by_uci["b0a2"]]

        ordered = LocalSearchMoveOrderer(depth=1).order_moves(state, candidates)

        self.assertEqual([move.to_uci() for move in ordered], ["b0a2", "b2b9"])

    def test_static_root_guard_demotes_move_exposing_another_piece_to_capture(self) -> None:
        state = GameState.from_uci_position(
            "position startpos moves h2e2 h9g7 h0g2 b9c7 g3g4 a6a5 i0h0"
        )

        ordered = LocalSearchMoveOrderer(depth=1).order_moves(
            state,
            state.legal_moves(),
        )

        self.assertNotEqual(ordered[0].to_uci(), "c6c5")

    def test_static_root_guard_credits_recapture_of_rook_capture(self) -> None:
        state = GameState.from_uci_position(
            "position startpos moves h2e2 h9g7 h0g2 b9c7 g3g4 a6a5 i0h0"
        )

        ordered = LocalSearchMoveOrderer(depth=1).order_moves(
            state,
            state.legal_moves(),
        )
        ordered_uci = [move.to_uci() for move in ordered]

        self.assertLess(ordered_uci.index("i9h9"), ordered_uci.index("b7b0"))

    def test_static_evaluation_prefers_knight_development_to_side_pawn_push(self) -> None:
        state = GameState.from_uci_position("position startpos moves h2e2")
        legal_by_uci = {move.to_uci(): move for move in state.legal_moves()}
        candidates = [legal_by_uci["a6a5"], legal_by_uci["b9c7"]]

        ordered = LocalSearchMoveOrderer(depth=1).order_moves(state, candidates)

        self.assertEqual([move.to_uci() for move in ordered], ["b9c7", "a6a5"])

    def test_node_limit_bounds_search_and_keeps_all_candidates(self) -> None:
        position = Position.start()
        candidates = list(reversed(position.legal_moves()))
        orderer = LocalSearchMoveOrderer(depth=3, node_limit=5)

        ordered = orderer.order_moves(position, candidates)

        self.assertEqual(orderer.nodes_searched, 5)
        self.assertEqual(
            {move.to_uci() for move in ordered},
            {move.to_uci() for move in candidates},
        )

    def test_search_preserves_game_state_history(self) -> None:
        state = GameState.from_uci_position("position startpos moves h2e2")

        ordered = LocalSearchMoveOrderer(depth=2, node_limit=5000).order_moves(
            state,
            state.legal_moves(),
        )

        self.assertEqual(len(ordered), len(state.legal_moves()))
        self.assertTrue(all(move in state.legal_moves() for move in ordered))

    def test_negamax_scores_threefold_draw_as_neutral(self) -> None:
        state = GameState.from_uci_position(
            "position fen 4k4/9/9/9/9/9/4P4/9/R8/4K4 w - - 0 1 "
            "moves a1a2 e9e8 a2a1 e8e9 a1a2 e9e8 a2a1 e8e9"
        )

        score = LocalSearchMoveOrderer(depth=1)._negamax(
            state,
            depth=1,
            alpha=-1_000_000,
            beta=1_000_000,
            ply=0,
        )

        self.assertEqual(score, 0)

    def test_zero_time_limit_returns_deterministic_order_without_search(self) -> None:
        position = Position.start()
        candidates = list(reversed(position.legal_moves()))
        orderer = LocalSearchMoveOrderer(depth=3, node_limit=5000)

        ordered = orderer.order_moves_with_time_limit(position, candidates, 0)

        self.assertEqual(orderer.nodes_searched, 0)
        self.assertEqual(
            [move.to_uci() for move in ordered],
            sorted(move.to_uci() for move in candidates),
        )

    def test_stop_event_interrupts_local_ordering(self) -> None:
        position = Position.start()
        stop_event = Event()
        stop_event.set()
        orderer = LocalSearchMoveOrderer(depth=3)

        ordered = orderer.order_moves_with_stop_event(
            position,
            position.legal_moves(),
            stop_event,
        )

        self.assertEqual(orderer.nodes_searched, 0)
        self.assertEqual(
            [move.to_uci() for move in ordered],
            sorted(move.to_uci() for move in position.legal_moves()),
        )

    def test_transposition_table_reuses_positions_at_the_same_depth(self) -> None:
        position = Position.start()
        legal_by_uci = {move.to_uci(): move for move in position.legal_moves()}
        candidates = [legal_by_uci["a3a4"], legal_by_uci["c3c4"]]
        orderer = LocalSearchMoveOrderer(depth=3)

        ordered = orderer.order_moves(position, candidates)

        self.assertEqual(orderer.completed_depth, 3)
        self.assertGreater(orderer.transposition_hits, 0)
        self.assertEqual(
            {move.to_uci() for move in ordered},
            {move.to_uci() for move in candidates},
        )

    def test_timeout_keeps_last_completed_depth_ordering(self) -> None:
        position = Position.from_fen(IMMEDIATE_LOSS_FEN)
        legal_by_uci = {move.to_uci(): move for move in position.legal_moves()}
        candidates = [legal_by_uci["a6b4"], legal_by_uci["d2e1"]]
        shallow = LocalSearchMoveOrderer(depth=1).order_moves(position, candidates)
        completed_depth_two = LocalSearchMoveOrderer(depth=2).order_moves(position, candidates)

        self.assertNotEqual(
            [move.to_uci() for move in shallow],
            [move.to_uci() for move in completed_depth_two],
        )
        clock = _TimeoutAfterCalls(10)
        orderer = LocalSearchMoveOrderer(depth=3)
        with patch("xiangqi_evaluators.local_search.perf_counter", new=clock):
            ordered = orderer.order_moves_with_time_limit(position, candidates, 100)

        self.assertEqual(orderer.completed_depth, 1)
        self.assertEqual(
            [move.to_uci() for move in ordered],
            [move.to_uci() for move in shallow],
        )


class _TimeoutAfterCalls:
    def __init__(self, permitted_calls: int) -> None:
        self.permitted_calls = permitted_calls
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        return 0.0 if self.calls <= self.permitted_calls else 1.0


if __name__ == "__main__":
    unittest.main()
