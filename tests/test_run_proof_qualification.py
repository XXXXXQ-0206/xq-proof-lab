from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import context  # noqa: F401
from fixtures import RED_WIN_IN_ONE_FEN
from xiangqi_core import GameState
from xiangqi_solver import ProofStore
from tools.run_proof_qualification import run_development_ab


class RunProofQualificationTests(unittest.TestCase):
    def test_development_ab_rejects_proof_store_that_differs_from_frozen_corpus(self) -> None:
        state = GameState.from_uci_position(f"position fen {RED_WIN_IN_ONE_FEN}")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            corpus_path = tmp_path / "development.json"
            store_path = tmp_path / "proofs.sqlite"
            ProofStore(store_path)
            corpus_path.write_text(
                json.dumps(
                    {
                        "report_type": "proof_qualification_corpus",
                        "split": "development",
                        "config": {"proof_store": {"sha256": "not-the-store"}},
                        "positions": [
                            {
                                "selection_key": "test-position",
                                "position": state.to_uci_position(),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "frozen proof-store hash"):
                run_development_ab(
                    corpus_path,
                    proof_store_path=store_path,
                    max_ply=1,
                    node_limit=100,
                    local_search_depth=1,
                    local_search_node_limit=100,
                    go_command="go depth 1",
                    timeout=5.0,
                )

    def test_development_ab_reverifies_proof_and_reports_local_move_difference(self) -> None:
        state = GameState.from_uci_position(f"position fen {RED_WIN_IN_ONE_FEN}")
        position = state.to_uci_position()
        corpus = {
            "report_type": "proof_qualification_corpus",
            "report_schema_version": 1,
            "split": "development",
            "config": {"selection_rule_version": "test"},
            "corpus_digest": "test-corpus",
            "positions": [
                {
                    "selection_key": hashlib.sha256(position.encode("utf-8")).hexdigest(),
                    "position": position,
                    "fen": state.to_fen(),
                    "history_signature": state.history_signature(),
                    "side_to_move": "red",
                    "tactical_moves": [
                        {"move": "a8a0", "capture": False, "check": True}
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            corpus_path = tmp_path / "development.json"
            store_path = tmp_path / "proofs.sqlite"
            ProofStore(store_path)
            store_sha256 = hashlib.sha256(store_path.read_bytes()).hexdigest()
            corpus_path.write_text(json.dumps(corpus), encoding="utf-8")
            corpus["config"]["proof_store"] = {"sha256": store_sha256}
            corpus_path.write_text(json.dumps(corpus), encoding="utf-8")

            output = run_development_ab(
                corpus_path,
                proof_store_path=store_path,
                max_ply=1,
                node_limit=100,
                local_search_depth=1,
                local_search_node_limit=100,
                go_command="go depth 1",
                timeout=5.0,
            )
            frozen_store_sha256_after_run = hashlib.sha256(store_path.read_bytes()).hexdigest()

        self.assertFalse(output["accepted"])
        self.assertEqual(output["summary"]["positions"], 1)
        self.assertEqual(output["summary"]["proof_moves"], 1)
        self.assertEqual(output["summary"]["verified_proof_moves"], 1)
        self.assertEqual(output["summary"]["different_local_moves"], 0)
        entry = output["entries"][0]
        self.assertEqual(entry["execution_order"], ["proof", "local_only"])
        self.assertEqual(entry["proof"]["source"], "proof")
        self.assertTrue(entry["proof"]["artifact_verification"]["valid"])
        self.assertTrue(entry["proof"]["artifact_verification"]["history_continuity_valid"])
        self.assertIsInstance(entry["proof"]["artifact_verification"]["artifact_sha256"], str)
        self.assertEqual(entry["local_only"]["source"], "self_fallback")
        self.assertEqual(entry["proof"]["bestmove"], "a8a0")
        self.assertEqual(entry["local_only"]["bestmove"], "a8a0")
        self.assertTrue(output["config"]["closed"])
        self.assertFalse(output["config"]["external_inputs_allowed"])
        self.assertEqual(output["config"]["proof_store_initial_sha256"], store_sha256)
        self.assertEqual(output["config"]["frozen_proof_store_sha256"], store_sha256)
        self.assertEqual(frozen_store_sha256_after_run, store_sha256)
        self.assertEqual(output["artifacts"]["frozen_proof_store_sha256"], store_sha256)
        self.assertEqual(output["artifacts"]["runtime_proof_store_initial_sha256"], store_sha256)
        self.assertNotEqual(output["artifacts"]["runtime_proof_store_sha256"], store_sha256)

    def test_development_ab_rejects_proof_telemetry_with_different_max_ply(self) -> None:
        state = GameState.from_uci_position(f"position fen {RED_WIN_IN_ONE_FEN}")
        position = state.to_uci_position()
        corpus = {
            "report_type": "proof_qualification_corpus",
            "report_schema_version": 1,
            "split": "development",
            "config": {"selection_rule_version": "test"},
            "corpus_digest": "test-corpus",
            "positions": [{"selection_key": "test-position", "position": position}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            corpus_path = tmp_path / "development.json"
            store_path = tmp_path / "proofs.sqlite"
            ProofStore(store_path)
            corpus["config"]["proof_store"] = {
                "sha256": hashlib.sha256(store_path.read_bytes()).hexdigest()
            }
            corpus_path.write_text(json.dumps(corpus), encoding="utf-8")

            output = run_development_ab(
                corpus_path,
                proof_store_path=store_path,
                max_ply=0,
                node_limit=100,
                local_search_depth=1,
                local_search_node_limit=100,
                go_command="go depth 1",
                timeout=5.0,
            )

        verification = output["entries"][0]["proof"]["artifact_verification"]
        self.assertFalse(verification["valid"])
        self.assertEqual(verification["reason"], "proof_telemetry_max_ply_mismatch")

    def test_development_ab_rejects_holdout_corpus(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            corpus_path = Path(tmp) / "holdout.json"
            corpus_path.write_text(
                json.dumps(
                    {
                        "report_type": "proof_qualification_corpus",
                        "split": "holdout",
                        "positions": [],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "development corpus"):
                run_development_ab(corpus_path, proof_store_path=Path(tmp) / "proofs.sqlite")


if __name__ == "__main__":
    unittest.main()
