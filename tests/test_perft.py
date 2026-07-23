from __future__ import annotations

import unittest

import context  # noqa: F401
from tools.perft import perft
from xiangqi_core import Position


class PerftTests(unittest.TestCase):
    def test_start_position_depth_1(self) -> None:
        self.assertEqual(perft(Position.start(), 1), 44)

    def test_start_position_depth_2(self) -> None:
        self.assertEqual(perft(Position.start(), 2), 1920)


if __name__ == "__main__":
    unittest.main()
