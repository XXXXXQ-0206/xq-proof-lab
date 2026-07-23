from __future__ import annotations

import shlex
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import context  # noqa: F401
from xiangqi_core import Position
from xiangqi_evaluators import (
    UciEngine,
    UciEngineError,
    extract_go_searchmoves,
    extract_perft_divide,
    extract_pv_moves,
    split_engine_command,
)
from xiangqi_evaluators.uci_engine import _stop_grace_seconds


def _shell_command(parts: list[str]) -> str:
    if sys.platform == "win32":
        return subprocess.list2cmdline(parts)
    return shlex.join(parts)


class UciEngineTests(unittest.TestCase):
    def test_split_engine_command_handles_quoted_paths(self) -> None:
        parts = [sys.executable, str(Path("engine dir") / "fake uci.py"), "--flag", "two words"]

        self.assertEqual(split_engine_command(_shell_command(parts)), parts)

    def test_extract_pv_moves_uses_latest_pv_and_multipv_order(self) -> None:
        lines = (
            "info depth 1 pv a0a1 a9a8",
            "info depth 2 multipv 2 score cp 5 pv b0b1 b9b8",
            "info depth 2 multipv 1 score cp 10 pv c0c1 c9c8",
            "bestmove c0c1",
        )

        self.assertEqual(
            extract_pv_moves(lines),
            ("c0c1", "b0b1"),
        )

    def test_extract_pv_moves_ignores_info_string_pv_text(self) -> None:
        lines = (
            "info string pv a0a0 is diagnostic text",
            "debug pv b0b1",
            "info depth 1 score cp 0 pv h2e2 h9g7",
        )

        self.assertEqual(extract_pv_moves(lines), ("h2e2",))

    def test_extract_go_searchmoves_stops_at_later_go_limits(self) -> None:
        self.assertEqual(
            extract_go_searchmoves("go movetime 200 searchmoves a0a1 i0i1 depth 3"),
            ("a0a1", "i0i1"),
        )
        self.assertEqual(
            extract_go_searchmoves("go searchmoves a8b8 wtime 1000 btime 1000"),
            ("a8b8",),
        )
        self.assertEqual(
            extract_go_searchmoves("go searchmoves a8b8 mate 1"),
            ("a8b8",),
        )
        self.assertEqual(extract_go_searchmoves("gobad searchmoves a0a1"), ())

    def test_extract_perft_divide_accepts_colon_and_space_lines(self) -> None:
        lines = (
            "a0a1: 12",
            "b0c2 34",
            "info string ignored",
            "Nodes searched: 46",
        )

        self.assertEqual(extract_perft_divide(lines), {"a0a1": 12, "b0c2": 34})

    def test_go_rejects_prefixed_non_go_token(self) -> None:
        engine = UciEngine([sys.executable, "-c", "pass"])

        with self.assertRaises(ValueError):
            engine.go("gobad")

    def test_stop_grace_does_not_expand_uci_timeout_budget(self) -> None:
        self.assertEqual(_stop_grace_seconds(0.051), 0.1)
        self.assertEqual(_stop_grace_seconds(5.0), 0.1)

    def test_fake_engine_initialization_position_perft_and_bestmove(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_engine = Path(tmp) / "fake_uci.py"
            fake_engine.write_text(
                textwrap.dedent(
                    """
                    import sys

                    last_position = ""
                    for line in sys.stdin:
                        command = line.strip()
                        if command == "uci":
                            print("id name Fake Xiangqi Engine", flush=True)
                            print("uciok", flush=True)
                        elif command == "isready":
                            print("readyok", flush=True)
                        elif command.startswith("position "):
                            last_position = command
                        elif command == "go perft 1":
                            assert "position fen" in last_position
                            print("h2e2: 1", flush=True)
                            print("b0a2: 1", flush=True)
                            print("Nodes searched: 44", flush=True)
                        elif command == "go depth 1":
                            print("info depth 1 score cp 0 pv h2e2", flush=True)
                            print("bestmove h2e2", flush=True)
                        elif command == "quit":
                            break
                    """
                ).strip(),
                encoding="utf-8",
            )

            with UciEngine([sys.executable, str(fake_engine)], timeout=2.0) as engine:
                engine.initialize()
                engine.set_position(Position.START_FEN)
                perft_result = engine.go_perft(1)
                bestmove, lines = engine.go_depth(1)

        self.assertEqual(perft_result.nodes, 44)
        self.assertEqual(perft_result.divide, {"h2e2": 1, "b0a2": 1})
        self.assertEqual(bestmove, "h2e2")
        self.assertIn("bestmove h2e2", lines)
        self.assertEqual(extract_pv_moves(lines), ("h2e2",))

    def test_silent_engine_go_times_out(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_engine = Path(tmp) / "silent_uci.py"
            fake_engine.write_text(
                textwrap.dedent(
                    """
                    import sys
                    import time

                    for line in sys.stdin:
                        command = line.strip()
                        if command == "uci":
                            print("id name Silent Fake", flush=True)
                            print("uciok", flush=True)
                        elif command == "isready":
                            print("readyok", flush=True)
                        elif command.startswith("go"):
                            time.sleep(10)
                        elif command == "quit":
                            break
                    """
                ).strip(),
                encoding="utf-8",
            )

            with UciEngine([sys.executable, str(fake_engine)], timeout=2.0) as engine:
                engine.initialize()
                engine.timeout = 0.05
                with self.assertRaises(UciEngineError):
                    engine.go_depth(1)

    def test_go_timeout_sends_stop_and_accepts_late_bestmove(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_engine = Path(tmp) / "stop_recover_uci.py"
            fake_engine.write_text(
                textwrap.dedent(
                    """
                    import sys
                    import threading
                    import time

                    stop_event = threading.Event()
                    worker = None

                    def search():
                        deadline = time.time() + 1.0
                        while time.time() < deadline and not stop_event.is_set():
                            time.sleep(0.005)
                        if stop_event.is_set():
                            print("bestmove h2e2", flush=True)

                    for line in sys.stdin:
                        command = line.strip()
                        if command == "uci":
                            print("id name Stop Recover Fake", flush=True)
                            print("uciok", flush=True)
                        elif command == "isready":
                            print("readyok", flush=True)
                        elif command == "go depth 1":
                            print("info depth 1 score cp 0 pv h2e2", flush=True)
                            stop_event.clear()
                            worker = threading.Thread(target=search, daemon=True)
                            worker.start()
                        elif command == "stop":
                            stop_event.set()
                            if worker is not None:
                                worker.join(timeout=0.2)
                        elif command == "quit":
                            break
                    """
                ).strip(),
                encoding="utf-8",
            )

            with UciEngine([sys.executable, str(fake_engine)], timeout=2.0) as engine:
                engine.initialize()
                engine.timeout = 0.05
                bestmove, lines = engine.go_depth(1)

        self.assertEqual(bestmove, "h2e2")
        self.assertIn("bestmove h2e2", lines)
        self.assertIn("info depth 1 score cp 0 pv h2e2", lines)

    def test_go_timeout_reports_clean_engine_exit_when_stop_cannot_be_sent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_engine = Path(tmp) / "exit_on_go_uci.py"
            fake_engine.write_text(
                textwrap.dedent(
                    """
                    import sys
                    import time

                    for line in sys.stdin:
                        command = line.strip()
                        if command == "uci":
                            print("id name Exit On Go Fake", flush=True)
                            print("uciok", flush=True)
                        elif command == "isready":
                            print("readyok", flush=True)
                        elif command == "go depth 1":
                            time.sleep(0.1)
                            sys.exit(7)
                        elif command == "quit":
                            break
                    """
                ).strip(),
                encoding="utf-8",
            )

            with UciEngine([sys.executable, str(fake_engine)], timeout=0.05) as engine:
                engine.initialize()
                with self.assertRaises(UciEngineError) as exc_info:
                    engine.go_depth(1)

        self.assertIn("engine exited with code 7", str(exc_info.exception))

    def test_engine_with_noisy_stderr_still_returns_bestmove(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_engine = Path(tmp) / "noisy_uci.py"
            fake_engine.write_text(
                textwrap.dedent(
                    """
                    import sys

                    for line in sys.stdin:
                        command = line.strip()
                        if command == "uci":
                            print("id name Noisy Fake", flush=True)
                            print("uciok", flush=True)
                        elif command == "isready":
                            print("readyok", flush=True)
                        elif command == "go depth 1":
                            for _ in range(5000):
                                print("x" * 200, file=sys.stderr, flush=True)
                            print("bestmove h2e2", flush=True)
                        elif command == "quit":
                            break
                    """
                ).strip(),
                encoding="utf-8",
            )

            with UciEngine([sys.executable, str(fake_engine)], timeout=2.0) as engine:
                engine.initialize()
                bestmove, _lines = engine.go_depth(1)

        self.assertEqual(bestmove, "h2e2")

    def test_malformed_bestmove_line_raises_uci_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_engine = Path(tmp) / "bad_bestmove_uci.py"
            fake_engine.write_text(
                textwrap.dedent(
                    """
                    import sys

                    for line in sys.stdin:
                        command = line.strip()
                        if command == "uci":
                            print("id name Bad Bestmove Fake", flush=True)
                            print("uciok", flush=True)
                        elif command == "isready":
                            print("readyok", flush=True)
                        elif command == "go depth 1":
                            print("bestmove", flush=True)
                        elif command == "quit":
                            break
                    """
                ).strip(),
                encoding="utf-8",
            )

            with UciEngine([sys.executable, str(fake_engine)], timeout=2.0) as engine:
                engine.initialize()
                with self.assertRaises(UciEngineError):
                    engine.go_depth(1)


if __name__ == "__main__":
    unittest.main()
