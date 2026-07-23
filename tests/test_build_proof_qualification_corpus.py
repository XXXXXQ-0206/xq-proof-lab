from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import context  # noqa: F401
from tools.build_proof_qualification_corpus import (
    REPORT_SCHEMA_VERSION,
    build_corpus,
    build_preflight_config,
)


class BuildProofQualificationCorpusTests(unittest.TestCase):
    def test_build_corpus_selects_deduplicated_balanced_natural_tactical_positions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            primary = tmp_path / "primary.json"
            duplicate = tmp_path / "duplicate.json"
            primary.write_text(
                json.dumps(
                    {
                        "report_type": "uci_match_batch_aggregate",
                        "games": [
                            {
                                "game": 1,
                                "valid": True,
                                "start_name": "natural_opening",
                                "start_tags": ["natural"],
                                "records": [
                                    {"ply": 1, "position": "position startpos"},
                                    {"ply": 2, "position": "position startpos moves h2e2"},
                                    {
                                        "ply": 3,
                                        "position": "position startpos moves h2e2 h9g7",
                                    },
                                    {
                                        "ply": 4,
                                        "position": "position startpos moves h2e2 h9g7 e3e4",
                                    },
                                ],
                            },
                            {
                                "game": 2,
                                "valid": False,
                                "start_name": "invalid_natural",
                                "start_tags": ["natural"],
                                "records": [
                                    {
                                        "ply": 1,
                                        "position": "position startpos moves c3c4",
                                    }
                                ],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            duplicate.write_text(
                json.dumps(
                    {
                        "report_type": "uci_match_batch_aggregate",
                        "games": [
                            {
                                "game": 3,
                                "valid": True,
                                "start_name": "duplicate_natural",
                                "start_tags": ["natural"],
                                "records": [
                                    {"ply": 1, "position": "position startpos"},
                                ],
                            },
                            {
                                "game": 4,
                                "valid": True,
                                "start_name": "not_natural",
                                "start_tags": ["tactical"],
                                "records": [
                                    {
                                        "ply": 1,
                                        "position": "position startpos moves c3c4",
                                    }
                                ],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            output = build_corpus([primary, duplicate], split="development", count=4)

        self.assertEqual(output["report_type"], "proof_qualification_corpus")
        self.assertEqual(output["report_schema_version"], REPORT_SCHEMA_VERSION)
        self.assertEqual(output["split"], "development")
        self.assertEqual(output["summary"]["selected"], 4)
        self.assertEqual(output["summary"]["selected_by_side"], {"red": 2, "black": 2})
        self.assertEqual(len({entry["position"] for entry in output["positions"]}), 4)
        self.assertTrue(all(entry["tactical_moves"] for entry in output["positions"]))
        self.assertTrue(all(entry["source_game"] == 1 for entry in output["positions"]))
        self.assertEqual(
            [entry["selection_key"] for entry in output["positions"]],
            sorted(entry["selection_key"] for entry in output["positions"]),
        )

    def test_build_corpus_requires_an_even_color_balanced_count(self) -> None:
        with self.assertRaisesRegex(ValueError, "count must be positive and even"):
            build_corpus([], split="holdout", count=3)

    def test_build_preflight_config_uses_replay_position_without_fen(self) -> None:
        output = build_preflight_config(
            {
                "report_type": "proof_qualification_corpus",
                "split": "development",
                "corpus_digest": "corpus-digest",
                "positions": [
                    {
                        "selection_key": "position-key",
                        "position": "position startpos moves h2e2",
                        "fen": "ignored-for-preflight",
                    }
                ],
            }
        )

        self.assertEqual(output["source_corpus_digest"], "corpus-digest")
        self.assertEqual(
            output["positions"],
            [{"name": "position-key", "position": "position startpos moves h2e2"}],
        )


if __name__ == "__main__":
    unittest.main()
