from __future__ import annotations

import unittest

import context  # noqa: F401
from xiangqi_core import (
    AttackedPieceInfo,
    Color,
    GameState,
    Move,
    PieceType,
    Position,
    RuleEvent,
)
from xiangqi_solver import BoundedProofSearch, ProofArtifact, ProofStatus, ProofVerifier


class GameStateTests(unittest.TestCase):
    def test_parse_startpos_moves(self) -> None:
        state = GameState.from_uci_position("position startpos moves h2e2")
        self.assertEqual(state.position.side_to_move, Color.BLACK)
        self.assertEqual(state.moves, (Move.from_uci("h2e2"),))
        self.assertEqual(state.position.halfmove_clock, 1)
        self.assertEqual(state.to_uci_position(), "position startpos moves h2e2")
        self.assertEqual(state.rule_events[-1].moved_color, Color.RED)
        self.assertFalse(state.rule_events[-1].captured)
        self.assertFalse(state.rule_events[-1].gives_check)

    def test_parse_fen_moves(self) -> None:
        state = GameState.from_uci_position(
            "position fen 4k4/9/9/9/9/9/4P4/9/R8/4K4 w - - 0 1 moves a1a2"
        )
        self.assertEqual(state.position.to_fen(), "4k4/9/9/9/9/9/4P4/R8/9/4K4 b - - 1 1")
        self.assertEqual(
            state.to_uci_position(),
            "position fen 4k4/9/9/9/9/9/4P4/9/R8/4K4 w - - 0 1 moves a1a2",
        )

    def test_quiet_threefold_repetition_is_a_draw(self) -> None:
        state = GameState.from_uci_position(
            "position fen 4k4/9/9/9/9/9/4P4/9/R8/4K4 w - - 0 1 "
            "moves a1a2 e9e8 a2a1 e8e9 a1a2 e9e8 a2a1 e8e9"
        )
        self.assertEqual(state.repetition_count(), 3)
        self.assertEqual(state.game_result(), "draw")
        judgement = state.rule_judgement()
        self.assertEqual(judgement.result, "draw")
        self.assertEqual(judgement.reason, "repetition_draw")
        self.assertTrue(judgement.adjudicated)
        self.assertEqual(len(state.rule_events), len(state.moves) + 1)
        repetition = state.repetition_info()
        self.assertIsNotNone(repetition)
        assert repetition is not None
        self.assertEqual(repetition.count, 3)
        self.assertEqual(repetition.cycle_plies, 4)
        self.assertEqual(repetition.cycle_start_ply, 4)
        self.assertEqual(repetition.cycle_end_ply, 8)
        self.assertEqual(
            repetition.cycle_moves,
            ("a1a2", "e9e8", "a2a1", "e8e9"),
        )
        self.assertFalse(repetition.has_capture)
        self.assertEqual(repetition.checking_sides, ())

    def test_replayed_unilateral_perpetual_check_loses_for_checking_side(self) -> None:
        state = GameState.from_uci_position(
            "position fen 4k4/9/3R5/9/9/9/4P4/9/9/4K4 w - - 0 1 "
            "moves d7e7 e9d9 e7d7 d9e9 d7e7 e9d9 e7d7 d9e9"
        )

        self.assertEqual(state.repetition_count(), 3)
        self.assertEqual(state.game_result(), "black_win")
        self.assertEqual(state.rule_judgement().reason, "perpetual_check_loss")
        self.assertTrue(all(event.gives_check for event in state.rule_events[1::2]))

    def test_unilateral_perpetual_check_repetition_loses_for_checking_side(self) -> None:
        state = _synthetic_repetition(checking_sides=(Color.RED,))

        self.assertEqual(state.repetition_result(), "black_win")
        self.assertEqual(state.game_result(), "black_win")
        judgement = state.rule_judgement()
        self.assertEqual(judgement.reason, "perpetual_check_loss")
        self.assertTrue(judgement.adjudicated)

    def test_mutual_perpetual_check_repetition_draws(self) -> None:
        state = _synthetic_repetition(checking_sides=(Color.RED, Color.BLACK))

        self.assertEqual(state.repetition_result(), "draw")
        self.assertEqual(state.game_result(), "draw")
        judgement = state.rule_judgement()
        self.assertEqual(judgement.reason, "mutual_perpetual_check_draw")
        self.assertTrue(judgement.adjudicated)

    def test_repetition_info_summarizes_attack_patterns(self) -> None:
        state = _synthetic_repetition(attacking_sides=(Color.RED,))
        repetition = state.repetition_info()

        self.assertIsNotNone(repetition)
        assert repetition is not None
        red_pattern = next(
            pattern for pattern in repetition.attack_patterns if pattern.color is Color.RED
        )
        black_pattern = next(
            pattern for pattern in repetition.attack_patterns if pattern.color is Color.BLACK
        )
        self.assertEqual(repetition.cycle_start_ply, 2)
        self.assertEqual(repetition.cycle_end_ply, 4)
        self.assertEqual(repetition.cycle_moves, ("a1a0", "e8e9"))
        self.assertTrue(red_pattern.attacks_every_move)
        self.assertEqual(red_pattern.moves, ("a1a0",))
        self.assertEqual(red_pattern.common_attacked_squares, ("e7",))
        self.assertEqual(len(red_pattern.common_targets), 1)
        self.assertEqual(red_pattern.common_targets[0].kind, PieceType.ROOK)
        self.assertFalse(red_pattern.common_targets[0].always_protected_by_owner)
        self.assertTrue(red_pattern.common_targets[0].ever_unprotected)
        self.assertFalse(red_pattern.common_targets[0].always_recapturable_by_owner)
        self.assertFalse(red_pattern.common_targets[0].ever_recapturable_by_owner)
        self.assertTrue(red_pattern.common_targets[0].always_chase_relevant)
        self.assertFalse(black_pattern.attacks_every_move)
        self.assertEqual(repetition.possible_chasing_sides, (Color.RED,))
        self.assertEqual(repetition.perpetual_chasing_sides, (Color.RED,))
        judgement = state.rule_judgement()
        self.assertEqual(judgement.result, "black_win")
        self.assertEqual(judgement.reason, "perpetual_chase_loss")
        self.assertTrue(judgement.adjudicated)

    def test_mutual_perpetual_chase_repetition_draws(self) -> None:
        state = _synthetic_repetition(attacking_sides=(Color.RED, Color.BLACK))
        repetition = state.repetition_info()

        self.assertIsNotNone(repetition)
        assert repetition is not None
        self.assertEqual(repetition.perpetual_chasing_sides, (Color.RED, Color.BLACK))
        judgement = state.rule_judgement()
        self.assertEqual(judgement.result, "draw")
        self.assertEqual(judgement.reason, "mutual_perpetual_chase_draw")
        self.assertTrue(judgement.adjudicated)

    def test_multiple_repeated_targets_keep_chase_unadjudicated(self) -> None:
        state = _synthetic_repetition(
            attacking_sides=(Color.RED,),
            extra_target_square="e6",
        )
        repetition = state.repetition_info()

        self.assertIsNotNone(repetition)
        assert repetition is not None
        red_pattern = next(
            pattern for pattern in repetition.attack_patterns if pattern.color is Color.RED
        )
        self.assertEqual(red_pattern.common_attacked_squares, ("e6", "e7"))
        self.assertEqual(repetition.possible_chasing_sides, (Color.RED,))
        self.assertEqual(repetition.perpetual_chasing_sides, ())
        judgement = state.rule_judgement()
        self.assertEqual(judgement.result, "unknown_rule_state")
        self.assertEqual(judgement.reason, "possible_chase_unadjudicated")
        self.assertFalse(judgement.adjudicated)

    def test_moving_chase_target_is_adjudicated_by_piece_identity(self) -> None:
        state = GameState.from_uci_position(
            "position fen 4k4/9/9/9/9/3N1r3/9/9/4A4/4K4 w - - 0 1 "
            "moves d4e6 f4f5 e6d4 f5f4 d4e6 f4f5 e6d4 f5f4"
        )

        repetition = state.repetition_info()
        judgement = state.rule_judgement()

        self.assertIsNotNone(repetition)
        assert repetition is not None
        red_pattern = next(
            pattern for pattern in repetition.attack_patterns if pattern.color is Color.RED
        )
        self.assertTrue(red_pattern.attacks_every_move)
        self.assertEqual(red_pattern.common_attacked_squares, ())
        self.assertEqual(repetition.possible_chasing_sides, (Color.RED,))
        self.assertEqual(repetition.perpetual_chasing_sides, (Color.RED,))
        self.assertEqual(judgement.result, "black_win")
        self.assertEqual(judgement.reason, "perpetual_chase_loss")
        self.assertTrue(judgement.adjudicated)

    def test_moving_chase_target_is_adjudicated_when_cycle_starts_with_black(self) -> None:
        state = GameState.from_uci_position(
            "position fen 4k4/9/9/4N4/9/5r3/9/9/4A4/4K4 b - - 1 1 "
            "moves f4f5 e6d4 f5f4 d4e6 f4f5 e6d4 f5f4 d4e6"
        )

        repetition = state.repetition_info()
        judgement = state.rule_judgement()

        self.assertIsNotNone(repetition)
        assert repetition is not None
        self.assertEqual(repetition.possible_chasing_sides, (Color.RED,))
        self.assertEqual(repetition.perpetual_chasing_sides, (Color.RED,))
        self.assertEqual(judgement.result, "black_win")
        self.assertEqual(judgement.reason, "perpetual_chase_loss")
        self.assertTrue(judgement.adjudicated)

    def test_perpetual_check_is_not_reported_as_possible_chase(self) -> None:
        state = _synthetic_repetition(
            checking_sides=(Color.RED,),
            attacking_sides=(Color.RED,),
        )
        repetition = state.repetition_info()

        self.assertIsNotNone(repetition)
        assert repetition is not None
        self.assertEqual(repetition.checking_sides, (Color.RED,))
        self.assertEqual(repetition.possible_chasing_sides, ())
        self.assertEqual(repetition.perpetual_chasing_sides, ())

    def test_unpromoted_pawn_target_is_not_reported_as_possible_chase(self) -> None:
        state = _synthetic_repetition(
            attacking_sides=(Color.RED,),
            target_kind=PieceType.PAWN,
            chase_relevant=False,
        )
        repetition = state.repetition_info()

        self.assertIsNotNone(repetition)
        assert repetition is not None
        self.assertEqual(repetition.possible_chasing_sides, ())
        self.assertEqual(repetition.perpetual_chasing_sides, ())

    def test_rule_events_record_captures(self) -> None:
        state = GameState.from_uci_position(
            "position fen 4k4/9/9/9/9/9/4P4/p8/R8/4K4 w - - 0 1 moves a1a2"
        )
        self.assertTrue(state.rule_events[-1].captured)
        self.assertEqual(state.position.halfmove_clock, 0)

    def test_rule_events_record_checks(self) -> None:
        state = GameState.from_uci_position(
            "position fen 4k4/9/9/9/9/9/9/9/R8/3K5 w - - 0 1 moves a1e1"
        )
        self.assertTrue(state.rule_events[-1].gives_check)
        self.assertIn(":a1e1:w:0:1", state.history_signature())

    def test_rule_events_record_attacked_opponent_pieces(self) -> None:
        state = GameState.from_uci_position(
            "position fen 4k4/9/9/9/4r4/9/4P4/9/9/4K4 b - - 0 1 moves e5e4"
        )
        self.assertEqual(state.rule_events[-1].attacked_opponent_pieces, ("e3",))
        detail = state.rule_events[-1].attacked_opponent_details[0]
        self.assertEqual(detail.square, "e3")
        self.assertEqual(detail.kind, PieceType.PAWN)
        self.assertFalse(detail.protected_by_owner)

    def test_rule_events_record_attacked_piece_protection(self) -> None:
        state = GameState.from_uci_position(
            "position fen 4k4/9/9/9/4r4/9/4P4/9/4R4/4K4 b - - 0 1 moves e5e4"
        )
        detail = state.rule_events[-1].attacked_opponent_details[0]
        self.assertEqual(detail.square, "e3")
        self.assertTrue(detail.protected_by_owner)
        self.assertEqual(detail.defenders, ("e1",))

    def test_rule_events_mark_chase_relevant_attackers(self) -> None:
        state = GameState.from_uci_position(
            "position fen 4k4/9/9/9/4r4/9/4N4/9/9/4K4 b - - 0 1 moves e5e4"
        )
        detail = state.rule_events[-1].attacked_opponent_details[0]
        self.assertEqual(detail.attackers, ("e4",))
        self.assertEqual(detail.legal_chase_attackers, ("e4",))
        self.assertEqual(detail.chase_relevant_attackers, ("e4",))
        self.assertTrue(detail.chase_relevant)

    def test_recapturable_target_is_not_chase_relevant(self) -> None:
        state = GameState.from_uci_position(
            "position fen 4k4/9/9/9/4r4/9/4R4/9/4R4/4K4 b - - 0 1 moves e5e4"
        )
        detail = state.rule_events[-1].attacked_opponent_details[0]
        self.assertEqual(detail.square, "e3")
        self.assertEqual(detail.kind, PieceType.ROOK)
        self.assertTrue(detail.protected_by_owner)
        self.assertEqual(detail.legal_chase_attackers, ("e4",))
        self.assertEqual(detail.recapture_defenders, ("e1",))
        self.assertTrue(detail.recapturable_by_owner)
        self.assertEqual(detail.chase_relevant_attackers, ())
        self.assertFalse(detail.chase_relevant)

    def test_unpromoted_pawn_target_is_not_chase_relevant(self) -> None:
        state = GameState.from_uci_position(
            "position fen 4k4/9/9/9/4r4/9/4P4/9/9/4K4 b - - 0 1 moves e5e4"
        )
        detail = state.rule_events[-1].attacked_opponent_details[0]
        self.assertEqual(detail.kind, PieceType.PAWN)
        self.assertFalse(detail.chase_relevant)

    def test_pawn_attacker_is_not_chase_relevant(self) -> None:
        state = GameState.from_uci_position(
            "position fen 4k4/9/9/9/4p4/9/4R4/9/9/4K4 b - - 0 1 moves e5e4"
        )
        detail = state.rule_events[-1].attacked_opponent_details[0]
        self.assertEqual(detail.attackers, ("e4",))
        self.assertEqual(detail.chase_relevant_attackers, ())
        self.assertFalse(detail.chase_relevant)

    def test_rule_events_do_not_record_blocked_attacks(self) -> None:
        state = GameState.from_uci_position(
            "position fen 4k4/4b4/9/9/4r4/9/4P4/9/9/4K4 b - - 0 1 moves e5d5"
        )
        self.assertEqual(state.rule_events[-1].attacked_opponent_pieces, ())

    def test_search_records_quiet_repetition_draw(self) -> None:
        state = GameState.from_uci_position(
            "position fen 4k4/9/9/9/9/9/4P4/9/R8/4K4 w - - 0 1 "
            "moves a1a2 e9e8 a2a1 e8e9 a1a2 e9e8 a2a1 e8e9"
        )
        result = BoundedProofSearch("red", max_ply=1).search(state)
        self.assertEqual(result.artifact.status, ProofStatus.DRAW)
        self.assertEqual(result.artifact.reason, "repetition_draw")
        self.assertEqual(result.artifact.history_signature, state.history_signature())
        self.assertEqual(result.artifact.position_command, state.to_uci_position())
        self.assertEqual(
            ProofArtifact.from_dict(result.artifact.to_dict()).history_signature,
            state.history_signature(),
        )
        self.assertEqual(
            ProofArtifact.from_dict(result.artifact.to_dict()).position_command,
            state.to_uci_position(),
        )
        self.assertTrue(ProofVerifier().verify(result.artifact).valid)


