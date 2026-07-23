from __future__ import annotations

import unittest
import sys
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import patch

import context  # noqa: F401
from fixtures import RED_WIN_IN_ONE_FEN
from xiangqi_core import GameState, Position
from xiangqi_evaluators import (
    CachedMoveOrderer,
    ChessDbMove,
    ChessDbMoveOrderer,
    ChessDbResponse,
    ChessDbStatus,
    HeuristicMoveOrderer,
    LexicographicMoveOrderer,
    PersistentUciBestMoveOrderer,
    UciBestMoveOrderer,
)
from xiangqi_evaluators.move_ordering import (
    _order_from_external_moves,
    _uci_timeout_seconds,
)
from xiangqi_solver import BoundedProofSearch, ProofStatus


class FakeChessDbClient:
    def query_all(self, fen: str, **_kwargs) -> ChessDbResponse:
        return ChessDbResponse(
            ChessDbStatus.OK,
            "move:a8e8,rank:1|move:d0d9,rank:2",
            moves=(ChessDbMove("a8e8", rank=1), ChessDbMove("d0d9", rank=2)),
        )

    def query_best(self, fen: str, **_kwargs) -> ChessDbResponse:
        raise AssertionError("query_best should not be used when query_all has moves")


class RecordingChessDbClient:
    def __init__(self) -> None:
        self.query_all_kwargs = None

    def query_all(self, fen: str, **kwargs) -> ChessDbResponse:
        self.query_all_kwargs = kwargs
        return ChessDbResponse(
            ChessDbStatus.OK,
            "move:a8e8,rank:1",
            moves=(ChessDbMove("a8e8", rank=1),),
        )

    def query_best(self, fen: str, **_kwargs) -> ChessDbResponse:
        raise AssertionError("query_best should not be used when query_all has moves")


class CountingMoveOrderer:
    def __init__(self, preferred: str) -> None:
        self.preferred = preferred
        self.calls = 0
        self.options: list[tuple[str, str | None]] = []

    def order_moves(self, position, moves):
        self.calls += 1
        legal_by_uci = {move.to_uci(): move for move in moves}
        fallback = sorted(moves, key=lambda move: move.to_uci())
        if self.preferred not in legal_by_uci:
            return fallback
        return [legal_by_uci[self.preferred]] + [
            move for move in fallback if move.to_uci() != self.preferred
        ]

    def set_option(self, name: str, value: str | None = None) -> None:
        self.options.append((name, value))


class TimeAwareCountingMoveOrderer:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def order_moves_with_time_limit(self, position, moves, time_limit_ms):
        self.calls.append(time_limit_ms)
        fallback = sorted(moves, key=lambda move: move.to_uci())
        if len(fallback) < 2 or time_limit_ms < 100:
            return fallback
        return [fallback[1], fallback[0], *fallback[2:]]


