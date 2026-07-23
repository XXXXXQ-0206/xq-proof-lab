from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from xiangqi_core import Color, Move, Piece, PieceType, Position
from xiangqi_core.coordinates import BOARD_RANKS, file_rank

from .chessdb import ChessDbClient, ChessDbResponse, ChessDbStatus
from .uci_engine import UciEngine, UciEngineError, extract_pv_moves


class LexicographicMoveOrderer:
    def order_moves(self, position: Position, moves: Sequence[Move]) -> list[Move]:
        return sorted(moves, key=lambda move: move.to_uci())


def parse_uci_options(values: Sequence[str] | None) -> tuple[tuple[str, str], ...]:
    options: list[tuple[str, str]] = []
    for raw_value in values or ():
        name, separator, value = str(raw_value).partition("=")
        if not separator or not name.strip() or not value.strip():
            raise ValueError(f"UCI option must use NAME=VALUE syntax: {raw_value!r}")
        options.append((name.strip(), value.strip()))
    return tuple(options)


class HeuristicMoveOrderer:
    def order_moves(self, position: Position, moves: Sequence[Move]) -> list[Move]:
        fallback_moves = LexicographicMoveOrderer().order_moves(position, moves)
        return sorted(
            fallback_moves,
            key=lambda move: (-_heuristic_move_score(position, move), move.to_uci()),
        )


@dataclass(slots=True)
class CachedMoveOrderer:
    orderer: object
    max_entries: int = 4096
    _cache: OrderedDict[
        tuple[str, str, str, tuple[str, ...], str, int | None], tuple[str, ...]
    ] = field(default_factory=OrderedDict)

    def order_moves(self, position: Position, moves: Sequence[Move]) -> list[Move]:
        return self._order_moves(position, moves, time_context="fixed", time_limit_ms=None)

    def order_moves_with_time_limit(
        self,
        position: Position,
        moves: Sequence[Move],
        time_limit_ms: int,
    ) -> list[Move]:
        context = "expired" if time_limit_ms <= 0 else "timed"
        return self._order_moves(position, moves, time_context=context, time_limit_ms=time_limit_ms)

    def bestmove_with_go_command(
        self,
        position: Position,
        moves: Sequence[Move],
        go_command: str,
    ) -> Move | None:
        bestmove = getattr(self.orderer, "bestmove_with_go_command", None)
        if not callable(bestmove):
            return None
        return bestmove(position, moves, go_command)

    def _order_moves(
        self,
        position: Position,
        moves: Sequence[Move],
        time_context: str,
        time_limit_ms: int | None,
    ) -> list[Move]:
        fallback_moves = LexicographicMoveOrderer().order_moves(position, moves)
        legal_by_uci = {move.to_uci(): move for move in fallback_moves}
        if not legal_by_uci:
            return []

        cache_time_limit = None if time_context == "fixed" else time_limit_ms
        key = (*_cache_key(position, tuple(legal_by_uci)), time_context, cache_time_limit)
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return _moves_from_cached_order(cached, fallback_moves, legal_by_uci)

        ordered_moves = _order_moves_with_optional_time_limit(
            self.orderer,
            position,
            moves,
            time_limit_ms,
        )
        ordered_uci = tuple(
            move.to_uci() for move in ordered_moves if move.to_uci() in legal_by_uci
        )
        self._cache[key] = ordered_uci
        self._cache.move_to_end(key)
        while len(self._cache) > self.max_entries:
            self._cache.popitem(last=False)
        return _moves_from_cached_order(ordered_uci, fallback_moves, legal_by_uci)

    def new_game(self) -> None:
        self._cache.clear()
        new_game = getattr(self.orderer, "new_game", None)
        if callable(new_game):
            new_game()

    def clear_cache(self) -> None:
        self._cache.clear()
        clear_cache = getattr(self.orderer, "clear_cache", None)
        if callable(clear_cache):
            clear_cache()

    def set_option(self, name: str, value: str | None = None) -> None:
        self._cache.clear()
        set_option = getattr(self.orderer, "set_option", None)
        if callable(set_option):
            set_option(name, value)

    def uci_options(self) -> tuple[str, ...]:
        uci_options = getattr(self.orderer, "uci_options", None)
        if callable(uci_options):
            return tuple(uci_options())
        return ()

    def close(self) -> None:
        self._cache.clear()
        close = getattr(self.orderer, "close", None)
        if callable(close):
            close()

    @property
    def uses_external_engine(self) -> bool:
        return bool(getattr(self.orderer, "uses_external_engine", False))


