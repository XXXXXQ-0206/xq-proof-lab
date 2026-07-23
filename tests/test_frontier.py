from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import context  # noqa: F401
from context import ROOT
from fixtures import RED_WIN_IN_ONE_FEN, TERMINAL_RED_WIN_FEN
from xiangqi_core import GameState, Move, Position
from xiangqi_solver import (
    BoundedProofSearch,
    FrontierNode,
    ProofStatus,
    ProofStore,
    ProofTarget,
    collect_frontier,
)


class FrontierTests(unittest.TestCase):
    def test_collect_frontier_from_unknown_root(self) -> None:
        artifact = BoundedProofSearch("red", max_ply=0).search(
            Position.from_fen(RED_WIN_IN_ONE_FEN)
        ).artifact
        frontier = collect_frontier(artifact)

        self.assertEqual(len(frontier), 1)
        self.assertEqual(frontier[0].fen, artifact.fen)
        self.assertEqual(frontier[0].reason, "ply_bound")
        self.assertEqual(frontier[0].history_signature, "")
        self.assertEqual(frontier[0].position_command, "")
        self.assertEqual(frontier[0].proof, artifact.proof)
        self.assertEqual(frontier[0].disproof, artifact.disproof)

    def test_collect_frontier_preserves_history_signature(self) -> None:
        state = GameState.from_uci_position(
            "position fen 4k4/9/9/9/9/3N1r3/9/9/4A4/4K4 w - - 0 1 "
            "moves d4e6 f4f5 e6d4 f5f4"
        )
        artifact = BoundedProofSearch("red", max_ply=0).search(state).artifact

        frontier = collect_frontier(artifact)

        self.assertEqual(frontier[0].history_signature, state.history_signature())
        self.assertEqual(frontier[0].position_command, state.to_uci_position())

    def test_store_frontier_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProofStore(Path(tmp) / "proofs.sqlite")
            artifact = BoundedProofSearch("red", max_ply=0).search(
                Position.from_fen(RED_WIN_IN_ONE_FEN)
            ).artifact
            count = store.enqueue_frontier(collect_frontier(artifact))
            pending = store.pending_frontier()
            summary = store.database_summary()

            self.assertEqual(count, 1)
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0].status, "pending")
            self.assertEqual(pending[0].proof, artifact.proof)
            self.assertEqual(pending[0].disproof, artifact.disproof)
            self.assertEqual(summary["frontier_jobs"]["pending"], 1)

            store.mark_frontier_running(pending[0].id)
            store.finish_frontier(pending[0].id, ProofStatus.PROVEN.value)
            self.assertEqual(store.pending_frontier(), [])

    def test_store_frontier_queue_preserves_history_signature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProofStore(Path(tmp) / "proofs.sqlite")
            state = GameState.from_uci_position(
                "position fen 4k4/9/9/9/9/3N1r3/9/9/4A4/4K4 w - - 0 1 "
                "moves d4e6 f4f5 e6d4 f5f4"
            )
            artifact = BoundedProofSearch("red", max_ply=0).search(state).artifact

            store.enqueue_frontier(collect_frontier(artifact))
            pending = store.pending_frontier()

        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].history_signature, state.history_signature())
        self.assertEqual(pending[0].position_command, state.to_uci_position())
        self.assertTrue(pending[0].position_key.startswith("history:"))

    def test_pending_frontier_prefers_low_attempt_actionable_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProofStore(Path(tmp) / "proofs.sqlite")
            store.enqueue_frontier(
                (
                    FrontierNode(
                        fen=RED_WIN_IN_ONE_FEN,
                        target=ProofTarget.RED,
                        remaining_ply=0,
                        reason="unknown_rule_state",
                    ),
                    FrontierNode(
                        fen=TERMINAL_RED_WIN_FEN,
                        target=ProofTarget.RED,
                        remaining_ply=2,
                        reason="ply_bound",
                    ),
                    FrontierNode(
                        fen=RED_WIN_IN_ONE_FEN,
                        target=ProofTarget.BLACK,
                        remaining_ply=0,
                        reason="ply_bound",
                    ),
                )
            )

            pending = store.pending_frontier(limit=3)

        self.assertEqual(
            [(job.reason, job.remaining_ply) for job in pending],
            [("ply_bound", 0), ("ply_bound", 2), ("unknown_rule_state", 0)],
        )

    def test_pending_frontier_delays_repeated_unknown_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProofStore(Path(tmp) / "proofs.sqlite")
            store.enqueue_frontier(
                (
                    FrontierNode(
                        fen=RED_WIN_IN_ONE_FEN,
                        target=ProofTarget.RED,
                        remaining_ply=0,
                        reason="ply_bound",
                    ),
                    FrontierNode(
                        fen=TERMINAL_RED_WIN_FEN,
                        target=ProofTarget.RED,
                        remaining_ply=0,
                        reason="unknown_rule_state",
                    ),
                )
            )
            retried_job = store.pending_frontier(limit=1)[0]
            store.mark_frontier_running(retried_job.id)
            store.finish_frontier(retried_job.id, ProofStatus.UNKNOWN.value)

            pending = store.pending_frontier(limit=2)

        self.assertEqual(pending[0].attempts, 0)
        self.assertEqual(pending[1].attempts, 1)

    def test_finish_frontier_backfills_unknown_result_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProofStore(Path(tmp) / "proofs.sqlite")
            store.enqueue_frontier(
                (
                    FrontierNode(
                        fen=RED_WIN_IN_ONE_FEN,
                        target=ProofTarget.RED,
                        remaining_ply=0,
                        reason="ply_bound",
                        proof=9,
                        disproof=9,
                    ),
                )
            )
            job = store.pending_frontier(limit=1)[0]
            store.mark_frontier_running(job.id)
            store.finish_frontier(
                job.id,
                ProofStatus.UNKNOWN.value,
                proof=3,
                disproof=5,
                reason="threshold",
            )

            pending = store.pending_frontier(limit=1)

        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].status, "pending")
        self.assertEqual(pending[0].attempts, 1)
        self.assertEqual(pending[0].proof, 3)
        self.assertEqual(pending[0].disproof, 5)
        self.assertEqual(pending[0].reason, "threshold")
        self.assertEqual(pending[0].last_result_status, ProofStatus.UNKNOWN.value)

    def test_finish_frontier_marks_split_unknown_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProofStore(Path(tmp) / "proofs.sqlite")
            store.enqueue_frontier(
                (
                    FrontierNode(
                        fen=RED_WIN_IN_ONE_FEN,
                        target=ProofTarget.RED,
                        remaining_ply=0,
                        reason="ply_bound",
                    ),
                )
            )
            job = store.pending_frontier(limit=1)[0]
            store.mark_frontier_running(job.id)
            store.finish_frontier(
                job.id,
                ProofStatus.UNKNOWN.value,
                proof=2,
                disproof=4,
                reason="dfpn_partial",
                split=True,
            )

            pending = store.pending_frontier(limit=1)
            done = store.iter_frontier(status="done")

        self.assertEqual(pending, [])
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0].proof, 2)
        self.assertEqual(done[0].disproof, 4)
        self.assertEqual(done[0].reason, "dfpn_partial")
        self.assertEqual(done[0].last_result_status, ProofStatus.UNKNOWN.value)

    def test_pending_frontier_uses_proof_numbers_for_ties(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProofStore(Path(tmp) / "proofs.sqlite")
            store.enqueue_frontier(
                (
                    FrontierNode(
                        fen=RED_WIN_IN_ONE_FEN,
                        target=ProofTarget.RED,
                        remaining_ply=0,
                        reason="ply_bound",
                        proof=5,
                        disproof=2,
                    ),
                    FrontierNode(
                        fen=TERMINAL_RED_WIN_FEN,
                        target=ProofTarget.RED,
                        remaining_ply=0,
                        reason="ply_bound",
                        proof=1,
                        disproof=7,
                    ),
                )
            )

            pending = store.pending_frontier(limit=2)

        self.assertEqual([(job.proof, job.disproof) for job in pending], [(1, 7), (5, 2)])

    def test_pending_frontier_can_filter_reason_and_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProofStore(Path(tmp) / "proofs.sqlite")
            store.enqueue_frontier(
                (
                    FrontierNode(
                        fen=RED_WIN_IN_ONE_FEN,
                        target=ProofTarget.RED,
                        remaining_ply=0,
                        reason="threshold",
                    ),
                    FrontierNode(
                        fen=TERMINAL_RED_WIN_FEN,
                        target=ProofTarget.RED,
                        remaining_ply=0,
                        reason="ply_bound",
                    ),
                )
            )
            threshold_job = store.pending_frontier(limit=1, reasons=("threshold",))[0]
            store.mark_frontier_running(threshold_job.id)
            store.finish_frontier(threshold_job.id, ProofStatus.UNKNOWN.value)

            threshold_fresh = store.pending_frontier(
                limit=2,
                reasons=("threshold",),
                max_attempts=0,
            )
            threshold_retried = store.pending_frontier(
                limit=2,
                reasons=("threshold",),
                max_attempts=1,
            )
            ply_bound = store.pending_frontier(limit=2, reasons=("ply_bound",))

        self.assertEqual(threshold_fresh, [])
        self.assertEqual(len(threshold_retried), 1)
        self.assertEqual(threshold_retried[0].attempts, 1)
        self.assertEqual(len(ply_bound), 1)
        self.assertEqual(ply_bound[0].reason, "ply_bound")

    def test_pending_frontier_can_filter_resource_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProofStore(Path(tmp) / "proofs.sqlite")
            store.enqueue_frontier(
                (
                    FrontierNode(
                        fen=RED_WIN_IN_ONE_FEN,
                        target=ProofTarget.RED,
                        remaining_ply=0,
                        reason="ply_bound",
                        proof=2,
                        disproof=8,
                    ),
                    FrontierNode(
                        fen=TERMINAL_RED_WIN_FEN,
                        target=ProofTarget.RED,
                        remaining_ply=2,
                        reason="ply_bound",
                        proof=1,
                        disproof=3,
                    ),
                    FrontierNode(
                        fen=RED_WIN_IN_ONE_FEN,
                        target=ProofTarget.BLACK,
                        remaining_ply=1,
                        reason="ply_bound",
                        proof=9,
                        disproof=1,
                    ),
                )
            )

            shallow_low_proof = store.pending_frontier(
                limit=10,
                max_remaining_ply=1,
                max_proof=3,
            )
            deep_low_disproof = store.pending_frontier(
                limit=10,
                min_remaining_ply=1,
                max_disproof=3,
            )

        self.assertEqual([(job.remaining_ply, job.proof) for job in shallow_low_proof], [(0, 2)])
        self.assertEqual(
            [(job.remaining_ply, job.disproof) for job in deep_low_disproof],
            [(2, 3), (1, 1)],
        )

    def test_reset_running_frontier_can_keep_fresh_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "proofs.sqlite"
            store = ProofStore(store_path)
            store.enqueue_frontier(
                (
                    FrontierNode(
                        fen=RED_WIN_IN_ONE_FEN,
                        target=ProofTarget.RED,
                        remaining_ply=0,
                        reason="ply_bound",
                    ),
                    FrontierNode(
                        fen=TERMINAL_RED_WIN_FEN,
                        target=ProofTarget.RED,
                        remaining_ply=0,
                        reason="ply_bound",
                    ),
                )
            )
            jobs = store.pending_frontier(limit=2)
            for job in jobs:
                store.mark_frontier_running(job.id)
            old_time = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
            with closing(sqlite3.connect(store_path)) as con:
                con.execute(
                    "UPDATE frontier_jobs SET updated_at = ? WHERE id = ?",
                    (old_time, jobs[0].id),
                )
                con.commit()

            reset_count = store.reset_running_frontier(max_age_seconds=60)
            statuses = {job.id: job.status for job in store.iter_frontier(limit=2)}
            reset_all_count = store.reset_running_frontier()
            final_statuses = {job.id: job.status for job in store.iter_frontier(limit=2)}

        self.assertEqual(reset_count, 1)
        self.assertEqual(statuses[jobs[0].id], "pending")
        self.assertEqual(statuses[jobs[1].id], "running")
        self.assertEqual(reset_all_count, 1)
        self.assertEqual(final_statuses[jobs[0].id], "pending")
        self.assertEqual(final_statuses[jobs[1].id], "pending")

    def test_run_frontier_cli_can_use_dfpn_and_reuse_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "proofs.sqlite"
            store = ProofStore(store_path)
            artifact = BoundedProofSearch("red", max_ply=0).search(
                Position.from_fen(RED_WIN_IN_ONE_FEN)
            ).artifact
            store.save(artifact, node_limit=1000)
            store.enqueue_frontier(collect_frontier(artifact))

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "run_frontier.py"),
                    "--store",
                    str(store_path),
                    "--limit",
                    "1",
                    "--extra-ply",
                    "1",
                    "--node-limit",
                    "1000",
                    "--searcher",
                    "dfpn",
                    "--reuse-store",
                    "--reset-running-max-age-seconds",
                    "60",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            output = json.loads(result.stdout)
            summary = ProofStore(store_path).database_summary()

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(output["searcher"], "dfpn")
        self.assertTrue(output["reuse_store"])
        self.assertEqual(output["reset_running_max_age_seconds"], 60.0)
        self.assertEqual(output["running_frontier_reset"], 0)
        self.assertIn("frontier_metrics", output)
        self.assertEqual(output["processed"][0]["status"], ProofStatus.PROVEN.value)
        self.assertIn("frontier_proof", output["processed"][0])
        self.assertIn("result_proof", output["processed"][0])
        self.assertEqual(summary["frontier_jobs"]["done"], 1)

    def test_run_frontier_cli_bounded_searcher_reuses_store(self) -> None:
        fen = "4k4/1N6R/9/3N5/4P4/9/9/9/9/4KR3 w - - 0 1"
        winning_move = Move.from_uci("i8i0")
        root = Position.from_fen(fen)
        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "proofs.sqlite"
            store = ProofStore(store_path)
            unknown = BoundedProofSearch("red", max_ply=0).search(root).artifact
            child_proof = BoundedProofSearch("red", max_ply=0).search(
                root.make_move(winning_move)
            ).artifact
            store.save(unknown, node_limit=100)
            store.save(child_proof, node_limit=100)
            store.enqueue_frontier(collect_frontier(unknown))

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "run_frontier.py"),
                    "--store",
                    str(store_path),
                    "--limit",
                    "1",
                    "--extra-ply",
                    "1",
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
            summary = ProofStore(store_path).database_summary()

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(output["searcher"], "bounded")
        self.assertTrue(output["reuse_store"])
        self.assertEqual(output["processed"][0]["status"], ProofStatus.PROVEN.value)
        self.assertGreaterEqual(output["processed"][0]["resolved_store_hits"], 1)
        self.assertEqual(summary["frontier_jobs"]["done"], 1)

    def test_run_frontier_cli_filters_reason_and_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "proofs.sqlite"
            store = ProofStore(store_path)
            store.enqueue_frontier(
                (
                    FrontierNode(
                        fen=RED_WIN_IN_ONE_FEN,
                        target=ProofTarget.RED,
                        remaining_ply=0,
                        reason="threshold",
                    ),
                    FrontierNode(
                        fen=TERMINAL_RED_WIN_FEN,
                        target=ProofTarget.RED,
                        remaining_ply=0,
                        reason="ply_bound",
                    ),
                )
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "run_frontier.py"),
                    "--store",
                    str(store_path),
                    "--limit",
                    "10",
                    "--extra-ply",
                    "1",
                    "--node-limit",
                    "1000",
                    "--reason",
                    "threshold",
                    "--max-attempts",
                    "0",
                    "--max-remaining-ply",
                    "0",
                    "--max-proof",
                    "1",
                    "--max-disproof",
                    "1",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            output = json.loads(result.stdout)
            pending = ProofStore(store_path).pending_frontier(limit=10)

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(
            output["filters"],
            {
                "reason": ["threshold"],
                "max_attempts": 0,
                "min_remaining_ply": None,
                "max_remaining_ply": 0,
                "max_proof": 1,
                "max_disproof": 1,
            },
        )
        self.assertEqual(len(output["processed"]), 1)
        self.assertEqual(output["processed"][0]["status"], ProofStatus.PROVEN.value)
        self.assertEqual([(job.reason, job.status) for job in pending], [("ply_bound", "pending")])

    def test_run_frontier_cli_stops_between_jobs_at_node_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "proofs.sqlite"
            store = ProofStore(store_path)
            store.enqueue_frontier(
                (
                    FrontierNode(
                        fen=RED_WIN_IN_ONE_FEN,
                        target=ProofTarget.RED,
                        remaining_ply=0,
                        reason="ply_bound",
                        proof=1,
                    ),
                    FrontierNode(
                        fen=TERMINAL_RED_WIN_FEN,
                        target=ProofTarget.RED,
                        remaining_ply=0,
                        reason="ply_bound",
                        proof=2,
                    ),
                )
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "run_frontier.py"),
                    "--store",
                    str(store_path),
                    "--limit",
                    "10",
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
            output = json.loads(result.stdout)
            summary = ProofStore(store_path).database_summary()

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(output["node_budget"], 1)
        self.assertTrue(output["node_budget_reached"])
        self.assertGreaterEqual(output["nodes_consumed"], 1)
        self.assertEqual(len(output["processed"]), 1)
        self.assertEqual(summary["frontier_jobs"]["done"], 1)
        self.assertEqual(summary["frontier_jobs"]["pending"], 1)

    def test_proof_db_status_reports_pending_frontier_details(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "proofs.sqlite"
            store = ProofStore(store_path)
            artifact = BoundedProofSearch("red", max_ply=1).search(
                Position.from_fen(RED_WIN_IN_ONE_FEN)
            ).artifact
            store.save(artifact, node_limit=1000)
            store.enqueue_frontier(
                (
                    FrontierNode(
                        fen=RED_WIN_IN_ONE_FEN,
                        target=ProofTarget.RED,
                        remaining_ply=0,
                        reason="ply_bound",
                        proof=3,
                        disproof=4,
                    ),
                )
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "proof_db_status.py"),
                    "--store",
                    str(store_path),
                    "--proof-limit",
                    "1",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(output["frontier_jobs"]["pending"], 1)
        self.assertEqual(output["pending_frontier"][0]["proof"], 3)
        self.assertEqual(output["pending_frontier"][0]["disproof"], 4)
        self.assertEqual(len(output["recent_proofs"]), 1)
        self.assertEqual(output["recent_proofs"][0]["status"], ProofStatus.PROVEN.value)
        self.assertEqual(output["recent_proofs"][0]["reason"], "target_move_proves")
        self.assertEqual(
            output["frontier_metrics"]["by_status_reason"][0],
            {
                "status": "pending",
                "reason": "ply_bound",
                "count": 1,
                "min_remaining_ply": 0,
                "max_remaining_ply": 0,
                "min_attempts": 0,
                "max_attempts": 0,
                "min_proof": 3,
                "max_proof": 3,
                "min_disproof": 4,
                "max_disproof": 4,
            },
        )
        self.assertEqual(output["frontier_metrics"]["by_status_attempts"][0]["count"], 1)

    def test_verify_store_checks_frontier_position_commands(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store_path = Path(tmp) / "proofs.sqlite"
            store = ProofStore(store_path)
            state = GameState.from_uci_position(
                "position fen 4k4/9/9/9/9/9/4P4/9/R8/4K4 w - - 0 1 "
                "moves a1a2 e9e8"
            )
            artifact = BoundedProofSearch("red", max_ply=0).search(state).artifact
            store.enqueue_frontier(collect_frontier(artifact))

            clean = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "verify_store.py"), "--store", str(store_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            with closing(sqlite3.connect(store_path)) as con:
                con.execute("UPDATE frontier_jobs SET position_command = ?", ("position startpos",))
                con.commit()
            corrupt = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "verify_store.py"), "--store", str(store_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            corrupt_output = json.loads(corrupt.stdout)
            del store

        self.assertEqual(clean.returncode, 0, clean.stderr or clean.stdout)
        self.assertEqual(corrupt.returncode, 1, corrupt.stderr or corrupt.stdout)
        self.assertFalse(corrupt_output["valid"])
        self.assertTrue(corrupt_output["frontier_errors"])


if __name__ == "__main__":
    unittest.main()
