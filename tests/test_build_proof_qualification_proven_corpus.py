from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import context  # noqa: F401
from fixtures import RED_WIN_IN_ONE_FEN
from xiangqi_core import GameState, Position
from xiangqi_solver import BoundedProofSearch, ProofStore
from tools import build_proof_qualification_proven_corpus
from tools.build_proof_qualification_proven_corpus import build_proven_corpus


class BuildProofQualificationProvenCorpusTests(unittest.TestCase):
    def test_build_preflight_config_keeps_only_replayable_position_identity(self) -> None:
        state = GameState.from_uci_position(f"position fen {RED_WIN_IN_ONE_FEN}")
        position = state.to_uci_position()
        corpus = {
            "report_type": "proof_qualification_corpus",
            "report_schema_version": 1,
            "split": "development",
            "corpus_digest": "development-corpus-digest",
            "positions": [
                {
                    "selection_key": "position-key",
                    "position": position,
                    "local_proof": {"proven_root_moves": ["a8a0"]},
                    "source_report": "offline-only-diagnostic.json",
                }
            ],
        }

        self.assertTrue(hasattr(build_proof_qualification_proven_corpus, "build_preflight_config"))
        output = build_proof_qualification_proven_corpus.build_preflight_config(
            corpus,
            corpus_sha256="corpus-sha256",
        )

        self.assertEqual(output["report_type"], "proof_qualification_preflight")
        self.assertEqual(output["source_corpus"], {
            "corpus_digest": "development-corpus-digest",
            "corpus_sha256": "corpus-sha256",
        })
        self.assertEqual(output["positions"], [{"name": "position-key", "position": position}])
        self.assertNotIn("local_proof", output["positions"][0])
        self.assertNotIn("source_report", output["positions"][0])

    def test_build_proven_corpus_reports_verification_time_limit_separately(self) -> None:
        state = GameState.from_uci_position(f"position fen {RED_WIN_IN_ONE_FEN}")
        artifact = BoundedProofSearch("red", max_ply=1).search(state).artifact
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            store_path = tmp_path / "proofs.sqlite"
            source_report_path = tmp_path / "natural-match.json"
            source_report_path.write_text("{}", encoding="utf-8")
            source_report_sha256 = __import__("hashlib").sha256(
                source_report_path.read_bytes()
            ).hexdigest()
            ProofStore(store_path).save(artifact, node_limit=100)
            candidates_path = tmp_path / "candidates.json"
            candidates_path.write_text(
                json.dumps(
                    {
                        "report_type": "pikafish_mate_mining",
                        "candidates": [
                            {
                                "position": state.to_uci_position(),
                                "target": "red",
                                "source_report": str(source_report_path),
                                "source_report_sha256": source_report_sha256,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            output = build_proven_corpus(
                candidates_path,
                store_path,
                verification_time_limit_seconds=0,
            )

        self.assertEqual(output["summary"]["verified_positions"], 0)
        self.assertEqual(output["summary"]["excluded"], {"proof_verification_time_limit": 1})

    def test_build_proven_corpus_keeps_only_locally_verified_natural_candidates(self) -> None:
        state = GameState.from_uci_position(f"position fen {RED_WIN_IN_ONE_FEN}")
        artifact = BoundedProofSearch("red", max_ply=1).search(state).artifact
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            store_path = tmp_path / "proofs.sqlite"
            source_report_path = tmp_path / "natural-match.json"
            source_report_path.write_text("{}", encoding="utf-8")
            source_report_sha256 = __import__("hashlib").sha256(
                source_report_path.read_bytes()
            ).hexdigest()
            ProofStore(store_path).save(artifact, node_limit=100)
            candidates_path = tmp_path / "candidates.json"
            candidates_path.write_text(
                json.dumps(
                    {
                        "report_type": "pikafish_mate_mining",
                        "candidates": [
                            {
                                "position": state.to_uci_position(),
                                "target": "red",
                                "source_report": str(source_report_path),
                                "source_report_sha256": source_report_sha256,
                                "source_game": 1,
                                "source_ply": 7,
                                "pv_root_move": "a8a0",
                            },
                            {
                                "position": "position startpos moves h2e2",
                                "target": "black",
                                "source_report": str(source_report_path),
                                "source_report_sha256": source_report_sha256,
                                "source_game": 1,
                                "source_ply": 8,
                                "pv_root_move": "h9g7",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            output = build_proven_corpus(candidates_path, store_path)

        self.assertEqual(output["report_type"], "proof_qualification_corpus")
        self.assertEqual(output["split"], "development")
        self.assertEqual(output["summary"]["candidates"], 2)
        self.assertEqual(output["summary"]["verified_positions"], 1)
        self.assertEqual(output["summary"]["excluded"]["proof_artifact_missing"], 1)
        position = output["positions"][0]
        self.assertEqual(position["position"], state.to_uci_position())
        self.assertEqual(position["local_proof"]["max_ply"], 1)
        self.assertEqual(position["local_proof"]["proven_root_moves"], ["a8a0"])
        self.assertEqual(output["config"]["source_reports"], [
            {"path": str(source_report_path), "sha256": source_report_sha256}
        ])


if __name__ == "__main__":
    unittest.main()
