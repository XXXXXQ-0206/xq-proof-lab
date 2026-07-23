from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import context  # noqa: F401
from fixtures import RED_WIN_IN_ONE_FEN


class ProveCliTests(unittest.TestCase):
    def test_verification_reserve_compacts_proven_artifact_before_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact_path = Path(tmp) / "proof.json"
            result = subprocess.run(
                [
                    sys.executable,
                    "tools/prove.py",
                    "--fen",
                    RED_WIN_IN_ONE_FEN,
                    "--target",
                    "red",
                    "--max-ply",
                    "1",
                    "--time-limit-seconds",
                    "1",
                    "--verification-reserve-seconds",
                    "0.5",
                    "--compact-proven-certificate",
                    "--artifact",
                    str(artifact_path),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue(output["valid"])
        self.assertEqual(output["status"], "proven")
        self.assertTrue(output["certificate_compacted"])
        self.assertEqual(output["verification_reserve_seconds"], 0.5)
        self.assertTrue(output["artifact_written"])

    def test_rejects_verification_reserve_larger_than_total_limit(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "tools/prove.py",
                "--fen",
                RED_WIN_IN_ONE_FEN,
                "--target",
                "red",
                "--max-ply",
                "1",
                "--time-limit-seconds",
                "1",
                "--verification-reserve-seconds",
                "2",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("verification reserve", result.stderr)

    def test_time_limit_skips_unknown_verification_and_persistence(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "tools/prove.py",
                "--fen",
                RED_WIN_IN_ONE_FEN,
                "--target",
                "red",
                "--max-ply",
                "1",
                "--time-limit-seconds",
                "0",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        output = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(output["status"], "unknown")
        self.assertTrue(output["time_limit_reached"])
        self.assertEqual(output["nodes_searched"], 0)
        self.assertFalse(output["verification_performed"])
        self.assertFalse(output["artifact_written"])
        self.assertFalse(output["proof_saved"])


if __name__ == "__main__":
    unittest.main()