class MoveOrderingTests(unittest.TestCase):
    def test_external_orderer_checks_only_recommended_safe_move(self) -> None:
        position = Position.start()
        fallback_moves = LexicographicMoveOrderer().order_moves(
            position,
            position.legal_moves(),
        )
        recommended = fallback_moves[0]
        legal_by_uci = {move.to_uci(): move for move in fallback_moves}

        with patch(
            "xiangqi_evaluators.move_ordering._move_allows_opponent_immediate_win",
            return_value=False,
        ) as safety_check:
            ordered = _order_from_external_moves(
                (recommended.to_uci(),),
                fallback_moves,
                legal_by_uci,
                position,
            )

        self.assertEqual(ordered[0], recommended)
        self.assertEqual(safety_check.call_count, 1)

    def test_lexicographic_orderer_is_deterministic(self) -> None:
        position = Position.from_fen(RED_WIN_IN_ONE_FEN)
        moves = LexicographicMoveOrderer().order_moves(position, position.legal_moves())
        self.assertEqual(moves, sorted(moves, key=lambda move: move.to_uci()))

    def test_chessdb_orderer_promotes_cloud_moves_without_dropping_legal_moves(self) -> None:
        position = Position.from_fen(RED_WIN_IN_ONE_FEN)
        orderer = ChessDbMoveOrderer(FakeChessDbClient())  # type: ignore[arg-type]
        moves = orderer.order_moves(position, position.legal_moves())

        self.assertEqual(moves[0].to_uci(), "a8e8")
        self.assertEqual({move.to_uci() for move in moves}, {move.to_uci() for move in position.legal_moves()})

    def test_chessdb_orderer_passes_endgame_hints(self) -> None:
        position = Position.from_fen(RED_WIN_IN_ONE_FEN)
        client = RecordingChessDbClient()
        orderer = ChessDbMoveOrderer(
            client,  # type: ignore[arg-type]
            egtbmetric="dtm",
            ban=("a8a0", "a8a1"),
        )

        orderer.order_moves(position, position.legal_moves())

        self.assertEqual(
            client.query_all_kwargs,
            {"egtbmetric": "dtm", "ban": ("a8a0", "a8a1")},
        )

    def test_cached_orderer_reuses_position_ordering_without_skipping_legal_rebuild(self) -> None:
        position = Position.start()
        underlying = CountingMoveOrderer("i3i4")
        orderer = CachedMoveOrderer(underlying)

        first = orderer.order_moves(position, position.legal_moves())
        second = orderer.order_moves(position, list(reversed(position.legal_moves())))

        self.assertEqual(first[0].to_uci(), "i3i4")
        self.assertEqual(second[0].to_uci(), "i3i4")
        self.assertEqual(underlying.calls, 1)

    def test_cached_orderer_clear_cache_drops_local_entry(self) -> None:
        position = Position.start()
        underlying = CountingMoveOrderer("i3i4")
        orderer = CachedMoveOrderer(underlying)

        orderer.order_moves(position, position.legal_moves())
        orderer.clear_cache()
        orderer.order_moves(position, position.legal_moves())

        self.assertEqual(underlying.calls, 2)

    def test_cached_orderer_forwards_runtime_option_and_clears_cache(self) -> None:
        position = Position.start()
        underlying = CountingMoveOrderer("i3i4")
        orderer = CachedMoveOrderer(underlying)

        orderer.order_moves(position, position.legal_moves())
        orderer.set_option("Hash", "128")
        orderer.order_moves(position, position.legal_moves())

        self.assertEqual(underlying.options, [("Hash", "128")])
        self.assertEqual(underlying.calls, 2)

    def test_cached_orderer_forwards_advertised_uci_options(self) -> None:
        class AdvertisingOrderer(CountingMoveOrderer):
            def uci_options(self) -> tuple[str, ...]:
                return ("option name Hash type spin default 16 min 1 max 1048576",)

        orderer = CachedMoveOrderer(AdvertisingOrderer("i3i4"))

        self.assertEqual(
            orderer.uci_options(),
            ("option name Hash type spin default 16 min 1 max 1048576",),
        )

    def test_cached_orderer_separates_distinct_time_budgets(self) -> None:
        position = Position.start()
        underlying = TimeAwareCountingMoveOrderer()
        orderer = CachedMoveOrderer(underlying)

        short_budget = orderer.order_moves_with_time_limit(
            position,
            position.legal_moves(),
            50,
        )
        long_budget = orderer.order_moves_with_time_limit(
            position,
            position.legal_moves(),
            150,
        )
        repeated_short_budget = orderer.order_moves_with_time_limit(
            position,
            position.legal_moves(),
            50,
        )

        self.assertEqual(underlying.calls, [50, 150])
        self.assertEqual(
            [move.to_uci() for move in short_budget[:2]],
            [move.to_uci() for move in repeated_short_budget[:2]],
        )
        self.assertNotEqual(
            [move.to_uci() for move in short_budget[:2]],
            [move.to_uci() for move in long_budget[:2]],
        )

    def test_timed_uci_timeout_tracks_allocated_budget(self) -> None:
        self.assertAlmostEqual(_uci_timeout_seconds(5.0, 123), 0.173)
        self.assertAlmostEqual(_uci_timeout_seconds(5.0, 1), 0.051)
        self.assertAlmostEqual(_uci_timeout_seconds(0.25, 123), 0.173)
        self.assertEqual(_uci_timeout_seconds(5.0, None), 5.0)

    def test_heuristic_orderer_prefers_material_gain_over_lexicographic_order(self) -> None:
        position = Position.from_fen("4k4/9/9/9/4p4/9/9/9/8r/4K3R w - - 0 1")

        moves = HeuristicMoveOrderer().order_moves(position, position.legal_moves())

        self.assertEqual(moves[0].to_uci(), "i0i1")
        self.assertEqual(
            {move.to_uci() for move in moves},
            {move.to_uci() for move in position.legal_moves()},
        )

    def test_heuristic_orderer_demotes_moves_allowing_immediate_loss(self) -> None:
        position = Position.from_fen(
            "9/9/5k3/N1r2b3/7b1/9/4n4/3A5/9/1p3K3 w - - 0 1"
        )
        legal_by_uci = {move.to_uci(): move for move in position.legal_moves()}

        after_bad = position.make_move(legal_by_uci["a6b4"], validate=False)
        mate = next(move for move in after_bad.legal_moves() if move.to_uci() == "c6c0")
        self.assertEqual(after_bad.make_move(mate, validate=False).game_result(), "black_win")

        after_safe = position.make_move(legal_by_uci["d2e1"], validate=False)
        self.assertFalse(
            any(
                after_safe.make_move(reply, validate=False).game_result() == "black_win"
                for reply in after_safe.legal_moves()
            )
        )

        moves = HeuristicMoveOrderer().order_moves(position, position.legal_moves())

        ordered = [move.to_uci() for move in moves]
        self.assertEqual(ordered[0], "d2e1")
        self.assertLess(ordered.index("d2e1"), ordered.index("a6b4"))

    def test_search_uses_injected_orderer_but_still_verifies_proof(self) -> None:
        position = Position.from_fen(RED_WIN_IN_ONE_FEN)
        orderer = ChessDbMoveOrderer(FakeChessDbClient())  # type: ignore[arg-type]
        result = BoundedProofSearch("red", max_ply=1, move_orderer=orderer).search(position)

        self.assertEqual(result.artifact.status, ProofStatus.PROVEN)
        self.assertEqual(result.artifact.children[0].move, "a8e8")

    def test_uci_orderer_promotes_engine_bestmove(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_engine = Path(tmp) / "fake_uci.py"
            fake_engine.write_text(
                textwrap.dedent(
                    """
                    import sys
                    for line in sys.stdin:
                        command = line.strip()
                        if command == "uci":
                            print("id name Fake UCI", flush=True)
                            print("uciok", flush=True)
                        elif command == "isready":
                            print("readyok", flush=True)
                        elif command.startswith("go depth"):
                            print("bestmove a8e8", flush=True)
                        elif command == "quit":
                            break
                    """
                ).strip(),
                encoding="utf-8",
            )
            position = Position.from_fen(RED_WIN_IN_ONE_FEN)
            orderer = UciBestMoveOrderer([sys.executable, str(fake_engine)], depth=1)
            moves = orderer.order_moves(position, position.legal_moves())

        self.assertEqual(moves[0].to_uci(), "a8e8")

    def test_uci_orderer_defers_external_move_allowing_immediate_loss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_engine = Path(tmp) / "fake_uci.py"
            fake_engine.write_text(
                textwrap.dedent(
                    """
                    import sys
                    for line in sys.stdin:
                        command = line.strip()
                        if command == "uci":
                            print("id name Fake UCI", flush=True)
                            print("uciok", flush=True)
                        elif command == "isready":
                            print("readyok", flush=True)
                        elif command.startswith("go"):
                            print("bestmove a6b4", flush=True)
                        elif command == "quit":
                            break
                    """
                ).strip(),
                encoding="utf-8",
            )
            position = Position.from_fen(
                "9/9/5k3/N1r2b3/7b1/9/4n4/3A5/9/1p3K3 w - - 0 1"
            )
            orderer = UciBestMoveOrderer(
                [sys.executable, str(fake_engine)],
                depth=1,
                fallback=HeuristicMoveOrderer(),
            )
            moves = orderer.order_moves(position, position.legal_moves())

        ordered = [move.to_uci() for move in moves]
        self.assertEqual(ordered[0], "d2e1")
        self.assertLess(ordered.index("d2e1"), ordered.index("a6b4"))

    def test_uci_orderer_keeps_unique_legal_set_when_guard_is_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_engine = Path(tmp) / "fake_uci.py"
            fake_engine.write_text(
                textwrap.dedent(
                    """
                    import sys
                    for line in sys.stdin:
                        command = line.strip()
                        if command == "uci":
                            print("id name Fake UCI", flush=True)
                            print("uciok", flush=True)
                        elif command == "isready":
                            print("readyok", flush=True)
                        elif command.startswith("go"):
                            print("bestmove d2e1", flush=True)
                        elif command == "quit":
                            break
                    """
                ).strip(),
                encoding="utf-8",
            )
            position = Position.from_fen(
                "9/9/5k3/N1r2b3/7b1/9/4n4/3A5/9/1p3K3 w - - 0 1"
            )
            legal = position.legal_moves()
            orderer = UciBestMoveOrderer(
                [sys.executable, str(fake_engine)],
                depth=1,
                fallback=HeuristicMoveOrderer(),
            )
            moves = orderer.order_moves(position, legal)

        ordered = [move.to_uci() for move in moves]
        legal_uci = {move.to_uci() for move in legal}
        self.assertEqual(ordered[0], "d2e1")
        self.assertEqual(len(ordered), len(legal_uci))
        self.assertEqual(set(ordered), legal_uci)

    def test_uci_orderer_promotes_pv_moves_after_bestmove(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_engine = Path(tmp) / "fake_uci.py"
            fake_engine.write_text(
                textwrap.dedent(
                    """
                    import sys
                    for line in sys.stdin:
                        command = line.strip()
                        if command == "uci":
                            print("id name Fake UCI", flush=True)
                            print("uciok", flush=True)
                        elif command == "isready":
                            print("readyok", flush=True)
                        elif command.startswith("go depth"):
                            print("info depth 1 multipv 1 score cp 0 pv a8g8 d0d9", flush=True)
                            print("info depth 1 multipv 2 score cp 0 pv d0d9 a8e8", flush=True)
                            print("bestmove a8g8", flush=True)
                        elif command == "quit":
                            break
                    """
                ).strip(),
                encoding="utf-8",
            )
            position = Position.from_fen(RED_WIN_IN_ONE_FEN)
            orderer = UciBestMoveOrderer([sys.executable, str(fake_engine)], depth=1)
            moves = orderer.order_moves(position, position.legal_moves())

        self.assertEqual([move.to_uci() for move in moves[:2]], ["a8g8", "d0d9"])

    def test_uci_orderer_configures_multipv_before_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_path = tmp_path / "commands.txt"
            fake_engine = tmp_path / "fake_uci.py"
            fake_engine.write_text(
                textwrap.dedent(
                    f"""
                    import sys
                    from pathlib import Path

                    log_path = Path({str(log_path)!r})
                    for line in sys.stdin:
                        command = line.strip()
                        if command.startswith("setoption") or command.startswith("go depth"):
                            previous = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
                            log_path.write_text(previous + command + "\\n", encoding="utf-8")
                        if command == "uci":
                            print("id name Fake UCI", flush=True)
                            print("uciok", flush=True)
                        elif command == "isready":
                            print("readyok", flush=True)
                        elif command.startswith("go depth"):
                            print("info depth 1 multipv 1 pv a8g8", flush=True)
                            print("info depth 1 multipv 2 pv d0d9", flush=True)
                            print("bestmove a8g8", flush=True)
                        elif command == "quit":
                            break
                    """
                ).strip(),
                encoding="utf-8",
            )
            position = Position.from_fen(RED_WIN_IN_ONE_FEN)
            orderer = UciBestMoveOrderer(
                [sys.executable, str(fake_engine)],
                depth=1,
                multipv=2,
            )
            moves = orderer.order_moves(position, position.legal_moves())
            commands = log_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual([move.to_uci() for move in moves[:2]], ["a8g8", "d0d9"])
        self.assertEqual(commands, ["setoption name MultiPV value 2", "go depth 1"])

    def test_uci_orderer_forwards_engine_options_before_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_path = tmp_path / "commands.txt"
            fake_engine = tmp_path / "fake_uci.py"
            fake_engine.write_text(
                textwrap.dedent(
                    f"""
                    import sys
                    from pathlib import Path

                    log_path = Path({str(log_path)!r})
                    for line in sys.stdin:
                        command = line.strip()
                        if command.startswith("setoption") or command.startswith("go depth"):
                            previous = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
                            log_path.write_text(previous + command + "\\n", encoding="utf-8")
                        if command == "uci":
                            print("id name Fake UCI", flush=True)
                            print("uciok", flush=True)
                        elif command == "isready":
                            print("readyok", flush=True)
                        elif command.startswith("go depth"):
                            print("bestmove a8g8", flush=True)
                        elif command == "quit":
                            break
                    """
                ).strip(),
                encoding="utf-8",
            )
            position = Position.from_fen(RED_WIN_IN_ONE_FEN)
            orderer = UciBestMoveOrderer(
                [sys.executable, str(fake_engine)],
                depth=1,
                options=(("Threads", "2"), ("Hash", "64")),
            )
            moves = orderer.order_moves(position, position.legal_moves())
            commands = log_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(moves[0].to_uci(), "a8g8")
        self.assertEqual(
            commands,
            [
                "setoption name Threads value 2",
                "setoption name Hash value 64",
                "go depth 1",
            ],
        )

    def test_uci_orderer_records_runtime_options_for_next_search(self) -> None:
        orderer = UciBestMoveOrderer([sys.executable, "-c", "pass"], depth=1)

        orderer.set_option("Hash", "128")
        orderer.set_option("MultiPV", "3")

        self.assertEqual(orderer.options, (("Hash", "128"),))
        self.assertEqual(orderer.multipv, 3)
        self.assertIn(
            "option name Hash type spin default 128 min 1 max 1048576",
            orderer.uci_options(),
        )
        self.assertIn(
            "option name MultiPV type spin default 3 min 1 max 256",
            orderer.uci_options(),
        )

    def test_uci_orderer_advertises_configured_fallback_option_defaults(self) -> None:
        orderer = UciBestMoveOrderer(
            [sys.executable, "-c", "pass"],
            depth=1,
            multipv=4,
            options=(("Threads", "2"), ("Hash", "512")),
        )

        self.assertEqual(
            orderer.uci_options(),
            (
                "option name Hash type spin default 512 min 1 max 1048576",
                "option name Threads type spin default 2 min 1 max 1024",
                "option name MultiPV type spin default 4 min 1 max 256",
            ),
        )

    def test_uci_orderer_uses_movetime_when_time_limited(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_path = tmp_path / "commands.txt"
            fake_engine = tmp_path / "fake_uci.py"
            fake_engine.write_text(
                textwrap.dedent(
                    f"""
                    import sys
                    from pathlib import Path

                    log_path = Path({str(log_path)!r})
                    for line in sys.stdin:
                        command = line.strip()
                        if command.startswith("go"):
                            previous = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
                            log_path.write_text(previous + command + "\\n", encoding="utf-8")
                        if command == "uci":
                            print("id name Fake UCI", flush=True)
                            print("uciok", flush=True)
                        elif command == "isready":
                            print("readyok", flush=True)
                        elif command.startswith("go"):
                            print("bestmove a8g8", flush=True)
                        elif command == "quit":
                            break
                    """
                ).strip(),
                encoding="utf-8",
            )
            position = Position.from_fen(RED_WIN_IN_ONE_FEN)
            orderer = UciBestMoveOrderer([sys.executable, str(fake_engine)], depth=4)
            moves = orderer.order_moves_with_time_limit(
                position,
                position.legal_moves(),
                123,
            )
            commands = log_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(moves[0].to_uci(), "a8g8")
        self.assertEqual(commands, ["go depth 4 movetime 123"])

    def test_persistent_uci_fallback_forwards_original_go_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_path = tmp_path / "commands.txt"
            fake_engine = tmp_path / "fake_uci.py"
            fake_engine.write_text(
                textwrap.dedent(
                    f"""
                    import sys
                    from pathlib import Path

                    log_path = Path({str(log_path)!r})
                    for line in sys.stdin:
                        command = line.strip()
                        if command.startswith("go"):
                            log_path.write_text(command, encoding="utf-8")
                        if command == "uci":
                            print("id name Fake UCI", flush=True)
                            print("uciok", flush=True)
                        elif command == "isready":
                            print("readyok", flush=True)
                        elif command.startswith("go"):
                            print("bestmove a8g8", flush=True)
                        elif command == "quit":
                            break
                    """
                ).strip(),
                encoding="utf-8",
            )
            position = Position.from_fen(RED_WIN_IN_ONE_FEN)
            orderer = PersistentUciBestMoveOrderer([sys.executable, str(fake_engine)])

            move = orderer.bestmove_with_go_command(
                position,
                position.legal_moves(),
                "go movetime 123",
            )
            orderer.close()

            command = log_path.read_text(encoding="utf-8")

        self.assertIsNotNone(move)
        self.assertEqual(move.to_uci(), "a8g8")
        self.assertEqual(command, "go movetime 123")

    def test_persistent_direct_fallback_skips_local_ordering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_engine = Path(tmp) / "fake_uci.py"
            fake_engine.write_text(
                textwrap.dedent(
                    """
                    import sys
                    for line in sys.stdin:
                        command = line.strip()
                        if command == "uci":
                            print("id name Fake UCI", flush=True)
                            print("uciok", flush=True)
                        elif command == "isready":
                            print("readyok", flush=True)
                        elif command.startswith("go"):
                            print("bestmove a8g8", flush=True)
                        elif command == "quit":
                            break
                    """
                ).strip(),
                encoding="utf-8",
            )
            position = Position.from_fen(RED_WIN_IN_ONE_FEN)
            fallback = CountingMoveOrderer("a8g8")
            orderer = PersistentUciBestMoveOrderer(
                [sys.executable, str(fake_engine)],
                fallback=fallback,  # type: ignore[arg-type]
            )
            try:
                move = orderer.bestmove_with_go_command(
                    position,
                    position.legal_moves(),
                    "go movetime 123",
                )
            finally:
                orderer.close()

        self.assertIsNotNone(move)
        self.assertEqual(move.to_uci(), "a8g8")
        self.assertEqual(fallback.calls, 0)

    def test_uci_orderer_forwards_subset_as_searchmoves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_path = tmp_path / "commands.txt"
            fake_engine = tmp_path / "fake_uci.py"
            fake_engine.write_text(
                textwrap.dedent(
                    f"""
                    import sys
                    from pathlib import Path

                    log_path = Path({str(log_path)!r})
                    for line in sys.stdin:
                        command = line.strip()
                        if command.startswith("go"):
                            previous = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
                            log_path.write_text(previous + command + "\\n", encoding="utf-8")
                        if command == "uci":
                            print("id name Fake UCI", flush=True)
                            print("uciok", flush=True)
                        elif command == "isready":
                            print("readyok", flush=True)
                        elif command.startswith("go"):
                            print("bestmove a8b8", flush=True)
                        elif command == "quit":
                            break
                    """
                ).strip(),
                encoding="utf-8",
            )
            position = Position.from_fen(RED_WIN_IN_ONE_FEN)
            subset = [move for move in position.legal_moves() if move.to_uci() == "a8b8"]
            orderer = UciBestMoveOrderer([sys.executable, str(fake_engine)], depth=2)
            moves = orderer.order_moves(position, subset)
            commands = log_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual([move.to_uci() for move in moves], ["a8b8"])
        self.assertEqual(commands, ["go searchmoves a8b8 depth 2"])

    def test_uci_orderer_forwards_subset_and_movetime_together(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_path = tmp_path / "commands.txt"
            fake_engine = tmp_path / "fake_uci.py"
            fake_engine.write_text(
                textwrap.dedent(
                    f"""
                    import sys
                    from pathlib import Path

                    log_path = Path({str(log_path)!r})
                    for line in sys.stdin:
                        command = line.strip()
                        if command.startswith("go"):
                            previous = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
                            log_path.write_text(previous + command + "\\n", encoding="utf-8")
                        if command == "uci":
                            print("id name Fake UCI", flush=True)
                            print("uciok", flush=True)
                        elif command == "isready":
                            print("readyok", flush=True)
                        elif command.startswith("go"):
                            print("bestmove a8b8", flush=True)
                        elif command == "quit":
                            break
                    """
                ).strip(),
                encoding="utf-8",
            )
            position = Position.from_fen(RED_WIN_IN_ONE_FEN)
            subset = [move for move in position.legal_moves() if move.to_uci() == "a8b8"]
            orderer = UciBestMoveOrderer([sys.executable, str(fake_engine)], depth=4)
            moves = orderer.order_moves_with_time_limit(position, subset, 123)
            commands = log_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual([move.to_uci() for move in moves], ["a8b8"])
        self.assertEqual(commands, ["go searchmoves a8b8 depth 4 movetime 123"])

    def test_uci_orderer_replays_history_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_engine = Path(tmp) / "fake_uci.py"
            fake_engine.write_text(
                textwrap.dedent(
                    """
                    import sys
                    seen_history = False
                    for line in sys.stdin:
                        command = line.strip()
                        if command == "uci":
                            print("id name Fake UCI", flush=True)
                            print("uciok", flush=True)
                        elif command == "isready":
                            print("readyok", flush=True)
                        elif command.startswith("position fen") and " moves " in command:
                            seen_history = True
                        elif command.startswith("go depth"):
                            print(f"bestmove {'a1a2' if seen_history else 'a1a0'}", flush=True)
                        elif command == "quit":
                            break
                    """
                ).strip(),
                encoding="utf-8",
            )
            state = GameState.from_uci_position(
                "position fen 4k4/9/9/9/9/9/4P4/9/R8/4K4 w - - 0 1 "
                "moves a1a2 e9e8 a2a1 e8e9"
            )
            orderer = UciBestMoveOrderer([sys.executable, str(fake_engine)], depth=1)
            moves = orderer.order_moves(state, state.legal_moves())

        self.assertEqual(moves[0].to_uci(), "a1a2")

    def test_persistent_uci_orderer_reuses_engine_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            start_log = tmp_path / "starts.txt"
            fake_engine = tmp_path / "fake_uci.py"
            fake_engine.write_text(
                textwrap.dedent(
                    f"""
                    import sys
                    from pathlib import Path

                    Path({str(start_log)!r}).write_text(
                        Path({str(start_log)!r}).read_text(encoding="utf-8") + "start\\n"
                        if Path({str(start_log)!r}).exists()
                        else "start\\n",
                        encoding="utf-8",
                    )

                    for line in sys.stdin:
                        command = line.strip()
                        if command == "uci":
                            print("id name Fake UCI", flush=True)
                            print("uciok", flush=True)
                        elif command == "isready":
                            print("readyok", flush=True)
                        elif command.startswith("go depth"):
                            print("bestmove i3i4", flush=True)
                        elif command == "quit":
                            break
                    """
                ).strip(),
                encoding="utf-8",
            )
            position = Position.start()
            orderer = PersistentUciBestMoveOrderer([sys.executable, str(fake_engine)], depth=1)
            try:
                first = orderer.order_moves(position, position.legal_moves())
                second = orderer.order_moves(position, position.legal_moves())
            finally:
                orderer.close()
            starts = start_log.read_text(encoding="utf-8").splitlines()

        self.assertEqual(first[0].to_uci(), "i3i4")
        self.assertEqual(second[0].to_uci(), "i3i4")
        self.assertEqual(starts, ["start"])

    def test_persistent_uci_orderer_forwards_subset_as_searchmoves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_path = tmp_path / "commands.txt"
            fake_engine = tmp_path / "fake_uci.py"
            fake_engine.write_text(
                textwrap.dedent(
                    f"""
                    import sys
                    from pathlib import Path

                    log_path = Path({str(log_path)!r})
                    for line in sys.stdin:
                        command = line.strip()
                        if command.startswith("go"):
                            previous = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
                            log_path.write_text(previous + command + "\\n", encoding="utf-8")
                        if command == "uci":
                            print("id name Fake UCI", flush=True)
                            print("uciok", flush=True)
                        elif command == "isready":
                            print("readyok", flush=True)
                        elif command.startswith("go"):
                            print("bestmove a8b8", flush=True)
                        elif command == "quit":
                            break
                    """
                ).strip(),
                encoding="utf-8",
            )
            position = Position.from_fen(RED_WIN_IN_ONE_FEN)
            subset = [move for move in position.legal_moves() if move.to_uci() == "a8b8"]
            orderer = PersistentUciBestMoveOrderer([sys.executable, str(fake_engine)], depth=2)
            try:
                moves = orderer.order_moves(position, subset)
            finally:
                orderer.close()
            commands = log_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual([move.to_uci() for move in moves], ["a8b8"])
        self.assertEqual(commands, ["go searchmoves a8b8 depth 2"])

    def test_persistent_uci_orderer_forwards_clear_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_path = tmp_path / "commands.txt"
            fake_engine = tmp_path / "fake_uci.py"
            fake_engine.write_text(
                textwrap.dedent(
                    f"""
                    import sys
                    from pathlib import Path

                    log_path = Path({str(log_path)!r})
                    for line in sys.stdin:
                        command = line.strip()
                        if command.startswith("setoption") or command == "isready":
                            previous = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
                            log_path.write_text(previous + command + "\\n", encoding="utf-8")
                        if command == "uci":
                            print("id name Fake UCI", flush=True)
                            print("uciok", flush=True)
                        elif command == "isready":
                            print("readyok", flush=True)
                        elif command.startswith("go depth"):
                            print("bestmove i3i4", flush=True)
                        elif command == "quit":
                            break
                    """
                ).strip(),
                encoding="utf-8",
            )
            orderer = PersistentUciBestMoveOrderer([sys.executable, str(fake_engine)], depth=1)
            try:
                orderer.clear_cache()
            finally:
                orderer.close()
            commands = log_path.read_text(encoding="utf-8").splitlines()

        self.assertIn("setoption name Clear Hash", commands)

    def test_persistent_uci_orderer_forwards_runtime_option(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_path = tmp_path / "commands.txt"
            fake_engine = tmp_path / "fake_uci.py"
            fake_engine.write_text(
                textwrap.dedent(
                    f"""
                    import sys
                    from pathlib import Path

                    log_path = Path({str(log_path)!r})
                    for line in sys.stdin:
                        command = line.strip()
                        if command.startswith("setoption") or command == "isready":
                            previous = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
                            log_path.write_text(previous + command + "\\n", encoding="utf-8")
                        if command == "uci":
                            print("id name Fake UCI", flush=True)
                            print("uciok", flush=True)
                        elif command == "isready":
                            print("readyok", flush=True)
                        elif command.startswith("go depth"):
                            print("bestmove i3i4", flush=True)
                        elif command == "quit":
                            break
                    """
                ).strip(),
                encoding="utf-8",
            )
            orderer = PersistentUciBestMoveOrderer([sys.executable, str(fake_engine)], depth=1)
            try:
                orderer.set_option("Hash", "128")
            finally:
                orderer.close()
            commands = log_path.read_text(encoding="utf-8").splitlines()

        self.assertIn("setoption name Hash value 128", commands)


if __name__ == "__main__":
    unittest.main()
