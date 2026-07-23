from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from threading import Event
from time import perf_counter

from xiangqi_core import Color, GameState, Move, PieceType, Position
from xiangqi_core.coordinates import BOARD_RANKS, file_rank


_PIECE_VALUES = {
    PieceType.KING: 20_000,
    PieceType.ROOK: 900,
    PieceType.CANNON: 450,
    PieceType.KNIGHT: 400,
    PieceType.BISHOP: 200,
    PieceType.ADVISOR: 200,
    PieceType.PAWN: 100,
}
_CHECK_PENALTY = 50
_MATE_SCORE = 1_000_000
_EXACT_BOUND = 0
_LOWER_BOUND = 1
_UPPER_BOUND = -1


def _terminal_score_for(result: str, perspective: Color, ply: int = 0) -> int:
    if result == "draw":
        return 0
    if result == "red_win":
        winner = Color.RED
    elif result == "black_win":
        winner = Color.BLACK
    else:
        raise ValueError(f"not an adjudicated terminal result: {result!r}")
    return _MATE_SCORE - ply if winner is perspective else -_MATE_SCORE + ply


def _is_adjudicated_terminal(result: str | None) -> bool:
    return result in {"red_win", "black_win", "draw"}


@dataclass(frozen=True, slots=True)
class _TranspositionEntry:
    score: int
    bound: int


class _SearchInterrupted(Exception):
    pass