@dataclass(slots=True)
class ChessDbMoveOrderer:
    client: ChessDbClient
    egtbmetric: str | None = None
    ban: tuple[str, ...] = ()
    fallback: LexicographicMoveOrderer = LexicographicMoveOrderer()

    def order_moves(self, position: Position, moves: Sequence[Move]) -> list[Move]:
        fallback_moves = self.fallback.order_moves(position, moves)
        legal_by_uci = {move.to_uci(): move for move in fallback_moves}
        if not legal_by_uci:
            return []

        recommended: list[str] = []
        try:
            all_response = self.client.query_all(
                position.to_fen(),
                egtbmetric=self.egtbmetric,
                ban=self.ban,
            )
            recommended.extend(_moves_from_response(all_response))
            if not recommended:
                best_response = self.client.query_best(
                    position.to_fen(),
                    egtbmetric=self.egtbmetric,
                    ban=self.ban,
                )
                recommended.extend(_moves_from_response(best_response))
        except Exception:
            return fallback_moves

        seen: set[str] = set()
        ordered: list[Move] = []
        for move_text in recommended:
            if move_text in legal_by_uci and move_text not in seen:
                ordered.append(legal_by_uci[move_text])
                seen.add(move_text)

        ordered.extend(move for move in fallback_moves if move.to_uci() not in seen)
        return ordered


@dataclass(slots=True)
class UciBestMoveOrderer:
    command: Sequence[str]
    depth: int = 4
    timeout: float = 5.0
    multipv: int = 1
    fallback: LexicographicMoveOrderer = LexicographicMoveOrderer()
    options: tuple[tuple[str, str | None], ...] = ()

    @property
    def uses_external_engine(self) -> bool:
        return True

    def order_moves(self, position: Position, moves: Sequence[Move]) -> list[Move]:
        return self._order_moves(position, moves, time_limit_ms=None)

    def order_moves_with_time_limit(
        self,
        position: Position,
        moves: Sequence[Move],
        time_limit_ms: int,
    ) -> list[Move]:
        return self._order_moves(position, moves, time_limit_ms=time_limit_ms)

    def _order_moves(
        self,
        position: Position,
        moves: Sequence[Move],
        time_limit_ms: int | None,
    ) -> list[Move]:
        if self.multipv <= 0:
            raise ValueError("multipv must be positive")
        fallback_moves = self.fallback.order_moves(position, moves)
        legal_by_uci = {move.to_uci(): move for move in fallback_moves}
        if not legal_by_uci:
            return []
        if time_limit_ms is not None and time_limit_ms <= 0:
            return fallback_moves

        try:
            with UciEngine(
                self.command,
                timeout=_uci_timeout_seconds(self.timeout, time_limit_ms),
            ) as engine:
                engine.initialize()
                _configure_uci_engine(engine, self.multipv, self.options)
                _set_uci_position(engine, position)
                bestmove, lines = engine.go(
                    _uci_go_command(
                        self.depth,
                        time_limit_ms,
                        _searchmoves_for_subset(position, fallback_moves),
                    )
                )
        except (OSError, UciEngineError, ValueError):
            return fallback_moves

        ordered = _order_from_external_moves(
            _moves_from_uci_search(bestmove, lines),
            fallback_moves,
            legal_by_uci,
            position,
        )
        if ordered == fallback_moves:
            return fallback_moves
        return ordered

    def clear_cache(self) -> None:
        return None

    def set_option(self, name: str, value: str | None = None) -> None:
        self._record_runtime_option(name, value)

    def uci_options(self) -> tuple[str, ...]:
        return _common_fallback_uci_options(self.options, self.multipv)

    def _record_runtime_option(self, name: str, value: str | None) -> None:
        normalized = _normalized_option_name(name)
        if normalized == "multipv":
            if value is None:
                raise ValueError("MultiPV requires a value")
            multipv = int(value)
            if multipv <= 0:
                raise ValueError("MultiPV must be positive")
            self.multipv = multipv
            return
        self.options = _upsert_uci_option(self.options, name, value)


