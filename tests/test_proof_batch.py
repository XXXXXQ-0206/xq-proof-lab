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
from xiangqi_core import GameState, Position
from xiangqi_solver import ProofArtifact, ProofStatus, ProofStore, ProofVerifier


class ProofBatchCliTests(unittest.TestCase):
    def test_pikafish_proof_store_batch_has_replayable_tactical_and_opening_roots(self) -> None:
        config_path = ROOT / "configs" / "pikafish_proof_store_batch.example.json"
        launcher_path = ROOT / "scripts" / "run_proof_assisted_pikafish.cmd"

        config = json.loads(config_path.read_text(encoding="utf-8"))
        jobs = config["jobs"]

        self.assertEqual(config["store"], "database/pikafish_proofs.sqlite")
        self.assertEqual(
            {job["name"] for job in jobs},
            {
                "red_tactical_mate1",
                "black_tactical_mate1",
                "opening_black_mate1_ply22",
                "opening_black_mate4_ply16",
                "opening_red_mate1_ply17",
                "opening_red_mate1_orderingfix_ply17",
            },
        )
        self.assertIn("database\\pikafish_proofs.sqlite", launcher_path.read_text(encoding="utf-8"))
        for job in jobs:
            if "position" in job:
                state = GameState.from_uci_position(job["position"])
            else:
                state = Position.from_fen(job["fen"])
            self.assertTrue(state.legal_moves())
        opening_jobs = [job for job in jobs if job["name"].startswith("opening_")]
        self.assertTrue(all(job["source_match_report"] for job in opening_jobs))

    def test_batch_config_runs_named_proof_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "batch.json"
            store_path = tmp_path / "proofs.sqlite"
            artifact_path = tmp_path / "red_win_in_one.json"
            report_path = tmp_path / "report.json"
            config_path.write_text(
                json.dumps(
                    {
                        "store": str(store_path),
                        "defaults": {
                            "target": "red",
                            "initial_ply": 0,
                            "extra_ply": 1,
                            "cycles": 1,
                            "frontier_limit": 4,
                            "node_limit": 1000,
                            "searcher": "dfpn",
                            "reuse_store": True,
                            "reset_running_max_age_seconds": 60,
                        },
                        "jobs": [
                            {
                                "name": "red_win_in_one",
                                "fen": RED_WIN_IN_ONE_FEN,
                                "artifact": str(artifact_path),
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "proof_batch.py"),
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
            summary = ProofStore(store_path).database_summary()

        self.assertTrue(output["valid"])
        self.assertEqual(report, output)
        self.assertIn("started_at", output)
        self.assertIn("finished_at", output)
        self.assertGreaterEqual(output["elapsed_seconds"], 0)
        self.assertEqual(output["jobs"][0]["name"], "red_win_in_one")
        self.assertIn("started_at", output["jobs"][0])
        self.assertIn("finished_at", output["jobs"][0])
        self.assertGreaterEqual(output["jobs"][0]["elapsed_seconds"], 0)
        self.assertEqual(output["jobs"][0]["status"], ProofStatus.PROVEN.value)
        self.assertEqual(output["jobs"][0]["searcher"], "dfpn")
        self.assertTrue(output["jobs"][0]["reuse_store"])
        self.assertEqual(output["jobs"][0]["reset_running_max_age_seconds"], 60.0)
        self.assertEqual(artifact.status, ProofStatus.PROVEN)
        self.assertTrue(verification.valid, verification.errors)
        self.assertEqual(summary["frontier_jobs"]["done"], 1)

    def test_batch_respects_enabled_flag_and_max_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "batch.json"
            first_artifact = tmp_path / "first.json"
            second_artifact = tmp_path / "second.json"
            disabled_artifact = tmp_path / "disabled.json"
            config_path.write_text(
                json.dumps(
                    {
                        "store": str(tmp_path / "proofs.sqlite"),
                        "defaults": {
                            "target": "red",
                            "initial_ply": 0,
                            "extra_ply": 1,
                            "cycles": 1,
                            "node_limit": 1000,
                        },
                        "jobs": [
                            {
                                "name": "first",
                                "fen": RED_WIN_IN_ONE_FEN,
                                "artifact": str(first_artifact),
                            },
                            {
                                "name": "disabled",
                                "enabled": False,
                                "fen": RED_WIN_IN_ONE_FEN,
                                "artifact": str(disabled_artifact),
                            },
                            {
                                "name": "second",
                                "fen": RED_WIN_IN_ONE_FEN,
                                "artifact": str(second_artifact),
                            },
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "proof_batch.py"),
                    "--config",
                    str(config_path),
                    "--max-jobs",
                    "1",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            output = json.loads(result.stdout)
            first_exists = first_artifact.exists()
            second_exists = second_artifact.exists()
            disabled_exists = disabled_artifact.exists()

        self.assertTrue(first_exists)
        self.assertFalse(second_exists)
        self.assertFalse(disabled_exists)
        self.assertEqual(
            [(job["name"], job["status"]) for job in output["jobs"]],
            [("first", "proven"), ("disabled", "skipped"), ("second", "skipped")],
        )

    def test_batch_respects_time_limit_between_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "batch.json"
            artifact_path = tmp_path / "first.json"
            config_path.write_text(
                json.dumps(
                    {
                        "store": str(tmp_path / "proofs.sqlite"),
                        "defaults": {
                            "target": "red",
                            "initial_ply": 0,
                            "extra_ply": 1,
                            "cycles": 1,
                            "node_limit": 1000,
                        },
                        "jobs": [
                            {
                                "name": "first",
                                "fen": RED_WIN_IN_ONE_FEN,
                                "artifact": str(artifact_path),
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "proof_batch.py"),
                    "--config",
                    str(config_path),
                    "--time-limit-seconds",
                    "0",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            output = json.loads(result.stdout)
            artifact_exists = artifact_path.exists()

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue(output["valid"])
        self.assertEqual(output["jobs"][0]["status"], "skipped")
        self.assertEqual(output["jobs"][0]["reason"], "time_limit")
        self.assertFalse(artifact_exists)

    def test_batch_passes_job_time_limit_to_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "batch.json"
            artifact_path = tmp_path / "first.json"
            config_path.write_text(
                json.dumps(
                    {
                        "store": str(tmp_path / "proofs.sqlite"),
                        "defaults": {
                            "target": "red",
                            "initial_ply": 0,
                            "extra_ply": 1,
                            "cycles": 1,
                            "node_limit": 1000,
                            "time_limit_seconds": 0,
                        },
                        "jobs": [
                            {
                                "name": "first",
                                "fen": RED_WIN_IN_ONE_FEN,
                                "artifact": str(artifact_path),
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "proof_batch.py"), "--config", str(config_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            output = json.loads(result.stdout)
            artifact = ProofArtifact.from_dict(json.loads(artifact_path.read_text(encoding="utf-8")))

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue(output["jobs"][0]["time_limit_reached"])
        self.assertEqual(output["jobs"][0]["status"], ProofStatus.UNKNOWN.value)
        self.assertEqual(artifact.status, ProofStatus.UNKNOWN)

    def test_batch_passes_job_node_budget_to_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "batch.json"
            artifact_path = tmp_path / "first.json"
            config_path.write_text(
                json.dumps(
                    {
                        "store": str(tmp_path / "proofs.sqlite"),
                        "defaults": {
                            "target": "red",
                            "initial_ply": 0,
                            "extra_ply": 1,
                            "cycles": 1,
                            "node_limit": 1000,
                            "node_budget": 1,
                        },
                        "jobs": [
                            {
                                "name": "first",
                                "fen": RED_WIN_IN_ONE_FEN,
                                "artifact": str(artifact_path),
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "proof_batch.py"), "--config", str(config_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            output = json.loads(result.stdout)
            artifact = ProofArtifact.from_dict(json.loads(artifact_path.read_text(encoding="utf-8")))

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(output["jobs"][0]["node_budget"], 1)
        self.assertTrue(output["jobs"][0]["node_budget_reached"])
        self.assertEqual(output["jobs"][0]["nodes_consumed"], 1)
        self.assertEqual(output["jobs"][0]["processed"], [])
        self.assertEqual(output["jobs"][0]["status"], ProofStatus.UNKNOWN.value)
        self.assertEqual(artifact.status, ProofStatus.UNKNOWN)

    def test_batch_passes_dfpn_thresholds_to_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "batch.json"
            artifact_path = tmp_path / "first.json"
            config_path.write_text(
                json.dumps(
                    {
                        "store": str(tmp_path / "proofs.sqlite"),
                        "defaults": {
                            "target": "red",
                            "initial_ply": 1,
                            "cycles": 0,
                            "node_limit": 1000,
                            "searcher": "dfpn",
                            "proof_threshold": 1,
                        },
                        "jobs": [
                            {
                                "name": "threshold",
                                "fen": Position.START_FEN,
                                "artifact": str(artifact_path),
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "proof_batch.py"), "--config", str(config_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            output = json.loads(result.stdout)
            artifact = ProofArtifact.from_dict(json.loads(artifact_path.read_text(encoding="utf-8")))

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue(output["jobs"][0]["initial_threshold_reached"])
        self.assertEqual(output["jobs"][0]["status"], ProofStatus.UNKNOWN.value)
        self.assertEqual(artifact.reason, "threshold")

    def test_batch_passes_iterative_dfpn_settings_to_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "batch.json"
            artifact_path = tmp_path / "first.json"
            config_path.write_text(
                json.dumps(
                    {
                        "store": str(tmp_path / "proofs.sqlite"),
                        "defaults": {
                            "target": "red",
                            "initial_ply": 1,
                            "cycles": 0,
                            "node_limit": 1000,
                            "searcher": "dfpn",
                            "proof_threshold": 1,
                            "disproof_threshold": 10**12,
                            "dfpn_iterative": True,
                            "dfpn_iterations": 3,
                        },
                        "jobs": [
                            {
                                "name": "iterative",
                                "fen": Position.START_FEN,
                                "artifact": str(artifact_path),
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "proof_batch.py"), "--config", str(config_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            output = json.loads(result.stdout)
            artifact = ProofArtifact.from_dict(json.loads(artifact_path.read_text(encoding="utf-8")))

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue(output["jobs"][0]["dfpn_iterative"])
        self.assertTrue(output["jobs"][0]["initial_threshold_reached"])
        self.assertEqual(len(output["jobs"][0]["initial_dfpn_iterations"]), 2)
        self.assertEqual(output["jobs"][0]["initial_dfpn_iterations"][1]["reason"], "dfpn_complete")
        self.assertEqual(artifact.reason, "dfpn_complete")

    def test_batch_passes_uci_ordering_to_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "batch.json"
            artifact_path = tmp_path / "first.json"
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
                        "defaults": {
                            "target": "red",
                            "initial_ply": 1,
                            "cycles": 0,
                            "node_limit": 1000,
                            "uci_engine": f"{sys.executable} {fake_engine}",
                            "uci_depth": 1,
                            "persistent_uci_ordering": True,
                        },
                        "jobs": [
                            {
                                "name": "uci_ordered",
                                "fen": RED_WIN_IN_ONE_FEN,
                                "artifact": str(artifact_path),
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "proof_batch.py"), "--config", str(config_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            artifact = ProofArtifact.from_dict(json.loads(artifact_path.read_text(encoding="utf-8")))
            lifecycle = lifecycle_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(artifact.children[0].move, "a8e8")
        self.assertEqual(lifecycle, ["start", "quit"])

    def test_batch_passes_frontier_filters_to_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "batch.json"
            artifact_path = tmp_path / "first.json"
            config_path.write_text(
                json.dumps(
                    {
                        "store": str(tmp_path / "proofs.sqlite"),
                        "defaults": {
                            "target": "red",
                            "initial_ply": 0,
                            "extra_ply": 1,
                            "cycles": 1,
                            "node_limit": 1000,
                            "frontier_reasons": ["threshold"],
                            "frontier_max_attempts": 0,
                            "frontier_max_remaining_ply": 0,
                            "frontier_max_proof": 1,
                            "frontier_max_disproof": 1,
                        },
                        "jobs": [
                            {
                                "name": "filtered",
                                "fen": RED_WIN_IN_ONE_FEN,
                                "artifact": str(artifact_path),
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "proof_batch.py"), "--config", str(config_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(
            output["jobs"][0]["frontier_filters"],
            {
                "reasons": ["threshold"],
                "max_attempts": 0,
                "min_remaining_ply": None,
                "max_remaining_ply": 0,
                "max_proof": 1,
                "max_disproof": 1,
            },
        )
        self.assertEqual(output["jobs"][0]["processed"], [])
        self.assertEqual(output["jobs"][0]["database"]["frontier_jobs"]["pending"], 1)

    def test_batch_can_resume_existing_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "batch.json"
            store_path = tmp_path / "proofs.sqlite"
            artifact_path = tmp_path / "resume.json"
            seed_config = {
                "store": str(store_path),
                "defaults": {
                    "target": "red",
                    "initial_ply": 0,
                    "extra_ply": 1,
                    "cycles": 0,
                    "node_limit": 1000,
                },
                "jobs": [
                    {
                        "name": "seed",
                        "fen": RED_WIN_IN_ONE_FEN,
                        "artifact": str(artifact_path),
                    }
                ],
            }
            resume_config = {
                "store": str(store_path),
                "defaults": {
                    "target": "red",
                    "extra_ply": 1,
                    "cycles": 1,
                    "node_limit": 1000,
                },
                "jobs": [
                    {
                        "name": "resume",
                        "resume_artifact": True,
                        "artifact": str(artifact_path),
                    }
                ],
            }
            config_path.write_text(json.dumps(seed_config), encoding="utf-8")
            seed = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "proof_batch.py"), "--config", str(config_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            config_path.write_text(json.dumps(resume_config), encoding="utf-8")
            resumed = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "proof_batch.py"), "--config", str(config_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

            self.assertEqual(seed.returncode, 0, seed.stderr or seed.stdout)
            self.assertEqual(resumed.returncode, 0, resumed.stderr or resumed.stdout)
            output = json.loads(resumed.stdout)
            artifact = ProofArtifact.from_dict(json.loads(artifact_path.read_text(encoding="utf-8")))

        self.assertEqual(output["jobs"][0]["status"], ProofStatus.PROVEN.value)
        self.assertTrue(output["jobs"][0]["resumed"])
        self.assertEqual(artifact.status, ProofStatus.PROVEN)

    def test_batch_continue_on_error_runs_later_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "batch.json"
            store_path = tmp_path / "proofs.sqlite"
            later_artifact = tmp_path / "later.json"
            config_path.write_text(
                json.dumps(
                    {
                        "store": str(store_path),
                        "defaults": {
                            "target": "red",
                            "initial_ply": 0,
                            "extra_ply": 1,
                            "cycles": 1,
                            "node_limit": 1000,
                        },
                        "jobs": [
                            {
                                "name": "missing",
                                "resume_artifact": True,
                                "artifact": str(tmp_path / "missing.json"),
                            },
                            {
                                "name": "later",
                                "fen": RED_WIN_IN_ONE_FEN,
                                "artifact": str(later_artifact),
                            },
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "proof_batch.py"),
                    "--config",
                    str(config_path),
                    "--continue-on-error",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            output = json.loads(result.stdout)
            later_exists = later_artifact.exists()

        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
        self.assertFalse(output["valid"])
        self.assertFalse(output["jobs"][0]["valid"])
        self.assertEqual(output["jobs"][1]["status"], ProofStatus.PROVEN.value)
        self.assertTrue(later_exists)


if __name__ == "__main__":
    unittest.main()
