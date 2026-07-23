from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import context  # noqa: F401
from fixtures import RED_WIN_IN_ONE_FEN
from tools.uci_search_probe import REPORT_SCHEMA_VERSION, _cases_from_config, _config_digest


class UciSearchProbeTests(unittest.TestCase):
    def test_probe_rejects_prefixed_non_go_token(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "tools/uci_search_probe.py",
                "--engine",
                f"{sys.executable} -c pass",
                "--go",
                "gobad",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("--go must start with 'go'", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_probe_rejects_non_positive_timeout(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "tools/uci_search_probe.py",
                "--engine",
                f"{sys.executable} -c pass",
                "--timeout",
                "0",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("--timeout must be positive", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_probe_accepts_legal_bestmove_and_pv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = _write_search_engine(
                Path(tmp),
                bestmove="h2e2",
                pv_lines=["info depth 1 score cp 0 pv h2e2 h9g7"],
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/uci_search_probe.py",
                    "--engine",
                    f"{sys.executable} {engine}",
                    "--go",
                    "go depth 1",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        output = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue(output["valid"])
        self.assertEqual(output["report_type"], "uci_search_probe")
        self.assertEqual(output["report_schema_version"], REPORT_SCHEMA_VERSION)
        self.assertEqual(output["config_digest"], _config_digest(output["config"]))
        self.assertEqual(output["count"], 1)
        self.assertEqual(output["completed"], 1)
        self.assertEqual(output["phase"], "probe")
        self.assertEqual(output["summary"]["illegal_bestmoves"], 0)
        self.assertEqual(output["summary"]["illegal_pv_roots"], 0)
        self.assertEqual(output["summary"]["illegal_pv_lines"], 0)
        self.assertEqual(output["summary"]["missing_pv"], 0)
        self.assertEqual(output["summary"]["engine_errors"], 0)
        self.assertEqual(output["bestmove"], "h2e2")
        self.assertTrue(output["bestmove_valid"])
        self.assertEqual(output["pv_root_moves"], ["h2e2"])
        self.assertEqual(output["pv_lines"], [{"multipv": 1, "moves": ["h2e2", "h9g7"]}])
        self.assertEqual(output["illegal_pv_lines"], [])
        self.assertTrue(output["pv_available"])
        self.assertEqual(output["failure_reasons"], [])

    def test_probe_flags_illegal_bestmove(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = _write_search_engine(Path(tmp), bestmove="a0a0", pv_lines=[])

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/uci_search_probe.py",
                    "--engine",
                    f"{sys.executable} {engine}",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        output = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
        self.assertFalse(output["valid"])
        self.assertFalse(output["bestmove_valid"])
        self.assertEqual(output["bestmove_error"], "move is not legal in the local rules core")
        self.assertEqual(output["failure_reasons"], ["bestmove_illegal"])
        self.assertEqual(output["summary"]["illegal_bestmoves"], 1)

    def test_probe_flags_bestmove_and_pv_root_outside_searchmoves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = _write_search_engine(
                Path(tmp),
                bestmove="h2e2",
                pv_lines=["info depth 1 score cp 0 pv h2e2"],
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/uci_search_probe.py",
                    "--engine",
                    f"{sys.executable} {engine}",
                    "--go",
                    "go searchmoves a0a1 depth 1",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        output = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
        self.assertFalse(output["valid"])
        self.assertEqual(output["searchmoves"], ["a0a1"])
        self.assertEqual(output["searchmoves_legal_move_count"], 1)
        self.assertFalse(output["bestmove_valid"])
        self.assertFalse(output["bestmove_searchmoves_valid"])
        self.assertEqual(output["bestmove_error"], "move is outside go searchmoves")
        self.assertEqual(output["bestmove_searchmoves_error"], "move is outside go searchmoves")
        self.assertEqual(output["illegal_pv_roots"], [])
        self.assertEqual(
            output["searchmoves_pv_root_violations"],
            [
                {
                    "move": "h2e2",
                    "error": "move is outside go searchmoves",
                    "searchmoves": ["a0a1"],
                    "searchmoves_legal_move_count": 1,
                    "searchmoves_legal_moves_sample": ["a0a1"],
                }
            ],
        )
        self.assertEqual(
            output["failure_reasons"],
            ["bestmove_searchmoves_violation", "pv_root_searchmoves_violation"],
        )
        self.assertEqual(output["summary"]["illegal_bestmoves"], 0)
        self.assertEqual(output["summary"]["searchmoves_bestmove_violations"], 1)
        self.assertEqual(output["summary"]["searchmoves_pv_root_violations"], 1)

    def test_probe_allows_null_bestmove_when_searchmoves_have_no_legal_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = _write_search_engine(Path(tmp), bestmove="0000", pv_lines=[])

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/uci_search_probe.py",
                    "--engine",
                    f"{sys.executable} {engine}",
                    "--go",
                    "go searchmoves a0a0 depth 1",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        output = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue(output["valid"])
        self.assertTrue(output["bestmove_valid"])
        self.assertTrue(output["bestmove_searchmoves_valid"])
        self.assertIsNone(output["bestmove_error"])
        self.assertEqual(output["legal_move_count"], 44)
        self.assertEqual(output["searchmoves"], ["a0a0"])
        self.assertEqual(output["searchmoves_legal_move_count"], 0)
        self.assertEqual(output["failure_reasons"], [])

    def test_probe_can_require_pv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = _write_search_engine(Path(tmp), bestmove="h2e2", pv_lines=[])

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/uci_search_probe.py",
                    "--engine",
                    f"{sys.executable} {engine}",
                    "--require-pv",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        output = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
        self.assertFalse(output["valid"])
        self.assertTrue(output["config"]["require_pv"])
        self.assertFalse(output["pv_available"])
        self.assertEqual(output["failure_reasons"], ["pv_missing"])
        self.assertEqual(output["summary"]["missing_pv"], 1)

    def test_probe_ignores_info_string_pv_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = _write_search_engine(
                Path(tmp),
                bestmove="h2e2",
                pv_lines=["info string pv a0a0 diagnostic text"],
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/uci_search_probe.py",
                    "--engine",
                    f"{sys.executable} {engine}",
                    "--require-pv",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        output = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
        self.assertFalse(output["valid"])
        self.assertTrue(output["bestmove_valid"])
        self.assertEqual(output["pv_root_moves"], [])
        self.assertEqual(output["pv_lines"], [])
        self.assertEqual(output["illegal_pv_roots"], [])
        self.assertEqual(output["illegal_pv_lines"], [])
        self.assertEqual(output["failure_reasons"], ["pv_missing"])
        self.assertEqual(output["summary"]["missing_pv"], 1)

    def test_probe_flags_illegal_pv_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = _write_search_engine(
                Path(tmp),
                bestmove="h2e2",
                pv_lines=["info depth 1 multipv 1 pv a0a0 h9g7"],
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/uci_search_probe.py",
                    "--engine",
                    f"{sys.executable} {engine}",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        output = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
        self.assertFalse(output["valid"])
        self.assertEqual(output["pv_root_moves"], ["a0a0"])
        self.assertEqual(output["illegal_pv_roots"][0]["move"], "a0a0")
        self.assertEqual(
            output["illegal_pv_roots"][0]["error"],
            "move is not legal in the local rules core",
        )
        self.assertEqual(output["failure_reasons"], ["pv_root_illegal"])
        self.assertEqual(output["summary"]["illegal_pv_roots"], 1)

    def test_probe_flags_illegal_later_pv_move(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = _write_search_engine(
                Path(tmp),
                bestmove="h2e2",
                pv_lines=["info depth 1 multipv 1 pv h2e2 a0a0"],
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/uci_search_probe.py",
                    "--engine",
                    f"{sys.executable} {engine}",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        output = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
        self.assertFalse(output["valid"])
        self.assertEqual(output["pv_root_moves"], ["h2e2"])
        self.assertEqual(output["illegal_pv_roots"], [])
        self.assertEqual(output["pv_lines"], [{"multipv": 1, "moves": ["h2e2", "a0a0"]}])
        self.assertEqual(output["illegal_pv_lines"][0]["multipv"], 1)
        self.assertEqual(output["illegal_pv_lines"][0]["ply"], 2)
        self.assertEqual(output["illegal_pv_lines"][0]["move"], "a0a0")
        self.assertEqual(output["illegal_pv_lines"][0]["prefix"], ["h2e2"])
        self.assertEqual(
            output["illegal_pv_lines"][0]["error"],
            "move is not legal in the local rules core",
        )
        self.assertEqual(output["failure_reasons"], ["pv_line_illegal"])
        self.assertEqual(output["summary"]["illegal_pv_lines"], 1)

    def test_probe_flags_pv_continuing_after_local_terminal_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = _write_search_engine(
                Path(tmp),
                bestmove="a8a0",
                pv_lines=["info depth 1 multipv 1 pv a8a0 e9e8"],
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/uci_search_probe.py",
                    "--engine",
                    f"{sys.executable} {engine}",
                    "--fen",
                    RED_WIN_IN_ONE_FEN,
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        output = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
        self.assertFalse(output["valid"])
        self.assertTrue(output["bestmove_valid"])
        self.assertEqual(output["pv_root_moves"], ["a8a0"])
        self.assertEqual(output["illegal_pv_roots"], [])
        self.assertEqual(output["pv_lines"], [{"multipv": 1, "moves": ["a8a0", "e9e8"]}])
        illegal = output["illegal_pv_lines"][0]
        self.assertEqual(illegal["multipv"], 1)
        self.assertEqual(illegal["ply"], 2)
        self.assertEqual(illegal["move"], "e9e8")
        self.assertEqual(illegal["prefix"], ["a8a0"])
        self.assertEqual(illegal["error"], "PV continues after local rule result")
        self.assertEqual(illegal["local_result"], "red_win")
        self.assertEqual(illegal["rule_reason"], "no_legal_moves")
        self.assertTrue(illegal["adjudicated"])
        self.assertEqual(output["failure_reasons"], ["pv_line_illegal"])
        self.assertEqual(output["summary"]["illegal_pv_lines"], 1)

    def test_probe_corpus_reuses_engine_and_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            engine = _write_configurable_search_engine(tmp_path)
            config = tmp_path / "search_corpus.json"
            report = tmp_path / "probe.json"
            start_log = tmp_path / "starts.txt"
            config.write_text(
                json.dumps(
                    {
                        "positions": [
                            {"name": "start", "fen": _START_FEN},
                            {
                                "name": "history",
                                "position": "position startpos moves h2e2",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/uci_search_probe.py",
                    "--engine",
                    f"{sys.executable} {engine} {start_log}",
                    "--config",
                    str(config),
                    "--report",
                    str(report),
                    "--option",
                    "Threads=2",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)
            report_output = json.loads(report.read_text(encoding="utf-8"))
            starts = start_log.read_text(encoding="utf-8").splitlines()

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue(output["valid"])
        self.assertEqual(output, report_output)
        self.assertEqual(output["config"]["options"], [{"name": "Threads", "value": "2"}])
        self.assertEqual([entry["name"] for entry in output["entries"]], ["start", "history"])
        self.assertTrue(all(entry["bestmove_valid"] for entry in output["entries"]))
        self.assertEqual(starts, ["start"])

    def test_example_rule_corpus_loads_as_search_probe_cases(self) -> None:
        cases = _cases_from_config("configs/rule_corpus.example.json")
        names = [case.name for case in cases]
        commands = {case.name: case.position_command for case in cases}

        self.assertIn("quiet_threefold_repetition", names)
        self.assertIn("opening_two_plies", names)
        self.assertIn("developing_knights_four_plies", names)
        self.assertIn("central_attack_skirmish", names)
        self.assertIn("immediate_loss_fallback_guard", names)
        self.assertIn("pawn_capture_tension", names)
        self.assertIn("flying_general_file_pressure", names)
        self.assertIn("cannon_screen_tension", names)
        self.assertIn("blocked_knight_leg", names)
        self.assertIn("red_win_in_one_tactical", names)
        self.assertIn("protected_rook_chase_seed", names)
        self.assertIn("rule60_counter_is_not_terminal", names)
        self.assertIn("moves a1a2", commands["quiet_threefold_repetition"])
        self.assertIn("moves h2e2 h9g7", commands["opening_two_plies"])
        self.assertTrue(all(case.state.to_fen() for case in cases))
        self.assertTrue(all(case.state.legal_moves() for case in cases))


def _write_search_engine(directory: Path, *, bestmove: str, pv_lines: list[str]) -> Path:
    path = directory / "search_probe_uci.py"
    lines = [
        "import sys",
        "",
        "for line in sys.stdin:",
        "    command = line.strip()",
        '    if command == "uci":',
        '        print("id name Search Probe Fake", flush=True)',
        '        print("uciok", flush=True)',
        '    elif command == "isready":',
        '        print("readyok", flush=True)',
        '    elif command.startswith("setoption"):',
        "        pass",
        '    elif command == "ucinewgame":',
        "        pass",
        '    elif command.startswith("position "):',
        "        pass",
        '    elif command.startswith("go"):',
    ]
    if pv_lines:
        lines.extend([f'        print("{line}", flush=True)' for line in pv_lines])
    else:
        lines.append("        pass")
    lines.extend(
        [
            f'        print("bestmove {bestmove}", flush=True)',
            '    elif command == "quit":',
            "        break",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_configurable_search_engine(directory: Path) -> Path:
    path = directory / "configurable_search_probe_uci.py"
    path.write_text(
        textwrap.dedent(
            """
            import sys
            from pathlib import Path

            start_log = Path(sys.argv[1])
            start_log.write_text(
                start_log.read_text(encoding="utf-8") + "start\\n"
                if start_log.exists()
                else "start\\n",
                encoding="utf-8",
            )
            current_position = "position startpos"

            for line in sys.stdin:
                command = line.strip()
                if command == "uci":
                    print("id name Configurable Search Probe Fake", flush=True)
                    print("uciok", flush=True)
                elif command == "isready":
                    print("readyok", flush=True)
                elif command.startswith("setoption"):
                    pass
                elif command == "ucinewgame":
                    pass
                elif command.startswith("position "):
                    current_position = command
                elif command.startswith("go"):
                    if "moves h2e2" in current_position:
                        print("info depth 1 pv h9g7", flush=True)
                        print("bestmove h9g7", flush=True)
                    else:
                        print("info depth 1 pv h2e2", flush=True)
                        print("bestmove h2e2", flush=True)
                elif command == "quit":
                    break
            """
        ).strip(),
        encoding="utf-8",
    )
    return path


_START_FEN = "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1"


if __name__ == "__main__":
    unittest.main()
