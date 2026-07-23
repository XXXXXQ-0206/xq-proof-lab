from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import context  # noqa: F401
from fixtures import TERMINAL_RED_WIN_FEN
from tools.compare_perft import REPORT_SCHEMA_VERSION, _cases_from_config, _config_digest


class ComparePerftTests(unittest.TestCase):
    def test_pikafish_compatible_corpus_and_launcher_are_reproducible(self) -> None:
        root = Path(__file__).resolve().parents[1]
        launcher = root / "scripts" / "run_pikafish_baseline.cmd"
        bootstrap = root / "scripts" / "bootstrap_pikafish_baseline.ps1"
        baseline_path = root / "configs" / "pikafish_baseline.json"
        corpus_path = root / "configs" / "pikafish_perft_corpus.example.json"

        self.assertTrue(launcher.is_file())
        self.assertTrue(bootstrap.is_file())
        self.assertTrue(baseline_path.is_file())
        self.assertIn(
            "external\\pikafish-official-2026-01-02",
            launcher.read_text(encoding="utf-8"),
        )
        self.assertIn("Windows\\pikafish-avx512.exe", launcher.read_text(encoding="utf-8"))
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        self.assertEqual(baseline["release_tag"], "Pikafish-2026-01-02")
        self.assertEqual(baseline["architecture"], "x86-64-avx512")
        self.assertEqual(
            baseline["archive_sha256"],
            "84257063905615919fb4ee6a70273a94843bb6ec04c45e3ac706098838bc1a49",
        )
        self.assertIn("Pikafish.2026-01-02.7z", bootstrap.read_text(encoding="utf-8"))
        self.assertIn(baseline["archive_sha256"], bootstrap.read_text(encoding="utf-8"))
        self.assertIn("[string]$Destination", bootstrap.read_text(encoding="utf-8"))
        self.assertIn(
            "$Destination = Join-Path $PSScriptRoot",
            bootstrap.read_text(encoding="utf-8"),
        )

        parse_result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "$tokens = $null; $errors = $null; "
                    "[System.Management.Automation.Language.Parser]::ParseFile("
                    f"'{bootstrap}', [ref]$tokens, [ref]$errors) | Out-Null; "
                    "if ($errors.Count) { $errors | ForEach-Object Message; exit 1 }"
                ),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(parse_result.returncode, 0, parse_result.stderr or parse_result.stdout)

        corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
        positions = corpus["positions"]
        self.assertGreaterEqual(len(positions), 3)
        for position in positions:
            self.assertIn("name", position)
            if "fen" in position:
                self.assertEqual(position["fen"], _START_FEN)
            else:
                self.assertTrue(position["position"].startswith("position startpos"))

    def test_compare_perft_rejects_non_positive_timeout(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "tools/compare_perft.py",
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

    def test_compare_perft_rejects_negative_depth(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "tools/compare_perft.py",
                "--engine",
                f"{sys.executable} -c pass",
                "--depth",
                "-1",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("--depth must be non-negative", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_compare_perft_rejects_negative_default_depth_in_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "perft_corpus.json"
            config.write_text(
                json.dumps(
                    {
                        "defaults": {"depth": -1},
                        "positions": [{"name": "start", "fen": _START_FEN}],
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/compare_perft.py",
                    "--engine",
                    f"{sys.executable} -c pass",
                    "--config",
                    str(config),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 2)
        self.assertIn("defaults.depth must be non-negative", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_compare_perft_rejects_negative_case_depth_in_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "perft_corpus.json"
            config.write_text(
                json.dumps(
                    {
                        "positions": [
                            {"name": "bad_depth", "fen": _START_FEN, "depth": -1}
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/compare_perft.py",
                    "--engine",
                    f"{sys.executable} -c pass",
                    "--config",
                    str(config),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 2)
        self.assertIn("perft corpus item 'bad_depth' depth must be non-negative", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_compare_perft_succeeds_when_uci_engine_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_engine = _write_fake_perft_engine(Path(tmp), nodes=44)

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/compare_perft.py",
                    "--engine",
                    f"{sys.executable} {fake_engine}",
                    "--depth",
                    "1",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        output = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue(output["valid"])
        self.assertEqual(output["report_type"], "perft_compare")
        self.assertEqual(output["report_schema_version"], REPORT_SCHEMA_VERSION)
        self.assertEqual(output["config_digest"], _config_digest(output["config"]))
        self.assertEqual(output["count"], 1)
        self.assertEqual(output["completed"], 1)
        self.assertEqual(output["phase"], "compare")
        self.assertEqual(output["failures"], 0)
        self.assertEqual(output["summary"]["node_mismatches"], 0)
        self.assertEqual(output["summary"]["root_divide_mismatches"], 0)
        self.assertEqual(output["summary"]["root_divide_unavailable"], 1)
        self.assertEqual(output["summary"]["engine_errors"], 0)
        self.assertEqual(output["summary"]["invalid_cases"], [])
        self.assertEqual(output["config"]["engine"], f"{sys.executable} {fake_engine}")
        self.assertEqual(output["config"]["depth"], 1)
        self.assertEqual(output["config"]["starts"][0]["name"], "single")
        self.assertEqual(output["local_nodes"], 44)
        self.assertEqual(output["engine_nodes"], 44)
        self.assertFalse(output["root_divide_available"])
        self.assertIsNone(output["root_divide_valid"])
        self.assertFalse(output["require_root_divide"])
        self.assertEqual(output["failure_reasons"], [])

    def test_compare_perft_can_require_root_divide(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_engine = _write_fake_perft_engine(Path(tmp), nodes=44)

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/compare_perft.py",
                    "--engine",
                    f"{sys.executable} {fake_engine}",
                    "--depth",
                    "1",
                    "--require-root-divide",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        output = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
        self.assertFalse(output["valid"])
        self.assertTrue(output["config"]["require_root_divide"])
        self.assertFalse(output["root_divide_available"])
        self.assertFalse(output["root_divide_valid"])
        self.assertTrue(output["require_root_divide"])
        self.assertEqual(output["failure_reasons"], ["root_divide_unavailable"])
        self.assertEqual(output["summary"]["root_divide_mismatches"], 0)
        self.assertEqual(output["summary"]["root_divide_unavailable"], 1)

    def test_compare_perft_does_not_require_root_divide_at_depth_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_engine = _write_fake_perft_engine(Path(tmp), nodes=1)

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/compare_perft.py",
                    "--engine",
                    f"{sys.executable} {fake_engine}",
                    "--depth",
                    "0",
                    "--require-root-divide",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        output = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue(output["valid"])
        self.assertFalse(output["root_divide_applicable"])
        self.assertFalse(output["root_divide_available"])
        self.assertIsNone(output["root_divide_valid"])
        self.assertTrue(output["require_root_divide"])
        self.assertEqual(output["failure_reasons"], [])
        self.assertEqual(output["summary"]["root_divide_unavailable"], 0)

    def test_compare_perft_does_not_require_root_divide_without_legal_root_moves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_engine = _write_computing_perft_engine(tmp_path)

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/compare_perft.py",
                    "--engine",
                    f"{sys.executable} {fake_engine} {tmp_path / 'starts.txt'}",
                    "--fen",
                    TERMINAL_RED_WIN_FEN,
                    "--depth",
                    "1",
                    "--require-root-divide",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        output = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue(output["valid"])
        self.assertEqual(output["local_nodes"], 0)
        self.assertEqual(output["engine_nodes"], 0)
        self.assertFalse(output["root_divide_applicable"])
        self.assertFalse(output["root_divide_available"])
        self.assertIsNone(output["root_divide_valid"])
        self.assertEqual(output["failure_reasons"], [])
        self.assertEqual(output["summary"]["root_divide_unavailable"], 0)

    def test_compare_perft_fails_when_uci_engine_differs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_engine = _write_fake_perft_engine(Path(tmp), nodes=43)

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/compare_perft.py",
                    "--engine",
                    f"{sys.executable} {fake_engine}",
                    "--depth",
                    "1",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        output = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
        self.assertFalse(output["valid"])
        self.assertEqual(output["count"], 1)
        self.assertEqual(output["completed"], 1)
        self.assertEqual(output["phase"], "compare")
        self.assertEqual(output["failures"], 1)
        self.assertEqual(output["summary"]["node_mismatches"], 1)
        self.assertEqual(output["summary"]["root_divide_mismatches"], 0)
        self.assertEqual(output["summary"]["engine_errors"], 0)
        self.assertEqual(output["summary"]["invalid_cases"], ["single"])
        self.assertEqual(output["local_nodes"], 44)
        self.assertEqual(output["engine_nodes"], 43)
        self.assertEqual(output["failure_reasons"], ["node_mismatch"])

    def test_compare_perft_fails_when_root_divide_differs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_engine = _write_fake_divide_engine(Path(tmp))

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/compare_perft.py",
                    "--engine",
                    f"{sys.executable} {fake_engine}",
                    "--depth",
                    "1",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        output = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
        self.assertFalse(output["valid"])
        self.assertEqual(output["count"], 1)
        self.assertEqual(output["completed"], 1)
        self.assertEqual(output["phase"], "compare")
        self.assertEqual(output["failures"], 1)
        self.assertEqual(output["summary"]["node_mismatches"], 0)
        self.assertEqual(output["summary"]["root_divide_mismatches"], 1)
        self.assertEqual(output["summary"]["engine_errors"], 0)
        self.assertEqual(output["summary"]["invalid_cases"], ["single"])
        self.assertEqual(output["local_nodes"], 44)
        self.assertEqual(output["engine_nodes"], 44)
        self.assertTrue(output["root_divide_available"])
        self.assertFalse(output["root_divide_valid"])
        self.assertEqual(output["failure_reasons"], ["root_divide_mismatch"])
        self.assertIn({"move": "h2e2", "local_nodes": 1, "engine_nodes": 2}, output["root_mismatches"])

    def test_compare_perft_accepts_single_position_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_engine = _write_computing_perft_engine(tmp_path)

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/compare_perft.py",
                    "--engine",
                    f"{sys.executable} {fake_engine} {tmp_path / 'starts.txt'}",
                    "--position",
                    "position startpos moves h2e2",
                    "--depth",
                    "1",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue(output["valid"])
        self.assertEqual(output["config"]["position"], "position startpos moves h2e2")
        self.assertIsNone(output["config"]["fen"])
        self.assertEqual(output["position_command"], "position startpos moves h2e2")
        self.assertIn("moves h2e2", output["config"]["starts"][0]["position"])

    def test_compare_perft_corpus_reuses_engine_and_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_engine = _write_computing_perft_engine(tmp_path)
            start_log = tmp_path / "starts.txt"
            config = tmp_path / "perft_corpus.json"
            report = tmp_path / "report.json"
            config.write_text(
                json.dumps(
                    {
                        "defaults": {"depth": 1},
                        "positions": [
                            {"name": "start", "fen": _START_FEN},
                            {
                                "name": "history",
                                "position": "position startpos moves h2e2",
                            },
                            {
                                "name": "rule60_counter_only",
                                "fen": "4k4/9/9/9/9/9/4P4/9/9/4K4 w - - 120 1",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/compare_perft.py",
                    "--engine",
                    f"{sys.executable} {fake_engine} {start_log}",
                    "--config",
                    str(config),
                    "--report",
                    str(report),
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
        self.assertEqual(output["report_type"], "perft_compare")
        self.assertEqual(output["report_schema_version"], REPORT_SCHEMA_VERSION)
        self.assertEqual(output["config_digest"], _config_digest(output["config"]))
        self.assertEqual(output["count"], 3)
        self.assertEqual(output["completed"], 3)
        self.assertEqual(output["phase"], "compare")
        self.assertEqual(output["failures"], 0)
        self.assertEqual(output["summary"]["node_mismatches"], 0)
        self.assertEqual(output["summary"]["root_divide_mismatches"], 0)
        self.assertEqual(output["summary"]["root_divide_unavailable"], 0)
        self.assertEqual(output["summary"]["engine_errors"], 0)
        self.assertEqual(output["summary"]["invalid_cases"], [])
        self.assertEqual([entry["name"] for entry in output["entries"]], ["start", "history", "rule60_counter_only"])
        self.assertIn("moves h2e2", output["entries"][1]["position_command"])
        self.assertTrue(all(entry["root_divide_available"] for entry in output["entries"]))
        self.assertTrue(all(entry["root_divide_valid"] for entry in output["entries"]))
        self.assertTrue(all(entry["failure_reasons"] == [] for entry in output["entries"]))
        self.assertEqual(output["entries"][0]["local_root_nodes"]["h2e2"], 1)
        self.assertEqual(starts, ["start"])

    def test_compare_perft_corpus_fails_when_any_position_differs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_engine = _write_computing_perft_engine(tmp_path, mismatch_fen="4k4")
            start_log = tmp_path / "starts.txt"
            config = tmp_path / "perft_corpus.json"
            config.write_text(
                json.dumps(
                    {
                        "positions": [
                            {"name": "start", "fen": _START_FEN, "depth": 1},
                            {
                                "name": "mismatch",
                                "fen": "4k4/9/9/9/9/9/4P4/9/9/4K4 w - - 120 1",
                                "depth": 1,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/compare_perft.py",
                    "--engine",
                    f"{sys.executable} {fake_engine} {start_log}",
                    "--config",
                    str(config),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
        self.assertFalse(output["valid"])
        self.assertEqual(output["report_type"], "perft_compare")
        self.assertEqual(output["completed"], 2)
        self.assertEqual(output["phase"], "compare")
        self.assertEqual(output["failures"], 1)
        self.assertEqual(output["summary"]["node_mismatches"], 1)
        self.assertEqual(output["summary"]["root_divide_mismatches"], 0)
        self.assertEqual(output["summary"]["engine_errors"], 0)
        self.assertEqual(output["summary"]["invalid_cases"], ["mismatch"])
        self.assertTrue(output["entries"][0]["valid"])
        self.assertFalse(output["entries"][1]["valid"])
        self.assertEqual(output["entries"][1]["failure_reasons"], ["node_mismatch"])

    def test_compare_perft_corpus_records_case_engine_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_engine = _write_case_error_perft_engine(tmp_path)
            config = tmp_path / "perft_corpus.json"
            config.write_text(
                json.dumps(
                    {
                        "positions": [
                            {"name": "start", "fen": _START_FEN, "depth": 1},
                            {
                                "name": "engine_error",
                                "fen": "4k4/9/9/9/9/9/4P4/9/9/4K4 w - - 120 1",
                                "depth": 1,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/compare_perft.py",
                    "--engine",
                    f"{sys.executable} {fake_engine}",
                    "--config",
                    str(config),
                    "--timeout",
                    "1",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
        self.assertFalse(output["valid"])
        self.assertEqual(output["failures"], 1)
        self.assertEqual(output["summary"]["node_mismatches"], 0)
        self.assertEqual(output["summary"]["root_divide_unavailable"], 1)
        self.assertEqual(output["summary"]["engine_errors"], 1)
        self.assertEqual(output["summary"]["invalid_cases"], ["engine_error"])
        self.assertTrue(output["entries"][0]["valid"])
        self.assertFalse(output["entries"][1]["valid"])
        self.assertEqual(output["entries"][1]["failure_reasons"], ["engine_error"])
        self.assertIsNone(output["entries"][1]["engine_nodes"])
        self.assertTrue(output["entries"][1]["engine_error"])
        self.assertEqual(output["entries"][1]["phase"], "compare")
        self.assertEqual(output["entries"][1]["local_nodes"], 4)

    def test_compare_perft_reports_initialize_failures_as_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            crashing_engine = _write_crashing_init_perft_engine(tmp_path)
            config = tmp_path / "perft_corpus.json"
            config.write_text(
                json.dumps(
                    {
                        "positions": [
                            {"name": "start", "fen": _START_FEN, "depth": 1},
                            {
                                "name": "history",
                                "position": "position startpos moves h2e2",
                                "depth": 1,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/compare_perft.py",
                    "--engine",
                    f"{sys.executable} {crashing_engine}",
                    "--config",
                    str(config),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
        self.assertFalse(output["valid"])
        self.assertEqual(output["phase"], "initialize")
        self.assertEqual(output["completed"], 0)
        self.assertEqual(output["count"], 2)
        self.assertEqual(output["failures"], 2)
        self.assertTrue(output["error"])
        self.assertEqual(output["summary"]["node_mismatches"], 0)
        self.assertEqual(output["summary"]["root_divide_unavailable"], 0)
        self.assertEqual(output["summary"]["engine_errors"], 2)
        self.assertEqual(output["summary"]["invalid_cases"], ["start", "history"])
        self.assertEqual([entry["phase"] for entry in output["entries"]], ["initialize", "initialize"])
        self.assertTrue(all(entry["failure_reasons"] == ["engine_error"] for entry in output["entries"]))

    def test_example_perft_corpus_loads_rule_sensitive_cases(self) -> None:
        cases = _cases_from_config("configs/perft_corpus.example.json", cli_depth=1)
        names = [case.name for case in cases]
        depths = {case.name: case.depth for case in cases}
        commands = {case.name: case.position_command for case in cases}

        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(depths["startpos_depth2"], 2)
        self.assertIn("red_win_in_one_tactical", names)
        self.assertIn("immediate_loss_fallback_guard", names)
        self.assertIn("flying_general_file_pressure", names)
        self.assertIn("cannon_screen_tension", names)
        self.assertIn("blocked_knight_leg", names)
        self.assertIn("advisor_palace_constraints", names)
        self.assertIn("elephant_river_boundary", names)
        self.assertIn("elephant_eye_pressure", names)
        self.assertIn("cannon_screen_capture_tension", names)
        self.assertIn("protected_rook_chase_seed", names)
        self.assertIn("repetition_history_preserved", names)
        self.assertIn("moves a1a2", commands["repetition_history_preserved"])
        self.assertTrue(all(case.position.legal_moves() for case in cases))


def _write_fake_perft_engine(directory: Path, nodes: int) -> Path:
    fake_engine = directory / "fake_perft_uci.py"
    fake_engine.write_text(
        textwrap.dedent(
            f"""
            import sys

            for line in sys.stdin:
                command = line.strip()
                if command == "uci":
                    print("id name Fake Perft Engine", flush=True)
                    print("uciok", flush=True)
                elif command == "isready":
                    print("readyok", flush=True)
                elif command.startswith("position "):
                    pass
                elif command.startswith("go perft"):
                    print("Nodes searched: {nodes}", flush=True)
                elif command == "quit":
                    break
            """
        ).strip(),
        encoding="utf-8",
    )
    return fake_engine


def _write_fake_divide_engine(directory: Path) -> Path:
    fake_engine = directory / "fake_divide_uci.py"
    fake_engine.write_text(
        textwrap.dedent(
            """
            import sys

            for line in sys.stdin:
                command = line.strip()
                if command == "uci":
                    print("id name Fake Divide Engine", flush=True)
                    print("uciok", flush=True)
                elif command == "isready":
                    print("readyok", flush=True)
                elif command.startswith("position "):
                    pass
                elif command.startswith("go perft"):
                    print("h2e2: 2", flush=True)
                    print("Nodes searched: 44", flush=True)
                elif command == "quit":
                    break
            """
        ).strip(),
        encoding="utf-8",
    )
    return fake_engine


def _write_computing_perft_engine(directory: Path, mismatch_fen: str | None = None) -> Path:
    fake_engine = directory / "computing_perft_uci.py"
    fake_engine.write_text(
        textwrap.dedent(
            f"""
            import sys
            from pathlib import Path

            sys.path.insert(0, str(Path.cwd() / "src"))
            sys.path.insert(0, str(Path.cwd() / "tools"))

            from xiangqi_core import GameState, Position
            from perft import perft

            start_log = Path(sys.argv[1])
            start_log.write_text(
                start_log.read_text(encoding="utf-8") + "start\\n"
                if start_log.exists()
                else "start\\n",
                encoding="utf-8",
            )
            state = GameState.from_position(Position.start())
            mismatch_fen = {mismatch_fen!r}

            for line in sys.stdin:
                command = line.strip()
                if command == "uci":
                    print("id name Computing Fake Perft Engine", flush=True)
                    print("uciok", flush=True)
                elif command == "isready":
                    print("readyok", flush=True)
                elif command.startswith("position "):
                    state = GameState.from_uci_position(command)
                elif command.startswith("go perft"):
                    depth = int(command.split()[-1])
                    if depth > 0:
                        for move in sorted(state.position.legal_moves(), key=lambda candidate: candidate.to_uci()):
                            child_nodes = perft(state.position.make_move(move), depth - 1)
                            print(f"{{move.to_uci()}}: {{child_nodes}}", flush=True)
                    nodes = perft(state.position, depth)
                    if mismatch_fen and mismatch_fen in state.to_fen():
                        nodes += 1
                    print(f"Nodes searched: {{nodes}}", flush=True)
                elif command == "quit":
                    break
            """
        ).strip(),
        encoding="utf-8",
    )
    return fake_engine


def _write_case_error_perft_engine(directory: Path) -> Path:
    fake_engine = directory / "case_error_perft_uci.py"
    fake_engine.write_text(
        textwrap.dedent(
            """
            import sys

            current_position = ""
            for line in sys.stdin:
                command = line.strip()
                if command == "uci":
                    print("id name Case Error Perft Engine", flush=True)
                    print("uciok", flush=True)
                elif command == "isready":
                    print("readyok", flush=True)
                elif command.startswith("position "):
                    current_position = command
                elif command.startswith("go perft"):
                    if "4k4/9/9/9/9/9/4P4/9/9/4K4" in current_position:
                        print("info string simulated perft failure", flush=True)
                    else:
                        print("Nodes searched: 44", flush=True)
                elif command == "quit":
                    break
            """
        ).strip(),
        encoding="utf-8",
    )
    return fake_engine


def _write_crashing_init_perft_engine(directory: Path) -> Path:
    fake_engine = directory / "crashing_init_perft_uci.py"
    fake_engine.write_text(
        textwrap.dedent(
            """
            import sys

            for line in sys.stdin:
                command = line.strip()
                if command == "uci":
                    sys.exit(7)
            """
        ).strip(),
        encoding="utf-8",
    )
    return fake_engine


_START_FEN = "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1"


if __name__ == "__main__":
    unittest.main()