@dataclass(slots=True)
class PersistentUciBestMoveOrderer:
    command: Sequence[str]
    depth: int = 4
    timeout: float = 5.0
    multipv: int = 1
    fallback: LexicographicMoveOrderer = LexicographicMoveOrderer()
    options: tuple[tuple[str, str | None], ...] = ()

    @property
    def uses_external_engine(self) -> bool:
        return True
    _engine: UciEngine | None = None

    def __enter__(self) -> "PersistentUciBestMoveOrderer":
        self._ensure_engine()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        if self._engine is None:
            return
        self._engine.close()
        self._engine = None

    def new_game(self) -> None:
        try:
            self._ensure_engine().new_game()
        except (OSError, UciEngineError, ValueError):
            self.close()

    def clear_cache(self) -> None:
        try:
            engine = self._ensure_engine()
            engine.set_option("Clear Hash")
            engine.wait_ready()
        except (OSError, UciEngineError, ValueError):
            self.close()

    def set_option(self, name: str, value: str | None = None) -> None:
        self._record_runtime_option(name, value)
        try:
            engine = self._ensure_engine()
            engine.set_option(name, value)
            engine.wait_ready()
        except (OSError, UciEngineError, ValueError):
            self.close()

    def uci_options(self) -> tuple[str, ...]:
        return _common_fallback_uci_options(self.options, self.multipv)

    def _record_runtime_option(self, name: str, value: str | None) -> None:
        normalized = _normalized_option_name(name)
        if normalized == "multipv":
            if value is None:
                raise ValueError("MultiPV requires a value")
            multipv = int(value)
            if multipv <= 0:
                raise ValueError("MultiPV must be positive")
            self.multipv = multipv
            return
        self.options = _upsert_uci_option(self.options, name, value)

    def order_moves(self, position: Position, moves: Sequence[Move]) -> list[Move]:
        return self._order_moves(position, moves, time_limit_ms=None)

    def order_moves_with_time_limit(
        self,
        position: Position,
        moves: Sequence[Move],
        time_limit_ms: int,
    ) -> list[Move]:
        return self._order_moves(position, moves, time_limit_ms=time_limit_ms)

    def bestmove_with_go_command(
        self,
        position: Position,
        moves: Sequence[Move],
        go_command: str,
    ) -> Move | None:
        legal_by_uci = {move.to_uci(): move for move in moves}
        if not legal_by_uci or not go_command.startswith("go"):
            return None
        try:
            engine = self._ensure_engine()
            _set_uci_position(engine, position)
            bestmove, _lines = engine.go(go_command)
        except (OSError, UciEngineError, ValueError):
            self.close()
            return None
        return legal_by_uci.get(bestmove)

    def _order_moves(
        self,
        position: Position,
        moves: Sequence[Move],
        time_limit_ms: int | None,
    ) -> list[Move]:
        if self.multipv <= 0:
            raise ValueError("multipv must be positive")
        fallback_moves = self.fallback.order_moves(position, moves)
        legal_by_uci = {move.to_uci(): move for move in fallback_moves}
        if not legal_by_uci:
            return []
        if time_limit_ms is not None and time_limit_ms <= 0:
            return fallback_moves

        try:
            engine = self._ensure_engine()
            _set_uci_position(engine, position)
            previous_timeout = engine.timeout
            engine.timeout = _uci_timeout_seconds(self.timeout, time_limit_ms)
            try:
                bestmove, lines = engine.go(
                    _uci_go_command(
                        self.depth,
                        time_limit_ms,
                        _searchmoves_for_subset(position, fallback_moves),
                    )
                )
            finally:
                engine.timeout = previous_timeout
        except (OSError, UciEngineError, ValueError):
            self.close()
            return fallback_moves

        ordered = _order_from_external_moves(
            _moves_from_uci_search(bestmove, lines),
            fallback_moves,
            legal_by_uci,
            position,
        )
        if ordered == fallback_moves:
            return fallback_moves
        return ordered

    def _ensure_engine(self) -> UciEngine:
        if self._engine is None:
            engine = UciEngine(self.command, timeout=self.timeout)
            try:
                engine.initialize()
                _configure_uci_engine(engine, self.multipv, self.options)
            except Exception:
                engine.close()
                raise
            self._engine = engine
        return self._engine


