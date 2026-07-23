from __future__ import annotations

import json
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import context  # noqa: F401
from context import ROOT
from xiangqi_core import Position
from xiangqi_evaluators import ChessDbResponse, ChessDbRuleResult, ChessDbStatus


def _load_rule_probe_module():
    spec = importlib.util.spec_from_file_location(
        "rule_probe_under_test",
        ROOT / "tools" / "rule_probe.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_rule_corpus_module():
    tools_dir = str(ROOT / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    spec = importlib.util.spec_from_file_location(
        "rule_corpus_under_test",
        ROOT / "tools" / "rule_corpus.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RuleProbeCliTests(unittest.TestCase):
    def test_rule_probe_reports_local_repetition_state(self) -> None:
        position = (
            "position fen 4k4/9/9/9/9/9/4P4/9/R8/4K4 w - - 0 1 "
            "moves a1a2 e9e8 a2a1 e8e9 a1a2 e9e8 a2a1 e8e9"
        )
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "rule_probe.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "rule_probe.py"),
                    "--position",
                    position,
                    "--report",
                    str(report_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        output = json.loads(result.stdout)
        module = _load_rule_probe_module()
        self.assertEqual(output, report)
        self.assertEqual(output["report_type"], "rule_probe")
        self.assertEqual(output["report_schema_version"], module.REPORT_SCHEMA_VERSION)
        self.assertEqual(output["config_digest"], module._config_digest(output["config"]))
        self.assertTrue(output["valid"])
        self.assertEqual(output["gate"]["reasons"], [])
        self.assertEqual(output["config"]["position"], position)
        self.assertEqual(output["config"]["resolved_position"], position)
        self.assertEqual(output["local_result"], "draw")
        self.assertEqual(output["rule_judgement"]["result"], "draw")
        self.assertEqual(output["rule_judgement"]["reason"], "repetition_draw")
        self.assertTrue(output["rule_judgement"]["adjudicated"])
        self.assertEqual(output["repetition"]["count"], 3)
        self.assertEqual(output["repetition"]["cycle_plies"], 4)
        self.assertEqual(output["repetition"]["cycle_start_ply"], 4)
        self.assertEqual(output["repetition"]["cycle_end_ply"], 8)
        self.assertEqual(
            output["repetition"]["cycle_moves"],
            ["a1a2", "e9e8", "a2a1", "e8e9"],
        )
        self.assertIn("possible_chasing_sides", output["repetition"])
        self.assertIn("perpetual_chasing_sides", output["repetition"])
        self.assertIn("attack_patterns", output["repetition"])
        self.assertIn("moves", output["repetition"]["attack_patterns"][0])
        self.assertIn("common_targets", output["repetition"]["attack_patterns"][0])
        if output["repetition"]["attack_patterns"][0]["common_targets"]:
            self.assertIn(
                "always_chase_relevant",
                output["repetition"]["attack_patterns"][0]["common_targets"][0],
            )
        self.assertEqual(len(output["rule_events"]), 9)
        self.assertEqual(output["rule_events"][1]["move"], "a1a2")
        self.assertIn("attacked_opponent_pieces", output["rule_events"][1])
        self.assertIn("attacked_opponent_details", output["rule_events"][1])
        self.assertEqual(
            output["chessdb_rule_query"]["fen"],
            "4k4/9/9/9/9/9/4P4/9/R8/4K4 w - - 0 1",
        )
        self.assertEqual(
            output["chessdb_rule_query"]["movelist"],
            ["a1a2", "e9e8", "a2a1", "e8e9", "a1a2", "e9e8", "a2a1", "e8e9"],
        )
        self.assertEqual(output["chessdb_rule_query"]["reptimes"], 2)
        self.assertEqual(output["chessdb"], None)
        self.assertTrue(output["legal_moves"])

    def test_rule_probe_summarizes_chessdb_rule_results(self) -> None:
        module = _load_rule_probe_module()
        response = ChessDbResponse(
            ChessDbStatus.OK,
            "move:a1a2,rule:none|move:a1a0,rule:ban",
            rule_results=(
                ChessDbRuleResult("a1a2", "none"),
                ChessDbRuleResult("a1a0", "ban"),
            ),
        )

        summary = module._chessdb_rule_summary(response)

        self.assertEqual(summary["result_count"], 2)
        self.assertEqual(summary["rule_counts"], {"ban": 1, "none": 1})
        self.assertEqual(summary["non_none_rule_results"][0]["move"], "a1a0")
        self.assertEqual(summary["non_none_rule_results"][0]["rule"], "ban")

    def test_rule_probe_gate_flags_conflicts(self) -> None:
        module = _load_rule_probe_module()
        position = (
            "position fen 4k4/9/9/9/9/9/4P4/9/R8/4K4 w - - 0 1 "
            "moves a1a2 e9e8 a2a1 e8e9 a1a2 e9e8 a2a1 e8e9"
        )
        state = module.GameState.from_uci_position(position)  # noqa: SLF001
        legal_moves = tuple(sorted(move.to_uci() for move in state.legal_moves()))
        response = ChessDbResponse(
            ChessDbStatus.OK,
            "move:a1a2,rule:ban",
            rule_results=(ChessDbRuleResult("a1a2", "ban"),),
        )
        comparison = module._rule_comparison(state, response, legal_moves)  # noqa: SLF001
        args = type(
            "Args",
            (),
            {
                "fail_on_legal_move_rule_conflict": True,
                "fail_on_flag_disagreement": True,
            },
        )()

        gate = module._gate_summary(args, comparison)  # noqa: SLF001

        self.assertFalse(gate["valid"])
        self.assertEqual(gate["legal_move_rule_conflicts"], 1)
        self.assertEqual(gate["flag_disagreements"], 1)
        self.assertIn("legal_move_rule_conflict", gate["reasons"])
        self.assertIn("flag_disagreement", gate["reasons"])

    def test_rule_probe_gate_can_fail_when_chessdb_is_unavailable(self) -> None:
        module = _load_rule_probe_module()
        args = type(
            "Args",
            (),
            {
                "fail_on_legal_move_rule_conflict": False,
                "fail_on_flag_disagreement": False,
                "fail_on_chessdb_unavailable": True,
            },
        )()
        comparison = {
            "category": "chessdb_unavailable",
            "legal_move_rule_conflict": False,
        }

        gate = module._gate_summary(args, comparison)  # noqa: SLF001

        self.assertFalse(gate["valid"])
        self.assertEqual(gate["chessdb_unavailable"], 1)
        self.assertEqual(gate["reasons"], ["chessdb_unavailable"])

    def test_rule_corpus_batches_local_rule_probes(self) -> None:
        position = (
            "position fen 4k4/9/9/9/9/9/4P4/9/R8/4K4 w - - 0 1 "
            "moves a1a2 e9e8 a2a1 e8e9 a1a2 e9e8 a2a1 e8e9"
        )
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "rules.json"
            report_path = Path(tmp) / "rule_report.json"
            config_path.write_text(
                json.dumps(
                    {"positions": [{"name": "quiet_repetition", "position": position}]},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "rule_corpus.py"),
                    "--config",
                    str(config_path),
                    "--report",
                    str(report_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            output = json.loads(result.stdout)
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        module = _load_rule_corpus_module()
        self.assertEqual(output, report)
        self.assertEqual(output["report_type"], "rule_corpus")
        self.assertEqual(output["report_schema_version"], module.REPORT_SCHEMA_VERSION)
        self.assertEqual(output["config_digest"], module._config_digest(output["config"]))
        self.assertTrue(output["valid"])
        self.assertEqual(output["gate"]["reasons"], [])
        self.assertFalse(output["gate"]["fail_on_legal_move_rule_conflict"])
        self.assertFalse(output["gate"]["fail_on_flag_disagreement"])
        self.assertEqual(output["config"]["config"], str(config_path))
        self.assertFalse(output["config"]["chessdb"])
        self.assertEqual(output["config"]["positions"][0]["name"], "quiet_repetition")
        self.assertIn("moves a1a2", output["config"]["positions"][0]["position"])
        self.assertEqual(output["count"], 1)
        self.assertFalse(output["chessdb"])
        self.assertEqual(output["entries"][0]["name"], "quiet_repetition")
        self.assertEqual(output["entries"][0]["repetition"]["count"], 3)
        self.assertIn("legal_moves", output["entries"][0])
        self.assertEqual(output["entries"][0]["chessdb"], None)
        self.assertEqual(output["entries"][0]["rule_comparison"], None)
        self.assertEqual(output["comparison_summary"]["not_run"], 1)
        self.assertEqual(output["entries"][0]["chessdb_rule_query"]["reptimes"], 2)

    def test_rule_corpus_compares_local_and_chessdb_rule_flags(self) -> None:
        class FakeRuleClient:
            def query_rule(self, fen, movelist=(), reptimes=None):  # noqa: ANN001
                return ChessDbResponse(
                    ChessDbStatus.OK,
                    "move:a1a2,rule:draw",
                    rule_results=(ChessDbRuleResult("a1a2", "draw"),),
                )

        module = _load_rule_corpus_module()
        position = (
            "position fen 4k4/9/9/9/9/9/4P4/9/R8/4K4 w - - 0 1 "
            "moves a1a2 e9e8 a2a1 e8e9 a1a2 e9e8 a2a1 e8e9"
        )

        entry = module._probe_entry(  # noqa: SLF001 - focused comparison regression.
            {"name": "quiet_repetition", "position": position},
            FakeRuleClient(),
        )
        summary = module._comparison_summary([entry])  # noqa: SLF001

        self.assertEqual(entry["rule_comparison"]["category"], "candidate_flags_agree")
        self.assertTrue(entry["rule_comparison"]["flag_agreement"])
        self.assertEqual(entry["rule_comparison"]["chessdb_rules"], ["draw"])
        self.assertEqual(
            entry["rule_comparison"]["flagged_locally_legal_moves"],
            [{"move": "a1a2", "rule": "draw"}],
        )
        self.assertEqual(entry["rule_comparison"]["flagged_locally_illegal_moves"], [])
        self.assertFalse(entry["rule_comparison"]["legal_move_rule_conflict"])
        self.assertEqual(entry["rule_comparison"]["candidate_rule_mismatches"], [])
        self.assertEqual(summary["candidate_flags_agree"], 1)
        self.assertEqual(summary["legal_move_rule_conflicts"], 0)

        args = type(
            "Args",
            (),
            {
                "fail_on_legal_move_rule_conflict": True,
                "fail_on_flag_disagreement": True,
            },
        )()
        gate = module._gate_summary(args, summary)  # noqa: SLF001

        self.assertTrue(gate["valid"])
        self.assertEqual(gate["legal_move_rule_conflicts"], 0)
        self.assertEqual(gate["flag_disagreements"], 0)
        self.assertEqual(gate["reasons"], [])

    def test_rule_probe_marks_only_replayed_identity_chase_as_ban(self) -> None:
        module = _load_rule_probe_module()
        state = module.GameState.from_uci_position(
            "position fen 4k4/9/9/9/9/3N1r3/9/9/4A4/4K4 w - - 0 1 "
            "moves d4e6 f4f5 e6d4 f5f4 d4e6 f4f5 e6d4 f5f4"
        )
        legal_moves = tuple(sorted(move.to_uci() for move in state.legal_moves()))
        response = ChessDbResponse(
            ChessDbStatus.OK,
            "move:d4e6,rule:ban",
            rule_results=tuple(
                ChessDbRuleResult(move, "ban" if move == "d4e6" else "none")
                for move in legal_moves
            ),
        )

        comparison = module._rule_comparison(state, response, legal_moves)  # noqa: SLF001

        self.assertEqual(comparison["category"], "candidate_flags_agree")
        self.assertTrue(comparison["flag_agreement"])
        self.assertEqual(
            comparison["local_non_none_rule_results"],
            [{"move": "d4e6", "rule": "ban"}],
        )
        self.assertEqual(comparison["unverified_local_candidates"], [])
        self.assertEqual(comparison["candidate_rule_mismatches"], [])

    def test_rule_corpus_gate_can_fail_when_chessdb_is_unavailable(self) -> None:
        module = _load_rule_corpus_module()
        args = type(
            "Args",
            (),
            {
                "fail_on_legal_move_rule_conflict": False,
                "fail_on_flag_disagreement": False,
                "fail_on_chessdb_unavailable": True,
            },
        )()
        summary = {
            "not_run": 0,
            "chessdb_unavailable": 2,
            "candidate_flags_agree": 0,
            "candidate_flags_disagree": 0,
            "candidate_flags_unverified": 0,
            "legal_move_rule_conflicts": 0,
        }

        gate = module._gate_summary(args, summary)  # noqa: SLF001

        self.assertFalse(gate["valid"])
        self.assertEqual(gate["chessdb_unavailable"], 2)
        self.assertEqual(gate["reasons"], ["chessdb_unavailable"])

    def test_rule_corpus_skips_chessdb_for_positions_without_four_move_history(self) -> None:
        class UnexpectedRuleClient:
            def query_rule(self, fen, movelist=(), reptimes=None):  # noqa: ANN001
                raise AssertionError("queryrule requires at least four historical moves")

        module = _load_rule_corpus_module()
        entry = module._probe_entry(  # noqa: SLF001 - focused API-boundary regression.
            {"name": "start", "fen": Position.START_FEN},
            UnexpectedRuleClient(),
        )
        summary = module._comparison_summary([entry])  # noqa: SLF001

        self.assertEqual(entry["chessdb"]["status"], "not_applicable")
        self.assertEqual(entry["rule_comparison"]["category"], "queryrule_not_applicable")
        self.assertEqual(summary["queryrule_not_applicable"], 1)
        self.assertEqual(summary["chessdb_unavailable"], 0)

    def test_example_rule_corpus_loads_rule_sensitive_cases(self) -> None:
        module = _load_rule_corpus_module()
        config = json.loads((ROOT / "configs" / "rule_corpus.example.json").read_text(encoding="utf-8"))
        positions = module._positions(config)  # noqa: SLF001 - config regression.
        entries = [module._probe_entry(item, None) for item in positions]  # noqa: SLF001
        names = [entry["name"] for entry in entries]

        self.assertEqual(len(names), len(set(names)))
        self.assertIn("quiet_threefold_repetition", names)
        self.assertIn("opening_two_plies", names)
        self.assertIn("developing_knights_four_plies", names)
        self.assertIn("central_attack_skirmish", names)
        self.assertIn("immediate_loss_fallback_guard", names)
        self.assertIn("pawn_capture_tension", names)
        self.assertIn("flying_general_file_pressure", names)
        self.assertIn("cannon_screen_tension", names)
        self.assertIn("blocked_knight_leg", names)
        self.assertIn("advisor_palace_constraints", names)
        self.assertIn("elephant_river_boundary", names)
        self.assertIn("elephant_eye_pressure", names)
        self.assertIn("cannon_screen_capture_tension", names)
        self.assertIn("red_win_in_one_tactical", names)
        self.assertIn("capture_resets_counter", names)
        self.assertIn("rook_safety_fallback", names)
        self.assertIn("protected_rook_chase_seed", names)
        self.assertIn("rule60_counter_is_not_terminal", names)
        self.assertTrue(all(entry["legal_moves"] for entry in entries))
        self.assertIsNone(
            entries[names.index("rule60_counter_is_not_terminal")]["rule_judgement"]["result"]
        )


if __name__ == "__main__":
    unittest.main()
