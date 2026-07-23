from __future__ import annotations

import json
import shlex
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import context  # noqa: F401
from fixtures import RED_WIN_IN_ONE_FEN, TERMINAL_RED_WIN_FEN
from xiangqi_core import GameState, Position
from xiangqi_solver import BoundedProofSearch, ProofStore
from tools.play_uci_match import (
    REPORT_SCHEMA_VERSION,
    ClockConfig,
    MatchStart,
    _acceptance_summary,
    _config_digest,
    _games_digest,
    _starts_from_suite,
    run_game,
)


def _shell_command(parts: list[str]) -> str:
    if sys.platform == "win32":
        return subprocess.list2cmdline(parts)
    return shlex.join(parts)


class PlayUciMatchTests(unittest.TestCase):
    def test_match_rejects_prefixed_non_go_command(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "tools/play_uci_match.py",
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

    def test_match_rejects_non_positive_timeout(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "tools/play_uci_match.py",
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

    def test_acceptance_rejects_external_candidate_configuration_before_starting_engines(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "tools/play_uci_match.py",
                "--red",
                f"{sys.executable} tools/proof_uci.py --closed --fallback-uci-engine pikafish",
                "--red-name",
                "candidate",
                "--black",
                f"{sys.executable} -c pass",
                "--black-name",
                "baseline",
                "--accept-candidate",
                "candidate",
                "--accept-baseline",
                "baseline",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("must be closed", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_match_accepts_locally_legal_bestmoves_and_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            legal_engine = _write_legal_engine(tmp_path)
            legal_engine_path = str(legal_engine.resolve())
            legal_engine_size = legal_engine.stat().st_size
            report_path = tmp_path / "match.json"

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/play_uci_match.py",
                    "--red",
                    f"{sys.executable} {legal_engine}",
                    "--black",
                    f"{sys.executable} {legal_engine}",
                    "--max-plies",
                    "4",
                    "--report",
                    str(report_path),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue(output["valid"])
        self.assertEqual(output["report_type"], "uci_match")
        self.assertEqual(output["report_schema_version"], REPORT_SCHEMA_VERSION)
        self.assertEqual(output, report)
        self.assertEqual(output["config"]["red_command"], f"{sys.executable} {legal_engine}")
        self.assertEqual(output["config"]["black_command"], f"{sys.executable} {legal_engine}")
        self.assertEqual(output["config_digest"], _config_digest(output["config"]))
        self.assertEqual(output["games_digest"], _games_digest(output["games"]))
        provenance = output["config"]["engine_provenance"]["red"]["files"]
        script_record = next(item for item in provenance if item["path"] == legal_engine_path)
        self.assertEqual(script_record["bytes"], legal_engine_size)
        self.assertEqual(len(script_record["sha256"]), 64)
        self.assertEqual(output["config"]["go"], "go depth 1")
        self.assertEqual(output["config"]["max_plies"], 4)
        self.assertEqual(output["config"]["starts"][0]["name"], "single")
        self.assertEqual(output["summary"]["games"], 1)
        self.assertEqual(output["games"][0]["result"], "unfinished")
        self.assertEqual(output["games"][0]["reason"], "max_plies")
        self.assertEqual(len(output["games"][0]["moves"]), 4)
        self.assertEqual(len(output["games"][0]["records"]), 4)
        self.assertEqual(output["summary"]["unclassified_moves"], 4)
        engine_stats = next(iter(output["summary"]["engines"].values()))
        self.assertEqual(engine_stats["unfinished"], 2)
        self.assertEqual(engine_stats["scored_games"], 0)
        self.assertIsNone(engine_stats["score_rate_ci95"])
        self.assertIsNone(engine_stats["elo_diff_ci95"])

    def test_match_counts_perpetual_chase_loss_in_engine_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            legal_engine = _write_legal_engine(tmp_path)
            repetition_position = (
                "position fen 4k4/9/9/9/9/3N1r3/9/9/4A4/4K4 w - - 0 1 "
                "moves d4e6 f4f5 e6d4 f5f4 d4e6 f4f5 e6d4 f5f4"
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/play_uci_match.py",
                    "--red",
                    f"{sys.executable} {legal_engine}",
                    "--black",
                    f"{sys.executable} {legal_engine}",
                    "--position",
                    repetition_position,
                    "--max-plies",
                    "1",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue(output["valid"])
        self.assertEqual(output["games"][0]["result"], "black_win")
        self.assertEqual(output["games"][0]["reason"], "perpetual_chase_loss")
        self.assertEqual(output["games"][0]["plies"], 0)
        engine_stats = next(iter(output["summary"]["engines"].values()))
        self.assertEqual(engine_stats["unknown_rule_state"], 0)
        self.assertEqual(engine_stats["wins"], 1)
        self.assertEqual(engine_stats["losses"], 1)
        self.assertEqual(engine_stats["scored_games"], 2)

    def test_match_counts_emergency_bestmoves_separately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            emergency_engine = _write_emergency_engine(tmp_path)
            legal_engine = _write_legal_engine(tmp_path)

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/play_uci_match.py",
                    "--red",
                    f"{sys.executable} {emergency_engine}",
                    "--red-name",
                    "emergency",
                    "--black",
                    f"{sys.executable} {legal_engine}",
                    "--black-name",
                    "legal",
                    "--max-plies",
                    "1",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(output["summary"]["emergency_moves"], 1)
        self.assertEqual(output["summary"]["unclassified_moves"], 0)
        self.assertEqual(output["summary"]["starts"]["single"]["emergency_moves"], 1)
        self.assertEqual(output["summary"]["engines"]["emergency"]["emergency_moves"], 1)
        self.assertEqual(output["games"][0]["records"][0]["source"], "emergency")

    def test_terminal_start_does_not_require_engine_initialization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            crashing_engine = _write_crashing_init_engine(tmp_path)

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/play_uci_match.py",
                    "--red",
                    _shell_command([sys.executable, str(crashing_engine)]),
                    "--red-name",
                    "badinit",
                    "--black",
                    _shell_command([sys.executable, str(crashing_engine)]),
                    "--black-name",
                    "also-bad",
                    "--fen",
                    TERMINAL_RED_WIN_FEN,
                    "--max-plies",
                    "1",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue(output["valid"])
        self.assertEqual(output["games"][0]["result"], "red_win")
        self.assertEqual(output["games"][0]["reason"], "no_legal_moves")
        self.assertEqual(output["games"][0]["records"], [])
        self.assertEqual(output["summary"]["engine_errors"], 0)
        self.assertEqual(output["summary"]["invalid_games"], 0)

    def test_terminal_short_fen_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            crashing_engine = _write_crashing_init_engine(tmp_path)
            short_fen = " ".join(TERMINAL_RED_WIN_FEN.split()[:2])

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/play_uci_match.py",
                    "--red",
                    _shell_command([sys.executable, str(crashing_engine)]),
                    "--black",
                    _shell_command([sys.executable, str(crashing_engine)]),
                    "--fen",
                    short_fen,
                    "--max-plies",
                    "1",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue(output["valid"])
        self.assertEqual(output["games"][0]["result"], "red_win")
        self.assertEqual(output["games"][0]["reason"], "no_legal_moves")

    def test_match_rejects_illegal_bestmove(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            legal_engine = _write_legal_engine(tmp_path)
            illegal_engine = _write_illegal_engine(tmp_path)

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/play_uci_match.py",
                    "--red",
                    f"{sys.executable} {illegal_engine}",
                    "--red-name",
                    "illegal",
                    "--black",
                    f"{sys.executable} {legal_engine}",
                    "--black-name",
                    "legal",
                    "--max-plies",
                    "4",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
        self.assertFalse(output["valid"])
        self.assertEqual(output["summary"]["invalid_games"], 1)
        self.assertEqual(output["games"][0]["result"], "black_win")
        self.assertEqual(output["games"][0]["reason"], "illegal_bestmove")
        self.assertEqual(output["summary"]["reasons"], {"illegal_bestmove": 1})
        self.assertEqual(
            output["summary"]["starts"]["single"]["reasons"],
            {"illegal_bestmove": 1},
        )
        self.assertEqual(output["games"][0]["illegal"]["side"], "w")
        self.assertEqual(output["games"][0]["illegal"]["bestmove"], "a0a0")
        self.assertEqual(output["games"][0]["illegal"]["engine"], "illegal")
        self.assertEqual(
            output["games"][0]["illegal"]["validation_error"],
            "move is not legal in the local rules core",
        )
        self.assertEqual(output["games"][0]["illegal"]["legal_move_count"], 44)
        self.assertIn("a0a1", output["games"][0]["illegal"]["legal_moves_sample"])
        self.assertEqual(output["summary"]["engines"]["illegal"]["illegal_losses"], 1)
        self.assertEqual(output["summary"]["engines"]["illegal"]["invalid_games"], 1)
        self.assertEqual(
            output["summary"]["engines"]["illegal"]["reasons"],
            {"illegal_bestmove": 1},
        )
        self.assertEqual(
            output["summary"]["engines"]["legal"]["reasons"],
            {"illegal_bestmove": 1},
        )
        self.assertEqual(output["summary"]["engines"]["illegal"]["losses"], 0)
        self.assertEqual(output["summary"]["engines"]["illegal"]["scored_games"], 0)
        self.assertEqual(output["summary"]["engines"]["legal"]["wins"], 0)
        self.assertEqual(output["summary"]["engines"]["legal"]["scored_games"], 0)

    def test_match_reports_null_bestmove_with_legal_move_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            null_engine = _write_fixed_bestmove_engine(tmp_path, "0000")
            legal_engine = _write_legal_engine(tmp_path)

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/play_uci_match.py",
                    "--red",
                    _shell_command([sys.executable, str(null_engine)]),
                    "--red-name",
                    "nuller",
                    "--black",
                    _shell_command([sys.executable, str(legal_engine)]),
                    "--black-name",
                    "legal",
                    "--max-plies",
                    "1",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
        self.assertFalse(output["valid"])
        illegal = output["games"][0]["illegal"]
        self.assertEqual(output["games"][0]["reason"], "illegal_bestmove")
        self.assertEqual(output["summary"]["reasons"], {"illegal_bestmove": 1})
        self.assertEqual(illegal["bestmove"], "0000")
        self.assertEqual(illegal["validation_error"], "null move returned while legal moves exist")
        self.assertEqual(illegal["legal_move_count"], 44)
        self.assertEqual(len(illegal["legal_moves_sample"]), 8)
        self.assertIn("a0a1", illegal["legal_moves_sample"])
        self.assertEqual(output["summary"]["engines"]["nuller"]["illegal_losses"], 1)

    def test_match_rejects_bestmove_outside_searchmoves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            outside_engine = _write_fixed_bestmove_engine(tmp_path, "h2e2")
            legal_engine = _write_legal_engine(tmp_path)

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/play_uci_match.py",
                    "--red",
                    _shell_command([sys.executable, str(outside_engine)]),
                    "--red-name",
                    "outside",
                    "--black",
                    _shell_command([sys.executable, str(legal_engine)]),
                    "--black-name",
                    "legal",
                    "--go",
                    "go searchmoves a0a1 depth 1",
                    "--max-plies",
                    "1",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
        self.assertFalse(output["valid"])
        illegal = output["games"][0]["illegal"]
        self.assertEqual(output["games"][0]["reason"], "illegal_bestmove")
        self.assertEqual(illegal["bestmove"], "h2e2")
        self.assertEqual(illegal["validation_error"], "move is outside go searchmoves")
        self.assertEqual(illegal["searchmoves"], ["a0a1"])
        self.assertEqual(illegal["searchmoves_error"], "move is outside go searchmoves")
        self.assertEqual(illegal["searchmoves_legal_move_count"], 1)
        self.assertEqual(illegal["searchmoves_legal_moves_sample"], ["a0a1"])
        self.assertEqual(output["summary"]["engines"]["outside"]["illegal_losses"], 1)

    def test_match_records_engine_go_error_without_aborting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            crashing_engine = _write_crashing_go_engine(tmp_path)
            legal_engine = _write_legal_engine(tmp_path)

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/play_uci_match.py",
                    "--red",
                    _shell_command([sys.executable, str(crashing_engine)]),
                    "--red-name",
                    "crasher",
                    "--black",
                    _shell_command([sys.executable, str(legal_engine)]),
                    "--black-name",
                    "legal",
                    "--max-plies",
                    "1",
                    "--timeout",
                    "0.5",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
        self.assertFalse(output["valid"])
        self.assertEqual(output["summary"]["engine_errors"], 1)
        self.assertEqual(output["summary"]["starts"]["single"]["engine_errors"], 1)
        self.assertEqual(output["summary"]["engines"]["crasher"]["engine_errors"], 1)
        self.assertEqual(output["summary"]["engines"]["crasher"]["invalid_games"], 1)
        self.assertEqual(output["summary"]["engines"]["crasher"]["illegal_losses"], 0)
        self.assertEqual(output["games"][0]["reason"], "engine_error")
        self.assertEqual(output["games"][0]["illegal"]["engine"], "crasher")
        self.assertEqual(output["games"][0]["illegal"]["go_command"], "go depth 1")
        self.assertEqual(output["games"][0]["records"][0]["reason"], "engine_error")
        self.assertEqual(output["games"][0]["records"][0]["bestmove"], "0000")

    def test_match_records_engine_initialization_error_without_aborting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            crashing_engine = _write_crashing_init_engine(tmp_path)
            legal_engine = _write_legal_engine(tmp_path)

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/play_uci_match.py",
                    "--red",
                    _shell_command([sys.executable, str(crashing_engine)]),
                    "--red-name",
                    "badinit",
                    "--black",
                    _shell_command([sys.executable, str(legal_engine)]),
                    "--black-name",
                    "legal",
                    "--max-plies",
                    "1",
                    "--timeout",
                    "0.5",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
        self.assertFalse(output["valid"])
        self.assertEqual(output["summary"]["engine_errors"], 1)
        self.assertEqual(output["summary"]["engines"]["badinit"]["engine_errors"], 1)
        self.assertEqual(output["summary"]["engines"]["badinit"]["invalid_games"], 1)
        self.assertEqual(output["games"][0]["result"], "black_win")
        self.assertEqual(output["games"][0]["reason"], "engine_error")
        self.assertEqual(output["games"][0]["records"], [])
        self.assertEqual(output["games"][0]["illegal"]["phase"], "initialize")
        self.assertEqual(output["games"][0]["illegal"]["engine"], "badinit")

    def test_match_summarizes_proof_uci_fallback_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            legal_engine = _write_legal_engine(Path(tmp))

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/play_uci_match.py",
                    "--red",
                    f"{sys.executable} tools/proof_uci.py --max-ply 0",
                    "--black",
                    f"{sys.executable} {legal_engine}",
                    "--max-plies",
                    "1",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(output["summary"]["fallback_moves"], 1)
        self.assertEqual(output["summary"]["proof_moves"], 0)
        proof_engine = output["games"][0]["records"][0]["engine"]
        self.assertEqual(output["summary"]["engines"][proof_engine]["moves"], 1)
        self.assertEqual(output["summary"]["engines"][proof_engine]["fallback_moves"], 1)
        self.assertEqual(output["summary"]["engines"][proof_engine]["proof_moves"], 0)
        self.assertEqual(output["games"][0]["records"][0]["source"], "self_fallback")
        self.assertEqual(output["summary"]["self_fallback_moves"], 1)
        self.assertEqual(output["games"][0]["records"][0]["status"], "unknown")
        self.assertIsInstance(output["games"][0]["records"][0]["nodes"], int)

    def test_match_ignores_unrelated_info_string_source_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            noisy_engine = _write_noisy_proof_info_engine(tmp_path)
            legal_engine = _write_legal_engine(tmp_path)

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/play_uci_match.py",
                    "--red",
                    _shell_command([sys.executable, str(noisy_engine)]),
                    "--black",
                    _shell_command([sys.executable, str(legal_engine)]),
                    "--max-plies",
                    "1",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(output["summary"]["fallback_moves"], 1)
        self.assertEqual(output["summary"]["unclassified_moves"], 0)
        record = output["games"][0]["records"][0]
        self.assertEqual(record["source"], "fallback")
        self.assertEqual(output["summary"]["legacy_fallback_moves"], 1)
        self.assertEqual(record["reason"], "unknown")
        self.assertEqual(record["nodes"], 3)
        self.assertEqual(record["resolved_store_hits"], 1)

    def test_match_summarizes_proof_store_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            store_path = tmp_path / "proofs.sqlite"
            store = ProofStore(store_path)
            artifact = BoundedProofSearch("red", max_ply=1).search(
                Position.from_fen(RED_WIN_IN_ONE_FEN)
            ).artifact
            store.save(artifact, node_limit=100)
            legal_engine = _write_legal_engine(tmp_path)
            proof_command = _shell_command(
                [
                    sys.executable,
                    "tools/proof_uci.py",
                    "--max-ply",
                    "0",
                    "--proof-store",
                    str(store_path),
                ]
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/play_uci_match.py",
                    "--red",
                    proof_command,
                    "--black",
                    _shell_command([sys.executable, str(legal_engine)]),
                    "--fen",
                    RED_WIN_IN_ONE_FEN,
                    "--max-plies",
                    "1",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(output["summary"]["proof_moves"], 1)
        self.assertEqual(output["summary"]["proof_store_moves"], 1)
        self.assertEqual(output["summary"]["starts"]["single"]["proof_store_moves"], 1)
        self.assertEqual(output["summary"]["resolved_store_hits"], 1)
        self.assertEqual(output["summary"]["starts"]["single"]["resolved_store_hits"], 1)
        self.assertEqual(output["summary"]["fallback_moves"], 0)
        self.assertEqual(output["games"][0]["records"][0]["source"], "proof_store")
        self.assertEqual(output["games"][0]["records"][0]["bestmove"], "a8a0")
        self.assertEqual(output["games"][0]["records"][0]["resolved_store_hits"], 1)
        proof_engine = output["games"][0]["records"][0]["engine"]
        self.assertEqual(output["summary"]["engines"][proof_engine]["moves"], 1)
        self.assertEqual(output["summary"]["engines"][proof_engine]["proof_moves"], 1)
        self.assertEqual(output["summary"]["engines"][proof_engine]["proof_store_moves"], 1)
        self.assertEqual(output["summary"]["engines"][proof_engine]["resolved_store_hits"], 1)

    def test_match_summarizes_online_proof_store_saves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            store_path = tmp_path / "proofs.sqlite"
            legal_engine = _write_legal_engine(tmp_path)
            proof_command = _shell_command(
                [
                    sys.executable,
                    "tools/proof_uci.py",
                    "--max-ply",
                    "1",
                    "--node-limit",
                    "100",
                    "--proof-store",
                    str(store_path),
                    "--save-online-proofs",
                ]
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/play_uci_match.py",
                    "--red",
                    proof_command,
                    "--red-name",
                    "proof",
                    "--black",
                    _shell_command([sys.executable, str(legal_engine)]),
                    "--black-name",
                    "legal",
                    "--fen",
                    RED_WIN_IN_ONE_FEN,
                    "--max-plies",
                    "1",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)
            stored = ProofStore(store_path).load(RED_WIN_IN_ONE_FEN, "red", 1)

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(output["summary"]["proof_store_saves"], 1)
        self.assertEqual(output["summary"]["proof_store_save_errors"], 0)
        self.assertEqual(output["summary"]["starts"]["single"]["proof_store_saves"], 1)
        self.assertEqual(output["summary"]["engines"]["proof"]["proof_store_saves"], 1)
        record = output["games"][0]["records"][0]
        self.assertTrue(record["proof_store_saved"])
        self.assertIsNone(record["proof_store_save_error"])
        self.assertIsNotNone(stored)

    def test_match_records_proof_uci_time_limit_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            legal_engine = _write_legal_engine(Path(tmp))

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/play_uci_match.py",
                    "--red",
                    f"{sys.executable} tools/proof_uci.py --max-ply 1",
                    "--black",
                    f"{sys.executable} {legal_engine}",
                    "--go",
                    "go movetime 0",
                    "--max-plies",
                    "1",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(output["summary"]["fallback_moves"], 1)
        self.assertEqual(output["summary"]["time_limited_moves"], 1)
        self.assertEqual(output["summary"]["starts"]["single"]["time_limited_moves"], 1)
        record = output["games"][0]["records"][0]
        self.assertEqual(record["source"], "self_fallback")
        self.assertEqual(record["reason"], "time_limit")
        self.assertEqual(record["max_ply"], 1)
        self.assertEqual(record["node_limit"], 10_000)
        self.assertEqual(record["time_limit_ms"], 0)
        self.assertTrue(record["time_limit_reached"])
        self.assertIsInstance(record["external_ordering_elapsed_ms"], int)
        self.assertIsInstance(record["proof_search_elapsed_ms"], int)
        self.assertIsInstance(record["total_search_elapsed_ms"], int)

    def test_match_sends_uci_clock_and_records_timing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            engine, log_path = _write_go_logging_engine(tmp_path, bestmove="i3i4")

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/play_uci_match.py",
                    "--red",
                    _shell_command([sys.executable, str(engine)]),
                    "--black",
                    _shell_command([sys.executable, str(engine)]),
                    "--red-time-ms",
                    "1000",
                    "--black-time-ms",
                    "2000",
                    "--red-increment-ms",
                    "100",
                    "--black-increment-ms",
                    "200",
                    "--movestogo",
                    "20",
                    "--max-plies",
                    "1",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)
            go_commands = log_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue(output["valid"])
        self.assertEqual(
            output["config"]["clock"],
            {
                "red_time_ms": 1000,
                "black_time_ms": 2000,
                "red_increment_ms": 100,
                "black_increment_ms": 200,
                "movestogo": 20,
            },
        )
        self.assertEqual(
            go_commands,
            ["go depth 1 wtime 1000 btime 2000 winc 100 binc 200 movestogo 20"],
        )
        record = output["games"][0]["records"][0]
        self.assertEqual(record["go_command"], go_commands[0])
        self.assertEqual(record["red_time_before_ms"], 1000)
        self.assertEqual(record["black_time_before_ms"], 2000)
        self.assertIsInstance(record["elapsed_ms"], int)
        self.assertGreaterEqual(record["red_time_after_ms"], 1000)
        self.assertLessEqual(record["red_time_after_ms"], 1100)
        self.assertEqual(record["black_time_after_ms"], 2000)

    def test_match_flags_time_forfeit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            slow_engine = _write_slow_fixed_engine(tmp_path, bestmove="i3i4", sleep_seconds=0.03)
            legal_engine = _write_legal_engine(tmp_path)

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/play_uci_match.py",
                    "--red",
                    _shell_command([sys.executable, str(slow_engine)]),
                    "--red-name",
                    "slow",
                    "--black",
                    _shell_command([sys.executable, str(legal_engine)]),
                    "--black-name",
                    "legal",
                    "--red-time-ms",
                    "1",
                    "--black-time-ms",
                    "1000",
                    "--max-plies",
                    "1",
                    "--timeout",
                    "2",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue(output["valid"])
        self.assertEqual(output["games"][0]["result"], "black_win")
        self.assertEqual(output["games"][0]["reason"], "time_forfeit")
        self.assertEqual(output["summary"]["time_forfeits"], 1)
        self.assertEqual(output["summary"]["starts"]["single"]["time_forfeits"], 1)
        self.assertEqual(output["summary"]["engines"]["slow"]["time_forfeit_losses"], 1)
        self.assertEqual(output["games"][0]["moves"], [])
        self.assertEqual(output["games"][0]["forfeit"]["side"], "w")
        self.assertEqual(output["games"][0]["forfeit"]["engine"], "slow")
        self.assertGreater(output["games"][0]["forfeit"]["overrun_ms"], 0)
        self.assertEqual(output["games"][0]["records"][0]["red_time_after_ms"], 0)

    def test_engine_error_after_clock_expiry_is_reported_as_time_forfeit(self) -> None:
        class FailingEngine:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            def initialize(self) -> None:
                return None

            def new_game(self) -> None:
                return None

            def set_position_command(self, _command: str) -> None:
                return None

            def wait_ready(self) -> None:
                return None

            def go(self, _command: str):
                time.sleep(0.01)
                raise RuntimeError("simulated go failure")

            def close(self) -> None:
                return None

        with patch("tools.play_uci_match.UciEngine", FailingEngine):
            report = run_game(
                game=1,
                red_command="failing",
                black_command="unused",
                red_label="candidate",
                black_label="baseline",
                start=MatchStart("single", GameState.from_position(Position.start())),
                go_command="go depth 1",
                max_plies=1,
                clock=ClockConfig(red_time_ms=1, black_time_ms=1000),
            )

        self.assertTrue(report.valid)
        self.assertEqual(report.result, "black_win")
        self.assertEqual(report.reason, "time_forfeit")
        self.assertIsNotNone(report.forfeit)

    def test_match_excludes_position_setup_from_clock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            slow_position_engine = _write_slow_position_engine(
                tmp_path,
                bestmove="i3i4",
                sleep_seconds=0.03,
            )
            legal_engine = _write_legal_engine(tmp_path)

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/play_uci_match.py",
                    "--red",
                    _shell_command([sys.executable, str(slow_position_engine)]),
                    "--red-name",
                    "slow_position",
                    "--black",
                    _shell_command([sys.executable, str(legal_engine)]),
                    "--black-name",
                    "legal",
                    "--red-time-ms",
                    "1",
                    "--black-time-ms",
                    "1000",
                    "--max-plies",
                    "1",
                    "--timeout",
                    "2",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue(output["valid"])
        self.assertEqual(output["games"][0]["result"], "unfinished")
        self.assertEqual(output["summary"]["time_forfeits"], 0)
        self.assertLessEqual(output["games"][0]["records"][0]["elapsed_ms"], 1)

    def test_match_accepts_engine_that_returns_bestmove_after_stop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            stop_engine = _write_stop_recover_engine(tmp_path, bestmove="i3i4")
            legal_engine = _write_legal_engine(tmp_path)

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/play_uci_match.py",
                    "--red",
                    _shell_command([sys.executable, str(stop_engine)]),
                    "--red-name",
                    "recover",
                    "--black",
                    _shell_command([sys.executable, str(legal_engine)]),
                    "--black-name",
                    "legal",
                    "--max-plies",
                    "1",
                    "--timeout",
                    "0.2",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue(output["valid"])
        self.assertEqual(output["summary"]["engine_errors"], 0)
        self.assertEqual(output["games"][0]["moves"], ["i3i4"])
        self.assertEqual(output["games"][0]["records"][0]["bestmove"], "i3i4")

    def test_match_records_proof_uci_external_fallback_move(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fallback_engine = _write_fixed_bestmove_engine(tmp_path, "i3i4")
            legal_engine = _write_legal_engine(tmp_path)
            fallback_command = _shell_command([sys.executable, str(fallback_engine)])
            proof_command = _shell_command(
                [
                    sys.executable,
                    "tools/proof_uci.py",
                    "--max-ply",
                    "0",
                    "--fallback-uci-engine",
                    fallback_command,
                    "--fallback-uci-depth",
                    "1",
                ]
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/play_uci_match.py",
                    "--red",
                    proof_command,
                    "--red-name",
                    "proof",
                    "--black",
                    _shell_command([sys.executable, str(legal_engine)]),
                    "--black-name",
                    "legal",
                    "--max-plies",
                    "1",
                    "--timeout",
                    "10",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue(output["valid"])
        self.assertEqual(output["summary"]["fallback_moves"], 1)
        self.assertEqual(output["games"][0]["moves"], ["i3i4"])
        self.assertEqual(output["games"][0]["records"][0]["bestmove"], "i3i4")
        self.assertEqual(output["games"][0]["records"][0]["source"], "external_fallback")
        self.assertEqual(output["summary"]["external_fallback_moves"], 1)

    def test_acceptance_rejects_candidate_external_fallback_moves(self) -> None:
        summary = {
            "engines": {
                "candidate": {
                    "scored_games": 1,
                    "external_fallback_moves": 1,
                    "elo_diff_ci95": {"lower": 10.0, "upper": 20.0},
                },
                "baseline": {"scored_games": 1},
            }
        }

        acceptance = _acceptance_summary(summary, "candidate", "baseline", 1, 0.0)

        self.assertFalse(acceptance["accepted"])
        self.assertEqual(acceptance["candidate_external_fallback_moves"], 1)
        self.assertIn("candidate_external_fallback_moves", acceptance["reasons"])

    def test_match_can_alternate_colors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            legal_engine = _write_legal_engine(Path(tmp))

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/play_uci_match.py",
                    "--red",
                    f"{sys.executable} {legal_engine}",
                    "--red-name",
                    "alpha",
                    "--black",
                    f"{sys.executable} {legal_engine}",
                    "--black-name",
                    "beta",
                    "--games",
                    "2",
                    "--alternate-colors",
                    "--max-plies",
                    "2",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue(output["valid"])
        self.assertEqual(output["summary"]["games"], 2)
        self.assertEqual(len(output["games"]), 2)
        self.assertEqual(output["games"][0]["red_engine"], "alpha")
        self.assertEqual(output["games"][1]["red_engine"], "beta")

    def test_match_forwards_uci_options_with_engine_identity_when_alternating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            alpha_engine, alpha_log = _write_option_logging_engine(tmp_path, "alpha")
            beta_engine, beta_log = _write_option_logging_engine(tmp_path, "beta")

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/play_uci_match.py",
                    "--red",
                    _shell_command([sys.executable, str(alpha_engine)]),
                    "--red-name",
                    "alpha",
                    "--red-option",
                    "Threads=2",
                    "--black",
                    _shell_command([sys.executable, str(beta_engine)]),
                    "--black-name",
                    "beta",
                    "--black-option",
                    "Hash=64",
                    "--games",
                    "2",
                    "--alternate-colors",
                    "--position",
                    "position startpos moves h2e2",
                    "--max-plies",
                    "1",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)
            alpha_options = alpha_log.read_text(encoding="utf-8").splitlines()
            beta_options = beta_log.read_text(encoding="utf-8").splitlines()

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue(output["valid"])
        self.assertEqual(alpha_options, ["setoption name Threads value 2"] * 2)
        self.assertEqual(beta_options, ["setoption name Hash value 64"] * 2)
        self.assertEqual(
            output["games"][0]["red_options"],
            [{"name": "Threads", "value": "2"}],
        )
        self.assertEqual(
            output["games"][0]["black_options"],
            [{"name": "Hash", "value": "64"}],
        )
        self.assertEqual(
            output["config"]["red_options"],
            [{"name": "Threads", "value": "2"}],
        )
        self.assertEqual(
            output["config"]["black_options"],
            [{"name": "Hash", "value": "64"}],
        )
        self.assertEqual(
            output["games"][1]["red_options"],
            [{"name": "Hash", "value": "64"}],
        )
        self.assertEqual(
            output["games"][1]["black_options"],
            [{"name": "Threads", "value": "2"}],
        )

    def test_match_reports_engine_scores_and_elo_with_alternating_colors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            legal_engine = _write_legal_engine(Path(tmp))

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/play_uci_match.py",
                    "--red",
                    f"{sys.executable} {legal_engine}",
                    "--red-name",
                    "alpha",
                    "--black",
                    f"{sys.executable} {legal_engine}",
                    "--black-name",
                    "beta",
                    "--games",
                    "2",
                    "--alternate-colors",
                    "--fen",
                    TERMINAL_RED_WIN_FEN,
                    "--max-plies",
                    "2",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)

        alpha = output["summary"]["engines"]["alpha"]
        beta = output["summary"]["engines"]["beta"]
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(output["summary"]["red_win"], 2)
        self.assertEqual(alpha["games"], 2)
        self.assertEqual(alpha["red_games"], 1)
        self.assertEqual(alpha["black_games"], 1)
        self.assertEqual(alpha["wins"], 1)
        self.assertEqual(alpha["losses"], 1)
        self.assertEqual(alpha["scored_games"], 2)
        self.assertEqual(alpha["score"], 1)
        self.assertEqual(alpha["score_rate"], 0.5)
        self.assertEqual(alpha["elo_diff"], 0.0)
        self.assertLess(alpha["score_rate_ci95"]["lower"], 0.5)
        self.assertGreater(alpha["score_rate_ci95"]["upper"], 0.5)
        self.assertLess(alpha["elo_diff_ci95"]["lower"], 0.0)
        self.assertGreater(alpha["elo_diff_ci95"]["upper"], 0.0)
        self.assertEqual(beta["score_rate"], 0.5)
        self.assertEqual(beta["elo_diff"], 0.0)
        self.assertEqual(beta["score_rate_ci95"], alpha["score_rate_ci95"])
        self.assertEqual(beta["elo_diff_ci95"], alpha["elo_diff_ci95"])

    def test_match_acceptance_gate_uses_candidate_elo_lower_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            legal_engine = _write_legal_engine(Path(tmp))
            command = [
                sys.executable,
                "tools/play_uci_match.py",
                "--red",
                f"{sys.executable} {legal_engine}",
                "--red-name",
                "candidate",
                "--black",
                f"{sys.executable} {legal_engine}",
                "--black-name",
                "baseline",
                "--fen",
                TERMINAL_RED_WIN_FEN,
                "--max-plies",
                "1",
                "--accept-candidate",
                "candidate",
                "--accept-baseline",
                "baseline",
            ]

            passing = subprocess.run(
                command + ["--accept-min-elo-lower", "-300"],
                text=True,
                capture_output=True,
                check=False,
            )
            failing = subprocess.run(
                command + ["--accept-min-elo-lower", "0"],
                text=True,
                capture_output=True,
                check=False,
            )

            passing_output = json.loads(passing.stdout)
            failing_output = json.loads(failing.stdout)

        self.assertEqual(passing.returncode, 0, passing.stderr or passing.stdout)
        self.assertTrue(passing_output["valid"])
        self.assertTrue(passing_output["accepted"])
        self.assertTrue(passing_output["acceptance"]["accepted"])
        self.assertEqual(passing_output["acceptance"]["candidate"], "candidate")
        self.assertEqual(passing_output["acceptance"]["baseline"], "baseline")
        self.assertEqual(passing_output["acceptance"]["candidate_scored_games"], 1)
        self.assertEqual(passing_output["acceptance"]["evidence_class"], "closed")
        self.assertTrue(passing_output["acceptance"]["closed_elo_eligible"])
        self.assertEqual(
            passing_output["acceptance"]["closed_elo_diff"],
            passing_output["acceptance"]["candidate_elo_diff"],
        )
        self.assertEqual(
            passing_output["acceptance"]["closed_elo_diff_ci95"],
            passing_output["acceptance"]["candidate_elo_diff_ci95"],
        )
        self.assertEqual(passing_output["acceptance"]["reasons"], [])

        self.assertEqual(failing.returncode, 1, failing.stderr or failing.stdout)
        self.assertTrue(failing_output["valid"])
        self.assertFalse(failing_output["accepted"])
        self.assertIn(
            "elo_lower_bound_below_threshold",
            failing_output["acceptance"]["reasons"],
        )

    def test_match_acceptance_gate_rejects_unfinished_match_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            legal_engine = _write_legal_engine(Path(tmp))

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/play_uci_match.py",
                    "--red",
                    f"{sys.executable} {legal_engine}",
                    "--red-name",
                    "candidate",
                    "--black",
                    f"{sys.executable} {legal_engine}",
                    "--black-name",
                    "baseline",
                    "--max-plies",
                    "1",
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

        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
        self.assertTrue(output["valid"])
        self.assertFalse(output["accepted"])
        self.assertEqual(output["summary"]["unfinished"], 1)
        self.assertEqual(output["acceptance"]["candidate_unfinished_games"], 1)
        self.assertEqual(output["acceptance"]["baseline_unfinished_games"], 1)
        self.assertIn("candidate_unfinished_games", output["acceptance"]["reasons"])
        self.assertIn("baseline_unfinished_games", output["acceptance"]["reasons"])

    def test_match_acceptance_gate_rejects_candidate_emergency_moves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            emergency_engine = _write_emergency_engine(tmp_path)
            legal_engine = _write_legal_engine(tmp_path)

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/play_uci_match.py",
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
                    "--max-plies",
                    "2",
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
        self.assertEqual(output["games"][0]["result"], "red_win")
        self.assertEqual(output["summary"]["engines"]["candidate"]["wins"], 1)
        self.assertEqual(output["summary"]["engines"]["candidate"]["emergency_moves"], 1)
        self.assertEqual(output["acceptance"]["candidate_scored_games"], 1)
        self.assertEqual(output["acceptance"]["candidate_emergency_moves"], 1)
        self.assertIn("candidate_emergency_moves", output["acceptance"]["reasons"])

    def test_match_acceptance_gate_rejects_candidate_unclassified_moves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            candidate_engine = _write_legal_engine(tmp_path)
            baseline_engine = _write_legal_engine(tmp_path)

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/play_uci_match.py",
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
                    "--max-plies",
                    "2",
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
        self.assertEqual(output["games"][0]["result"], "red_win")
        self.assertEqual(output["summary"]["engines"]["candidate"]["wins"], 1)
        self.assertEqual(output["summary"]["engines"]["candidate"]["unclassified_moves"], 1)
        self.assertEqual(output["acceptance"]["candidate_scored_games"], 1)
        self.assertEqual(output["acceptance"]["candidate_unclassified_moves"], 1)
        self.assertIn("candidate_unclassified_moves", output["acceptance"]["reasons"])

    def test_match_acceptance_gate_rejects_inconsistent_proof_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            candidate_engine = _write_inconsistent_proof_engine(tmp_path)
            baseline_engine = _write_legal_engine(tmp_path)

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/play_uci_match.py",
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
                    "--max-plies",
                    "2",
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
        self.assertEqual(output["games"][0]["result"], "red_win")
        self.assertEqual(output["summary"]["invalid_proof_telemetry_moves"], 1)
        self.assertEqual(
            output["summary"]["invalid_proof_telemetry_reasons"],
            {"proof_without_artifact_hash": 1},
        )
        self.assertEqual(
            output["summary"]["engines"]["candidate"]["invalid_proof_telemetry_moves"],
            1,
        )
        self.assertEqual(
            output["summary"]["starts"]["single"]["invalid_proof_telemetry_moves"],
            1,
        )
        self.assertEqual(output["acceptance"]["candidate_scored_games"], 1)
        self.assertEqual(output["acceptance"]["candidate_invalid_proof_telemetry_moves"], 1)
        self.assertIn("candidate_invalid_proof_telemetry", output["acceptance"]["reasons"])

    def test_acceptance_summary_reports_missing_or_insufficient_evidence(self) -> None:
        summary = {
            "engines": {
                "candidate": {
                    "scored_games": 2,
                    "invalid_games": 0,
                    "time_forfeit_losses": 0,
                    "score_rate": 0.5,
                    "score_rate_ci95": {"lower": 0.094531, "upper": 0.905469},
                    "elo_diff": 0.0,
                    "elo_diff_ci95": {"lower": -392.4, "upper": 392.4},
                }
            }
        }

        acceptance = _acceptance_summary(
            summary,
            candidate="candidate",
            baseline="baseline",
            min_games=4,
            min_elo_lower=0,
        )

        self.assertFalse(acceptance["accepted"])
        self.assertIn("baseline_missing", acceptance["reasons"])
        self.assertIn("insufficient_scored_games", acceptance["reasons"])
        self.assertIn("elo_lower_bound_below_threshold", acceptance["reasons"])

    def test_acceptance_summary_rejects_invalid_or_time_forfeit_evidence(self) -> None:
        summary = {
            "engines": {
                "candidate": {
                    "scored_games": 4,
                    "invalid_games": 1,
                    "unfinished": 1,
                    "unknown_rule_state": 1,
                    "time_forfeit_losses": 1,
                    "emergency_moves": 2,
                    "unclassified_moves": 3,
                    "none_moves": 1,
                    "invalid_proof_telemetry_moves": 2,
                    "score_rate": 0.75,
                    "score_rate_ci95": {"lower": 0.300642, "upper": 0.954413},
                    "elo_diff": 190.8,
                    "elo_diff_ci95": {"lower": -146.6, "upper": 528.2},
                },
                "baseline": {
                    "scored_games": 4,
                    "invalid_games": 0,
                    "unfinished": 1,
                    "unknown_rule_state": 1,
                    "time_forfeit_losses": 0,
                    "emergency_moves": 1,
                    "invalid_proof_telemetry_moves": 1,
                    "score_rate": 0.25,
                    "score_rate_ci95": {"lower": 0.045587, "upper": 0.699358},
                    "elo_diff": -190.8,
                    "elo_diff_ci95": {"lower": -528.2, "upper": 146.6},
                },
            }
        }

        acceptance = _acceptance_summary(
            summary,
            candidate="candidate",
            baseline="baseline",
            min_games=1,
            min_elo_lower=-200,
        )

        self.assertFalse(acceptance["accepted"])
        self.assertEqual(acceptance["candidate_invalid_games"], 1)
        self.assertEqual(acceptance["candidate_unfinished_games"], 1)
        self.assertEqual(acceptance["baseline_unfinished_games"], 1)
        self.assertEqual(acceptance["candidate_unknown_rule_state"], 1)
        self.assertEqual(acceptance["baseline_unknown_rule_state"], 1)
        self.assertEqual(acceptance["candidate_time_forfeit_losses"], 1)
        self.assertEqual(acceptance["candidate_emergency_moves"], 2)
        self.assertEqual(acceptance["baseline_emergency_moves"], 1)
        self.assertEqual(acceptance["candidate_unclassified_moves"], 3)
        self.assertEqual(acceptance["candidate_none_moves"], 1)
        self.assertEqual(acceptance["candidate_invalid_proof_telemetry_moves"], 2)
        self.assertEqual(acceptance["baseline_invalid_proof_telemetry_moves"], 1)
        self.assertIn("candidate_invalid_games", acceptance["reasons"])
        self.assertIn("candidate_unfinished_games", acceptance["reasons"])
        self.assertIn("baseline_unfinished_games", acceptance["reasons"])
        self.assertIn("candidate_unknown_rule_state", acceptance["reasons"])
        self.assertIn("baseline_unknown_rule_state", acceptance["reasons"])
        self.assertIn("candidate_time_forfeits", acceptance["reasons"])
        self.assertIn("candidate_emergency_moves", acceptance["reasons"])
        self.assertIn("baseline_emergency_moves", acceptance["reasons"])
        self.assertIn("candidate_unclassified_moves", acceptance["reasons"])
        self.assertIn("candidate_none_moves", acceptance["reasons"])
        self.assertIn("candidate_invalid_proof_telemetry", acceptance["reasons"])
        self.assertIn("baseline_invalid_proof_telemetry", acceptance["reasons"])

    def test_closed_elo_fields_hide_ineligible_candidate_evidence(self) -> None:
        candidate = {
            "scored_games": 4,
            "invalid_games": 0,
            "unfinished": 0,
            "unknown_rule_state": 0,
            "time_forfeit_losses": 0,
            "emergency_moves": 0,
            "unclassified_moves": 0,
            "none_moves": 0,
            "external_fallback_moves": 0,
            "legacy_fallback_moves": 0,
            "invalid_proof_telemetry_moves": 0,
            "score_rate": 0.75,
            "score_rate_ci95": {"lower": 0.300642, "upper": 0.954413},
            "elo_diff": 190.8,
            "elo_diff_ci95": {"lower": -146.6, "upper": 528.2},
        }
        baseline = {
            "scored_games": 4,
            "invalid_games": 0,
            "unfinished": 0,
            "unknown_rule_state": 0,
            "time_forfeit_losses": 0,
            "emergency_moves": 0,
            "invalid_proof_telemetry_moves": 0,
            "score_rate": 0.25,
            "elo_diff": -190.8,
            "elo_diff_ci95": {"lower": -528.2, "upper": 146.6},
        }
        cases = {
            "external": ("external_fallback_moves", "assisted"),
            "legacy": ("legacy_fallback_moves", "assisted"),
            "emergency": ("emergency_moves", "closed"),
            "unclassified": ("unclassified_moves", "closed"),
            "none": ("none_moves", "closed"),
            "invalid telemetry": ("invalid_proof_telemetry_moves", "closed"),
            "invalid": ("invalid_games", "closed"),
            "unfinished": ("unfinished", "closed"),
            "unknown": ("unknown_rule_state", "closed"),
            "time forfeit": ("time_forfeit_losses", "closed"),
        }

        for name, (field, evidence_class) in cases.items():
            with self.subTest(name=name):
                ineligible_candidate = {**candidate, field: 1}
                acceptance = _acceptance_summary(
                    {"engines": {"candidate": ineligible_candidate, "baseline": baseline}},
                    candidate="candidate",
                    baseline="baseline",
                    min_games=1,
                    min_elo_lower=-200,
                )

                self.assertEqual(acceptance["evidence_class"], evidence_class)
                self.assertFalse(acceptance["closed_elo_eligible"])
                self.assertIsNone(acceptance["closed_elo_diff"])
                self.assertIsNone(acceptance["closed_elo_diff_ci95"])
                self.assertEqual(acceptance["candidate_elo_diff"], 190.8)
                self.assertEqual(
                    acceptance["candidate_elo_diff_ci95"],
                    {"lower": -146.6, "upper": 528.2},
                )

    def test_match_suite_runs_each_start_and_preserves_opening_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            legal_engine = _write_legal_engine(tmp_path)
            suite = tmp_path / "openings.json"
            suite.write_text(
                json.dumps(
                    {
                        "positions": [
                            {
                                "name": "startpos",
                                "fen": "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1",
                            },
                             {
                                 "name": "after_cannon",
                                 "position": "position startpos moves h2e2",
                                "tags": ["opening", "history"],
                             },
                         ],
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/play_uci_match.py",
                    "--red",
                    f"{sys.executable} {legal_engine}",
                    "--red-name",
                    "alpha",
                    "--black",
                    f"{sys.executable} {legal_engine}",
                    "--black-name",
                    "beta",
                    "--suite",
                    str(suite),
                    "--games",
                    "2",
                    "--alternate-colors",
                    "--max-plies",
                    "1",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue(output["valid"])
        self.assertEqual(output["summary"]["games"], 4)
        self.assertEqual(len(output["games"]), 4)
        self.assertEqual(output["games"][0]["start_name"], "startpos")
        self.assertEqual(output["games"][2]["start_name"], "after_cannon")
        self.assertEqual(output["summary"]["starts"]["startpos"]["games"], 2)
        self.assertEqual(output["summary"]["starts"]["after_cannon"]["games"], 2)
        history_game = output["games"][2]
        self.assertEqual(history_game["start_moves"], ["h2e2"])
        self.assertEqual(history_game["start_tags"], ["opening", "history"])
        self.assertEqual(history_game["plies"], 1)
        self.assertEqual(len(history_game["moves"]), 1)
        self.assertIn("moves h2e2", history_game["records"][0]["position"])
        self.assertEqual(output["config"]["starts"][1]["tags"], ["opening", "history"])
        self.assertEqual(output["games"][0]["red_engine"], "alpha")
        self.assertEqual(output["games"][1]["red_engine"], "beta")
        self.assertEqual(output["games"][2]["red_engine"], "alpha")
        self.assertEqual(output["games"][3]["red_engine"], "beta")

    def test_match_suite_can_filter_starts_by_tag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            legal_engine = _write_legal_engine(tmp_path)
            suite = tmp_path / "tagged.json"
            suite.write_text(
                json.dumps(
                    {
                        "positions": [
                            {
                                "name": "startpos",
                                "fen": "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1",
                            },
                            {
                                "name": "after_cannon",
                                "position": "position startpos moves h2e2",
                                "tags": ["opening", "history"],
                            },
                            {
                                "name": "fallback_guard",
                                "fen": "9/9/5k3/N1r2b3/7b1/9/4n4/3A5/9/1p3K3 w - - 0 1",
                                "tags": ["tactical"],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/play_uci_match.py",
                    "--red",
                    f"{sys.executable} {legal_engine}",
                    "--red-name",
                    "alpha",
                    "--black",
                    f"{sys.executable} {legal_engine}",
                    "--black-name",
                    "beta",
                    "--suite",
                    str(suite),
                    "--suite-tag",
                    "history",
                    "--games",
                    "1",
                    "--max-plies",
                    "1",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        output = json.loads(result.stdout)
        self.assertTrue(output["valid"])
        self.assertEqual(output["summary"]["games"], 1)
        self.assertEqual([game["start_name"] for game in output["games"]], ["after_cannon"])
        self.assertEqual(list(output["summary"]["starts"]), ["after_cannon"])
        self.assertEqual(output["config"]["suite_tags"], ["history"])
        self.assertEqual([start["name"] for start in output["config"]["starts"]], ["after_cannon"])
        self.assertEqual(output["games"][0]["start_tags"], ["opening", "history"])

    def test_example_match_suite_loads_nonterminal_starts(self) -> None:
        starts = _starts_from_suite("configs/match_suite.example.json")
        names = [start.name for start in starts]

        self.assertEqual(len(names), len(set(names)))
        self.assertIn("developing_knights_four_plies", names)
        self.assertIn("immediate_loss_fallback_guard", names)
        self.assertIn("flying_general_file_pressure", names)
        self.assertIn("cannon_screen_tension", names)
        self.assertIn("blocked_knight_leg", names)
        self.assertIn("advisor_palace_constraints", names)
        self.assertIn("elephant_river_boundary", names)
        self.assertIn("elephant_eye_pressure", names)
        self.assertIn("cannon_screen_capture_tension", names)
        self.assertIn("red_win_in_one_tactical", names)
        self.assertIn("protected_rook_chase_seed", names)
        self.assertTrue(all(start.state.legal_moves() for start in starts))
        self.assertTrue(all(start.state.rule_judgement().result is None for start in starts))


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


def _write_illegal_engine(directory: Path) -> Path:
    path = directory / "illegal_uci.py"
    path.write_text(
        textwrap.dedent(
            """
            import sys

            for line in sys.stdin:
                command = line.strip()
                if command == "uci":
                    print("id name Illegal Fake", flush=True)
                    print("uciok", flush=True)
                elif command == "isready":
                    print("readyok", flush=True)
                elif command.startswith("go"):
                    print("bestmove a0a0", flush=True)
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


def _write_noisy_proof_info_engine(directory: Path) -> Path:
    path = directory / "noisy_proof_info_uci.py"
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
                    print("id name Noisy Proof Info Fake", flush=True)
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
                        "info string source=fallback status=unknown reason=unknown "
                        "nodes=3 max_ply=0 node_limit=7 time_limit_ms=0 "
                        "time_limit_reached=0 resolved_store_hits=1 resolved_store_misses=2",
                        flush=True,
                    )
                    print(
                        "info string source=debug reason=diagnostic nodes=999 "
                        "resolved_store_hits=999",
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
                        "info string source=proof status=proven reason=bad_fixture "
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


def _write_crashing_go_engine(directory: Path) -> Path:
    path = directory / "crashing_go_uci.py"
    path.write_text(
        textwrap.dedent(
            """
            import sys

            for line in sys.stdin:
                command = line.strip()
                if command == "uci":
                    print("id name Crashing Fake", flush=True)
                    print("uciok", flush=True)
                elif command == "isready":
                    print("readyok", flush=True)
                elif command.startswith("go"):
                    raise SystemExit(7)
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


def _write_fixed_bestmove_engine(directory: Path, bestmove: str) -> Path:
    path = directory / "fixed bestmove uci.py"
    path.write_text(
        textwrap.dedent(
            f"""
            import sys

            for line in sys.stdin:
                command = line.strip()
                if command == "uci":
                    print("id name Fixed Bestmove Fake", flush=True)
                    print("uciok", flush=True)
                elif command == "isready":
                    print("readyok", flush=True)
                elif command.startswith("go"):
                    print("bestmove {bestmove}", flush=True)
                elif command == "quit":
                    break
            """
        ).strip(),
        encoding="utf-8",
    )
    return path


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


def _write_slow_fixed_engine(directory: Path, bestmove: str, sleep_seconds: float) -> Path:
    path = directory / "slow_fixed_uci.py"
    path.write_text(
        textwrap.dedent(
            f"""
            import sys
            import time

            for line in sys.stdin:
                command = line.strip()
                if command == "uci":
                    print("id name Slow Fixed Fake", flush=True)
                    print("uciok", flush=True)
                elif command == "isready":
                    print("readyok", flush=True)
                elif command.startswith("go"):
                    time.sleep({sleep_seconds!r})
                    print("bestmove {bestmove}", flush=True)
                elif command == "quit":
                    break
            """
        ).strip(),
        encoding="utf-8",
    )
    return path


def _write_slow_position_engine(directory: Path, bestmove: str, sleep_seconds: float) -> Path:
    path = directory / "slow_position_uci.py"
    path.write_text(
        textwrap.dedent(
            f"""
            import sys
            import time

            for line in sys.stdin:
                command = line.strip()
                if command == "uci":
                    print("id name Slow Position Fake", flush=True)
                    print("uciok", flush=True)
                elif command == "isready":
                    print("readyok", flush=True)
                elif command.startswith("position "):
                    time.sleep({sleep_seconds!r})
                elif command.startswith("go"):
                    print("bestmove {bestmove}", flush=True)
                elif command == "quit":
                    break
            """
        ).strip(),
        encoding="utf-8",
    )
    return path


def _write_stop_recover_engine(directory: Path, bestmove: str) -> Path:
    path = directory / "stop_recover_uci.py"
    path.write_text(
        textwrap.dedent(
            f"""
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
                    print("bestmove {bestmove}", flush=True)

            for line in sys.stdin:
                command = line.strip()
                if command == "uci":
                    print("id name Stop Recover Fake", flush=True)
                    print("uciok", flush=True)
                elif command == "isready":
                    print("readyok", flush=True)
                elif command.startswith("go"):
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


if __name__ == "__main__":
    unittest.main()