def _moves_from_response(response: ChessDbResponse) -> list[str]:
    if response.status is not ChessDbStatus.OK:
        return []
    if response.moves:
        return [move.move for move in response.moves]
    if response.best_move:
        return [response.best_move]
    if response.pv:
        return [response.pv[0]]
    return []


def _order_moves_with_optional_time_limit(
    orderer: object,
    position: Position,
    moves: Sequence[Move],
    time_limit_ms: int | None,
) -> list[Move]:
    timed_order = getattr(orderer, "order_moves_with_time_limit", None)
    if time_limit_ms is not None and callable(timed_order):
        return timed_order(position, moves, time_limit_ms)
    return orderer.order_moves(position, moves)  # type: ignore[attr-defined]


def _uci_go_command(
    depth: int,
    time_limit_ms: int | None,
    searchmoves: Sequence[str] = (),
) -> str:
    command = "go"
    if searchmoves:
        command += " searchmoves " + " ".join(searchmoves)
    command += f" depth {depth}"
    if time_limit_ms is not None:
        command += f" movetime {time_limit_ms}"
    return command


def _searchmoves_for_subset(position: Position, moves: Sequence[Move]) -> tuple[str, ...]:
    try:
        full_legal = {move.to_uci() for move in position.legal_moves()}
    except Exception:
        return ()
    selected = tuple(move.to_uci() for move in moves)
    selected_set = set(selected)
    if not selected_set or selected_set == full_legal:
        return ()
    if not selected_set.issubset(full_legal):
        return ()
    return selected


def _uci_timeout_seconds(default_timeout: float, time_limit_ms: int | None) -> float:
    if time_limit_ms is None:
        return default_timeout
    return min(
        default_timeout,
        max(_MIN_TIMED_UCI_TIMEOUT_SECONDS, time_limit_ms / 1000.0 + 0.05),
    )


def _moves_from_uci_search(bestmove: str, lines: Sequence[str]) -> tuple[str, ...]:
    moves: list[str] = []
    seen: set[str] = set()
    for move in (bestmove, *extract_pv_moves(lines)):
        if move not in seen:
            moves.append(move)
            seen.add(move)
    return tuple(moves)


def _order_from_external_moves(
    recommended: Sequence[str],
    fallback_moves: list[Move],
    legal_by_uci: dict[str, Move],
    position: Position | None = None,
) -> list[Move]:
    seen: set[str] = set()
    ordered: list[Move] = []
    safety: dict[str, bool] = {}

    def is_safe(move: Move) -> bool:
        if position is None:
            return True
        uci = move.to_uci()
        if uci not in safety:
            safety[uci] = not _move_allows_opponent_immediate_win(position, move)
        return safety[uci]

    try:
        for move_text in recommended:
            move = legal_by_uci.get(move_text)
            if move is not None and move_text not in seen and is_safe(move):
                _append_unseen_move(ordered, seen, move)
        if not ordered:
            for move in fallback_moves:
                if is_safe(move):
                    _append_unseen_move(ordered, seen, move)
                    break
    except Exception:
        ordered.clear()
        seen.clear()
    _append_unseen_moves(ordered, seen, fallback_moves)
    return ordered


