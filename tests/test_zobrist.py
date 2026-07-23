from __future__ import annotations

import unittest

import context  # noqa: F401
from xiangqi_core import Move, Position, Zobrist


class ZobristTests(unittest.TestCase):
    def test_hash_is_deterministic(self) -> None:
        position = Position.start()
        self.assertEqual(Zobrist().hash_position(position), Zobrist().hash_position(position))

    def test_hash_changes_after_move(self) -> None:
        position = Position.start()
        moved = position.make_move(Move.from_uci("h2e2"))
        zobrist = Zobrist()
        self.assertNotEqual(zobrist.hash_position(position), zobrist.hash_position(moved))


if __name__ == "__main__":
    unittest.main()
