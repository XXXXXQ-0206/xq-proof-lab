from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import context  # noqa: F401
from context import ROOT
from fixtures import RED_WIN_IN_ONE_FEN
from xiangqi_solver import ProofArtifact, ProofStatus, ProofVerifier


class ProofWorkerCliTests(unittest.TestCase):
    def test_worker_resumes_artifact_across_rounds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "worker.json"
            store_path = tmp_path / "proofs.sqlite"
            artifact_path = tmp_path / "worker_root.json"
            report_path = tmp_path / "worker_report.json"
            config_path.write_text(
                json.dumps(
                    {
                        "store": str(store_path),
                        "artifact": str(artifact_path),
                        "target": "red",
                        "fen": RED_WIN_IN_ONE_FEN,
                        "max_rounds": 2,
                        "initial_ply": 0,
                        "extra_ply": 1,
                        "cycles": 1,
                        "frontier_limit": 10,
                        "node_limit": 1000,
                        "node_budget": 1,
                        "reset_running_max_age_seconds": 60,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "proof_worker.py"),
                    "--config",
                    str(config_path),
                    "--report",
                    str(report_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            output = json.loads(result.stdout)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            artifact = ProofArtifact.from_dict(json.loads(artifact_path.read_text(encoding="utf-8")))
            verification = ProofVerifier().verify(artifact)

        self.assertEqual(output, report)
        self.assertTrue(output["valid"])
        self.assertEqual(output["stop_reason"], "resolved")
        self.assertEqual(output["final_status"], ProofStatus.PROVEN.value)
        self.assertIn("final_frontier_metrics", output)
        self.assertEqual(len(output["rounds"]), 2)
        self.assertFalse(output["rounds"][0]["resumed"])
        self.assertEqual(output["rounds"][0]["reset_running_max_age_seconds"], 60.0)
        self.assertTrue(output["rounds"][0]["node_budget_reached"])
        self.assertEqual(output["rounds"][0]["processed"], [])
        self.assertTrue(output["rounds"][1]["resumed"])
        self.assertEqual(output["rounds"][1]["status"], ProofStatus.PROVEN.value)
        self.assertEqual(artifact.status, ProofStatus.PROVEN)
        self.assertTrue(verification.valid, verification.errors)

    def test_worker_passes_uci_ordering_to_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "worker.json"
            artifact_path = tmp_path / "worker_root.json"
            fake_engine = tmp_path / "fake_uci.py"
            lifecycle_path = tmp_path / "lifecycle.txt"
            fake_engine.write_text(
                textwrap.dedent(
                    f"""
                    import sys
                    from pathlib import Path

                    lifecycle_path = Path({str(lifecycle_path)!r})
                    lifecycle_path.write_text("start\\n", encoding="utf-8")
                    for line in sys.stdin:
                        command = line.strip()
                        if command == "uci":
                            print("id name Fake UCI", flush=True)
                            print("uciok", flush=True)
                        elif command == "isready":
                            print("readyok", flush=True)
                        elif command.startswith("go depth"):
                            print("bestmove a8e8", flush=True)
                        elif command == "quit":
                            lifecycle_path.write_text(
                                lifecycle_path.read_text(encoding="utf-8") + "quit\\n",
                                encoding="utf-8",
                            )
                            break
                    """
                ).strip(),
                encoding="utf-8",
            )
            config_path.write_text(
                json.dumps(
                    {
                        "store": str(tmp_path / "proofs.sqlite"),
                        "artifact": str(artifact_path),
                        "target": "red",
                        "fen": RED_WIN_IN_ONE_FEN,
                        "max_rounds": 1,
                        "initial_ply": 1,
                        "cycles": 0,
                        "node_limit": 1000,
                        "uci_engine": f"{sys.executable} {fake_engine}",
                        "uci_depth": 1,
                        "persistent_uci_ordering": True,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "proof_worker.py"),
                    "--config",
                    str(config_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            artifact = ProofArtifact.from_dict(json.loads(artifact_path.read_text(encoding="utf-8")))
            lifecycle = lifecycle_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(artifact.children[0].move, "a8e8")
        self.assertEqual(lifecycle, ["start", "quit"])

    def test_worker_respects_wall_time_before_starting_next_round(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "worker.json"
            artifact_path = tmp_path / "worker_root.json"
            config_path.write_text(
                json.dumps(
                    {
                        "store": str(tmp_path / "proofs.sqlite"),
                        "artifact": str(artifact_path),
                        "target": "red",
                        "fen": RED_WIN_IN_ONE_FEN,
                        "max_rounds": 2,
                        "wall_time_seconds": 0,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "proof_worker.py"),
                    "--config",
                    str(config_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue(output["valid"])
        self.assertEqual(output["stop_reason"], "wall_time")
        self.assertEqual(output["rounds"], [])
        self.assertFalse(artifact_path.exists())


if __name__ == "__main__":
    unittest.main()
