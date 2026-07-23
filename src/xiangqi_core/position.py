from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import ClassVar, Iterable

from .coordinates import (
    BOARD_FILES,
    BOARD_RANKS,
    SQUARE_COUNT,
    coords_to_square,
    file_rank,
    in_bounds,
)
from .model import Color, Piece, PieceType
from .move import Move


MAX_PIECES_BY_KIND = {
    PieceType.ROOK: 2,
    PieceType.ADVISOR: 2,
    PieceType.CANNON: 2,
    PieceType.PAWN: 5,
    PieceType.KNIGHT: 2,
    PieceType.BISHOP: 2,
    PieceType.KING: 1,
}


def _palace_contains(color: Color, file: int, rank: int) -> bool:
    if file < 3 or file > 5:
        return False
    return 0 <= rank <= 2 if color is Color.RED else 7 <= rank <= 9


def _bishop_rank_ok(color: Color, rank: int) -> bool:
    return rank <= 4 if color is Color.RED else rank >= 5


def _pawn_crossed_river(color: Color, rank: int) -> bool:
    return rank >= 5 if color is Color.RED else rank <= 4


@lru_cache(maxsize=SQUARE_COUNT)
def _attack_candidate_squares(square: int) -> tuple[int, ...]:
    file, rank = file_rank(square)
    candidates: list[int] = []
    for step_file, step_rank in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        candidate_file = file + step_file
        candidate_rank = rank + step_rank
        while in_bounds(candidate_file, candidate_rank):
            candidates.append(candidate_rank * BOARD_FILES + candidate_file)
            candidate_file += step_file
            candidate_rank += step_rank

    for delta_file, delta_rank in (
        (1, 2),
        (2, 1),
        (2, -1),
        (1, -2),
        (-1, -2),
        (-2, -1),
        (-2, 1),
        (-1, 2),
        (1, 1),
        (1, -1),
        (-1, 1),
        (-1, -1),
        (2, 2),
        (2, -2),
        (-2, 2),
        (-2, -2),
    ):
        candidate_file = file + delta_file
        candidate_rank = rank + delta_rank
        if in_bounds(candidate_file, candidate_rank):
            candidates.append(candidate_rank * BOARD_FILES + candidate_file)
    return tuple(candidates)


