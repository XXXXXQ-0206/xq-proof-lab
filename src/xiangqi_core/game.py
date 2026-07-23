from __future__ import annotations

from dataclasses import dataclass, replace

from .coordinates import file_rank, square_name
from .model import Color, Piece, PieceType
from .move import Move
from .position import Position
from .zobrist import Zobrist


@dataclass(frozen=True, slots=True)
class AttackedPieceInfo:
    square: str
    color: Color
    kind: PieceType
    protected_by_owner: bool
    defenders: tuple[str, ...] = ()
    attackers: tuple[str, ...] = ()
    legal_chase_attackers: tuple[str, ...] = ()
    recapture_defenders: tuple[str, ...] = ()
    recapturable_by_owner: bool = False
    chase_relevant_attackers: tuple[str, ...] = ()
    chase_relevant: bool = False


@dataclass(frozen=True, slots=True)
class RuleEvent:
    key: str
    move: str | None = None
    moved_color: Color | None = None
    captured: bool = False
    gives_check: bool = False
    attacked_opponent_pieces: tuple[str, ...] = ()
    attacked_opponent_details: tuple[AttackedPieceInfo, ...] = ()

    def signature(self) -> str:
        moved = self.moved_color.value if self.moved_color is not None else "-"
        return (
            f"{self.key}:{self.move or '-'}:{moved}:"
            f"{int(self.captured)}:{int(self.gives_check)}"
        )


@dataclass(frozen=True, slots=True)
class RepetitionInfo:
    count: int
    cycle_plies: int
    cycle_start_ply: int
    cycle_end_ply: int
    cycle_moves: tuple[str, ...]
    has_capture: bool
    checking_sides: tuple[Color, ...]
    attack_patterns: tuple["CycleAttackInfo", ...] = ()
    possible_chasing_sides: tuple[Color, ...] = ()
    perpetual_chasing_sides: tuple[Color, ...] = ()


@dataclass(frozen=True, slots=True)
class RuleJudgement:
    result: str | None
    reason: str
    adjudicated: bool = False
    repetition: RepetitionInfo | None = None


@dataclass(frozen=True, slots=True)
class CycleAttackInfo:
    color: Color
    attacks_every_move: bool
    moves: tuple[str, ...] = ()
    common_attacked_squares: tuple[str, ...] = ()
    common_targets: tuple["RepeatedAttackTarget", ...] = ()


@dataclass(frozen=True, slots=True)
class RepeatedAttackTarget:
    square: str
    color: Color
    kind: PieceType
    always_protected_by_owner: bool
    ever_unprotected: bool
    always_recapturable_by_owner: bool = False
    ever_recapturable_by_owner: bool = False
    always_chase_relevant: bool = False


