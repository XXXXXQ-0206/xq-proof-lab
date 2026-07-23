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
from xiangqi_core import Move, Position
from xiangqi_solver import (
    BoundedProofSearch,
    ProofArtifact,
    ProofStatus,
    ProofStore,
    ProofVerifier,
    run_proof_cycle,
)


class ProofCycleCliTests(unittest.TestCase):
    def test_cycle_applies_global_time_limit_to_initial_dfpn_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            artifact_path = tmp_path / "cycle.json"
            output = run_proof_cycle(
                Position.from_fen(RED_WIN_IN_ONE_FEN),
                target="red",
                store_path=tmp_path / "cycle.sqlite",
                artifact_path=artifact_path,
                initial_ply=1,
                cycles=0,
                searcher="dfpn",
                time_limit_seconds=0,
            )
            artifact = ProofArtifact.from_dict(json.loads(artifact_path.read_text(encoding="utf-8")))

        self.assertTrue(output["time_limit_reached"])
        self.assertEqual(output["status"], ProofStatus.UNKNOWN.value)
        self.assertEqual(artifact.reason, "time_limit")

    def test_cycle_continues_frontier_and_writes_verified_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            store_path = tmp_path / "cycle.sqlite"
            artifact_path = tmp_path / "cycle.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "proof_cycle.py"),
                    "--fen",
                    RED_WIN_IN_ONE_FEN,
                    "--target",
                    "red",
                    "--store",
                    str(store_path),
                    "--artifact",
                    str(artifact_path),
                    "--initial-ply",
                    "0",
                    "--cycles",
                    "1",
                    "--extra-ply",
                    "1",
                    "--frontier-limit",
                    "4",
                    "--node-limit",
                    "1000",
                    "--reset-running-max-age-seconds",
                    "60",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            output = json.loads(result.stdout)
            artifact = ProofArtifact.from_dict(json.loads(artifact_path.read_text(encoding="utf-8")))
            verification = ProofVerifier().verify(artifact)
            summary = ProofStore(store_path).database_summary()

        self.assertEqual(output["status"], ProofStatus.PROVEN.value)
        self.assertEqual(output["reset_running_max_age_seconds"], 60.0)
        self.assertEqual(output["running_frontier_reset"], 0)
        self.assertIn("frontier_metrics", output)
        self.assertIn("frontier_proof", output["processed"][0])
        self.assertIn("result_proof", output["processed"][0])
        self.assertEqual(artifact.status, ProofStatus.PROVEN)
        self.assertTrue(verification.valid, verification.errors)
        self.assertEqual(summary["frontier_jobs"]["done"], 1)
        self.assertGreaterEqual(summary["proof_results"]["proven"], 1)

    def test_cycle_replays_uci_position_history_from_frontier(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            store_path = tmp_path / "cycle.sqlite"
            artifact_path = tmp_path / "cycle.json"
            position_command = f"position fen {RED_WIN_IN_ONE_FEN}"

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "proof_cycle.py"),
                    "--position",
                    position_command,
                    "--target",
                    "red",
                    "--store",
                    str(store_path),
                    "--artifact",
                    str(artifact_path),
                    "--initial-ply",
                    "0",
                    "--cycles",
                    "1",
                    "--extra-ply",
                    "1",
                    "--node-limit",
                    "1000",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            artifact = ProofArtifact.from_dict(json.loads(artifact_path.read_text(encoding="utf-8")))
            pending = ProofStore(store_path).iter_proofs()

        self.assertEqual(artifact.status, ProofStatus.PROVEN)
        self.assertEqual(artifact.position_command, position_command)
        self.assertTrue(any(proof.artifact.position_command == position_command for proof in pending))

    def test_cycle_resumes_existing_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            store_path = tmp_path / "cycle.sqlite"
            artifact_path = tmp_path / "cycle.json"

            initial = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "proof_cycle.py"),
                    "--fen",
                    RED_WIN_IN_ONE_FEN,
                    "--target",
                    "red",
                    "--store",
                    str(store_path),
                    "--artifact",
                    str(artifact_path),
                    "--initial-ply",
                    "0",
                    "--cycles",
                    "0",
                    "--node-limit",
                    "1000",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            resumed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "proof_cycle.py"),
                    "--resume-artifact",
                    "--target",
                    "red",
                    "--store",
                    str(store_path),
                    "--artifact",
                    str(artifact_path),
                    "--cycles",
                    "1",
                    "--extra-ply",
                    "1",
                    "--node-limit",
                    "1000",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

            self.assertEqual(initial.returncode, 0, initial.stderr or initial.stdout)
            self.assertEqual(resumed.returncode, 0, resumed.stderr or resumed.stdout)
            output = json.loads(resumed.stdout)
            artifact = ProofArtifact.from_dict(json.loads(artifact_path.read_text(encoding="utf-8")))

        self.assertTrue(output["resumed"])
        self.assertEqual(output["initial_nodes_searched"], 0)
        self.assertEqual(output["status"], ProofStatus.PROVEN.value)
        self.assertEqual(artifact.status, ProofStatus.PROVEN)

    def test_cycle_can_use_dfpn_searcher(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            store_path = tmp_path / "cycle.sqlite"
            artifact_path = tmp_path / "cycle.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "proof_cycle.py"),
                    "--fen",
                    RED_WIN_IN_ONE_FEN,
                    "--target",
                    "red",
                    "--store",
                    str(store_path),
                    "--artifact",
                    str(artifact_path),
                    "--initial-ply",
                    "0",
                    "--cycles",
                    "1",
                    "--extra-ply",
                    "1",
                    "--node-limit",
                    "1000",
                    "--searcher",
                    "dfpn",
                    "--reuse-store",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            output = json.loads(result.stdout)
            artifact = ProofArtifact.from_dict(json.loads(artifact_path.read_text(encoding="utf-8")))

        self.assertEqual(output["searcher"], "dfpn")
        self.assertTrue(output["reuse_store"])
        self.assertIn("initial_cache_hits", output)
        self.assertIn("initial_resolved_store_misses", output)
        self.assertIn("resolved_store_misses", output["processed"][0])
        self.assertEqual(output["status"], ProofStatus.PROVEN.value)
        self.assertEqual(artifact.status, ProofStatus.PROVEN)

    def test_cycle_bounded_searcher_reuses_store(self) -> None:
        fen = "4k4/1N6R/9/3N5/4P4/9/9/9/9/4KR3 w - - 0 1"
        winning_move = Move.from_uci("i8i0")
        root = Position.from_fen(fen)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            store_path = tmp_path / "cycle.sqlite"
            artifact_path = tmp_path / "cycle.json"
            store = ProofStore(store_path)
            child_proof = BoundedProofSearch("red", max_ply=0).search(
                root.make_move(winning_move)
            ).artifact
            store.save(child_proof, node_limit=100)

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "proof_cycle.py"),
                    "--fen",
                    fen,
                    "--target",
                    "red",
                    "--store",
                    str(store_path),
                    "--artifact",
                    str(artifact_path),
                    "--initial-ply",
                    "1",
                    "--cycles",
                    "0",
                    "--node-limit",
                    "1",
                    "--searcher",
                    "bounded",
                    "--reuse-store",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

            output = json.loads(result.stdout)
            artifact = ProofArtifact.from_dict(json.loads(artifact_path.read_text(encoding="utf-8")))

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(output["searcher"], "bounded")
        self.assertTrue(output["reuse_store"])
        self.assertEqual(output["status"], ProofStatus.PROVEN.value)
        self.assertGreaterEqual(output["initial_resolved_store_hits"], 1)
        self.assertGreaterEqual(output["initial_total_resolved_store_hits"], 1)
        self.assertEqual(artifact.children[0].move, winning_move.to_uci())

    def test_cycle_can_use_uci_ordering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            store_path = tmp_path / "cycle.sqlite"
            artifact_path = tmp_path / "cycle.json"
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

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "proof_cycle.py"),
                    "--fen",
                    RED_WIN_IN_ONE_FEN,
                    "--target",
                    "red",
                    "--store",
                    str(store_path),
                    "--artifact",
                    str(artifact_path),
                    "--initial-ply",
                    "1",
                    "--cycles",
                    "0",
                    "--node-limit",
                    "1000",
                    "--uci-engine",
                    f"{sys.executable} {fake_engine}",
                    "--uci-depth",
                    "1",
                    "--uci-option",
                    "Threads=16",
                    "--uci-option",
                    "Hash=1024",
                    "--persistent-uci-ordering",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            artifact = ProofArtifact.from_dict(json.loads(artifact_path.read_text(encoding="utf-8")))
            lifecycle = lifecycle_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(artifact.children[0].move, "a8e8")
        self.assertEqual(lifecycle, ["start", "quit"])

    def test_cycle_passes_dfpn_thresholds_to_initial_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            store_path = tmp_path / "cycle.sqlite"
            artifact_path = tmp_path / "cycle.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "proof_cycle.py"),
                    "--fen",
                    Position.START_FEN,
                    "--target",
                    "red",
                    "--store",
                    str(store_path),
                    "--artifact",
                    str(artifact_path),
                    "--initial-ply",
                    "1",
                    "--cycles",
                    "0",
                    "--node-limit",
                    "1000",
                    "--searcher",
                    "dfpn",
                    "--proof-threshold",
                    "1",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            output = json.loads(result.stdout)
            artifact = ProofArtifact.from_dict(json.loads(artifact_path.read_text(encoding="utf-8")))

        self.assertTrue(output["initial_threshold_reached"])
        self.assertEqual(output["status"], ProofStatus.UNKNOWN.value)
        self.assertEqual(artifact.reason, "threshold")

    def test_cycle_can_run_iterative_dfpn_threshold_growth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            store_path = tmp_path / "cycle.sqlite"
            artifact_path = tmp_path / "cycle.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "proof_cycle.py"),
                    "--fen",
                    Position.START_FEN,
                    "--target",
                    "red",
                    "--store",
                    str(store_path),
                    "--artifact",
                    str(artifact_path),
                    "--initial-ply",
                    "1",
                    "--cycles",
                    "0",
                    "--node-limit",
                    "1000",
                    "--searcher",
                    "dfpn",
                    "--proof-threshold",
                    "1",
                    "--disproof-threshold",
                    str(10**12),
                    "--dfpn-iterative",
                    "--dfpn-iterations",
                    "3",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            output = json.loads(result.stdout)
            artifact = ProofArtifact.from_dict(json.loads(artifact_path.read_text(encoding="utf-8")))

        self.assertTrue(output["dfpn_iterative"])
        self.assertTrue(output["initial_threshold_reached"])
        self.assertEqual(len(output["initial_dfpn_iterations"]), 2)
        self.assertEqual(output["initial_dfpn_iterations"][0]["reason"], "threshold")
        self.assertEqual(output["initial_dfpn_iterations"][1]["reason"], "dfpn_complete")
        self.assertGreater(output["initial_total_nodes_searched"], output["initial_nodes_searched"])
        self.assertEqual(output["status"], ProofStatus.UNKNOWN.value)
        self.assertEqual(artifact.reason, "dfpn_complete")

    def test_cycle_filters_frontier_jobs_by_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            store_path = tmp_path / "cycle.sqlite"
            artifact_path = tmp_path / "cycle.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "proof_cycle.py"),
                    "--fen",
                    RED_WIN_IN_ONE_FEN,
                    "--target",
                    "red",
                    "--store",
                    str(store_path),
                    "--artifact",
                    str(artifact_path),
                    "--initial-ply",
                    "0",
                    "--cycles",
                    "1",
                    "--extra-ply",
                    "1",
                    "--node-limit",
                    "1000",
                    "--frontier-reason",
                    "threshold",
                    "--frontier-max-attempts",
                    "0",
                    "--frontier-max-remaining-ply",
                    "0",
                    "--frontier-max-proof",
                    "1",
                    "--frontier-max-disproof",
                    "1",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            output = json.loads(result.stdout)
            summary = ProofStore(store_path).database_summary()

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(
            output["frontier_filters"],
            {
                "reasons": ["threshold"],
                "max_attempts": 0,
                "min_remaining_ply": None,
                "max_remaining_ply": 0,
                "max_proof": 1,
                "max_disproof": 1,
            },
        )
        self.assertEqual(output["processed"], [])
        self.assertEqual(summary["frontier_jobs"]["pending"], 1)

    def test_cycle_stops_between_frontier_jobs_at_time_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            store_path = tmp_path / "cycle.sqlite"
            artifact_path = tmp_path / "cycle.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "proof_cycle.py"),
                    "--fen",
                    RED_WIN_IN_ONE_FEN,
                    "--target",
                    "red",
                    "--store",
                    str(store_path),
                    "--artifact",
                    str(artifact_path),
                    "--initial-ply",
                    "0",
                    "--cycles",
                    "1",
                    "--extra-ply",
                    "1",
                    "--node-limit",
                    "1000",
                    "--time-limit-seconds",
                    "0",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            output = json.loads(result.stdout)
            artifact = ProofArtifact.from_dict(json.loads(artifact_path.read_text(encoding="utf-8")))
            summary = ProofStore(store_path).database_summary()

        self.assertTrue(output["time_limit_reached"])
        self.assertEqual(output["processed"], [])
        self.assertEqual(output["status"], ProofStatus.UNKNOWN.value)
        self.assertEqual(artifact.status, ProofStatus.UNKNOWN)
        self.assertEqual(summary["frontier_jobs"]["pending"], 1)

    def test_cycle_stops_before_frontier_when_node_budget_reached(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            store_path = tmp_path / "cycle.sqlite"
            artifact_path = tmp_path / "cycle.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "proof_cycle.py"),
                    "--fen",
                    RED_WIN_IN_ONE_FEN,
                    "--target",
                    "red",
                    "--store",
                    str(store_path),
                    "--artifact",
                    str(artifact_path),
                    "--initial-ply",
                    "0",
                    "--cycles",
                    "1",
                    "--extra-ply",
                    "1",
                    "--node-limit",
                    "1000",
                    "--node-budget",
                    "1",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            output = json.loads(result.stdout)
            artifact = ProofArtifact.from_dict(json.loads(artifact_path.read_text(encoding="utf-8")))
            summary = ProofStore(store_path).database_summary()

        self.assertEqual(output["node_budget"], 1)
        self.assertTrue(output["node_budget_reached"])
        self.assertEqual(output["nodes_consumed"], 1)
        self.assertEqual(output["processed"], [])
        self.assertEqual(output["status"], ProofStatus.UNKNOWN.value)
        self.assertEqual(artifact.status, ProofStatus.UNKNOWN)
        self.assertEqual(summary["frontier_jobs"]["pending"], 1)


if __name__ == "__main__":
    unittest.main()