@dataclass(slots=True)
class LocalSearchMoveOrderer:
    depth: int = 2
    node_limit: int | None = None
    nodes_searched: int = field(init=False, default=0)
    transposition_hits: int = field(init=False, default=0)
    completed_depth: int = field(init=False, default=0)
    _deadline: float | None = field(init=False, default=None, repr=False)
    _stop_event: Event | None = field(init=False, default=None, repr=False)
    _transposition_table: dict[tuple[object, ...], _TranspositionEntry] = field(
        init=False,
        default_factory=dict,
        repr=False,
    )

    def __post_init__(self) -> None:
        if self.depth <= 0:
            raise ValueError("depth must be positive")
        if self.node_limit is not None and self.node_limit <= 0:
            raise ValueError("node_limit must be positive")

    def order_moves(self, position: Position | GameState, moves: Sequence[Move]) -> list[Move]:
        self._begin_search(None)
        return self._order_moves(position, moves)

    def order_moves_with_time_limit(
        self,
        position: Position | GameState,
        moves: Sequence[Move],
        time_limit_ms: int,
    ) -> list[Move]:
        self._begin_search(None)
        if time_limit_ms <= 0:
            return sorted(moves, key=lambda candidate: candidate.to_uci())
        self._deadline = perf_counter() + time_limit_ms / 1000.0
        try:
            return self._order_moves(position, moves)
        finally:
            self._deadline = None

    def order_moves_with_stop_event(
        self,
        position: Position | GameState,
        moves: Sequence[Move],
        stop_event: Event,
        time_limit_ms: int | None = None,
    ) -> list[Move]:
        self._begin_search(None)
        root_moves = sorted(moves, key=lambda candidate: candidate.to_uci())
        if stop_event.is_set():
            return root_moves
        self._stop_event = stop_event
        if time_limit_ms is not None and time_limit_ms > 0:
            self._deadline = perf_counter() + time_limit_ms / 1000.0
        try:
            return self._order_moves(position, root_moves)
        finally:
            self._deadline = None
            self._stop_event = None

    def _order_moves(self, position: Position | GameState, moves: Sequence[Move]) -> list[Move]:
        root_moves = sorted(moves, key=lambda candidate: candidate.to_uci())
        best_order = self._cheap_static_order(position, root_moves)
        for search_depth in range(1, self.depth + 1):
            try:
                candidate_order = self._order_moves_at_depth(
                    position,
                    root_moves,
                    search_depth,
                )
            except _SearchInterrupted:
                break
            if self._deadline is not None and perf_counter() >= self._deadline:
                break
            best_order = candidate_order
            self.completed_depth = search_depth
        return best_order

    def _order_moves_at_depth(
        self,
        position: Position | GameState,
        root_moves: Sequence[Move],
        search_depth: int,
    ) -> list[Move]:
        scores: dict[str, int] = {}
        for move in root_moves:
            self._check_search_limits()
            scores[move.to_uci()] = self._root_static_score(
                position.make_move(move, validate=False),
                move.to_square,
            )
        if search_depth > 1:
            for move in sorted(
                root_moves,
                key=lambda candidate: (-scores[candidate.to_uci()], candidate.to_uci()),
            ):
                self._check_search_limits()
                scores[move.to_uci()] = -self._negamax(
                    position.make_move(move, validate=False),
                    search_depth - 1,
                    -_MATE_SCORE,
                    _MATE_SCORE,
                    1,
                )
        scored_moves = [(scores[move.to_uci()], move) for move in root_moves]
        return [
            move
            for _score, move in sorted(
                scored_moves,
                key=lambda item: (-item[0], item[1].to_uci()),
            )
        ]

    def _cheap_static_order(
        self,
        position: Position | GameState,
        root_moves: Sequence[Move],
    ) -> list[Move]:
        board_position = _base_position(position)
        scores: dict[str, int] = {}
        for move in root_moves:
            child = position.make_move(move, validate=False)
            scores[move.to_uci()] = (
                self._root_static_score(child, move.to_square)
                if board_position.piece_at(move.to_square) is not None
                else -self._evaluate(child)
            )
        return [
            move
            for _score, move in sorted(
                ((scores[move.to_uci()], move) for move in root_moves),
                key=lambda item: (-item[0], item[1].to_uci()),
            )
        ]

    def _negamax(
        self,
        position: Position | GameState,
        depth: int,
        alpha: int,
        beta: int,
        ply: int,
    ) -> int:
        self._check_search_limits()
        key = self._transposition_key(position, depth, ply)
        entry = self._transposition_table.get(key)
        if entry is not None:
            self.transposition_hits += 1
            if entry.bound == _EXACT_BOUND:
                return entry.score
            if entry.bound == _LOWER_BOUND:
                alpha = max(alpha, entry.score)
            else:
                beta = min(beta, entry.score)
            if alpha >= beta:
                return entry.score

        alpha_start = alpha
        beta_start = beta
        self.nodes_searched += 1

        legal_moves = position.legal_moves()
        result = position.game_result(legal_moves)
        if _is_adjudicated_terminal(result):
            best_score = _terminal_score_for(result, position.side_to_move, ply)
        elif depth == 0:
            best_score = self._evaluate(position)
        else:
            best_score = -_MATE_SCORE
            for move in sorted(
                legal_moves,
                key=lambda candidate: (
                    _base_position(position).piece_at(candidate.to_square) is None,
                    candidate.to_uci(),
                ),
            ):
                score = -self._negamax(
                    position.make_move(move, validate=False),
                    depth - 1,
                    -beta,
                    -alpha,
                    ply + 1,
                )
                best_score = max(best_score, score)
                alpha = max(alpha, score)
                if alpha >= beta:
                    break

        if best_score <= alpha_start:
            bound = _UPPER_BOUND
        elif best_score >= beta_start:
            bound = _LOWER_BOUND
        else:
            bound = _EXACT_BOUND
        self._transposition_table[key] = _TranspositionEntry(best_score, bound)
        return best_score

    def _begin_search(self, deadline: float | None) -> None:
        self.nodes_searched = 0
        self.transposition_hits = 0
        self.completed_depth = 0
        self._deadline = deadline
        self._stop_event = None
        self._transposition_table.clear()

    def _check_search_limits(self) -> None:
        if self._stop_event is not None and self._stop_event.is_set():
            raise _SearchInterrupted
        if self._deadline is not None and perf_counter() >= self._deadline:
            raise _SearchInterrupted
        if self.node_limit is not None and self.nodes_searched >= self.node_limit:
            raise _SearchInterrupted

    @staticmethod
    def _transposition_key(
        position: Position | GameState,
        depth: int,
        ply: int,
    ) -> tuple[object, ...]:
        board_position = _base_position(position)
        history_context = (
            (position.history_keys, position.rule_events, position.defer_rule_details)
            if isinstance(position, GameState)
            else None
        )
        return (
            board_position.board,
            board_position.side_to_move,
            depth,
            ply,
            history_context,
        )

    @staticmethod
    def _evaluate(position: Position | GameState) -> int:
        board_position = _base_position(position)
        score = 0
        for square, piece in enumerate(board_position.board):
            if piece is None:
                continue
            value = _PIECE_VALUES[piece.kind] + _piece_square_bonus(
                piece,
                square,
            )
            score += value if piece.color is position.side_to_move else -value
        if board_position.is_in_check(position.side_to_move):
            score -= _CHECK_PENALTY
        return score

    @classmethod
    def _root_static_score(cls, child: Position | GameState, root_to_square: str) -> int:
        root_side = child.side_to_move.opponent
        result = child.game_result()
        if _is_adjudicated_terminal(result):
            return _terminal_score_for(result, root_side)
        score = -cls._evaluate(child)
        child_position = _base_position(child)
        for reply in child.legal_moves():
            if child_position.piece_at(reply.to_square) is None:
                continue
            capturing_piece = child_position.piece_at(reply.from_square)
            replied = child.make_move(reply, validate=False)
            terminal = replied.game_result()
            reply_score = (
                _terminal_score_for(terminal, root_side)
                if _is_adjudicated_terminal(terminal)
                else cls._evaluate(replied)
            )
            if capturing_piece is not None and capturing_piece.kind in {
                PieceType.ROOK,
                PieceType.CANNON,
            }:
                replied_position = _base_position(replied)
                for source in replied_position.defenders_to(
                    reply.to_square,
                    root_side,
                    legal=True,
                ):
                    recaptured = replied.make_move(
                        Move(source, reply.to_square),
                        validate=False,
                    )
                    terminal = recaptured.game_result()
                    recapture_score = (
                        _terminal_score_for(terminal, root_side)
                        if _is_adjudicated_terminal(terminal)
                        else -cls._evaluate(recaptured)
                    )
                    reply_score = max(reply_score, recapture_score)
            score = min(score, reply_score)
        return score


def _base_position(position: Position | GameState) -> Position:
    return position.position if isinstance(position, GameState) else position


def _piece_square_bonus(piece, square: int) -> int:
    file, rank = file_rank(square)
    forward_rank = rank if piece.color.value == "w" else BOARD_RANKS - 1 - rank
    center_file = 4 - abs(file - 4)

    if piece.kind is PieceType.PAWN:
        crossed = forward_rank >= 5
        return forward_rank * (8 + center_file * 4) + center_file * 2 + (25 if crossed else 0)
    if piece.kind in {PieceType.KNIGHT, PieceType.CANNON}:
        center_rank = 4 - abs(forward_rank - 5)
        development = 6 if forward_rank > 0 else 0
        return center_file * 4 + center_rank * 2 + development
    if piece.kind is PieceType.ROOK:
        return center_file * 2 + forward_rank
    return 0