def _synthetic_repetition(
    checking_sides: tuple[Color, ...] = (),
    attacking_sides: tuple[Color, ...] = (),
    target_kind: PieceType = PieceType.ROOK,
    chase_relevant: bool = True,
    extra_target_square: str | None = None,
) -> GameState:
    keys = ("root", "mid", "root", "mid", "root")
    events = [
        RuleEvent(key="root"),
        RuleEvent(
            key="mid",
            move="a0a1",
            moved_color=Color.RED,
            gives_check=Color.RED in checking_sides,
            attacked_opponent_pieces=_synthetic_attacked_squares(
                "e7",
                extra_target_square,
            )
            if Color.RED in attacking_sides
            else (),
            attacked_opponent_details=_synthetic_attacked_details(
                Color.BLACK,
                "e7",
                target_kind,
                chase_relevant,
                extra_target_square,
            )
            if Color.RED in attacking_sides
            else (),
        ),
        RuleEvent(
            key="root",
            move="e9e8",
            moved_color=Color.BLACK,
            gives_check=Color.BLACK in checking_sides,
            attacked_opponent_pieces=("e2",) if Color.BLACK in attacking_sides else (),
            attacked_opponent_details=_synthetic_attacked_details(
                Color.RED,
                "e2",
                target_kind,
                chase_relevant,
                None,
            )
            if Color.BLACK in attacking_sides
            else (),
        ),
        RuleEvent(
            key="mid",
            move="a1a0",
            moved_color=Color.RED,
            gives_check=Color.RED in checking_sides,
            attacked_opponent_pieces=_synthetic_attacked_squares(
                "e7",
                extra_target_square,
            )
            if Color.RED in attacking_sides
            else (),
            attacked_opponent_details=_synthetic_attacked_details(
                Color.BLACK,
                "e7",
                target_kind,
                chase_relevant,
                extra_target_square,
            )
            if Color.RED in attacking_sides
            else (),
        ),
        RuleEvent(
            key="root",
            move="e8e9",
            moved_color=Color.BLACK,
            gives_check=Color.BLACK in checking_sides,
            attacked_opponent_pieces=("e2",) if Color.BLACK in attacking_sides else (),
            attacked_opponent_details=_synthetic_attacked_details(
                Color.RED,
                "e2",
                target_kind,
                chase_relevant,
                None,
            )
            if Color.BLACK in attacking_sides
            else (),
        ),
    ]
    return GameState(
        position=Position.from_fen("4k4/9/9/9/9/9/9/9/9/3K5 w - - 0 1"),
        history_keys=keys,
        rule_events=tuple(events),
    )


def _synthetic_attacked_details(
    color: Color,
    square: str,
    kind: PieceType,
    chase_relevant: bool,
    extra_square: str | None = None,
) -> tuple[AttackedPieceInfo, ...]:
    details = [
        AttackedPieceInfo(
            square=square,
            color=color,
            kind=kind,
            protected_by_owner=False,
            attackers=("a1",),
            chase_relevant_attackers=("a1",) if chase_relevant else (),
            chase_relevant=chase_relevant,
        )
    ]
    if extra_square is not None:
        details.append(
            AttackedPieceInfo(
                square=extra_square,
                color=color,
                kind=kind,
                protected_by_owner=False,
                attackers=("a1",),
                chase_relevant_attackers=("a1",) if chase_relevant else (),
                chase_relevant=chase_relevant,
            )
        )
    return tuple(details)


def _synthetic_attacked_squares(square: str, extra_square: str | None) -> tuple[str, ...]:
    if extra_square is None:
        return (square,)
    return (square, extra_square)


if __name__ == "__main__":
    unittest.main()