@dataclass(frozen=True, slots=True)
class GameState:
    position: Position
    history_keys: tuple[str, ...]
    moves: tuple[Move, ...] = ()
    initial_fen: str = Position.START_FEN
    rule_events: tuple[RuleEvent, ...] = ()
    defer_rule_details: bool = False

    @classmethod
    def from_position(cls, position: Position) -> "GameState":
        key = _repetition_key(position)
        return cls(
            position=position,
            history_keys=(key,),
            initial_fen=position.to_fen(),
            rule_events=(RuleEvent(key=key),),
        )

    @classmethod
    def from_uci_position(cls, command: str) -> "GameState":
        tokens = command.strip().split()
        if not tokens or tokens[0] != "position":
            raise ValueError("UCI position command must start with 'position'")
        if len(tokens) < 2:
            raise ValueError("UCI position command is missing position kind")

        cursor = 1
        if tokens[cursor] == "startpos":
            position = Position.start()
            initial_fen = Position.START_FEN
            cursor += 1
        elif tokens[cursor] == "fen":
            fen_start = cursor + 1
            fen_end = fen_start + 6
            if len(tokens) < fen_end:
                raise ValueError("position fen command must include a six-field FEN")
            initial_fen = " ".join(tokens[fen_start:fen_end])
            position = Position.from_fen(initial_fen)
            cursor = fen_end
        else:
            raise ValueError(f"unsupported UCI position kind: {tokens[cursor]!r}")

        state = cls.from_position(position)
        state = cls(
            position=state.position,
            history_keys=state.history_keys,
            moves=state.moves,
            initial_fen=initial_fen,
            rule_events=state.rule_events,
        )

        if cursor == len(tokens):
            return state
        if tokens[cursor] != "moves":
            raise ValueError(f"unexpected token after position: {tokens[cursor]!r}")

        for move_text in tokens[cursor + 1 :]:
            state = state.make_move(Move.from_uci(move_text))
        return state

    @property
    def side_to_move(self):
        return self.position.side_to_move

    def to_fen(self) -> str:
        return self.position.to_fen()

    def to_uci_position(self) -> str:
        if self.initial_fen == Position.START_FEN:
            command = "position startpos"
        else:
            command = f"position fen {self.initial_fen}"
        if self.moves:
            command += " moves " + " ".join(move.to_uci() for move in self.moves)
        return command

    def legal_moves(self):
        return self.position.legal_moves()

    def for_search(self) -> "GameState":
        return replace(self, defer_rule_details=True)

    def make_move(self, move: Move, validate: bool = True) -> "GameState":
        moving = self.position.side_to_move
        captured = self.position.piece_at(move.to_square) is not None
        next_position = self.position.make_move(move, validate=validate)
        gives_check = next_position.is_in_check(moving.opponent)
        key = _repetition_key(next_position)
        attacked_details = (
            ()
            if self.defer_rule_details
            else _attacked_opponent_details(next_position, moving)
        )
        return GameState(
            position=next_position,
            history_keys=self.history_keys + (key,),
            moves=self.moves + (move,),
            initial_fen=self.initial_fen,
            rule_events=self.rule_events
            + (
                RuleEvent(
                    key=key,
                    move=move.to_uci(),
                    moved_color=moving,
                    captured=captured,
                    gives_check=gives_check,
                    attacked_opponent_pieces=tuple(
                        detail.square for detail in attacked_details
                    ),
                    attacked_opponent_details=attacked_details,
                ),
            ),
            defer_rule_details=self.defer_rule_details,
        )

    def game_result(self, legal_moves=None) -> str | None:
        return self.rule_judgement(legal_moves=legal_moves).result

    def candidate_rule_judgement(self, move: Move) -> RuleJudgement:
        repetition = self.repetition_info()
        if (
            repetition is not None
            and repetition.cycle_moves
            and len(repetition.perpetual_chasing_sides) == 1
            and repetition.perpetual_chasing_sides[0] is self.position.side_to_move
            and move.to_uci() == repetition.cycle_moves[0]
        ):
            return self.rule_judgement()
        return self.make_move(move).rule_judgement()

    def rule_judgement(self, legal_moves=None) -> RuleJudgement:
        if self.defer_rule_details and self.repetition_count() >= 3:
            return self._materialize_rule_details().rule_judgement(legal_moves=legal_moves)
        result = self.position.game_result(legal_moves=legal_moves)
        if result is not None:
            return RuleJudgement(result=result, reason="no_legal_moves", adjudicated=True)
        if self.repetition_count() < 3:
            return RuleJudgement(result=None, reason="no_repetition")

        repetition = self.repetition_info()
        if repetition is None or repetition.count < 3:
            return RuleJudgement(
                result="unknown_rule_state",
                reason="repetition_metadata_missing",
                repetition=repetition,
            )
        if len(repetition.checking_sides) == 1:
            winner = repetition.checking_sides[0].opponent
            return RuleJudgement(
                result="red_win" if winner is Color.RED else "black_win",
                reason="perpetual_check_loss",
                adjudicated=True,
                repetition=repetition,
            )
        if len(repetition.checking_sides) == 2:
            return RuleJudgement(
                result="draw",
                reason="mutual_perpetual_check_draw",
                adjudicated=True,
                repetition=repetition,
            )
        if len(repetition.perpetual_chasing_sides) == 1:
            winner = repetition.perpetual_chasing_sides[0].opponent
            return RuleJudgement(
                result="red_win" if winner is Color.RED else "black_win",
                reason="perpetual_chase_loss",
                adjudicated=True,
                repetition=repetition,
            )
        if len(repetition.perpetual_chasing_sides) == 2:
            return RuleJudgement(
                result="draw",
                reason="mutual_perpetual_chase_draw",
                adjudicated=True,
                repetition=repetition,
            )
        if repetition.possible_chasing_sides:
            return RuleJudgement(
                result="unknown_rule_state",
                reason="possible_chase_unadjudicated",
                repetition=repetition,
            )
        return RuleJudgement(
            result="draw",
            reason="repetition_draw",
            adjudicated=True,
            repetition=repetition,
        )

    def repetition_count(self) -> int:
        current = self.history_keys[-1]
        return sum(1 for key in self.history_keys if key == current)

    def repetition_info(self) -> RepetitionInfo | None:
        if self.defer_rule_details and self.repetition_count() >= 3:
            return self._materialize_rule_details().repetition_info()
        current = self.history_keys[-1]
        matches = [index for index, key in enumerate(self.history_keys) if key == current]
        if len(matches) < 2:
            return None

        previous = matches[-2]
        cycle_events = self.rule_events[previous + 1 :]
        cycle_moves = tuple(event.move for event in cycle_events if event.move is not None)
        checking_sides = []
        attack_patterns = []
        for color in (Color.RED, Color.BLACK):
            color_events = [event for event in cycle_events if event.moved_color is color]
            color_moves = tuple(event.move for event in color_events if event.move is not None)
            if color_events and all(event.gives_check for event in color_events):
                checking_sides.append(color)
            attacked_sets = [set(event.attacked_opponent_pieces) for event in color_events]
            if attacked_sets:
                common_attacked = set.intersection(*attacked_sets)
                common_targets = tuple(
                    _repeated_attack_target(square, color_events)
                    for square in sorted(common_attacked)
                )
                attack_patterns.append(
                    CycleAttackInfo(
                        color=color,
                        moves=color_moves,
                        attacks_every_move=all(bool(targets) for targets in attacked_sets),
                        common_attacked_squares=tuple(sorted(common_attacked)),
                        common_targets=common_targets,
                    )
                )

        has_capture = any(event.captured for event in cycle_events)
        possible_chasing_sides = tuple(
            pattern.color
            for pattern in attack_patterns
            if (
                not has_capture
                and pattern.color not in checking_sides
                and pattern.attacks_every_move
                and (
                    any(target.always_chase_relevant for target in pattern.common_targets)
                    or all(
                        any(detail.chase_relevant for detail in event.attacked_opponent_details)
                        for event in cycle_events
                        if event.moved_color is pattern.color
                    )
                )
            )
        )
        perpetual_chasing_sides = tuple(
            pattern.color
            for pattern in attack_patterns
            if (
                not has_capture
                and pattern.color not in checking_sides
                and (
                    _is_strict_perpetual_chase_pattern(pattern)
                    or _is_identity_perpetual_chase_pattern(pattern, cycle_events)
                )
            )
        )

        return RepetitionInfo(
            count=len(matches),
            cycle_plies=len(cycle_events),
            cycle_start_ply=previous,
            cycle_end_ply=len(self.rule_events) - 1,
            cycle_moves=cycle_moves,
            has_capture=has_capture,
            checking_sides=tuple(checking_sides),
            attack_patterns=tuple(attack_patterns),
            possible_chasing_sides=possible_chasing_sides,
            perpetual_chasing_sides=perpetual_chasing_sides,
        )

    def repetition_result(self) -> str:
        judgement = self.rule_judgement()
        return judgement.result or "unknown_rule_state"

    def history_signature(self) -> str:
        return "|".join(event.signature() for event in self.rule_events)

    def _materialize_rule_details(self) -> "GameState":
        if not self.defer_rule_details:
            return self
        return GameState.from_uci_position(self.to_uci_position())


