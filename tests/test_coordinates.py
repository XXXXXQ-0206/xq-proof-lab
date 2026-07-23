from __future__ import annotations

import unittest

import context  # noqa: F401
from xiangqi_core import coords_to_square, file_rank, square_name, square_to_coords


class CoordinateTests(unittest.TestCase):
    def test_round_trip_all_squares(self) -> None:
        for file in range(9):
            for rank in range(10):
                square = coords_to_square(file, rank)
                self.assertEqual(file_rank(square), (file, rank))
                self.assertEqual(square_to_coords(square_name(square)), (file, rank))

    def test_reject_invalid_square_name(self) -> None:
        with self.assertRaises(ValueError):
            square_to_coords("j0")
        with self.assertRaises(ValueError):
            square_to_coords("a10")


if __name__ == "__main__":
    unittest.main()