def _safe_moves_first(fallback_moves: list[Move], unsafe_uci: set[str]) -> list[Move]:
    if not unsafe_uci:
        return fallback_moves
    safe = [move for move in fallback_moves if move.to_uci() not in unsafe_uci]
    unsafe = [move for move in fallback_moves if move.to_uci() in unsafe_uci]
    return safe + unsafe


def _append_unseen_moves(
    ordered: list[Move],
    seen: set[str],
    moves: Iterable[Move],
) -> None:
    for move in moves:
        _append_unseen_move(ordered, seen, move)


def _append_unseen_move(ordered: list[Move], seen: set[str], move: Move) -> None:
    uci = move.to_uci()
    if uci in seen:
        return
    ordered.append(move)
    seen.add(uci)


def _cache_key(
    position: Position,
    legal_moves: tuple[str, ...],
) -> tuple[str, str, str, tuple[str, ...]]:
    history_signature = getattr(position, "history_signature", None)
    position_command = getattr(position, "to_uci_position", None)
    return (
        position.to_fen(),
        history_signature() if callable(history_signature) else "",
        position_command() if callable(position_command) else "",
        tuple(sorted(legal_moves)),
    )


def _moves_from_cached_order(
    ordered_uci: tuple[str, ...],
    fallback_moves: list[Move],
    legal_by_uci: dict[str, Move],
) -> list[Move]:
    seen: set[str] = set()
    ordered: list[Move] = []
    for move_text in ordered_uci:
        if move_text in legal_by_uci and move_text not in seen:
            ordered.append(legal_by_uci[move_text])
            seen.add(move_text)
    ordered.extend(move for move in fallback_moves if move.to_uci() not in seen)
    return ordered


def _set_uci_position(engine: UciEngine, position: Position) -> None:
    initial_fen = getattr(position, "initial_fen", None)
    history_moves = getattr(position, "moves", None)
    if initial_fen is not None and history_moves is not None:
        engine.set_position(
            str(initial_fen),
            tuple(move.to_uci() for move in history_moves),
        )
        return
    engine.set_position(position.to_fen())


def _configure_uci_engine(
    engine: UciEngine,
    multipv: int,
    options: Sequence[tuple[str, str | None]],
) -> None:
    for name, value in options:
        engine.set_option(name, value)
    if multipv > 1:
        engine.set_option("MultiPV", multipv)
    if not options and multipv <= 1:
        return
    engine.wait_ready()


def _common_fallback_uci_options(
    options: Sequence[tuple[str, str | None]],
    multipv: int,
) -> tuple[str, ...]:
    configured = {
        _normalized_option_name(name): value
        for name, value in options
        if value is not None
    }
    hash_default = _positive_int_option(configured.get("hash"), default=16)
    threads_default = _positive_int_option(configured.get("threads"), default=1)
    multipv_default = max(1, multipv)
    return (
        f"option name Hash type spin default {hash_default} min 1 max 1048576",
        f"option name Threads type spin default {threads_default} min 1 max 1024",
        f"option name MultiPV type spin default {multipv_default} min 1 max 256",
    )


def _normalized_option_name(name: str) -> str:
    return name.replace(" ", "").lower()


def _upsert_uci_option(
    options: Sequence[tuple[str, str | None]],
    name: str,
    value: str | None,
) -> tuple[tuple[str, str | None], ...]:
    normalized = _normalized_option_name(name)
    updated: list[tuple[str, str | None]] = []
    replaced = False
    for option_name, option_value in options:
        if _normalized_option_name(option_name) == normalized:
            updated.append((name, value))
            replaced = True
        else:
            updated.append((option_name, option_value))
    if not replaced:
        updated.append((name, value))
    return tuple(updated)


