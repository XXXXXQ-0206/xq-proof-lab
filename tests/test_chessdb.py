from __future__ import annotations

import json
import subprocess
import sys
import unittest

import context  # noqa: F401
from context import ROOT
from xiangqi_core import GameState, Position
from xiangqi_evaluators import (
    ChessDbAction,
    ChessDbClient,
    ChessDbStatus,
    build_rule_query_from_state,
    parse_chessdb_response,
    parse_query_rule_results,
)


class ChessDbTests(unittest.TestCase):
    def test_parse_query_best(self) -> None:
        response = parse_chessdb_response("move:c3c4\x00", ChessDbAction.QUERY_BEST)
        self.assertEqual(response.status, ChessDbStatus.OK)
        self.assertEqual(response.best_move, "c3c4")
        self.assertEqual(response.best_move_source, "move")

    def test_parse_query_best_accepts_egtb_and_search_sources(self) -> None:
        egtb = parse_chessdb_response("egtb:c3c4", ChessDbAction.QUERY_BEST)
        search = parse_chessdb_response("search:h2e2", ChessDbAction.QUERY_BEST)

        self.assertEqual(egtb.best_move, "c3c4")
        self.assertEqual(egtb.best_move_source, "egtb")
        self.assertEqual(search.best_move, "h2e2")
        self.assertEqual(search.best_move_source, "search")

    def test_parse_query_all_moves(self) -> None:
        response = parse_chessdb_response(
            "move:c3c4,score:12,rank:1,winrate:51.2,note:book|move:h2e2,score:-8,rank:2",
            ChessDbAction.QUERY_ALL,
        )
        self.assertEqual(response.status, ChessDbStatus.OK)
        self.assertEqual(len(response.moves), 2)
        self.assertEqual(response.moves[0].move, "c3c4")
        self.assertEqual(response.moves[0].score, 12)
        self.assertEqual(response.moves[0].winrate, 51.2)
        self.assertEqual(response.moves[1].rank, 2)

    def test_parse_status_responses(self) -> None:
        self.assertEqual(
            parse_chessdb_response("checkmate", ChessDbAction.QUERY_BEST).status,
            ChessDbStatus.CHECKMATE,
        )
        self.assertEqual(
            parse_chessdb_response("stalemate", ChessDbAction.QUERY_BEST).status,
            ChessDbStatus.STALEMATE,
        )
        self.assertEqual(
            parse_chessdb_response("unknown", ChessDbAction.QUERY_BEST).status,
            ChessDbStatus.UNKNOWN,
        )
        self.assertEqual(
            parse_chessdb_response("nobestmove", ChessDbAction.QUERY_BEST).status,
            ChessDbStatus.NO_BEST_MOVE,
        )
        self.assertEqual(
            parse_chessdb_response("invalid board", ChessDbAction.QUERY_BEST).status,
            ChessDbStatus.INVALID_BOARD,
        )
        self.assertEqual(
            parse_chessdb_response("invalid movelist", ChessDbAction.QUERY_RULE).status,
            ChessDbStatus.INVALID_MOVELIST,
        )

    def test_parse_query_score_accepts_eval_field(self) -> None:
        response = parse_chessdb_response("eval:42", ChessDbAction.QUERY_SCORE)
        self.assertEqual(response.status, ChessDbStatus.OK)
        self.assertEqual(response.score, 42)

    def test_build_url_uses_learn_zero_by_default(self) -> None:
        url = ChessDbClient().build_url(ChessDbAction.QUERY_BEST, Position.START_FEN)
        self.assertIn("action=querybest", url)
        self.assertIn("learn=0", url)
        self.assertIn("board=", url)

    def test_build_query_rule_url_includes_movelist(self) -> None:
        url = ChessDbClient().build_url(
            ChessDbAction.QUERY_RULE,
            Position.START_FEN,
            movelist="c3c4|h2e2",
            reptimes=3,
        )
        self.assertIn("action=queryrule", url)
        self.assertIn("movelist=c3c4%7Ch2e2", url)
        self.assertIn("reptimes=3", url)

    def test_query_chessdb_dry_run_includes_egtbmetric_and_ban(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "query_chessdb.py"),
                "--fen",
                Position.START_FEN,
                "--action",
                "querybest",
                "--egtbmetric",
                "dtm",
                "--ban",
                "c3c4",
                "h2e2",
                "--dry-run",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(output["status"], "dry_run")
        self.assertIn("egtbmetric=dtm", output["url"])
        self.assertIn("ban=c3c4%7Ch2e2", output["url"])

    def test_parse_query_rule_results(self) -> None:
        response = parse_chessdb_response(
            "move:c3c4,rule:none|move:h2e2,rule:draw",
            ChessDbAction.QUERY_RULE,
        )
        self.assertEqual(response.status, ChessDbStatus.OK)
        self.assertIsNone(response.rule)
        self.assertEqual(len(response.rule_results), 2)
        self.assertEqual(response.rule_results[0].move, "c3c4")
        self.assertEqual(response.rule_results[1].rule, "draw")
        self.assertEqual(
            parse_query_rule_results("move:a0a1,rule:ban")[0].rule,
            "ban",
        )

    def test_build_rule_query_from_state_uses_initial_fen_and_history(self) -> None:
        position = (
            "position fen 4k4/9/9/9/9/9/4P4/9/R8/4K4 w - - 0 1 "
            "moves a1a2 e9e8 a2a1 e8e9 a1a2 e9e8 a2a1 e8e9"
        )
        state = GameState.from_uci_position(position)

        query = build_rule_query_from_state(state)

        self.assertEqual(query.fen, "4k4/9/9/9/9/9/4P4/9/R8/4K4 w - - 0 1")
        self.assertEqual(
            query.movelist,
            ("a1a2", "e9e8", "a2a1", "e8e9", "a1a2", "e9e8", "a2a1", "e8e9"),
        )
        self.assertEqual(query.reptimes, 2)

    def test_build_rule_query_omits_reptimes_without_repetition(self) -> None:
        state = GameState.from_uci_position(
            "position fen 4k4/9/9/9/9/9/4P4/9/R8/4K4 w - - 0 1 "
            "moves a1a2 e9e8 a2a3 e8e7"
        )

        self.assertIsNone(build_rule_query_from_state(state).reptimes)

    def test_query_chessdb_dry_run_builds_rule_query_from_position(self) -> None:
        position = (
            "position fen 4k4/9/9/9/9/9/4P4/9/R8/4K4 w - - 0 1 "
            "moves a1a2 e9e8 a2a1 e8e9 a1a2 e9e8 a2a1 e8e9"
        )

        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "query_chessdb.py"),
                "--position",
                position,
                "--action",
                "queryrule",
                "--dry-run",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(output["status"], "dry_run")
        self.assertEqual(output["fen"], "4k4/9/9/9/9/9/4P4/9/R8/4K4 w - - 0 1")
        self.assertEqual(
            output["movelist"],
            ["a1a2", "e9e8", "a2a1", "e8e9", "a1a2", "e9e8", "a2a1", "e8e9"],
        )
        self.assertEqual(output["reptimes"], 2)
        self.assertIn("movelist=a1a2%7Ce9e8", output["url"])


if __name__ == "__main__":
    unittest.main()
