from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path

import context  # noqa: F401
from tools.mine_pikafish_mates import REPORT_SCHEMA_VERSION, mine_reports


class MinePikafishMatesTests(unittest.TestCase):
    def test_mine_reports_preserves_first_qualifying_mate_and_local_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "match.json"
            report_path.write_text(
                json.dumps(
                    {
                        "report_type": "uci_match",
                        "valid": True,
                        "games": [
                            {
                                "game": 7,
                                "valid": True,
                                "start_name": "opening_seed",
                                "records": [
                                    {
                                        "ply": 2,
                                        "engine": "pikafish",
                                        "position": "position startpos moves h2e2",
                                        "engine_lines": [
                                            "info depth 3 score mate 3 time 25 pv h9g7",
                                            "info depth 8 score mate 3 time 250 pv h9g7 b0c2",
                                        ],
                                    },
                                    {
                                        "ply": 3,
                                        "engine": "pikafish",
                                        "position": "position startpos moves h2e2 h9g7",
                                        "engine_lines": [
                                            "info depth 4 score cp 20 time 300 pv b0c2",
                                        ],
                                    },
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            output = mine_reports([report_path], min_search_ms=100, max_mate_score=3)
            report_sha256 = hashlib.sha256(report_path.read_bytes()).hexdigest()

        self.assertEqual(output["report_type"], "pikafish_mate_mining")
        self.assertEqual(output["report_schema_version"], REPORT_SCHEMA_VERSION)
        self.assertEqual(output["summary"], {"reports": 1, "records": 2, "candidates": 1})
        candidate = output["candidates"][0]
        self.assertEqual(candidate["source_game"], 7)
        self.assertEqual(candidate["source_ply"], 2)
        self.assertEqual(candidate["target"], "black")
        self.assertEqual(candidate["mate_score"], 3)
        self.assertEqual(candidate["first_mate_time_ms"], 250)
        self.assertEqual(candidate["pv_root_move"], "h9g7")
        self.assertEqual(candidate["position"], "position startpos moves h2e2")
        self.assertEqual(candidate["source_report_sha256"], report_sha256)
        self.assertEqual(output["config"]["source_reports"], [
            {
                "path": str(report_path),
                "sha256": report_sha256,
                "report_type": "uci_match",
            }
        ])

    def test_mine_reports_rejects_invalid_source_game(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "match.json"
            report_path.write_text(
                json.dumps(
                    {
                        "report_type": "uci_match",
                        "valid": True,
                        "games": [{"game": 1, "valid": False, "records": []}],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "invalid game"):
                mine_reports([report_path], min_search_ms=100, max_mate_score=3)


if __name__ == "__main__":
    unittest.main()