@dataclass(frozen=True, slots=True)
class Position:
    board: tuple[Piece | None, ...]
    side_to_move: Color
    halfmove_clock: int = 0
    fullmove_number: int = 1

    START_FEN: ClassVar[str] = (
        "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/"
        "P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1"
    )

    @classmethod
    def start(cls) -> "Position":
        return cls.from_fen(cls.START_FEN)

    @classmethod
    def from_fen(cls, fen: str, strict: bool = True) -> "Position":
        parts = fen.strip().split()
        if strict and len(parts) != 6:
            raise ValueError("strict FEN must contain exactly 6 fields")
        if not strict and len(parts) < 2:
            raise ValueError("FEN must include placement and side to move")

        board: list[Piece | None] = [None] * SQUARE_COUNT
        rank = BOARD_RANKS - 1
        file = 0

        for char in parts[0]:
            if char == "/":
                if file != BOARD_FILES:
                    raise ValueError("rank ended before 9 files were described")
                rank -= 1
                file = 0
                if rank < 0:
                    raise ValueError("too many ranks in FEN")
                continue
            if char.isdigit():
                if char == "0":
                    raise ValueError("FEN empty-square digit must be 1 through 9")
                file += int(char)
                if file > BOARD_FILES:
                    raise ValueError("rank describes more than 9 files")
                continue
            if file >= BOARD_FILES:
                raise ValueError("rank describes more than 9 files")
            board[coords_to_square(file, rank)] = Piece.from_fen(char)
            file += 1

        if rank != 0 or file != BOARD_FILES:
            raise ValueError("FEN placement did not describe exactly 10 ranks")

        side = Color.from_fen(parts[1])
        halfmove = int(parts[4]) if len(parts) >= 5 else 0
        fullmove = int(parts[5]) if len(parts) >= 6 else 1
        position = cls(tuple(board), side, halfmove, fullmove)
        if strict:
            position._validate_position()
        else:
            position._require_one_king_each()
        return position

    def to_fen(self) -> str:
        return _to_fen_cached(
            self.board,
            self.side_to_move,
            self.halfmove_clock,
            self.fullmove_number,
        )

    def piece_at(self, square: int) -> Piece | None:
        return self.board[square]

    def king_square(self, color: Color) -> int:
        for square, piece in enumerate(self.board):
            if piece == Piece(color, PieceType.KING):
                return square
        raise ValueError(f"{color.name} king is missing")

    def legal_moves(self, color: Color | None = None) -> list[Move]:
        moving = color or self.side_to_move
        return list(self._legal_moves_cached(moving))

    @lru_cache(maxsize=32_768)
    def _legal_moves_cached(self, moving: Color) -> tuple[Move, ...]:
        king_square = self.king_square(moving)
        return tuple(
            move
            for move in self.pseudo_legal_moves(moving)
            if self._leaves_king_safe(move, moving, king_square)
        )

    def pseudo_legal_moves(self, color: Color | None = None) -> Iterable[Move]:
        moving = color or self.side_to_move
        for square, piece in enumerate(self.board):
            if piece is not None and piece.color is moving:
                yield from self._piece_moves(square, piece)

    def is_legal_move(self, move: Move, color: Color | None = None) -> bool:
        moving = color or self.side_to_move
        piece = self.piece_at(move.from_square)
        if piece is None or piece.color is not moving:
            return False
        if move not in set(self.pseudo_legal_moves(moving)):
            return False
        return self._leaves_king_safe(move, moving)

    def _leaves_king_safe(
        self,
        move: Move,
        moving: Color,
        king_square: int | None = None,
    ) -> bool:
        board = list(self.board)
        piece = board[move.from_square]
        board[move.from_square] = None
        board[move.to_square] = piece
        after = Position(tuple(board), moving.opponent)
        if king_square is None:
            king_square = self.king_square(moving)
        piece = self.piece_at(move.from_square)
        if piece is not None and piece.kind is PieceType.KING:
            king_square = move.to_square
        return not after.is_square_attacked(king_square, moving.opponent)

    def make_move(self, move: Move, validate: bool = True) -> "Position":
        if validate and not self.is_legal_move(move, self.side_to_move):
            raise ValueError(f"illegal move: {move}")

        piece = self.piece_at(move.from_square)
        if piece is None:
            raise ValueError(f"no piece on source square: {move}")
        captured = self.piece_at(move.to_square)
        if captured is not None and captured.kind is PieceType.KING:
            raise ValueError("king capture is not a legal move representation")

        board = list(self.board)
        board[move.from_square] = None
        board[move.to_square] = piece

        halfmove = 0 if captured is not None else self.halfmove_clock + 1
        fullmove = self.fullmove_number + (1 if self.side_to_move is Color.BLACK else 0)
        return Position(tuple(board), self.side_to_move.opponent, halfmove, fullmove)

    def is_in_check(self, color: Color) -> bool:
        return self.is_square_attacked(self.king_square(color), color.opponent)

    def game_result(self, legal_moves: list[Move] | None = None) -> str | None:
        if legal_moves is None:
            legal_moves = self.legal_moves(self.side_to_move)
        if legal_moves:
            return None
        return "black_win" if self.side_to_move is Color.RED else "red_win"

    def rule_state(self) -> str:
        return "no_history"

    def is_square_attacked(self, square: int, by_color: Color) -> bool:
        for source in _attack_candidate_squares(square):
            piece = self.board[source]
            if piece is not None and piece.color is by_color:
                if self._attacks_square(source, piece, square):
                    return True
        return False

    def attackers_to(self, square: int, by_color: Color, legal: bool = False) -> tuple[int, ...]:
        target_piece = self.piece_at(square)
        if target_piece is not None and target_piece.color is by_color:
            return ()

        return self._pieces_attacking_square(square, by_color, legal)

    def defenders_to(self, square: int, by_color: Color, legal: bool = False) -> tuple[int, ...]:
        return self._pieces_attacking_square(square, by_color, legal, exclude_square=square)

    def _pieces_attacking_square(
        self,
        square: int,
        by_color: Color,
        legal: bool = False,
        exclude_square: int | None = None,
    ) -> tuple[int, ...]:
        attackers: list[int] = []
        for from_square, piece in enumerate(self.board):
            if exclude_square is not None and from_square == exclude_square:
                continue
            if piece is None or piece.color is not by_color:
                continue
            if not self._attacks_square(from_square, piece, square):
                continue
            if legal and not self.is_legal_move(Move(from_square, square), by_color):
                continue
            attackers.append(from_square)
        return tuple(attackers)

    def _piece_moves(self, square: int, piece: Piece) -> Iterable[Move]:
        file, rank = file_rank(square)

        if piece.kind is PieceType.ROOK:
            yield from self._sliding_moves(square, piece.color, cannon=False)
        elif piece.kind is PieceType.CANNON:
            yield from self._sliding_moves(square, piece.color, cannon=True)
        elif piece.kind is PieceType.KNIGHT:
            for df, dr, lf, lr in (
                (1, 2, 0, 1),
                (2, 1, 1, 0),
                (2, -1, 1, 0),
                (1, -2, 0, -1),
                (-1, -2, 0, -1),
                (-2, -1, -1, 0),
                (-2, 1, -1, 0),
                (-1, 2, 0, 1),
            ):
                if in_bounds(file + lf, rank + lr) and self.piece_at(coords_to_square(file + lf, rank + lr)) is None:
                    yield from self._single_step(square, piece.color, file + df, rank + dr)
        elif piece.kind is PieceType.BISHOP:
            for df, dr in ((2, 2), (2, -2), (-2, 2), (-2, -2)):
                target_file, target_rank = file + df, rank + dr
                eye_file, eye_rank = file + df // 2, rank + dr // 2
                if (
                    in_bounds(target_file, target_rank)
                    and _bishop_rank_ok(piece.color, target_rank)
                    and self.piece_at(coords_to_square(eye_file, eye_rank)) is None
                ):
                    yield from self._single_step(square, piece.color, target_file, target_rank)
        elif piece.kind is PieceType.ADVISOR:
            for df, dr in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
                target_file, target_rank = file + df, rank + dr
                if _palace_contains(piece.color, target_file, target_rank):
                    yield from self._single_step(square, piece.color, target_file, target_rank)
        elif piece.kind is PieceType.KING:
            for df, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                target_file, target_rank = file + df, rank + dr
                if _palace_contains(piece.color, target_file, target_rank):
                    yield from self._single_step(square, piece.color, target_file, target_rank)
        elif piece.kind is PieceType.PAWN:
            forward = 1 if piece.color is Color.RED else -1
            yield from self._single_step(square, piece.color, file, rank + forward)
            if _pawn_crossed_river(piece.color, rank):
                yield from self._single_step(square, piece.color, file - 1, rank)
                yield from self._single_step(square, piece.color, file + 1, rank)

    def _single_step(self, source: int, color: Color, file: int, rank: int) -> Iterable[Move]:
        if not in_bounds(file, rank):
            return
        target = coords_to_square(file, rank)
        piece = self.piece_at(target)
        if piece is None or (piece.color is not color and piece.kind is not PieceType.KING):
            yield Move(source, target)

    def _sliding_moves(self, source: int, color: Color, cannon: bool) -> Iterable[Move]:
        file, rank = file_rank(source)
        for df, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            seen_screen = False
            step = 1
            while True:
                target_file = file + df * step
                target_rank = rank + dr * step
                if not in_bounds(target_file, target_rank):
                    break
                target = coords_to_square(target_file, target_rank)
                piece = self.piece_at(target)

                if not cannon:
                    if piece is None:
                        yield Move(source, target)
                    else:
                        if piece.color is not color and piece.kind is not PieceType.KING:
                            yield Move(source, target)
                        break
                else:
                    if not seen_screen:
                        if piece is None:
                            yield Move(source, target)
                        else:
                            seen_screen = True
                    else:
                        if piece is not None:
                            if piece.color is not color and piece.kind is not PieceType.KING:
                                yield Move(source, target)
                            break
                step += 1

    def _attacks_square(self, source: int, piece: Piece, target: int) -> bool:
        sf, sr = file_rank(source)
        tf, tr = file_rank(target)
        df, dr = tf - sf, tr - sr

        if piece.kind is PieceType.ROOK:
            return (sf == tf or sr == tr) and self._blockers_between(source, target) == 0

        if piece.kind is PieceType.CANNON:
            return (sf == tf or sr == tr) and self._blockers_between(source, target) == 1

        if piece.kind is PieceType.KNIGHT:
            knight_legs = {
                (1, 2): (0, 1),
                (2, 1): (1, 0),
                (2, -1): (1, 0),
                (1, -2): (0, -1),
                (-1, -2): (0, -1),
                (-2, -1): (-1, 0),
                (-2, 1): (-1, 0),
                (-1, 2): (0, 1),
            }
            leg = knight_legs.get((df, dr))
            return leg is not None and self.piece_at(coords_to_square(sf + leg[0], sr + leg[1])) is None

        if piece.kind is PieceType.BISHOP:
            if abs(df) != 2 or abs(dr) != 2 or not _bishop_rank_ok(piece.color, tr):
                return False
            return self.piece_at(coords_to_square(sf + df // 2, sr + dr // 2)) is None

        if piece.kind is PieceType.ADVISOR:
            return abs(df) == 1 and abs(dr) == 1 and _palace_contains(piece.color, tf, tr)

        if piece.kind is PieceType.KING:
            if sf == tf and self._blockers_between(source, target) == 0:
                return self.piece_at(target) == Piece(piece.color.opponent, PieceType.KING)
            return abs(df) + abs(dr) == 1 and _palace_contains(piece.color, tf, tr)

        if piece.kind is PieceType.PAWN:
            forward = 1 if piece.color is Color.RED else -1
            if df == 0 and dr == forward:
                return True
            return dr == 0 and abs(df) == 1 and _pawn_crossed_river(piece.color, sr)

        return False

    def _blockers_between(self, source: int, target: int) -> int:
        sf, sr = source % BOARD_FILES, source // BOARD_FILES
        tf, tr = target % BOARD_FILES, target // BOARD_FILES
        if sf != tf and sr != tr:
            raise ValueError("squares are not aligned")
        step_file = (tf > sf) - (tf < sf)
        step_rank = (tr > sr) - (tr < sr)
        file = sf + step_file
        rank = sr + step_rank
        blockers = 0
        while (file, rank) != (tf, tr):
            if self.board[rank * BOARD_FILES + file] is not None:
                blockers += 1
            file += step_file
            rank += step_rank
        return blockers

    def _validate_position(self) -> None:
        self._require_one_king_each()
        for color in (Color.RED, Color.BLACK):
            if sum(1 for piece in self.board if piece is not None and piece.color is color) > 16:
                raise ValueError(f"{color.name} has more than 16 pieces")

            for kind, maximum in MAX_PIECES_BY_KIND.items():
                count = sum(1 for piece in self.board if piece == Piece(color, kind))
                if count > maximum:
                    raise ValueError(f"{color.name} has more than {maximum} {kind.value} pieces")

        for square, piece in enumerate(self.board):
            if piece is None:
                continue
            file, rank = file_rank(square)
            if piece.kind in {PieceType.KING, PieceType.ADVISOR} and not _palace_contains(piece.color, file, rank):
                raise ValueError(f"{piece.color.name} {piece.kind.value} is outside the palace")
            if piece.kind is PieceType.BISHOP and not _bishop_rank_ok(piece.color, rank):
                raise ValueError(f"{piece.color.name} bishop crossed the river")
            if piece.kind is PieceType.PAWN and not _pawn_position_ok(piece.color, file, rank):
                raise ValueError(f"{piece.color.name} pawn is on an impossible square")

        if self.is_square_attacked(self.king_square(self.side_to_move.opponent), self.side_to_move):
            raise ValueError("side to move is already attacking the opponent king")

    def _require_one_king_each(self) -> None:
        for color in (Color.RED, Color.BLACK):
            count = sum(1 for piece in self.board if piece == Piece(color, PieceType.KING))
            if count != 1:
                raise ValueError(f"FEN must contain exactly one {color.name} king, found {count}")

@lru_cache(maxsize=131_072)
def _to_fen_cached(
    board: tuple[Piece | None, ...],
    side_to_move: Color,
    halfmove_clock: int,
    fullmove_number: int,
) -> str:
    ranks: list[str] = []
    for rank in range(BOARD_RANKS - 1, -1, -1):
        empty = 0
        row: list[str] = []
        for file in range(BOARD_FILES):
            piece = board[coords_to_square(file, rank)]
            if piece is None:
                empty += 1
                continue
            if empty:
                row.append(str(empty))
                empty = 0
            row.append(piece.to_fen())
        if empty:
            row.append(str(empty))
        ranks.append("".join(row))
    return (
        f"{'/'.join(ranks)} {side_to_move.value} - - "
        f"{halfmove_clock} {fullmove_number}"
    )


def _pawn_position_ok(color: Color, file: int, rank: int) -> bool:
    if color is Color.RED:
        if rank < 3:
            return False
        return rank >= 5 or file in {0, 2, 4, 6, 8}
    if rank > 6:
        return False
    return rank <= 4 or file in {0, 2, 4, 6, 8}
