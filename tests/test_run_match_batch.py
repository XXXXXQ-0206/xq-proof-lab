from __future__ import annotations

import json
import shlex
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import context  # noqa: F401
from fixtures import RED_WIN_IN_ONE_FEN, TERMINAL_RED_WIN_FEN
from tools.compare_perft import (
    REPORT_SCHEMA_VERSION as PERFT_REPORT_SCHEMA_VERSION,
    _config_digest as _perft_config_digest,
)
from tools.play_uci_match import REPORT_SCHEMA_VERSION, _config_digest
from tools.run_match_batch import _game_report_from_dict, _resume_relevant_config
from tools.uci_search_probe import (
    REPORT_SCHEMA_VERSION as SEARCH_PROBE_REPORT_SCHEMA_VERSION,
    _config_digest as _search_probe_config_digest,
)


def _shell_command(parts: list[str]) -> str:
    if sys.platform == "win32":
        return subprocess.list2cmdline(parts)
    return shlex.join(parts)


class RunMatchBatchTests(unittest.TestCase):
    def test_batch_runner_writes_batch_and_aggregate_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            engine = _write_idle_engine(tmp_path)
            batch_dir = tmp_path / "batches"
            report = tmp_path / "aggregate.json"

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/run_match_batch.py",
                    "--red",
                    _shell_command([sys.executable, str(engine)]),
                    "--red-name",
                    "candidate",
                    "--black",
                    _shell_command([sys.executable, str(engine)]),
                    "--black-name",
                    "baseline",
                    "--fen",
                    TERMINAL_RED_WIN_FEN,
                    "--batches",
                    "2",
                    "--games-per-batch",
                    "1",
                    "--alternate-colors",
                    "--max-plies",
                    "1",
                    "--batch-dir",
                    str(batch_dir),
                    "--report",
                    str(report),
                    "--accept-candidate",
                    "candidate",
                    "--accept-baseline",
                    "baseline",
                    "--accept-min-games",
                    "2",
                    "--accept-min-elo-lower",
                    "-10000",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)
            written = json.loads(report.read_text(encoding="utf-8"))
            batch_reports_exist = [
                Path(batch["report"]).exists() for batch in output["batches"]
            ]
            first_batch = json.loads(Path(output["batches"][0]["report"]).read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(output, written)
        self.assertTrue(output["valid"])
        self.assertEqual(output["report_type"], "uci_match_batch_aggregate")
        self.assertEqual(output["report_schema_version"], REPORT_SCHEMA_VERSION)
        self.assertEqual(
            output["config_digest"],
            _config_digest(_resume_relevant_config(output["config"]), scope="resume_relevant"),
        )
        self.assertEqual(output["config"]["red_name"], "candidate")
        self.assertEqual(output["config"]["black_name"], "baseline")
        self.assertEqual(output["config"]["batches"], 2)
        self.assertEqual(output["config"]["games_per_batch"], 1)
        self.assertTrue(output["config"]["alternate_colors"])
        self.assertEqual(output["config"]["starts"][0]["name"], "single")
        self.assertEqual(first_batch["config"]["red_name"], "candidate")
        self.assertEqual(first_batch["config"]["black_name"], "baseline")
        self.assertEqual(first_batch["report_type"], "uci_match_batch")
        self.assertEqual(first_batch["report_schema_version"], REPORT_SCHEMA_VERSION)
        self.assertEqual(
            first_batch["config_digest"],
            _config_digest(_resume_relevant_config(first_batch["config"]), scope="resume_relevant"),
        )
        self.assertEqual(output["summary"]["games"], 2)
        self.assertEqual(output["summary"]["reasons"], {"no_legal_moves": 2})
        self.assertEqual(output["summary"]["engines"]["candidate"]["score_rate"], 0.5)
        self.assertEqual(output["acceptance"]["evidence_class"], "closed")
        self.assertTrue(output["acceptance"]["closed_elo_eligible"])
        self.assertEqual(
            output["acceptance"]["closed_elo_diff"],
            output["acceptance"]["candidate_elo_diff"],
        )
        self.assertEqual(
            output["acceptance"]["closed_elo_diff_ci95"],
            output["acceptance"]["candidate_elo_diff_ci95"],
        )
        self.assertEqual(
            output["summary"]["engines"]["candidate"]["reasons"],
            {"no_legal_moves": 2},
        )
        self.assertEqual([batch["status"] for batch in output["batches"]], ["ran", "ran"])
        self.assertEqual(batch_reports_exist, [True, True])

    def test_batch_runner_includes_per_batch_summary_in_aggregate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            legal_engine = _write_legal_engine(tmp_path)
            batch_dir = tmp_path / "batches"

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/run_match_batch.py",
                    "--red",
                    _shell_command([sys.executable, "tools/proof_uci.py", "--max-ply", "0"]),
                    "--red-name",
                    "proof",
                    "--black",
                    _shell_command([sys.executable, str(legal_engine)]),
                    "--black-name",
                    "legal",
                    "--batches",
                    "1",
                    "--games-per-batch",
                    "1",
                    "--max-plies",
                    "1",
                    "--batch-dir",
                    str(batch_dir),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)
            batch_output = json.loads((batch_dir / "match_batch_0001.json").read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(output["summary"]["fallback_moves"], 1)
        self.assertEqual(output["summary"]["reasons"], {"max_plies": 1})
        self.assertEqual(output["batches"][0]["summary"], batch_output["summary"])
        self.assertEqual(output["batches"][0]["summary"]["fallback_moves"], 1)
        self.assertEqual(output["batches"][0]["summary"]["reasons"], {"max_plies": 1})
        self.assertEqual(output["batches"][0]["summary"]["engines"]["proof"]["fallback_moves"], 1)

    def test_batch_acceptance_gate_rejects_unfinished_match_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            legal_engine = _write_legal_engine(tmp_path)
            batch_dir = tmp_path / "batches"

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/run_match_batch.py",
                    "--red",
                    _shell_command([sys.executable, str(legal_engine)]),
                    "--red-name",
                    "candidate",
                    "--black",
                    _shell_command([sys.executable, str(legal_engine)]),
                    "--black-name",
                    "baseline",
                    "--batches",
                    "1",
                    "--games-per-batch",
                    "1",
                    "--max-plies",
                    "1",
                    "--batch-dir",
                    str(batch_dir),
                    "--accept-candidate",
                    "candidate",
                    "--accept-baseline",
                    "baseline",
                    "--accept-min-games",
                    "0",
                    "--accept-min-elo-lower",
                    "-10000",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)
            batch_output = json.loads(
                (batch_dir / "match_batch_0001.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
        self.assertTrue(output["valid"])
        self.assertFalse(output["accepted"])
        self.assertFalse(output["acceptance"]["accepted"])
        self.assertEqual(output["summary"]["unfinished"], 1)
        self.assertEqual(batch_output["summary"]["unfinished"], 1)
        self.assertEqual(output["acceptance"]["candidate_unfinished_games"], 1)
        self.assertEqual(output["acceptance"]["baseline_unfinished_games"], 1)
        self.assertEqual(output["summary"]["engines"]["candidate"]["unfinished"], 1)
        self.assertEqual(output["summary"]["engines"]["baseline"]["unfinished"], 1)
        self.assertIn("candidate_unfinished_games", output["acceptance"]["reasons"])
        self.assertIn("baseline_unfinished_games", output["acceptance"]["reasons"])

    def test_batch_acceptance_gate_rejects_candidate_emergency_moves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            emergency_engine = _write_emergency_engine(tmp_path)
            legal_engine = _write_legal_engine(tmp_path)
            batch_dir = tmp_path / "batches"

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/run_match_batch.py",
                    "--red",
                    _shell_command([sys.executable, str(emergency_engine)]),
                    "--red-name",
                    "candidate",
                    "--black",
                    _shell_command([sys.executable, str(legal_engine)]),
                    "--black-name",
                    "baseline",
                    "--fen",
                    RED_WIN_IN_ONE_FEN,
                    "--batches",
                    "1",
                    "--games-per-batch",
                    "1",
                    "--max-plies",
                    "2",
                    "--batch-dir",
                    str(batch_dir),
                    "--accept-candidate",
                    "candidate",
                    "--accept-baseline",
                    "baseline",
                    "--accept-min-games",
                    "1",
                    "--accept-min-elo-lower",
                    "-10000",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
        self.assertTrue(output["valid"])
        self.assertFalse(output["accepted"])
        self.assertEqual(output["summary"]["games"], 1)
        self.assertEqual(output["summary"]["engines"]["candidate"]["wins"], 1)
        self.assertEqual(output["summary"]["engines"]["candidate"]["emergency_moves"], 1)
        self.assertEqual(output["acceptance"]["candidate_scored_games"], 1)
        self.assertEqual(output["acceptance"]["candidate_emergency_moves"], 1)
        self.assertIn("candidate_emergency_moves", output["acceptance"]["reasons"])

    def test_batch_acceptance_gate_rejects_candidate_unclassified_moves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            candidate_engine = _write_legal_engine(tmp_path)
            baseline_engine = _write_legal_engine(tmp_path)
            batch_dir = tmp_path / "batches"

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/run_match_batch.py",
                    "--red",
                    _shell_command([sys.executable, str(candidate_engine)]),
                    "--red-name",
                    "candidate",
                    "--black",
                    _shell_command([sys.executable, str(baseline_engine)]),
                    "--black-name",
                    "baseline",
                    "--fen",
                    RED_WIN_IN_ONE_FEN,
                    "--batches",
                    "1",
                    "--games-per-batch",
                    "1",
                    "--max-plies",
                    "2",
                    "--batch-dir",
                    str(batch_dir),
                    "--accept-candidate",
                    "candidate",
                    "--accept-baseline",
                    "baseline",
                    "--accept-min-games",
                    "1",
                    "--accept-min-elo-lower",
                    "-10000",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
        self.assertTrue(output["valid"])
        self.assertFalse(output["accepted"])
        self.assertEqual(output["summary"]["games"], 1)
        self.assertEqual(output["summary"]["engines"]["candidate"]["wins"], 1)
        self.assertEqual(output["summary"]["engines"]["candidate"]["unclassified_moves"], 1)
        self.assertEqual(output["acceptance"]["candidate_scored_games"], 1)
        self.assertEqual(output["acceptance"]["candidate_unclassified_moves"], 1)
        self.assertIn("candidate_unclassified_moves", output["acceptance"]["reasons"])

    def test_batch_acceptance_gate_rejects_inconsistent_proof_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            candidate_engine = _write_inconsistent_proof_engine(tmp_path)
            baseline_engine = _write_legal_engine(tmp_path)
            batch_dir = tmp_path / "batches"

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/run_match_batch.py",
                    "--red",
                    _shell_command([sys.executable, str(candidate_engine)]),
                    "--red-name",
                    "candidate",
                    "--black",
                    _shell_command([sys.executable, str(baseline_engine)]),
                    "--black-name",
                    "baseline",
                    "--fen",
                    RED_WIN_IN_ONE_FEN,
                    "--batches",
                    "1",
                    "--games-per-batch",
                    "1",
                    "--max-plies",
                    "2",
                    "--batch-dir",
                    str(batch_dir),
                    "--accept-candidate",
                    "candidate",
                    "--accept-baseline",
                    "baseline",
                    "--accept-min-games",
                    "1",
                    "--accept-min-elo-lower",
                    "-10000",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
        self.assertTrue(output["valid"])
        self.assertFalse(output["accepted"])
        self.assertEqual(output["summary"]["games"], 1)
        self.assertEqual(output["summary"]["engines"]["candidate"]["wins"], 1)
        self.assertEqual(output["summary"]["invalid_proof_telemetry_moves"], 1)
        self.assertEqual(
            output["summary"]["engines"]["candidate"]["invalid_proof_telemetry_moves"],
            1,
        )
        self.assertEqual(
            output["batches"][0]["summary"]["engines"]["candidate"][
                "invalid_proof_telemetry_moves"
            ],
            1,
        )
        self.assertEqual(output["acceptance"]["candidate_scored_games"], 1)
        self.assertEqual(output["acceptance"]["candidate_invalid_proof_telemetry_moves"], 1)
        self.assertIn("candidate_invalid_proof_telemetry", output["acceptance"]["reasons"])

    def test_batch_runner_resume_loads_existing_batches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            engine = _write_idle_engine(tmp_path)
            batch_dir = tmp_path / "batches"
            common = [
                sys.executable,
                "tools/run_match_batch.py",
                "--red",
                _shell_command([sys.executable, str(engine)]),
                "--red-name",
                "candidate",
                "--black",
                _shell_command([sys.executable, str(engine)]),
                "--black-name",
                "baseline",
                "--fen",
                TERMINAL_RED_WIN_FEN,
                "--games-per-batch",
                "1",
                "--alternate-colors",
                "--max-plies",
                "1",
                "--batch-dir",
                str(batch_dir),
            ]

            first = subprocess.run(
                common + ["--batches", "1"],
                text=True,
                capture_output=True,
                check=False,
            )
            second = subprocess.run(
                common + ["--batches", "2", "--resume"],
                text=True,
                capture_output=True,
                check=False,
            )

            first_output = json.loads(first.stdout)
            second_output = json.loads(second.stdout)

        self.assertEqual(first.returncode, 0, first.stderr or first.stdout)
        self.assertEqual(second.returncode, 0, second.stderr or second.stdout)
        self.assertEqual(first_output["summary"]["games"], 1)
        self.assertEqual(second_output["summary"]["games"], 2)
        self.assertEqual([batch["status"] for batch in second_output["batches"]], ["loaded", "ran"])
        self.assertEqual(second_output["games"][0], first_output["games"][0])
        self.assertEqual(_game_report_from_dict({**first_output["games"][0], "start_tags": ["smoke", "resume"]}).start_tags, ("smoke", "resume"))

    def test_batch_runner_resume_allows_changed_preflight_search_report_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            engine = _write_idle_engine(tmp_path)
            search_engine = _write_search_preflight_engine(tmp_path, bestmove="e0d0")
            search_config = _write_single_search_config(tmp_path)
            batch_dir = tmp_path / "batches"
            common = [
                sys.executable,
                "tools/run_match_batch.py",
                "--red",
                _shell_command([sys.executable, str(engine)]),
                "--black",
                _shell_command([sys.executable, str(engine)]),
                "--fen",
                TERMINAL_RED_WIN_FEN,
                "--games-per-batch",
                "1",
                "--max-plies",
                "1",
                "--batch-dir",
                str(batch_dir),
                "--preflight-search-engine",
                _shell_command([sys.executable, str(search_engine)]),
                "--preflight-search-config",
                str(search_config),
                "--preflight-search-require-pv",
            ]

            first = subprocess.run(
                common + ["--batches", "1", "--preflight-search-report", str(tmp_path / "first.json")],
                text=True,
                capture_output=True,
                check=False,
            )
            second = subprocess.run(
                common
                + [
                    "--batches",
                    "2",
                    "--resume",
                    "--preflight-search-report",
                    str(tmp_path / "second.json"),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            first_output = json.loads(first.stdout)
            second_output = json.loads(second.stdout)

        self.assertEqual(first.returncode, 0, first.stderr or first.stdout)
        self.assertEqual(second.returncode, 0, second.stderr or second.stdout)
        self.assertEqual([batch["status"] for batch in second_output["batches"]], ["loaded", "ran"])
        self.assertEqual(second_output["games"][0], first_output["games"][0])

    def test_batch_runner_resume_rejects_incompatible_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            engine = _write_idle_engine(tmp_path)
            batch_dir = tmp_path / "batches"
            common = [
                sys.executable,
                "tools/run_match_batch.py",
                "--red",
                _shell_command([sys.executable, str(engine)]),
                "--red-name",
                "candidate",
                "--black",
                _shell_command([sys.executable, str(engine)]),
                "--black-name",
                "baseline",
                "--fen",
                TERMINAL_RED_WIN_FEN,
                "--games-per-batch",
                "1",
                "--max-plies",
                "1",
                "--batch-dir",
                str(batch_dir),
            ]

            first = subprocess.run(
                common + ["--batches", "1"],
                text=True,
                capture_output=True,
                check=False,
            )
            second = subprocess.run(
                common + ["--batches", "2", "--resume", "--go", "go depth 2"],
                text=True,
                capture_output=True,
                check=False,
            )
            second_batch_exists = (batch_dir / "match_batch_0002.json").exists()

        self.assertEqual(first.returncode, 0, first.stderr or first.stdout)
        self.assertNotEqual(second.returncode, 0, second.stderr or second.stdout)
        self.assertIn("--resume refuses", second.stderr)
        self.assertIn("config.go", second.stderr)
        self.assertNotIn("Traceback", second.stderr)
        self.assertFalse(second_batch_exists)

    def test_batch_runner_resume_rejects_digest_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            engine = _write_idle_engine(tmp_path)
            batch_dir = tmp_path / "batches"
            common = [
                sys.executable,
                "tools/run_match_batch.py",
                "--red",
                _shell_command([sys.executable, str(engine)]),
                "--black",
                _shell_command([sys.executable, str(engine)]),
                "--fen",
                TERMINAL_RED_WIN_FEN,
                "--games-per-batch",
                "1",
                "--max-plies",
                "1",
                "--batch-dir",
                str(batch_dir),
            ]

            first = subprocess.run(
                common + ["--batches", "1"],
                text=True,
                capture_output=True,
                check=False,
            )
            batch_path = batch_dir / "match_batch_0001.json"
            stored = json.loads(batch_path.read_text(encoding="utf-8"))
            stored["config_digest"]["value"] = "0" * 64
            batch_path.write_text(
                json.dumps(stored, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            second = subprocess.run(
                common + ["--batches", "2", "--resume"],
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(first.returncode, 0, first.stderr or first.stdout)
        self.assertNotEqual(second.returncode, 0, second.stderr or second.stdout)
        self.assertIn("--resume refuses", second.stderr)
        self.assertIn("stored config digest", second.stderr)
        self.assertNotIn("Traceback", second.stderr)

    def test_batch_runner_resume_rejects_changed_game_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            engine = _write_idle_engine(tmp_path)
            batch_dir = tmp_path / "batches"
            common = [
                sys.executable,
                "tools/run_match_batch.py",
                "--red",
                _shell_command([sys.executable, str(engine)]),
                "--black",
                _shell_command([sys.executable, str(engine)]),
                "--fen",
                TERMINAL_RED_WIN_FEN,
                "--games-per-batch",
                "1",
                "--max-plies",
                "1",
                "--batch-dir",
                str(batch_dir),
            ]
            first = subprocess.run(
                common + ["--batches", "1"],
                text=True,
                capture_output=True,
                check=False,
            )
            batch_path = batch_dir / "match_batch_0001.json"
            stored = json.loads(batch_path.read_text(encoding="utf-8"))
            stored["games"][0]["result"] = "black_win"
            batch_path.write_text(
                json.dumps(stored, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            second = subprocess.run(
                common + ["--batches", "2", "--resume"],
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(first.returncode, 0, first.stderr or first.stdout)
        self.assertNotEqual(second.returncode, 0, second.stderr or second.stdout)
        self.assertIn("stored games digest", second.stderr)
        self.assertNotIn("Traceback", second.stderr)

    def test_batch_runner_resume_rejects_missing_schema_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            engine = _write_idle_engine(tmp_path)
            batch_dir = tmp_path / "batches"
            common = [
                sys.executable,
                "tools/run_match_batch.py",
                "--red",
                _shell_command([sys.executable, str(engine)]),
                "--black",
                _shell_command([sys.executable, str(engine)]),
                "--fen",
                TERMINAL_RED_WIN_FEN,
                "--games-per-batch",
                "1",
                "--max-plies",
                "1",
                "--batch-dir",
                str(batch_dir),
            ]

            first = subprocess.run(
                common + ["--batches", "1"],
                text=True,
                capture_output=True,
                check=False,
            )
            batch_path = batch_dir / "match_batch_0001.json"
            stored = json.loads(batch_path.read_text(encoding="utf-8"))
            stored.pop("report_type")
            stored.pop("report_schema_version")
            batch_path.write_text(
                json.dumps(stored, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            second = subprocess.run(
                common + ["--batches", "2", "--resume"],
                text=True,
                capture_output=True,
                check=False,
            )
            second_batch_exists = (batch_dir / "match_batch_0002.json").exists()

        self.assertEqual(first.returncode, 0, first.stderr or first.stdout)
        self.assertNotEqual(second.returncode, 0, second.stderr or second.stdout)
        self.assertIn("--resume refuses", second.stderr)
        self.assertIn("stored report_type", second.stderr)
        self.assertNotIn("Traceback", second.stderr)
        self.assertFalse(second_batch_exists)

    def test_batch_runner_resume_rejects_missing_config_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            engine = _write_idle_engine(tmp_path)
            batch_dir = tmp_path / "batches"
            common = [
                sys.executable,
                "tools/run_match_batch.py",
                "--red",
                _shell_command([sys.executable, str(engine)]),
                "--black",
                _shell_command([sys.executable, str(engine)]),
                "--fen",
                TERMINAL_RED_WIN_FEN,
                "--games-per-batch",
                "1",
                "--max-plies",
                "1",
                "--batch-dir",
                str(batch_dir),
            ]

            first = subprocess.run(
                common + ["--batches", "1"],
                text=True,
                capture_output=True,
                check=False,
            )
            batch_path = batch_dir / "match_batch_0001.json"
            stored = json.loads(batch_path.read_text(encoding="utf-8"))
            stored.pop("config_digest")
            batch_path.write_text(
                json.dumps(stored, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            second = subprocess.run(
                common + ["--batches", "2", "--resume"],
                text=True,
                capture_output=True,
                check=False,
            )
            second_batch_exists = (batch_dir / "match_batch_0002.json").exists()

        self.assertEqual(first.returncode, 0, first.stderr or first.stdout)
        self.assertNotEqual(second.returncode, 0, second.stderr or second.stdout)
        self.assertIn("--resume refuses", second.stderr)
        self.assertIn("missing a config digest", second.stderr)
        self.assertNotIn("Traceback", second.stderr)
        self.assertFalse(second_batch_exists)

    def test_batch_runner_resume_rejects_partial_batch_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            engine = _write_idle_engine(tmp_path)
            batch_dir = tmp_path / "batches"
            common = [
                sys.executable,
                "tools/run_match_batch.py",
                "--red",
                _shell_command([sys.executable, str(engine)]),
                "--black",
                _shell_command([sys.executable, str(engine)]),
                "--fen",
                TERMINAL_RED_WIN_FEN,
                "--games-per-batch",
                "2",
                "--max-plies",
                "1",
                "--batch-dir",
                str(batch_dir),
            ]

            first = subprocess.run(
                common + ["--batches", "1"],
                text=True,
                capture_output=True,
                check=False,
            )
            batch_path = batch_dir / "match_batch_0001.json"
            stored = json.loads(batch_path.read_text(encoding="utf-8"))
            stored["games"] = stored["games"][:1]
            stored["summary"]["games"] = 1
            batch_path.write_text(
                json.dumps(stored, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            second = subprocess.run(
                common + ["--batches", "2", "--resume"],
                text=True,
                capture_output=True,
                check=False,
            )
            second_batch_exists = (batch_dir / "match_batch_0002.json").exists()

        self.assertEqual(first.returncode, 0, first.stderr or first.stdout)
        self.assertNotEqual(second.returncode, 0, second.stderr or second.stdout)
        self.assertIn("--resume refuses", second.stderr)
        self.assertIn("expected 2 games, found 1", second.stderr)
        self.assertNotIn("Traceback", second.stderr)
        self.assertFalse(second_batch_exists)

    def test_batch_runner_resume_loads_engine_error_batches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            crashing_engine = _write_crashing_init_engine(tmp_path)
            idle_engine = _write_idle_engine(tmp_path)
            batch_dir = tmp_path / "batches"
            common = [
                sys.executable,
                "tools/run_match_batch.py",
                "--red",
                _shell_command([sys.executable, str(crashing_engine)]),
                "--red-name",
                "badinit",
                "--black",
                _shell_command([sys.executable, str(idle_engine)]),
                "--black-name",
                "idle",
                "--batches",
                "1",
                "--games-per-batch",
                "1",
                "--max-plies",
                "1",
                "--batch-dir",
                str(batch_dir),
            ]

            first = subprocess.run(common, text=True, capture_output=True, check=False)
            second = subprocess.run(
                common + ["--resume"],
                text=True,
                capture_output=True,
                check=False,
            )

            first_output = json.loads(first.stdout)
            second_output = json.loads(second.stdout)

        self.assertEqual(first.returncode, 1, first.stderr or first.stdout)
        self.assertEqual(second.returncode, 1, second.stderr or second.stdout)
        self.assertEqual([batch["status"] for batch in second_output["batches"]], ["loaded"])
        self.assertEqual(first_output["games"], second_output["games"])
        self.assertEqual(second_output["summary"]["engine_errors"], 1)
        self.assertEqual(second_output["summary"]["reasons"], {"engine_error": 1})
        self.assertEqual(second_output["games"][0]["reason"], "engine_error")
        self.assertEqual(second_output["games"][0]["records"], [])

    def test_batch_runner_resume_preserves_proof_store_telemetry(self) -> None:
        report = _game_report_from_dict(
            {
                "game": 1,
                "red_engine": "candidate",
                "black_engine": "baseline",
                "start_name": "single",
                "start_fen": TERMINAL_RED_WIN_FEN,
                "start_position": f"position fen {TERMINAL_RED_WIN_FEN}",
                "start_moves": [],
                "valid": True,
                "result": "unfinished",
                "reason": "max_plies",
                "plies": 1,
                "moves": ["e0d0"],
                "illegal": None,
                "records": [
                    {
                        "ply": 1,
                        "side": "red",
                        "engine": "candidate",
                        "position": f"position fen {TERMINAL_RED_WIN_FEN}",
                        "go_command": "go depth 1",
                        "bestmove": "e0d0",
                        "source": "proof_store",
                        "status": "proven",
                        "reason": "proof",
                        "nodes": 3,
                        "max_ply": 4,
                        "node_limit": 5000,
                        "elapsed_ms": 1,
                        "red_time_before_ms": None,
                        "black_time_before_ms": None,
                        "red_time_after_ms": None,
                        "black_time_after_ms": None,
                        "time_limit_ms": None,
                        "time_limit_reached": False,
                        "resolved_store_hits": 1,
                        "resolved_store_misses": 2,
                        "proof_store_saved": True,
                        "proof_store_save_error": "disk_full",
                        "external_ordering_elapsed_ms": 12,
                        "proof_search_elapsed_ms": 34,
                        "total_search_elapsed_ms": 56,
                        "engine_lines": [
                            "info string source=proof_store status=proven proof_store_saved=1 proof_store_save_error=disk_full"
                        ],
                    }
                ],
                "red_options": [],
                "black_options": [],
                "forfeit": None,
            }
        )

        self.assertEqual(len(report.records), 1)
        self.assertEqual(report.records[0].max_ply, 4)
        self.assertEqual(report.records[0].node_limit, 5000)
        self.assertTrue(report.records[0].proof_store_saved)
        self.assertEqual(report.records[0].proof_store_save_error, "disk_full")
        self.assertEqual(report.records[0].external_ordering_elapsed_ms, 12)
        self.assertEqual(report.records[0].proof_search_elapsed_ms, 34)
        self.assertEqual(report.records[0].total_search_elapsed_ms, 56)

    def test_batch_runner_forwards_uci_options(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            red_engine, red_log = _write_option_logging_engine(tmp_path, "red")
            black_engine, black_log = _write_option_logging_engine(tmp_path, "black")

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/run_match_batch.py",
                    "--red",
                    _shell_command([sys.executable, str(red_engine)]),
                    "--red-name",
                    "red",
                    "--red-option",
                    "Threads=2",
                    "--black",
                    _shell_command([sys.executable, str(black_engine)]),
                    "--black-name",
                    "black",
                    "--black-option",
                    "Hash=64",
                    "--position",
                    "position startpos moves h2e2",
                    "--batches",
                    "1",
                    "--games-per-batch",
                    "1",
                    "--max-plies",
                    "1",
                    "--batch-dir",
                    str(tmp_path / "batches"),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)
            red_options = red_log.read_text(encoding="utf-8").splitlines()
            black_options = black_log.read_text(encoding="utf-8").splitlines()

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue(output["valid"])
        self.assertEqual(red_options, ["setoption name Threads value 2"])
        self.assertEqual(black_options, ["setoption name Hash value 64"])
        self.assertEqual(
            output["config"]["red_options"],
            [{"name": "Threads", "value": "2"}],
        )
        self.assertEqual(
            output["config"]["black_options"],
            [{"name": "Hash", "value": "64"}],
        )
        self.assertEqual(
            output["games"][0]["red_options"],
            [{"name": "Threads", "value": "2"}],
        )
        self.assertEqual(
            output["games"][0]["black_options"],
            [{"name": "Hash", "value": "64"}],
        )

    def test_batch_runner_forwards_clock_options(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            engine, log_path = _write_go_logging_engine(tmp_path, bestmove="i3i4")
            batch_dir = tmp_path / "batches"

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/run_match_batch.py",
                    "--red",
                    _shell_command([sys.executable, str(engine)]),
                    "--black",
                    _shell_command([sys.executable, str(engine)]),
                    "--batches",
                    "1",
                    "--games-per-batch",
                    "1",
                    "--max-plies",
                    "1",
                    "--batch-dir",
                    str(batch_dir),
                    "--red-time-ms",
                    "1000",
                    "--black-time-ms",
                    "2000",
                    "--red-increment-ms",
                    "10",
                    "--black-increment-ms",
                    "20",
                    "--movestogo",
                    "30",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)
            first_batch = json.loads((batch_dir / "match_batch_0001.json").read_text(encoding="utf-8"))
            go_commands = log_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue(output["valid"])
        self.assertEqual(
            output["config"]["clock"],
            {
                "red_time_ms": 1000,
                "black_time_ms": 2000,
                "red_increment_ms": 10,
                "black_increment_ms": 20,
                "movestogo": 30,
            },
        )
        self.assertEqual(first_batch["config"]["clock"], output["config"]["clock"])
        self.assertEqual(
            go_commands,
            ["go depth 1 wtime 1000 btime 2000 winc 10 binc 20 movestogo 30"],
        )
        self.assertEqual(output["games"][0]["records"][0]["go_command"], go_commands[0])

    def test_batch_runner_alternate_colors_counts_across_suite_starts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            engine = _write_idle_engine(tmp_path)
            suite_path = tmp_path / "suite.json"
            suite_path.write_text(
                json.dumps(
                    {
                        "positions": [
                            {"name": "terminal_1", "fen": TERMINAL_RED_WIN_FEN},
                            {"name": "terminal_2", "fen": TERMINAL_RED_WIN_FEN},
                        ]
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/run_match_batch.py",
                    "--red",
                    _shell_command([sys.executable, str(engine)]),
                    "--red-name",
                    "candidate",
                    "--black",
                    _shell_command([sys.executable, str(engine)]),
                    "--black-name",
                    "baseline",
                    "--suite",
                    str(suite_path),
                    "--batches",
                    "1",
                    "--games-per-batch",
                    "1",
                    "--alternate-colors",
                    "--max-plies",
                    "1",
                    "--batch-dir",
                    str(tmp_path / "batches"),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(
            [(game["start_name"], game["red_engine"], game["black_engine"]) for game in output["games"]],
            [
                ("terminal_1", "candidate", "baseline"),
                ("terminal_2", "baseline", "candidate"),
            ],
        )

    def test_batch_runner_filters_suite_starts_by_tag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            engine = _write_idle_engine(tmp_path)
            suite_path = tmp_path / "suite.json"
            suite_path.write_text(
                json.dumps(
                    {
                        "positions": [
                            {
                                "name": "smoke_start",
                                "fen": TERMINAL_RED_WIN_FEN,
                                "tags": ["smoke", "terminal"],
                            },
                            {
                                "name": "full_start",
                                "fen": TERMINAL_RED_WIN_FEN,
                                "tags": ["full", "terminal"],
                            },
                        ]
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/run_match_batch.py",
                    "--red",
                    _shell_command([sys.executable, str(engine)]),
                    "--black",
                    _shell_command([sys.executable, str(engine)]),
                    "--suite",
                    str(suite_path),
                    "--suite-tag",
                    "smoke",
                    "--batches",
                    "1",
                    "--games-per-batch",
                    "2",
                    "--max-plies",
                    "1",
                    "--batch-dir",
                    str(tmp_path / "batches"),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)
            first_batch = json.loads(
                (tmp_path / "batches" / "match_batch_0001.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(output["summary"]["games"], 2)
        self.assertEqual(output["config"]["suite_tags"], ["smoke"])
        self.assertEqual([start["name"] for start in output["config"]["starts"]], ["smoke_start"])
        self.assertEqual([game["start_name"] for game in output["games"]], ["smoke_start", "smoke_start"])
        self.assertEqual(first_batch["config"]["suite_tags"], ["smoke"])

    def test_batch_runner_rejects_empty_suite_tag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            engine = _write_idle_engine(tmp_path)
            suite_path = tmp_path / "suite.json"
            suite_path.write_text(
                json.dumps({"positions": [{"name": "terminal", "fen": TERMINAL_RED_WIN_FEN}]}),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/run_match_batch.py",
                    "--red",
                    _shell_command([sys.executable, str(engine)]),
                    "--black",
                    _shell_command([sys.executable, str(engine)]),
                    "--suite",
                    str(suite_path),
                    "--suite-tag",
                    "   ",
                    "--batches",
                    "1",
                    "--games-per-batch",
                    "1",
                    "--max-plies",
                    "1",
                    "--batch-dir",
                    str(tmp_path / "batches"),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 2)
        self.assertIn("--suite-tag values must be non-empty", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_batch_runner_accepts_terminal_short_fens_from_cli_and_suite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            engine = _write_idle_engine(tmp_path)
            short_fen = " ".join(TERMINAL_RED_WIN_FEN.split()[:2])
            suite_path = tmp_path / "suite.json"
            suite_path.write_text(
                json.dumps({"positions": [{"name": "terminal", "fen": short_fen}]}, ensure_ascii=False, indent=2)
                + "\n",
                encoding="utf-8",
            )
            batch_dir_cli = tmp_path / "batches_cli"
            batch_dir_suite = tmp_path / "batches_suite"

            cli_result = subprocess.run(
                [
                    sys.executable,
                    "tools/run_match_batch.py",
                    "--red",
                    _shell_command([sys.executable, str(engine)]),
                    "--black",
                    _shell_command([sys.executable, str(engine)]),
                    "--fen",
                    short_fen,
                    "--batches",
                    "1",
                    "--games-per-batch",
                    "1",
                    "--max-plies",
                    "1",
                    "--batch-dir",
                    str(batch_dir_cli),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            suite_result = subprocess.run(
                [
                    sys.executable,
                    "tools/run_match_batch.py",
                    "--red",
                    _shell_command([sys.executable, str(engine)]),
                    "--black",
                    _shell_command([sys.executable, str(engine)]),
                    "--suite",
                    str(suite_path),
                    "--batches",
                    "1",
                    "--games-per-batch",
                    "1",
                    "--max-plies",
                    "1",
                    "--batch-dir",
                    str(batch_dir_suite),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            cli_output = json.loads(cli_result.stdout)
            suite_output = json.loads(suite_result.stdout)

        self.assertEqual(cli_result.returncode, 0, cli_result.stderr or cli_result.stdout)
        self.assertEqual(suite_result.returncode, 0, suite_result.stderr or suite_result.stdout)
        self.assertTrue(cli_output["valid"])
        self.assertTrue(suite_output["valid"])
        self.assertEqual(cli_output["games"][0]["reason"], "no_legal_moves")
        self.assertEqual(suite_output["games"][0]["reason"], "no_legal_moves")
        self.assertEqual(suite_output["games"][0]["start_name"], "terminal")

    def test_batch_runner_runs_perft_preflight_before_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            match_engine = _write_idle_engine(tmp_path)
            perft_engine = _write_fake_perft_engine(tmp_path, nodes=44)
            perft_config = _write_single_perft_config(tmp_path)
            preflight_report = tmp_path / "preflight.json"
            batch_dir = tmp_path / "batches"

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/run_match_batch.py",
                    "--red",
                    _shell_command([sys.executable, str(match_engine)]),
                    "--black",
                    _shell_command([sys.executable, str(match_engine)]),
                    "--fen",
                    TERMINAL_RED_WIN_FEN,
                    "--batches",
                    "1",
                    "--games-per-batch",
                    "1",
                    "--max-plies",
                    "1",
                    "--batch-dir",
                    str(batch_dir),
                    "--preflight-perft-engine",
                    _shell_command([sys.executable, str(perft_engine)]),
                    "--preflight-perft-config",
                    str(perft_config),
                    "--preflight-perft-report",
                    str(preflight_report),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)
            preflight_output = json.loads(preflight_report.read_text(encoding="utf-8"))
            batch_report_exists = (batch_dir / "match_batch_0001.json").exists()

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue(output["valid"])
        self.assertTrue(output["preflight_perft"]["valid"])
        self.assertEqual(output["preflight_perft"], preflight_output)
        self.assertEqual(output["preflight_perft"]["report_type"], "perft_compare")
        self.assertEqual(output["preflight_perft"]["report_schema_version"], PERFT_REPORT_SCHEMA_VERSION)
        self.assertEqual(
            output["preflight_perft"]["config_digest"],
            _perft_config_digest(output["preflight_perft"]["report_config"]),
        )
        self.assertEqual(output["preflight_perft"]["count"], 1)
        self.assertEqual(output["preflight_perft"]["completed"], 1)
        self.assertIsNone(output["preflight_perft"]["phase"])
        self.assertEqual(output["preflight_perft"]["entries"][0]["local_nodes"], 44)
        self.assertEqual(output["preflight_perft"]["entries"][0]["engine_nodes"], 44)
        self.assertEqual(output["preflight_perft"]["summary"]["node_mismatches"], 0)
        self.assertEqual(output["preflight_perft"]["summary"]["root_divide_mismatches"], 0)
        self.assertEqual(output["preflight_perft"]["summary"]["engine_errors"], 0)
        self.assertEqual([batch["status"] for batch in output["batches"]], ["ran"])
        self.assertTrue(batch_report_exists)

    def test_batch_runner_skips_matches_when_perft_preflight_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            match_engine = _write_idle_engine(tmp_path)
            perft_engine = _write_fake_perft_engine(tmp_path, nodes=43)
            perft_config = _write_single_perft_config(tmp_path)
            batch_dir = tmp_path / "batches"

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/run_match_batch.py",
                    "--red",
                    _shell_command([sys.executable, str(match_engine)]),
                    "--black",
                    _shell_command([sys.executable, str(match_engine)]),
                    "--fen",
                    TERMINAL_RED_WIN_FEN,
                    "--batches",
                    "1",
                    "--games-per-batch",
                    "1",
                    "--max-plies",
                    "1",
                    "--batch-dir",
                    str(batch_dir),
                    "--preflight-perft-engine",
                    _shell_command([sys.executable, str(perft_engine)]),
                    "--preflight-perft-config",
                    str(perft_config),
                    "--accept-candidate",
                    "candidate",
                    "--accept-baseline",
                    "baseline",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)
            batch_report_exists = (batch_dir / "match_batch_0001.json").exists()

        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
        self.assertFalse(output["valid"])
        self.assertFalse(output["accepted"])
        self.assertFalse(output["acceptance"]["accepted"])
        self.assertEqual(output["acceptance"]["candidate_invalid_games"], 0)
        self.assertEqual(output["acceptance"]["baseline_invalid_games"], 0)
        self.assertEqual(output["acceptance"]["candidate_unfinished_games"], 0)
        self.assertEqual(output["acceptance"]["baseline_unfinished_games"], 0)
        self.assertEqual(output["acceptance"]["candidate_unknown_rule_state"], 0)
        self.assertEqual(output["acceptance"]["baseline_unknown_rule_state"], 0)
        self.assertEqual(output["acceptance"]["candidate_time_forfeit_losses"], 0)
        self.assertEqual(output["acceptance"]["baseline_time_forfeit_losses"], 0)
        self.assertEqual(output["acceptance"]["candidate_emergency_moves"], 0)
        self.assertEqual(output["acceptance"]["baseline_emergency_moves"], 0)
        self.assertEqual(output["acceptance"]["candidate_unclassified_moves"], 0)
        self.assertEqual(output["acceptance"]["candidate_none_moves"], 0)
        self.assertEqual(output["acceptance"]["evidence_class"], "closed")
        self.assertFalse(output["acceptance"]["closed_elo_eligible"])
        self.assertIsNone(output["acceptance"]["closed_elo_diff"])
        self.assertIsNone(output["acceptance"]["closed_elo_diff_ci95"])
        self.assertEqual(output["acceptance"]["reasons"], ["preflight_perft_failed"])
        self.assertFalse(output["preflight_perft"]["valid"])
        self.assertEqual(output["preflight_perft"]["failures"], 1)
        self.assertEqual(output["preflight_perft"]["summary"]["node_mismatches"], 1)
        self.assertEqual(output["preflight_perft"]["summary"]["invalid_cases"], ["start"])
        self.assertEqual(output["summary"]["games"], 0)
        self.assertEqual(output["batches"], [])
        self.assertEqual(output["games"], [])
        self.assertFalse(batch_report_exists)

    def test_batch_runner_reports_preflight_engine_initialization_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            match_engine = _write_idle_engine(tmp_path)
            perft_engine = _write_crashing_init_engine(tmp_path)
            batch_dir = tmp_path / "batches"

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/run_match_batch.py",
                    "--red",
                    _shell_command([sys.executable, str(match_engine)]),
                    "--black",
                    _shell_command([sys.executable, str(match_engine)]),
                    "--fen",
                    TERMINAL_RED_WIN_FEN,
                    "--batches",
                    "1",
                    "--games-per-batch",
                    "1",
                    "--max-plies",
                    "1",
                    "--batch-dir",
                    str(batch_dir),
                    "--preflight-perft-engine",
                    _shell_command([sys.executable, str(perft_engine)]),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)
            batch_report_exists = (batch_dir / "match_batch_0001.json").exists()

        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
        self.assertFalse(output["valid"])
        self.assertFalse(output["preflight_perft"]["valid"])
        self.assertEqual(output["preflight_perft"]["report_type"], "perft_compare")
        self.assertEqual(output["preflight_perft"]["report_schema_version"], PERFT_REPORT_SCHEMA_VERSION)
        self.assertEqual(
            output["preflight_perft"]["config_digest"],
            _perft_config_digest(output["preflight_perft"]["report_config"]),
        )
        self.assertEqual(output["preflight_perft"]["phase"], "initialize")
        self.assertEqual(output["preflight_perft"]["completed"], 0)
        self.assertEqual(output["preflight_perft"]["failures"], 1)
        self.assertEqual(output["preflight_perft"]["summary"]["node_mismatches"], 0)
        self.assertEqual(output["preflight_perft"]["summary"]["root_divide_unavailable"], 0)
        self.assertEqual(output["preflight_perft"]["summary"]["engine_errors"], 1)
        self.assertEqual(output["preflight_perft"]["summary"]["invalid_cases"], ["single"])
        self.assertEqual(len(output["preflight_perft"]["entries"]), 1)
        self.assertEqual(output["preflight_perft"]["entries"][0]["phase"], "initialize")
        self.assertEqual(output["preflight_perft"]["entries"][0]["failure_reasons"], ["engine_error"])
        self.assertEqual(output["preflight_perft"]["entries"][0]["name"], "single")
        self.assertEqual(output["preflight_perft"]["entries"][0]["local_nodes"], 0)
        self.assertTrue(output["preflight_perft"]["error"])
        self.assertEqual(output["batches"], [])
        self.assertEqual(output["games"], [])
        self.assertFalse(batch_report_exists)
        self.assertNotIn("Traceback", result.stderr)

    def test_batch_runner_can_require_preflight_root_divide(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            match_engine = _write_idle_engine(tmp_path)
            perft_engine = _write_fake_perft_engine(tmp_path, nodes=44)
            perft_config = _write_single_perft_config(tmp_path)
            batch_dir = tmp_path / "batches"

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/run_match_batch.py",
                    "--red",
                    _shell_command([sys.executable, str(match_engine)]),
                    "--black",
                    _shell_command([sys.executable, str(match_engine)]),
                    "--fen",
                    TERMINAL_RED_WIN_FEN,
                    "--batches",
                    "1",
                    "--games-per-batch",
                    "1",
                    "--max-plies",
                    "1",
                    "--batch-dir",
                    str(batch_dir),
                    "--preflight-perft-engine",
                    _shell_command([sys.executable, str(perft_engine)]),
                    "--preflight-perft-config",
                    str(perft_config),
                    "--preflight-perft-require-root-divide",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)
            batch_report_exists = (batch_dir / "match_batch_0001.json").exists()

        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
        self.assertFalse(output["valid"])
        self.assertFalse(output["preflight_perft"]["valid"])
        self.assertTrue(output["preflight_perft"]["report_config"]["require_root_divide"])
        self.assertFalse(output["preflight_perft"]["entries"][0]["root_divide_available"])
        self.assertFalse(output["preflight_perft"]["entries"][0]["root_divide_valid"])
        self.assertTrue(output["preflight_perft"]["entries"][0]["require_root_divide"])
        self.assertEqual(
            output["preflight_perft"]["entries"][0]["failure_reasons"],
            ["root_divide_unavailable"],
        )
        self.assertEqual(output["preflight_perft"]["summary"]["root_divide_mismatches"], 0)
        self.assertEqual(output["preflight_perft"]["summary"]["root_divide_unavailable"], 1)
        self.assertEqual(output["batches"], [])
        self.assertEqual(output["games"], [])
        self.assertFalse(batch_report_exists)

    def test_batch_runner_runs_search_preflight_before_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            match_engine = _write_idle_engine(tmp_path)
            search_engine = _write_search_preflight_engine(tmp_path, bestmove="e0d0")
            search_config = _write_single_search_config(tmp_path)
            search_report = tmp_path / "search_preflight.json"
            batch_dir = tmp_path / "batches"

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/run_match_batch.py",
                    "--red",
                    _shell_command([sys.executable, str(match_engine)]),
                    "--black",
                    _shell_command([sys.executable, str(match_engine)]),
                    "--fen",
                    TERMINAL_RED_WIN_FEN,
                    "--batches",
                    "1",
                    "--games-per-batch",
                    "1",
                    "--max-plies",
                    "1",
                    "--batch-dir",
                    str(batch_dir),
                    "--preflight-search-engine",
                    _shell_command([sys.executable, str(search_engine)]),
                    "--preflight-search-config",
                    str(search_config),
                    "--preflight-search-go",
                    "go depth 2",
                    "--preflight-search-option",
                    "Threads=2",
                    "--preflight-search-require-pv",
                    "--preflight-search-report",
                    str(search_report),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)
            search_output = json.loads(search_report.read_text(encoding="utf-8"))
            batch_report_exists = (batch_dir / "match_batch_0001.json").exists()

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue(output["valid"])
        self.assertTrue(output["preflight_search"]["valid"])
        self.assertEqual(output["preflight_search"], search_output)
        self.assertEqual(output["preflight_search"]["report_type"], "uci_search_probe")
        self.assertEqual(
            output["preflight_search"]["report_schema_version"],
            SEARCH_PROBE_REPORT_SCHEMA_VERSION,
        )
        self.assertEqual(
            output["preflight_search"]["config_digest"],
            _search_probe_config_digest(output["preflight_search"]["report_config"]),
        )
        self.assertEqual(output["preflight_search"]["count"], 1)
        self.assertEqual(output["preflight_search"]["completed"], 1)
        self.assertIsNone(output["preflight_search"]["phase"])
        self.assertEqual(output["preflight_search"]["entries"][0]["bestmove"], "e0d0")
        self.assertTrue(output["preflight_search"]["entries"][0]["pv_available"])
        self.assertEqual(output["preflight_search"]["summary"]["illegal_bestmoves"], 0)
        self.assertEqual(output["preflight_search"]["summary"]["missing_pv"], 0)
        self.assertEqual(output["preflight_search"]["summary"]["engine_errors"], 0)
        self.assertTrue(batch_report_exists)

    def test_batch_runner_rejects_prefixed_non_go_search_preflight_command(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "tools/run_match_batch.py",
                "--red",
                f"{sys.executable} -c pass",
                "--black",
                f"{sys.executable} -c pass",
                "--preflight-search-go",
                "gobad",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("--preflight-search-go must start with 'go'", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_batch_runner_rejects_prefixed_non_go_match_command(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "tools/run_match_batch.py",
                "--red",
                f"{sys.executable} -c pass",
                "--black",
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

    def test_batch_runner_rejects_non_positive_match_timeout(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "tools/run_match_batch.py",
                "--red",
                f"{sys.executable} -c pass",
                "--black",
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

    def test_batch_runner_rejects_negative_perft_corpus_depth_before_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = tmp_path / "perft_corpus.json"
            config.write_text(
                json.dumps(
                    {
                        "positions": [
                            {"name": "bad_depth", "fen": TERMINAL_RED_WIN_FEN, "depth": -1}
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/run_match_batch.py",
                    "--red",
                    f"{sys.executable} -c pass",
                    "--black",
                    f"{sys.executable} -c pass",
                    "--preflight-perft-engine",
                    f"{sys.executable} -c pass",
                    "--preflight-perft-config",
                    str(config),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 2)
        self.assertIn("perft corpus item 'bad_depth' depth must be non-negative", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_batch_runner_skips_matches_when_search_preflight_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            match_engine = _write_idle_engine(tmp_path)
            search_engine = _write_search_preflight_engine(tmp_path, bestmove="a0a0")
            search_config = _write_single_search_config(tmp_path)
            batch_dir = tmp_path / "batches"

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/run_match_batch.py",
                    "--red",
                    _shell_command([sys.executable, str(match_engine)]),
                    "--black",
                    _shell_command([sys.executable, str(match_engine)]),
                    "--fen",
                    TERMINAL_RED_WIN_FEN,
                    "--batches",
                    "1",
                    "--games-per-batch",
                    "1",
                    "--max-plies",
                    "1",
                    "--batch-dir",
                    str(batch_dir),
                    "--preflight-search-engine",
                    _shell_command([sys.executable, str(search_engine)]),
                    "--preflight-search-config",
                    str(search_config),
                    "--accept-candidate",
                    "candidate",
                    "--accept-baseline",
                    "baseline",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)
            batch_report_exists = (batch_dir / "match_batch_0001.json").exists()

        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
        self.assertFalse(output["valid"])
        self.assertFalse(output["accepted"])
        self.assertEqual(output["acceptance"]["reasons"], ["preflight_search_failed"])
        self.assertFalse(output["preflight_search"]["valid"])
        self.assertEqual(output["preflight_search"]["failures"], 1)
        self.assertEqual(output["preflight_search"]["summary"]["illegal_bestmoves"], 1)
        self.assertEqual(output["preflight_search"]["summary"]["invalid_cases"], ["single"])
        self.assertEqual(output["summary"]["games"], 0)
        self.assertEqual(output["batches"], [])
        self.assertEqual(output["games"], [])
        self.assertFalse(batch_report_exists)

    def test_batch_runner_skips_matches_when_search_preflight_violates_searchmoves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            match_engine = _write_idle_engine(tmp_path)
            search_engine = _write_search_preflight_engine(tmp_path, bestmove="e0d0")
            search_config = _write_single_search_config(tmp_path)
            batch_dir = tmp_path / "batches"

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/run_match_batch.py",
                    "--red",
                    _shell_command([sys.executable, str(match_engine)]),
                    "--black",
                    _shell_command([sys.executable, str(match_engine)]),
                    "--fen",
                    TERMINAL_RED_WIN_FEN,
                    "--batches",
                    "1",
                    "--games-per-batch",
                    "1",
                    "--max-plies",
                    "1",
                    "--batch-dir",
                    str(batch_dir),
                    "--preflight-search-engine",
                    _shell_command([sys.executable, str(search_engine)]),
                    "--preflight-search-config",
                    str(search_config),
                    "--preflight-search-go",
                    "go searchmoves e0f0 depth 1",
                    "--accept-candidate",
                    "candidate",
                    "--accept-baseline",
                    "baseline",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)
            batch_report_exists = (batch_dir / "match_batch_0001.json").exists()

        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
        self.assertFalse(output["valid"])
        self.assertFalse(output["accepted"])
        self.assertEqual(output["acceptance"]["reasons"], ["preflight_search_failed"])
        self.assertFalse(output["preflight_search"]["valid"])
        self.assertEqual(output["preflight_search"]["failures"], 1)
        self.assertEqual(output["preflight_search"]["summary"]["illegal_bestmoves"], 0)
        self.assertEqual(output["preflight_search"]["summary"]["searchmoves_bestmove_violations"], 1)
        self.assertEqual(output["preflight_search"]["summary"]["searchmoves_pv_root_violations"], 1)
        entry = output["preflight_search"]["entries"][0]
        self.assertEqual(entry["bestmove"], "e0d0")
        self.assertEqual(entry["searchmoves"], ["e0f0"])
        self.assertEqual(
            entry["failure_reasons"],
            ["bestmove_searchmoves_violation", "pv_root_searchmoves_violation"],
        )
        self.assertEqual(entry["bestmove_searchmoves_error"], "move is outside go searchmoves")
        self.assertEqual(
            entry["searchmoves_pv_root_violations"][0]["error"],
            "move is outside go searchmoves",
        )
        self.assertEqual(output["summary"]["games"], 0)
        self.assertEqual(output["batches"], [])
        self.assertEqual(output["games"], [])
        self.assertFalse(batch_report_exists)

    def test_batch_runner_skips_matches_when_search_preflight_pv_line_is_illegal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            match_engine = _write_idle_engine(tmp_path)
            search_engine = _write_search_preflight_engine(
                tmp_path,
                bestmove="e0d0",
                pv_moves=("e0d0", "a0a0"),
            )
            search_config = _write_single_search_config(tmp_path)
            batch_dir = tmp_path / "batches"

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/run_match_batch.py",
                    "--red",
                    _shell_command([sys.executable, str(match_engine)]),
                    "--black",
                    _shell_command([sys.executable, str(match_engine)]),
                    "--fen",
                    TERMINAL_RED_WIN_FEN,
                    "--batches",
                    "1",
                    "--games-per-batch",
                    "1",
                    "--max-plies",
                    "1",
                    "--batch-dir",
                    str(batch_dir),
                    "--preflight-search-engine",
                    _shell_command([sys.executable, str(search_engine)]),
                    "--preflight-search-config",
                    str(search_config),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)
            batch_report_exists = (batch_dir / "match_batch_0001.json").exists()

        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
        self.assertFalse(output["valid"])
        self.assertEqual(output["acceptance"], None)
        self.assertFalse(output["preflight_search"]["valid"])
        self.assertEqual(output["preflight_search"]["failures"], 1)
        self.assertEqual(output["preflight_search"]["summary"]["illegal_bestmoves"], 0)
        self.assertEqual(output["preflight_search"]["summary"]["illegal_pv_roots"], 0)
        self.assertEqual(output["preflight_search"]["summary"]["illegal_pv_lines"], 1)
        self.assertEqual(
            output["preflight_search"]["entries"][0]["failure_reasons"],
            ["pv_line_illegal"],
        )
        self.assertEqual(output["preflight_search"]["entries"][0]["illegal_pv_lines"][0]["ply"], 2)
        self.assertEqual(output["summary"]["games"], 0)
        self.assertEqual(output["batches"], [])
        self.assertEqual(output["games"], [])
        self.assertFalse(batch_report_exists)

    def test_batch_runner_reports_search_preflight_engine_initialization_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            match_engine = _write_idle_engine(tmp_path)
            search_engine = _write_crashing_init_engine(tmp_path)
            batch_dir = tmp_path / "batches"

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/run_match_batch.py",
                    "--red",
                    _shell_command([sys.executable, str(match_engine)]),
                    "--black",
                    _shell_command([sys.executable, str(match_engine)]),
                    "--fen",
                    TERMINAL_RED_WIN_FEN,
                    "--batches",
                    "1",
                    "--games-per-batch",
                    "1",
                    "--max-plies",
                    "1",
                    "--batch-dir",
                    str(batch_dir),
                    "--preflight-search-engine",
                    _shell_command([sys.executable, str(search_engine)]),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)
            batch_report_exists = (batch_dir / "match_batch_0001.json").exists()

        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
        self.assertFalse(output["valid"])
        self.assertFalse(output["preflight_search"]["valid"])
        self.assertEqual(output["preflight_search"]["report_type"], "uci_search_probe")
        self.assertEqual(
            output["preflight_search"]["report_schema_version"],
            SEARCH_PROBE_REPORT_SCHEMA_VERSION,
        )
        self.assertEqual(
            output["preflight_search"]["config_digest"],
            _search_probe_config_digest(output["preflight_search"]["report_config"]),
        )
        self.assertEqual(output["preflight_search"]["phase"], "initialize")
        self.assertEqual(output["preflight_search"]["completed"], 0)
        self.assertEqual(output["preflight_search"]["failures"], 1)
        self.assertEqual(output["preflight_search"]["summary"]["engine_errors"], 1)
        self.assertEqual(output["preflight_search"]["summary"]["invalid_cases"], ["single"])
        self.assertEqual(len(output["preflight_search"]["entries"]), 1)
        self.assertEqual(output["preflight_search"]["entries"][0]["phase"], "initialize")
        self.assertEqual(output["preflight_search"]["entries"][0]["failure_reasons"], ["engine_error"])
        self.assertEqual(output["preflight_search"]["entries"][0]["name"], "single")
        self.assertTrue(output["preflight_search"]["error"])
        self.assertEqual(output["batches"], [])
        self.assertEqual(output["games"], [])
        self.assertFalse(batch_report_exists)
        self.assertNotIn("Traceback", result.stderr)


def _write_idle_engine(directory: Path) -> Path:
    path = directory / "idle_uci.py"
    path.write_text(
        textwrap.dedent(
            """
            import sys

            for line in sys.stdin:
                command = line.strip()
                if command == "uci":
                    print("id name Idle Fake", flush=True)
                    print("uciok", flush=True)
                elif command == "isready":
                    print("readyok", flush=True)
                elif command.startswith("go"):
                    print("bestmove 0000", flush=True)
                elif command == "quit":
                    break
            """
        ).strip(),
        encoding="utf-8",
    )
    return path


def _write_legal_engine(directory: Path) -> Path:
    path = directory / "legal_uci.py"
    path.write_text(
        textwrap.dedent(
            """
            import sys
            from pathlib import Path

            sys.path.insert(0, str(Path.cwd() / "src"))

            from xiangqi_core import GameState, Position

            state = GameState.from_position(Position.start())
            for line in sys.stdin:
                command = line.strip()
                if command == "uci":
                    print("id name Legal Fake", flush=True)
                    print("uciok", flush=True)
                elif command == "isready":
                    print("readyok", flush=True)
                elif command == "ucinewgame":
                    state = GameState.from_position(Position.start())
                elif command.startswith("position "):
                    state = GameState.from_uci_position(command)
                elif command.startswith("go"):
                    moves = sorted(state.legal_moves(), key=lambda move: move.to_uci())
                    bestmove = moves[0].to_uci() if moves else "0000"
                    print(f"bestmove {bestmove}", flush=True)
                elif command == "quit":
                    break
            """
        ).strip(),
        encoding="utf-8",
    )
    return path


def _write_emergency_engine(directory: Path) -> Path:
    path = directory / "emergency_uci.py"
    path.write_text(
        textwrap.dedent(
            """
            import sys
            from pathlib import Path

            sys.path.insert(0, str(Path.cwd() / "src"))

            from xiangqi_core import GameState, Position

            state = GameState.from_position(Position.start())
            for line in sys.stdin:
                command = line.strip()
                if command == "uci":
                    print("id name Emergency Fake", flush=True)
                    print("uciok", flush=True)
                elif command == "isready":
                    print("readyok", flush=True)
                elif command == "ucinewgame":
                    state = GameState.from_position(Position.start())
                elif command.startswith("position "):
                    state = GameState.from_uci_position(command)
                elif command.startswith("go"):
                    moves = sorted(state.legal_moves(), key=lambda move: move.to_uci())
                    bestmove = moves[0].to_uci() if moves else "0000"
                    print(
                        "info string source=emergency status=unknown reason=go_error "
                        "nodes=0 max_ply=1 node_limit=100 time_limit_ms=0 "
                        "time_limit_reached=0 resolved_store_hits=0 resolved_store_misses=0",
                        flush=True,
                    )
                    print(f"bestmove {bestmove}", flush=True)
                elif command == "quit":
                    break
            """
        ).strip(),
        encoding="utf-8",
    )
    return path


def _write_inconsistent_proof_engine(directory: Path) -> Path:
    path = directory / "inconsistent_proof_uci.py"
    path.write_text(
        textwrap.dedent(
            """
            import sys
            from pathlib import Path

            sys.path.insert(0, str(Path.cwd() / "src"))

            from xiangqi_core import GameState, Position

            state = GameState.from_position(Position.start())
            for line in sys.stdin:
                command = line.strip()
                if command == "uci":
                    print("id name Inconsistent Proof Fake", flush=True)
                    print("uciok", flush=True)
                elif command == "isready":
                    print("readyok", flush=True)
                elif command == "ucinewgame":
                    state = GameState.from_position(Position.start())
                elif command.startswith("position "):
                    state = GameState.from_uci_position(command)
                elif command.startswith("go"):
                    moves = sorted(state.legal_moves(), key=lambda move: move.to_uci())
                    bestmove = moves[0].to_uci() if moves else "0000"
                    print(
                        "info string source=proof status=unknown reason=bad_fixture "
                        "nodes=1 max_ply=1 node_limit=100 time_limit_ms=0 "
                        "time_limit_reached=0 resolved_store_hits=0 resolved_store_misses=0",
                        flush=True,
                    )
                    print(f"bestmove {bestmove}", flush=True)
                elif command == "quit":
                    break
            """
        ).strip(),
        encoding="utf-8",
    )
    return path


def _write_crashing_init_engine(directory: Path) -> Path:
    path = directory / "crashing_init_uci.py"
    path.write_text(
        textwrap.dedent(
            """
            import sys

            for line in sys.stdin:
                command = line.strip()
                if command == "uci":
                    raise SystemExit(9)
                elif command == "quit":
                    break
            """
        ).strip(),
        encoding="utf-8",
    )
    return path


def _write_option_logging_engine(directory: Path, name: str) -> tuple[Path, Path]:
    path = directory / f"{name}_option_uci.py"
    log_path = directory / f"{name}_options.txt"
    path.write_text(
        textwrap.dedent(
            f"""
            import sys
            from pathlib import Path

            sys.path.insert(0, str(Path.cwd() / "src"))
            from xiangqi_core import GameState

            log_path = Path({str(log_path)!r})
            state = GameState.from_uci_position("position startpos")
            for line in sys.stdin:
                command = line.strip()
                if command.startswith("setoption"):
                    previous = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
                    log_path.write_text(previous + command + "\\n", encoding="utf-8")
                if command == "uci":
                    print("id name {name} Fake", flush=True)
                    print("uciok", flush=True)
                elif command == "isready":
                    print("readyok", flush=True)
                elif command.startswith("position "):
                    state = GameState.from_uci_position(command)
                elif command.startswith("go"):
                    moves = state.legal_moves()
                    bestmove = moves[0].to_uci() if moves else "0000"
                    print(f"bestmove {{bestmove}}", flush=True)
                elif command == "quit":
                    break
            """
        ).strip(),
        encoding="utf-8",
    )
    return path, log_path


def _write_go_logging_engine(directory: Path, bestmove: str) -> tuple[Path, Path]:
    path = directory / "go_logging_uci.py"
    log_path = directory / "go_commands.txt"
    path.write_text(
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
                    print("bestmove {bestmove}", flush=True)
                elif command == "uci":
                    print("id name Go Logging Fake", flush=True)
                    print("uciok", flush=True)
                elif command == "isready":
                    print("readyok", flush=True)
                elif command == "quit":
                    break
            """
        ).strip(),
        encoding="utf-8",
    )
    return path, log_path


def _write_fake_perft_engine(directory: Path, nodes: int) -> Path:
    path = directory / f"perft_{nodes}_uci.py"
    path.write_text(
        textwrap.dedent(
            f"""
            import sys

            for line in sys.stdin:
                command = line.strip()
                if command == "uci":
                    print("id name Perft Fake", flush=True)
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
    return path


def _write_search_preflight_engine(
    directory: Path,
    bestmove: str,
    pv_moves: tuple[str, ...] | None = None,
) -> Path:
    path = directory / f"search_{bestmove}_uci.py"
    pv = " ".join(pv_moves or (bestmove,))
    path.write_text(
        textwrap.dedent(
            f"""
            import sys

            for line in sys.stdin:
                command = line.strip()
                if command == "uci":
                    print("id name Search Preflight Fake", flush=True)
                    print("uciok", flush=True)
                elif command == "isready":
                    print("readyok", flush=True)
                elif command.startswith("setoption"):
                    pass
                elif command == "ucinewgame":
                    pass
                elif command.startswith("position "):
                    pass
                elif command.startswith("go"):
                    print("info depth 1 pv {pv}", flush=True)
                    print("bestmove {bestmove}", flush=True)
                elif command == "quit":
                    break
            """
        ).strip(),
        encoding="utf-8",
    )
    return path


def _write_single_perft_config(directory: Path) -> Path:
    path = directory / "perft_corpus.json"
    path.write_text(
        json.dumps(
            {
                "positions": [
                    {
                        "name": "start",
                        "fen": (
                            "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/"
                            "P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1"
                        ),
                        "depth": 1,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_single_search_config(directory: Path) -> Path:
    path = directory / "search_corpus.json"
    path.write_text(
        json.dumps(
            {
                "positions": [
                    {
                        "name": "single",
                        "fen": "4k4/9/9/9/9/9/9/9/9/4K4 w - - 0 1",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


if __name__ == "__main__":
    unittest.main()