def _repetition_key(position: Position) -> str:
    return f"{Zobrist().hash_position(position, include_rule_state=False):016x}"


def _attacked_opponent_details(position: Position, attacking: Color) -> tuple[AttackedPieceInfo, ...]:
    attacked: list[AttackedPieceInfo] = []
    for square, piece in enumerate(position.board):
        if piece is None or piece.color is attacking:
            continue
        attackers = position.attackers_to(square, attacking)
        if attackers:
            defenders = tuple(
                square_name(defender)
                for defender in position.defenders_to(square, piece.color)
            )
            legal_chase_attackers: list[int] = []
            recapture_defenders: set[int] = set()
            chase_attackers: list[int] = []
            target_chase_relevant = _is_chase_relevant_target(piece, square)
            for attacker in attackers:
                attacker_piece = position.piece_at(attacker)
                if (
                    not target_chase_relevant
                    or not _is_chase_relevant_attacker(attacker_piece)
                    or not position.is_legal_move(Move(attacker, square), attacking)
                ):
                    continue

                legal_chase_attackers.append(attacker)
                if _is_forced_chase_pair(attacker_piece.kind, piece.kind):
                    chase_attackers.append(attacker)
                    continue

                recaptures = _legal_recaptures_after_capture(
                    position,
                    attacker,
                    square,
                    attacking,
                )
                recapture_defenders.update(recaptures)
                if recaptures:
                    continue
                if _is_symmetric_attack(position, attacker, square, attacking):
                    continue
                chase_attackers.append(attacker)

            chase_attackers = tuple(
                square_name(attacker) for attacker in sorted(chase_attackers)
            )
            attacked.append(
                AttackedPieceInfo(
                    square=square_name(square),
                    color=piece.color,
                    kind=piece.kind,
                    protected_by_owner=bool(defenders),
                    defenders=defenders,
                    attackers=tuple(square_name(attacker) for attacker in attackers),
                    legal_chase_attackers=tuple(
                        square_name(attacker) for attacker in sorted(legal_chase_attackers)
                    ),
                    recapture_defenders=tuple(
                        square_name(defender) for defender in sorted(recapture_defenders)
                    ),
                    recapturable_by_owner=bool(recapture_defenders),
                    chase_relevant_attackers=chase_attackers,
                    chase_relevant=bool(chase_attackers),
                )
            )
    return tuple(attacked)