def _positive_int_option(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


_PIECE_VALUES = {
    PieceType.KING: 20_000,
    PieceType.ROOK: 900,
    PieceType.CANNON: 450,
    PieceType.KNIGHT: 400,
    PieceType.BISHOP: 200,
    PieceType.ADVISOR: 200,
    PieceType.PAWN: 100,
}

_IMMEDIATE_WIN_SCORE = 1_000_000
_OPPONENT_IMMEDIATE_WIN_PENALTY = 750_000
_FULL_IMMEDIATE_WIN_SCAN_LIMIT = 12
_MIN_TIMED_UCI_TIMEOUT_SECONDS = 0.05


def _heuristic_move_score(position: Position, move: Move) -> int:
    board_position = _board_position(position)
    piece = board_position.piece_at(move.from_square)
    if piece is None:
        return -1_000_000

    captured = board_position.piece_at(move.to_square)
    after = position.make_move(move, validate=False)
    after_board = _board_position(after)
    opponent = piece.color.opponent
    score = _piece_square_bonus(piece, move.to_square) - _piece_square_bonus(
        piece, move.from_square
    )

    result = after.game_result()
    if result == _win_result(piece.color):
        score += _IMMEDIATE_WIN_SCORE

    if captured is not None:
        score += 50_000 + _piece_value(captured) * 16 - _piece_value(piece)

    if after_board.is_in_check(opponent):
        score += 10_000

    if result is None and _opponent_has_immediate_win(after, opponent):
        score -= _OPPONENT_IMMEDIATE_WIN_PENALTY

    if after_board.attackers_to(move.to_square, opponent, legal=False):
        defenders = after_board.defenders_to(move.to_square, piece.color, legal=False)
        penalty = _piece_value(piece)
        score -= penalty // 2 if defenders else penalty

    return score


def _opponent_has_immediate_win(position, opponent: Color) -> bool:
    board_position = _board_position(position)
    if board_position.side_to_move is not opponent:
        return _opponent_has_immediate_win_slow(position, opponent)
    replies = board_position.legal_moves()
    full_scan = len(replies) <= _FULL_IMMEDIATE_WIN_SCAN_LIMIT
    defender = opponent.opponent
    for reply in replies:
        after_reply = board_position.make_move(reply, validate=False)
        gives_check = after_reply.is_in_check(defender)
        if not gives_check and not full_scan:
            continue
        if not after_reply.legal_moves():
            return True
    return False


def _opponent_has_immediate_win_slow(position, opponent: Color) -> bool:
    win_result = _win_result(opponent)
    for reply in position.legal_moves():
        if position.make_move(reply, validate=False).game_result() == win_result:
            return True
    return False


def _moves_allowing_opponent_immediate_win(
    position: Position | None,
    moves: Sequence[Move],
) -> set[str]:
    if position is None:
        return set()
    unsafe: set[str] = set()
    safe_exists = False
    try:
        for move in moves:
            if _move_allows_opponent_immediate_win(position, move):
                unsafe.add(move.to_uci())
            else:
                safe_exists = True
    except Exception:
        return set()
    return unsafe if safe_exists else set()


def _move_allows_opponent_immediate_win(position: Position, move: Move) -> bool:
    board_position = _board_position(position)
    piece = board_position.piece_at(move.from_square)
    if piece is None:
        return False
    after = position.make_move(move, validate=False)
    if after.game_result() is not None:
        return False
    return _opponent_has_immediate_win(after, piece.color.opponent)


def _piece_value(piece: Piece) -> int:
    return _PIECE_VALUES[piece.kind]


def _board_position(position) -> Position:
    return getattr(position, "position", position)


def _win_result(color: Color) -> str:
    return "red_win" if color is Color.RED else "black_win"


def _piece_square_bonus(piece: Piece, square: int) -> int:
    file, rank = file_rank(square)
    forward_rank = rank if piece.color is Color.RED else BOARD_RANKS - 1 - rank
    center_file = 4 - abs(file - 4)

    if piece.kind is PieceType.PAWN:
        crossed = forward_rank >= 5
        return forward_rank * 8 + center_file * 2 + (25 if crossed else 0)
    if piece.kind in {PieceType.KNIGHT, PieceType.CANNON}:
        center_rank = 4 - abs(forward_rank - 5)
        return center_file * 4 + center_rank * 2
    if piece.kind is PieceType.ROOK:
        return center_file * 2 + forward_rank
    return 0
