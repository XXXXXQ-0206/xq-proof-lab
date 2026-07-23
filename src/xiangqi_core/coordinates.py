from __future__ import annotations

BOARD_FILES = 9
BOARD_RANKS = 10
SQUARE_COUNT = BOARD_FILES * BOARD_RANKS
FILES = "abcdefghi"


def in_bounds(file: int, rank: int) -> bool:
    return 0 <= file < BOARD_FILES and 0 <= rank < BOARD_RANKS


def coords_to_square(file: int, rank: int) -> int:
    if not in_bounds(file, rank):
        raise ValueError(f"square out of bounds: file={file}, rank={rank}")
    return rank * BOARD_FILES + file


def file_rank(square: int) -> tuple[int, int]:
    if not 0 <= square < SQUARE_COUNT:
        raise ValueError(f"square out of bounds: {square}")
    return square % BOARD_FILES, square // BOARD_FILES


def square_name(square: int) -> str:
    file, rank = file_rank(square)
    return f"{FILES[file]}{rank}"


def square_to_coords(name: str) -> tuple[int, int]:
    if len(name) != 2:
        raise ValueError(f"invalid square name: {name!r}")
    file_char, rank_char = name[0], name[1]
    if file_char not in FILES or not rank_char.isdigit():
        raise ValueError(f"invalid square name: {name!r}")
    file = FILES.index(file_char)
    rank = int(rank_char)
    if not in_bounds(file, rank):
        raise ValueError(f"invalid square name: {name!r}")
    return file, rank


def square_from_name(name: str) -> int:
    return coords_to_square(*square_to_coords(name))