def _repeated_attack_target(square: str, events: list[RuleEvent]) -> RepeatedAttackTarget:
    details = [
        detail
        for event in events
        for detail in event.attacked_opponent_details
        if detail.square == square
    ]
    first = details[0]
    protected = [detail.protected_by_owner for detail in details]
    recapturable = [detail.recapturable_by_owner for detail in details]
    chase_relevant = [detail.chase_relevant for detail in details]
    return RepeatedAttackTarget(
        square=square,
        color=first.color,
        kind=first.kind,
        always_protected_by_owner=all(protected),
        ever_unprotected=not all(protected),
        always_recapturable_by_owner=all(recapturable),
        ever_recapturable_by_owner=any(recapturable),
        always_chase_relevant=all(chase_relevant),
    )


def _is_strict_perpetual_chase_pattern(pattern: CycleAttackInfo) -> bool:
    return (
        pattern.attacks_every_move
        and len(pattern.common_targets) == 1
        and pattern.common_targets[0].always_chase_relevant
    )


def _is_identity_perpetual_chase_pattern(
    pattern: CycleAttackInfo,
    cycle_events: tuple[RuleEvent, ...],
) -> bool:
    if not pattern.attacks_every_move or pattern.common_targets:
        return False
    chased_targets = []
    for index, event in enumerate(cycle_events):
        if event.moved_color is not pattern.color:
            continue
        targets = tuple(
            detail
            for detail in event.attacked_opponent_details
            if detail.chase_relevant
        )
        if event.move is None or len(targets) != 1:
            return False
        chased_targets.append((index, event.move, targets[0]))
    if len(chased_targets) < 2:
        return False
    for current, following in zip(chased_targets, chased_targets[1:]):
        if not _chase_link_is_continuous(current, following, cycle_events):
            return False
    return _chase_link_is_continuous(
        chased_targets[-1],
        chased_targets[0],
        cycle_events,
    )


def _chase_link_is_continuous(
    current: tuple[int, str, AttackedPieceInfo],
    following: tuple[int, str, AttackedPieceInfo],
    cycle_events: tuple[RuleEvent, ...],
) -> bool:
    current_index, current_move, current_target = current
    following_index, following_move, following_target = following
    if (
        current_target.color is not following_target.color
        or current_target.kind is not following_target.kind
        or current_target.square == following_target.square
        or current_move[2:] != following_move[:2]
    ):
        return False
    intervening = cycle_events[current_index + 1 : following_index]
    if following_index <= current_index:
        intervening = cycle_events[current_index + 1 :] + cycle_events[:following_index]
    return any(
        event.moved_color is current_target.color
        and event.move is not None
        and event.move[:2] == current_target.square
        and event.move[2:] == following_target.square
        for event in intervening
    )


def _is_chase_relevant_attacker(piece: Piece | None) -> bool:
    return piece is not None and piece.kind not in {PieceType.KING, PieceType.PAWN}


def _is_chase_relevant_target(piece: Piece, square: int) -> bool:
    if piece.kind is PieceType.KING:
        return False
    if piece.kind is not PieceType.PAWN:
        return True
    _, rank = file_rank(square)
    return _pawn_crossed_river(piece.color, rank)


def _pawn_crossed_river(color: Color, rank: int) -> bool:
    return rank >= 5 if color is Color.RED else rank <= 4


def _is_forced_chase_pair(attacker: PieceType, target: PieceType) -> bool:
    if attacker in {PieceType.KNIGHT, PieceType.CANNON} and target is PieceType.ROOK:
        return True
    if attacker in {PieceType.ADVISOR, PieceType.BISHOP} and target in {
        PieceType.ROOK,
        PieceType.CANNON,
        PieceType.KNIGHT,
    }:
        return True
    return False


def _legal_recaptures_after_capture(
    position: Position,
    attacker: int,
    target: int,
    attacking: Color,
) -> tuple[int, ...]:
    capture_position = Position(
        position.board,
        attacking,
        position.halfmove_clock,
        position.fullmove_number,
    ).make_move(Move(attacker, target), validate=False)
    return capture_position.attackers_to(target, attacking.opponent, legal=True)


def _is_symmetric_attack(
    position: Position,
    attacker: int,
    target: int,
    attacking: Color,
) -> bool:
    attacker_piece = position.piece_at(attacker)
    target_piece = position.piece_at(target)
    if attacker_piece is None or target_piece is None:
        return False
    if attacker_piece.kind is not target_piece.kind:
        return False
    return position.is_legal_move(Move(target, attacker), attacking.opponent)
