from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

import context  # noqa: F401
from context import ROOT
from xiangqi_evaluators import PersistentUciBestMoveOrderer


def _load_tool_module(name: str):
    path = ROOT / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_{name}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PersistentProofOrderingTests(unittest.TestCase):
    def test_proof_tools_select_persistent_orderer_when_requested(self) -> None:
        command = "fake-pikafish --uci"
        proof_cycle = _load_tool_module("proof_cycle")
        proof_batch = _load_tool_module("proof_batch")
        proof_worker = _load_tool_module("proof_worker")

        cycle_orderer = proof_cycle._move_orderer(
            SimpleNamespace(
                chessdb_ordering=False,
                uci_engine=command,
                uci_depth=3,
                uci_option=("Threads=16", "Hash=1024"),
                persistent_uci_ordering=True,
            )
        )
        batch_orderer = proof_batch._move_orderer(
            {
                "uci_engine": command,
                "uci_depth": 3,
                "uci_options": ["Threads=16", "Hash=1024"],
                "persistent_uci_ordering": True,
            },
            {},
            {},
        )
        worker_orderer = proof_worker._move_orderer(
            {
                "uci_engine": command,
                "uci_depth": 3,
                "uci_options": ["Threads=16", "Hash=1024"],
                "persistent_uci_ordering": True,
            }
        )

        try:
            self.assertIsInstance(cycle_orderer, PersistentUciBestMoveOrderer)
            self.assertIsInstance(batch_orderer, PersistentUciBestMoveOrderer)
            self.assertIsInstance(worker_orderer, PersistentUciBestMoveOrderer)
            self.assertEqual(cycle_orderer.depth, 3)
            self.assertEqual(batch_orderer.depth, 3)
            self.assertEqual(worker_orderer.depth, 3)
            self.assertEqual(cycle_orderer.options, (("Threads", "16"), ("Hash", "1024")))
            self.assertEqual(batch_orderer.options, (("Threads", "16"), ("Hash", "1024")))
            self.assertEqual(worker_orderer.options, (("Threads", "16"), ("Hash", "1024")))
        finally:
            for orderer in (cycle_orderer, batch_orderer, worker_orderer):
                close = getattr(orderer, "close", None)
                if callable(close):
                    close()


if __name__ == "__main__":
    unittest.main()
