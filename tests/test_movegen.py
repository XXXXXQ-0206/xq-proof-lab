from __future__ import annotations

import unittest
from unittest.mock import patch

import context  # noqa: F401
from xiangqi_core import Color, Move, Position


class MoveGenerationTests(unittest.TestCase):
    def test_start_position_has_44_legal_moves(self) -> None:
        moves = {move.to_uci() for move in Position.start().legal_moves()}
        self.assertEqual(len(moves), 44)
        self.assertIn("h2e2", moves)
        self.assertIn("b0a2", moves)
        self.assertIn("e0e1", moves)

    def test_legal_move_generation_finds_king_once_per_position(self) -> None:
        position = Position.start()
        original_king_square = Position.king_square
        calls = 0

        def counted_king_square(current, color):
            nonlocal calls
            calls += 1
            return original_king_square(current, color)

        Position._legal_moves_cached.cache_clear()
        with patch.object(Position, "king_square", counted_king_square):
            moves = position.legal_moves()

        self.assertEqual(len(moves), 44)
        self.assertEqual(calls, 1)

    def test_legal_moves_are_cached_for_an_immutable_position(self) -> None:
        position = Position.start()
        original_leaves_king_safe = Position._leaves_king_safe
        calls = 0

        def counted_leaves_king_safe(current, move, moving, king_square=None):
            nonlocal calls
            calls += 1
            return original_leaves_king_safe(current, move, moving, king_square)

        Position._legal_moves_cached.cache_clear()
        with patch.object(Position, "_leaves_king_safe", counted_leaves_king_safe):
            first = position.legal_moves()
            second = position.legal_moves()

        self.assertEqual(first, second)
        self.assertEqual(calls, 44)

    def test_attack_query_skips_pieces_that_cannot_reach_target(self) -> None:
        position = Position.from_fen(
            "4k4/9/9/4p4/9/9/9/9/r8/4K4 w - - 0 1"
        )
        original_attacks_square = Position._attacks_square
        calls = 0

        def counted_attacks_square(current, source, piece, target):
            nonlocal calls
            calls += 1
            return original_attacks_square(current, source, piece, target)

        with patch.object(Position, "_attacks_square", counted_attacks_square):
            self.assertFalse(position.is_square_attacked(Move.from_uci("e0e4").to_square, Color.BLACK))

        self.assertLess(calls, 3)

    def test_blocker_count_uses_validated_square_indices_directly(self) -> None:
        position = Position.start()
        move = Move.from_uci("e0e4")

        with patch(
            "xiangqi_core.position.coords_to_square",
            side_effect=AssertionError("aligned blocker scan should not revalidate coordinates"),
        ):
            self.assertEqual(position._blockers_between(move.from_square, move.to_square), 1)

    def test_attack_query_reads_candidate_piece_from_immutable_board(self) -> None:
        position = Position.from_fen(
            "4k4/9/9/9/4r4/9/9/9/9/4K4 b - - 0 1",
            strict=False,
        )
        target = Move.from_uci("e0e1").to_square

        with patch.object(
            Position,
            "piece_at",
            side_effect=AssertionError("candidate lookup should use the immutable board"),
        ):
            self.assertTrue(position.is_square_attacked(target, Color.BLACK))

    def test_legal_generation_does_not_build_full_move_state_for_attack_checks(self) -> None:
        position = Position.start()

        with patch.object(
            Position,
            "make_move",
            side_effect=AssertionError("legal generation should use an attack-only child board"),
        ):
            moves = position.legal_moves()

        self.assertEqual(len(moves), 44)

    def test_flying_general_counts_as_check(self) -> None:
        position = Position.from_fen("4k4/9/9/9/9/9/9/9/9/4K4 w - - 0 1", strict=False)
        self.assertTrue(position.is_in_check(Color.RED))
        self.assertTrue(position.is_in_check(Color.BLACK))

    def test_king_does_not_attack_non_king_along_open_file(self) -> None:
        position = Position.from_fen(
            "4k4/9/9/9/9/9/4P4/9/9/4K4 w - - 0 1",
            strict=False,
        )
        pawn_square = Move.from_uci("e0e3").to_square

        self.assertFalse(position.is_square_attacked(pawn_square, Color.RED))
        self.assertEqual(position.defenders_to(pawn_square, Color.RED), ())

    def test_legal_moves_cannot_expose_flying_general(self) -> None:
        position = Position.from_fen("4k4/9/9/9/4R4/9/9/9/9/4K4 w - - 0 1", strict=False)
        moves = {move.to_uci() for move in position.legal_moves()}
        self.assertNotIn("e5d5", moves)
        self.assertNotIn("e5f5", moves)
        self.assertIn("e5e6", moves)

    def test_make_move_updates_side_and_counters(self) -> None:
        position = Position.start()
        moved = position.make_move(Move.from_uci("h2e2"))
        self.assertEqual(moved.side_to_move, Color.BLACK)
        self.assertEqual(moved.halfmove_clock, 1)
        self.assertEqual(moved.fullmove_number, 1)

    def test_pawn_move_does_not_reset_rule60_counter(self) -> None:
        position = Position.from_fen(
            "4k4/9/9/9/9/9/4P4/9/9/4K4 w - - 17 1",
            strict=False,
        )
        moved = position.make_move(Move.from_uci("e3e4"))
        self.assertEqual(moved.halfmove_clock, 18)

    def test_capture_resets_rule60_counter(self) -> None:
        position = Position.from_fen(
            "4k4/9/9/9/9/4p4/4P4/9/9/4K4 w - - 17 1",
            strict=False,
        )
        moved = position.make_move(Move.from_uci("e3e4"))
        self.assertEqual(moved.halfmove_clock, 0)

    def test_attackers_to_finds_rook_attacker(self) -> None:
        position = Position.from_fen(
            "4k4/9/9/9/4r4/9/4P4/9/9/4K4 b - - 0 1",
            strict=False,
        )
        move = Move.from_uci("e5e3")
        self.assertEqual(position.attackers_to(move.to_square, Color.BLACK), (move.from_square,))

    def test_attackers_to_finds_cannon_attacker_with_screen(self) -> None:
        position = Position.from_fen(
            "4k4/9/9/9/4c4/4P4/4P4/9/9/4K4 b - - 0 1",
            strict=False,
        )
        move = Move.from_uci("e5e3")
        self.assertEqual(position.attackers_to(move.to_square, Color.BLACK), (move.from_square,))

    def test_attackers_to_obeys_knight_leg(self) -> None:
        open_leg = Position.from_fen(
            "4k4/4p4/9/9/3n5/9/4P4/9/9/4K4 b - - 0 1",
            strict=False,
        )
        blocked_leg = Position.from_fen(
            "4k4/4p4/9/9/3n5/3p5/4P4/9/9/4K4 b - - 0 1",
            strict=False,
        )
        move = Move.from_uci("d5e3")

        self.assertEqual(open_leg.attackers_to(move.to_square, Color.BLACK), (move.from_square,))
        self.assertEqual(blocked_leg.attackers_to(move.to_square, Color.BLACK), ())

    def test_attackers_to_ignores_own_piece_target(self) -> None:
        position = Position.from_fen(
            "4k4/9/9/9/4r4/9/4p4/9/9/4K4 b - - 0 1",
            strict=False,
        )
        move = Move.from_uci("e5e3")
        self.assertEqual(position.attackers_to(move.to_square, Color.BLACK), ())

    def test_defenders_to_finds_geometric_protectors(self) -> None:
        position = Position.from_fen(
            "4k4/9/9/9/9/9/4P4/9/4R4/4K4 w - - 0 1",
            strict=False,
        )
        move = Move.from_uci("e1e3")

        self.assertEqual(position.attackers_to(move.to_square, Color.RED), ())
        self.assertEqual(position.defenders_to(move.to_square, Color.RED), (move.from_square,))


if __name__ == "__main__":
    unittest.main()
