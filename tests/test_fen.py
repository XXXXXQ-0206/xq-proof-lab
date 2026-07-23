from __future__ import annotations

import unittest

import context  # noqa: F401
from xiangqi_core import Color, Piece, PieceType, Position, coords_to_square


class FenTests(unittest.TestCase):
    def test_start_fen_round_trips(self) -> None:
        position = Position.start()
        self.assertEqual(position.to_fen(), Position.START_FEN)
        self.assertEqual(position.side_to_move, Color.RED)

    def test_start_piece_locations(self) -> None:
        position = Position.start()
        self.assertEqual(position.piece_at(coords_to_square(4, 0)), Piece(Color.RED, PieceType.KING))
        self.assertEqual(position.piece_at(coords_to_square(4, 9)), Piece(Color.BLACK, PieceType.KING))
        self.assertEqual(position.piece_at(coords_to_square(1, 2)), Piece(Color.RED, PieceType.CANNON))
        self.assertEqual(position.piece_at(coords_to_square(7, 7)), Piece(Color.BLACK, PieceType.CANNON))

    def test_strict_fen_requires_six_fields(self) -> None:
        with self.assertRaises(ValueError):
            Position.from_fen("4k4/9/9/9/9/9/9/9/9/4K4 w")

    def test_rejects_zero_empty_square_digit(self) -> None:
        with self.assertRaises(ValueError):
            Position.from_fen("4k4/9/9/9/9/9/9/9/9/04K4 w - - 0 1")

    def test_rejects_too_many_pieces(self) -> None:
        with self.assertRaises(ValueError):
            Position.from_fen("4k4/9/9/9/9/9/9/9/RRR6/4K4 w - - 0 1")

    def test_rejects_piece_outside_rule_area(self) -> None:
        with self.assertRaises(ValueError):
            Position.from_fen("4k4/9/9/9/9/9/9/9/9/A3K4 w - - 0 1")
        with self.assertRaises(ValueError):
            Position.from_fen("4k4/9/9/9/4B4/9/9/9/9/4K4 w - - 0 1")
        with self.assertRaises(ValueError):
            Position.from_fen("4k4/9/9/9/9/9/9/9/4P4/4K4 w - - 0 1")


if __name__ == "__main__":
    unittest.main()
