from __future__ import annotations

import io
import subprocess
import sys
import shlex
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from queue import Empty, Queue
from threading import Event
from threading import Thread
from unittest.mock import patch

import context  # noqa: F401
from fixtures import IMMEDIATE_LOSS_FEN, RED_WIN_IN_ONE_FEN, TERMINAL_RED_WIN_FEN
from xiangqi_core import Color, GameState, Move, Position
from xiangqi_evaluators import (
    CachedMoveOrderer,
    HeuristicMoveOrderer,
    UciBestMoveOrderer,
    split_engine_command,
)
from xiangqi_solver import (
    BoundedProofSearch,
    ProofAssistedUciEngine,
    ProofStatus,
    ProofStore,
    ProofTarget,
    ProofVerifier,
)
from xiangqi_solver.pns import NodeKind
from xiangqi_solver.proof import ProofArtifact
from xiangqi_solver.uci_loop import (
    _parse_go_limits,
    _ordering_time_limit_ms,
    _proof_search_time_limit_seconds,
    _proof_aware_fallback_move,
    _root_limited_move_orderer,
    _search_position,
    _searchmoves_limited_result,
    run_uci_loop,
)


def _write_fake_uci(directory: Path, source: str) -> Path:
    fake_engine = directory / "fake uci.py"
    fake_engine.write_text(textwrap.dedent(source).strip(), encoding="utf-8")
    return fake_engine


def _shell_command(parts: list[str]) -> str:
    if sys.platform == "win32":
        return subprocess.list2cmdline(parts)
    return shlex.join(parts)


class ProofUciTests(unittest.TestCase):
    def test_tool_announces_clear_hash_option(self) -> None:
        output = io.StringIO()

        result = run_uci_loop(
            ProofAssistedUciEngine(),
            input_stream=io.StringIO("uci\nquit\n"),
            output_stream=output,
        )

        self.assertEqual(result, 0)
        self.assertIn("option name Clear Hash type button", output.getvalue())
        self.assertIn("option name Ponder type check default false", output.getvalue())

    def test_invalid_go_prefix_is_not_treated_as_search(self) -> None:
        output = io.StringIO()

        result = run_uci_loop(
            ProofAssistedUciEngine(),
            input_stream=io.StringIO("gobad\nquit\n"),
            output_stream=output,
        )

        self.assertEqual(result, 0)
        text = output.getvalue()
        self.assertIn("info string unsupported command: gobad", text)
        self.assertNotIn("bestmove ", text)

    def test_tool_does_not_announce_fallback_options_without_fallback(self) -> None:
        output = io.StringIO()

        result = run_uci_loop(
            ProofAssistedUciEngine(),
            input_stream=io.StringIO("uci\nquit\n"),
            output_stream=output,
        )

        self.assertEqual(result, 0)
        self.assertNotIn("option name Hash type spin", output.getvalue())
        self.assertNotIn("option name Threads type spin", output.getvalue())

    def test_tool_announces_external_fallback_options(self) -> None:
        output = io.StringIO()
        engine = ProofAssistedUciEngine(
            move_orderer=CachedMoveOrderer(
                UciBestMoveOrderer([sys.executable, "-c", "pass"], depth=1)
            )
        )

        result = run_uci_loop(
            engine,
            input_stream=io.StringIO("uci\nquit\n"),
            output_stream=output,
        )

        self.assertEqual(result, 0)
        text = output.getvalue()
        self.assertIn("option name Hash type spin", text)
        self.assertIn("option name Threads type spin", text)
        self.assertIn("option name MultiPV type spin", text)

    def test_cli_announces_external_fallback_options(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_engine = _write_fake_uci(
                Path(tmp),
                """
                import sys
                for line in sys.stdin:
                    command = line.strip()
                    if command == "uci":
                        print("id name Fake UCI", flush=True)
                        print("uciok", flush=True)
                    elif command == "isready":
                        print("readyok", flush=True)
                    elif command == "quit":
                        break
                """,
            )
            fallback_command = _shell_command([sys.executable, str(fake_engine)])
            process = subprocess.Popen(
                [
                    sys.executable,
                    "tools/proof_uci.py",
                    "--fallback-uci-engine",
                    fallback_command,
                    "--fallback-uci-depth",
                    "1",
                    "--fallback-uci-multipv",
                    "3",
                    "--fallback-uci-option",
                    "Threads=2",
                    "--fallback-uci-option",
                    "Hash=64",
                    "--fallback-uci-timeout",
                    "2",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, stderr = process.communicate("uci\nquit\n", timeout=10)

        self.assertEqual(process.returncode, 0, stderr)
        self.assertIn("option name Hash type spin default 64", stdout)
        self.assertIn("option name Threads type spin default 2", stdout)
        self.assertIn("option name MultiPV type spin default 3", stdout)

    def test_cli_can_enable_ponder_before_runtime_setoption(self) -> None:
        process = subprocess.Popen(
            [
                sys.executable,
                "tools/proof_uci.py",
                "--ponder",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = process.communicate("uci\nquit\n", timeout=10)

        self.assertEqual(process.returncode, 0, stderr)
        self.assertIn("option name Ponder type check default true", stdout)

    def test_cli_closed_mode_rejects_external_fallback(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "tools/proof_uci.py",
                "--closed",
                "--fallback-uci-engine",
                "pikafish.exe",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--closed forbids --fallback-uci-engine", result.stderr)

    def test_cli_local_only_rejects_proof_store(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "tools/proof_uci.py",
                "--closed",
                "--local-only",
                "--proof-store",
                "database/qualification.sqlite",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--local-only forbids --proof-store", result.stderr)

    def test_cli_local_only_uses_self_fallback_for_proven_root(self) -> None:
        process = subprocess.Popen(
            [
                sys.executable,
                "tools/proof_uci.py",
                "--closed",
                "--local-only",
                "--max-ply",
                "1",
                "--node-limit",
                "100",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = process.communicate(
            f"uci\nisready\nposition fen {RED_WIN_IN_ONE_FEN}\ngo depth 1\nquit\n",
            timeout=10,
        )

        self.assertEqual(process.returncode, 0, stderr)
        self.assertIn("info string source=self_fallback", stdout)
        self.assertNotIn("info string source=proof ", stdout)
        self.assertIn("bestmove a8a0", stdout)

    def test_proof_move_is_used_when_available(self) -> None:
        engine = ProofAssistedUciEngine(max_ply=1)
        engine.set_position(f"position fen {RED_WIN_IN_ONE_FEN}")

        best = engine.choose_bestmove()

        self.assertEqual(best.move, "a8a0")
        self.assertEqual(best.source, "proof")
        self.assertEqual(best.proof_status, ProofStatus.PROVEN)

    def test_position_preloads_verified_proof_store_hit_before_go(self) -> None:
        position_command = f"position fen {RED_WIN_IN_ONE_FEN}"
        artifact = BoundedProofSearch("red", max_ply=1).search(
            Position.from_fen(RED_WIN_IN_ONE_FEN)
        ).artifact

        class CountingStore:
            def __init__(self) -> None:
                self.calls = 0

            def resolve_proven(self, *_args, **_kwargs):
                self.calls += 1
                return artifact

        store = CountingStore()
        engine = ProofAssistedUciEngine(max_ply=1, proof_store=store)
        engine.set_position(position_command)

        self.assertEqual(store.calls, 1)
        best = engine.choose_bestmove(time_limit_ms=0)

        self.assertEqual(best.move, "a8a0")
        self.assertEqual(best.source, "proof_store")
        self.assertEqual(store.calls, 1)

    def test_external_ordering_cannot_claim_a_proof_move(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_engine = _write_fake_uci(
                Path(tmp),
                """
                import sys
                for line in sys.stdin:
                    command = line.strip()
                    if command == "uci":
                        print("id name Fake UCI", flush=True)
                        print("uciok", flush=True)
                    elif command == "isready":
                        print("readyok", flush=True)
                    elif command.startswith("go depth"):
                        print("bestmove a8g8", flush=True)
                    elif command == "quit":
                        break
                """,
            )
            engine = ProofAssistedUciEngine(
                max_ply=1,
                move_orderer=UciBestMoveOrderer([sys.executable, str(fake_engine)], depth=1),
            )
            engine.set_position(f"position fen {RED_WIN_IN_ONE_FEN}")

            best = engine.choose_bestmove()

        self.assertEqual(best.move, "a8a0")
        self.assertEqual(best.source, "external_fallback")
        self.assertEqual(best.proof_status, ProofStatus.UNKNOWN)
        self.assertEqual(best.reason, "external_ordering")

    def test_halfmove_120_position_still_gets_fallback_bestmove(self) -> None:
        engine = ProofAssistedUciEngine(max_ply=0)
        engine.set_position("position fen 4k4/9/9/9/9/9/4P4/9/9/4K4 w - - 120 1")

        best = engine.choose_bestmove()

        self.assertNotEqual(best.move, "0000")
        self.assertEqual(best.source, "self_fallback")
        self.assertEqual(best.proof_status, ProofStatus.UNKNOWN)

    def test_external_uci_fallback_has_distinct_source(self) -> None:
        class ExternalOrderer(HeuristicMoveOrderer):
            def bestmove_with_go_command(self, _position, moves, _go_command):
                return moves[0]

        engine = ProofAssistedUciEngine(
            max_ply=0,
            move_orderer=ExternalOrderer(),
            prefer_external_fallback=True,
        )
        engine.set_position("position startpos")

        best = engine.choose_bestmove(fallback_go_command="go depth 1")

        self.assertEqual(best.source, "external_fallback")
        self.assertEqual(best.reason, "external_uci")

    def test_go_budget_override_does_not_mutate_engine_defaults(self) -> None:
        engine = ProofAssistedUciEngine(max_ply=1, node_limit=100)
        engine.set_position(f"position fen {RED_WIN_IN_ONE_FEN}")

        shallow = engine.choose_bestmove(max_ply=0, node_limit=7)
        default = engine.choose_bestmove()

        self.assertEqual(shallow.source, "self_fallback")
        self.assertEqual(shallow.proof_status, ProofStatus.UNKNOWN)
        self.assertEqual(shallow.max_ply, 0)
        self.assertEqual(shallow.node_limit, 7)
        self.assertEqual(default.move, "a8a0")
        self.assertEqual(default.source, "proof")
        self.assertEqual(default.max_ply, 1)
        self.assertEqual(default.node_limit, 100)

    def test_clear_hash_option_clears_move_orderer_cache_without_resetting_position(self) -> None:
        class CountingOrderer:
            def __init__(self) -> None:
                self.calls = 0

            def order_moves(self, position, moves):
                self.calls += 1
                return sorted(moves, key=lambda move: move.to_uci())

        orderer = CountingOrderer()
        engine = ProofAssistedUciEngine(max_ply=0, move_orderer=CachedMoveOrderer(orderer))
        engine.set_position("position startpos")

        first = engine.choose_bestmove()
        engine.set_option("Clear Hash", None)
        second = engine.choose_bestmove()

        self.assertEqual(first.move, second.move)
        self.assertEqual(orderer.calls, 2)

    def test_proof_store_does_not_bypass_requested_max_ply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProofStore(Path(tmp) / "proofs.sqlite")
            artifact = BoundedProofSearch("red", max_ply=1).search(
                Position.from_fen(RED_WIN_IN_ONE_FEN)
            ).artifact
            store.save(artifact, node_limit=100)
            engine = ProofAssistedUciEngine(max_ply=1, proof_store=store)
            engine.set_position(f"position fen {RED_WIN_IN_ONE_FEN}")

            shallow = engine.choose_bestmove(max_ply=0)
            default = engine.choose_bestmove()

        self.assertEqual(shallow.source, "self_fallback")
        self.assertEqual(shallow.proof_status, ProofStatus.UNKNOWN)
        self.assertEqual(shallow.max_ply, 0)
        self.assertEqual(default.move, "a8a0")
        self.assertEqual(default.source, "proof_store")
        self.assertEqual(default.proof_status, ProofStatus.PROVEN)
        self.assertEqual(default.nodes_searched, 0)
        self.assertEqual(default.max_ply, 1)

    def test_proof_store_hit_does_not_query_move_orderer(self) -> None:
        class CountingOrderer:
            def __init__(self) -> None:
                self.calls = 0

            def order_moves(self, position, moves):
                self.calls += 1
                return sorted(moves, key=lambda move: move.to_uci())

        with tempfile.TemporaryDirectory() as tmp:
            store = ProofStore(Path(tmp) / "proofs.sqlite")
            artifact = BoundedProofSearch("red", max_ply=1).search(
                Position.from_fen(RED_WIN_IN_ONE_FEN)
            ).artifact
            store.save(artifact, node_limit=100)
            orderer = CountingOrderer()
            engine = ProofAssistedUciEngine(
                max_ply=1,
                proof_store=store,
                move_orderer=orderer,
            )
            engine.set_position(f"position fen {RED_WIN_IN_ONE_FEN}")

            best = engine.choose_bestmove()

        self.assertEqual(best.move, "a8a0")
        self.assertEqual(best.source, "proof_store")
        self.assertEqual(best.proof_status, ProofStatus.PROVEN)
        self.assertEqual(orderer.calls, 0)

    def test_proof_store_respects_searchmoves_even_when_orderer_prefers_other_move(self) -> None:
        class PreferA8B8Orderer:
            def order_moves(self, position, moves):
                fallback = sorted(moves, key=lambda move: move.to_uci())
                legal_by_uci = {move.to_uci(): move for move in fallback}
                preferred = "a8b8"
                if preferred not in legal_by_uci:
                    return fallback
                return [legal_by_uci[preferred]] + [
                    move for move in fallback if move.to_uci() != preferred
                ]

        with tempfile.TemporaryDirectory() as tmp:
            store = ProofStore(Path(tmp) / "proofs.sqlite")
            artifact = BoundedProofSearch("red", max_ply=1).search(
                Position.from_fen(RED_WIN_IN_ONE_FEN)
            ).artifact
            store.save(artifact, node_limit=100)
            engine = ProofAssistedUciEngine(
                max_ply=1,
                proof_store=store,
                move_orderer=PreferA8B8Orderer(),
            )
            engine.set_position(f"position fen {RED_WIN_IN_ONE_FEN}")

            best = engine.choose_bestmove(searchmoves=["a8a0"])

        self.assertEqual(best.move, "a8a0")
        self.assertEqual(best.source, "proof_store")
        self.assertEqual(best.proof_status, ProofStatus.PROVEN)
        self.assertEqual(best.nodes_searched, 0)

    def test_searchmoves_online_search_skips_root_store_hit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProofStore(Path(tmp) / "proofs.sqlite")
            artifact = BoundedProofSearch("red", max_ply=1).search(
                Position.from_fen(RED_WIN_IN_ONE_FEN)
            ).artifact
            store.save(artifact, node_limit=100)
            engine = ProofAssistedUciEngine(max_ply=1, proof_store=store)
            engine.set_position(f"position fen {RED_WIN_IN_ONE_FEN}")

            best = engine.choose_bestmove(searchmoves=["a8g8"])

        self.assertEqual(best.move, "a8g8")
        self.assertEqual(best.source, "self_fallback")
        self.assertEqual(best.proof_status, ProofStatus.UNKNOWN)
        self.assertNotEqual(best.source, "proof_store")
        self.assertGreater(best.nodes_searched, 0)

    def test_searchmoves_still_reuses_child_store_hit(self) -> None:
        fen = "4k4/1N6R/9/3N5/4P4/9/9/9/9/4KR3 w - - 0 1"
        winning_move = Move.from_uci("i8i0")
        root = Position.from_fen(fen)
        with tempfile.TemporaryDirectory() as tmp:
            store = ProofStore(Path(tmp) / "proofs.sqlite")
            child_proof = BoundedProofSearch("red", max_ply=0).search(
                root.make_move(winning_move)
            ).artifact
            store.save(child_proof, node_limit=100)
            engine = ProofAssistedUciEngine(max_ply=1, node_limit=1, proof_store=store)
            engine.set_position(f"position fen {fen}")

            best = engine.choose_bestmove(searchmoves=[winning_move.to_uci()])

        self.assertEqual(best.move, winning_move.to_uci())
        self.assertEqual(best.source, "proof")
        self.assertEqual(best.proof_status, ProofStatus.PROVEN)
        self.assertEqual(best.nodes_searched, 1)
        self.assertGreaterEqual(best.resolved_store_hits, 1)
        self.assertGreaterEqual(best.resolved_store_misses, 1)

    def test_proof_store_hit_survives_ordering_error(self) -> None:
        class RaisingOrderer:
            def order_moves(self, position, moves):
                raise RuntimeError("ordering exploded")

        with tempfile.TemporaryDirectory() as tmp:
            store = ProofStore(Path(tmp) / "proofs.sqlite")
            artifact = BoundedProofSearch("red", max_ply=1).search(
                Position.from_fen(RED_WIN_IN_ONE_FEN)
            ).artifact
            store.save(artifact, node_limit=100)
            engine = ProofAssistedUciEngine(
                max_ply=1,
                proof_store=store,
                move_orderer=RaisingOrderer(),
            )
            engine.set_position(f"position fen {RED_WIN_IN_ONE_FEN}")

            best = engine.choose_bestmove()

        self.assertEqual(best.move, "a8a0")
        self.assertEqual(best.source, "proof_store")
        self.assertEqual(best.proof_status, ProofStatus.PROVEN)
        self.assertEqual(best.nodes_searched, 0)

    def test_proof_store_lookup_preserves_history_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProofStore(Path(tmp) / "proofs.sqlite")
            state = GameState.from_uci_position(
                "position fen 9/R3k2N1/9/5N3/4P4/9/9/9/9/3RK4 b - - 0 1 "
                "moves e8e9"
            )
            artifact = BoundedProofSearch("red", max_ply=1).search(state).artifact
            store.save(artifact, node_limit=100)

            bare_engine = ProofAssistedUciEngine(max_ply=1, proof_store=store)
            bare_engine.set_position(f"position fen {state.to_fen()}")
            bare_best = bare_engine.choose_bestmove()

            history_engine = ProofAssistedUciEngine(max_ply=1, proof_store=store)
            history_engine.set_position(state.to_uci_position())
            history_best = history_engine.choose_bestmove()

        self.assertNotEqual(bare_best.source, "proof_store")
        self.assertEqual(history_best.move, "a8a0")
        self.assertEqual(history_best.source, "proof_store")

    def test_proof_store_child_proof_guides_online_search(self) -> None:
        fen = "4k4/1N6R/9/3N5/4P4/9/9/9/9/4KR3 w - - 0 1"
        winning_move = Move.from_uci("i8i0")
        root = Position.from_fen(fen)
        with tempfile.TemporaryDirectory() as tmp:
            store = ProofStore(Path(tmp) / "proofs.sqlite")
            child = root.make_move(winning_move)
            child_proof = BoundedProofSearch("red", max_ply=0).search(child).artifact
            store.save(child_proof, node_limit=100)
            engine = ProofAssistedUciEngine(max_ply=1, node_limit=1, proof_store=store)
            engine.set_position(f"position fen {fen}")

            best = engine.choose_bestmove()

        self.assertEqual(best.move, winning_move.to_uci())
        self.assertEqual(best.source, "proof")
        self.assertEqual(best.proof_status, ProofStatus.PROVEN)
        self.assertEqual(best.nodes_searched, 1)
        self.assertGreaterEqual(best.resolved_store_hits, 1)

    def test_invalid_child_proof_store_does_not_break_bestmove(self) -> None:
        fen = "4k4/1N6R/9/3N5/4P4/9/9/9/9/4KR3 w - - 0 1"
        winning_move = Move.from_uci("i8i0")
        root = Position.from_fen(fen)
        with tempfile.TemporaryDirectory() as tmp:
            store = ProofStore(Path(tmp) / "proofs.sqlite")
            child = root.make_move(winning_move)
            invalid = BoundedProofSearch("red", max_ply=0).search(child).artifact
            store.save(
                invalid.__class__(
                    fen=invalid.fen,
                    target=invalid.target,
                    max_ply=invalid.max_ply,
                    node_kind=invalid.node_kind,
                    status=ProofStatus.DISPROVEN,
                    proof=invalid.proof,
                    disproof=invalid.disproof,
                    move=invalid.move,
                    reason=invalid.reason,
                    history_signature=invalid.history_signature,
                    position_command=invalid.position_command,
                    children=invalid.children,
                ),
                node_limit=100,
                verify=False,
            )
            engine = ProofAssistedUciEngine(max_ply=1, node_limit=100, proof_store=store)
            engine.set_position(f"position fen {fen}")

            best = engine.choose_bestmove()

        self.assertNotEqual(best.move, "0000")
        self.assertEqual(best.source, "proof")
        self.assertEqual(best.proof_status, ProofStatus.PROVEN)

    def test_no_legal_move_position_returns_null_bestmove(self) -> None:
        engine = ProofAssistedUciEngine(max_ply=1)
        engine.set_position(f"position fen {TERMINAL_RED_WIN_FEN}")

        best = engine.choose_bestmove()

        self.assertEqual(best.move, "0000")
        self.assertEqual(best.source, "none")

    def test_searchmoves_limits_proof_and_fallback_choice(self) -> None:
        engine = ProofAssistedUciEngine(max_ply=1)
        engine.set_position(f"position fen {RED_WIN_IN_ONE_FEN}")

        best = engine.choose_bestmove(searchmoves=["a8g8"])

        self.assertEqual(best.move, "a8g8")
        self.assertEqual(best.source, "self_fallback")
        self.assertEqual(best.proof_status, ProofStatus.UNKNOWN)

    def test_searchmoves_allows_proof_when_allowed_root_move_proves(self) -> None:
        engine = ProofAssistedUciEngine(max_ply=1)
        engine.set_position(f"position fen {RED_WIN_IN_ONE_FEN}")

        best = engine.choose_bestmove(searchmoves=["a8a0"])

        self.assertEqual(best.move, "a8a0")
        self.assertEqual(best.source, "proof")
        self.assertEqual(best.proof_status, ProofStatus.PROVEN)

    def test_searchmoves_subset_disproof_is_reported_as_unknown(self) -> None:
        engine = ProofAssistedUciEngine(max_ply=2)
        engine.set_position(f"position fen {IMMEDIATE_LOSS_FEN}")

        best = engine.choose_bestmove(searchmoves=["a6b4"])

        self.assertEqual(best.move, "a6b4")
        self.assertEqual(best.source, "self_fallback")
        self.assertEqual(best.proof_status, ProofStatus.UNKNOWN)
        self.assertTrue(best.reason.startswith("searchmoves_limited_"))

    def test_fallback_prefers_unresolved_root_child_over_disproven_child(self) -> None:
        position = Position.from_fen(IMMEDIATE_LOSS_FEN)
        ordered_moves = sorted(position.legal_moves(), key=lambda move: move.to_uci())[:2]
        first_move, second_move = ordered_moves
        first_child = ProofArtifact(
            fen=position.make_move(first_move).to_fen(),
            target=ProofTarget.RED,
            max_ply=1,
            node_kind=NodeKind.AND,
            status=ProofStatus.DISPROVEN,
            proof=1_000_000,
            disproof=0,
            move=first_move.to_uci(),
            reason="all_children_refute_target",
        )
        second_child = ProofArtifact(
            fen=position.make_move(second_move).to_fen(),
            target=ProofTarget.RED,
            max_ply=1,
            node_kind=NodeKind.AND,
            status=ProofStatus.UNKNOWN,
            proof=1,
            disproof=1,
            move=second_move.to_uci(),
            reason="node_limit",
        )
        artifact = ProofArtifact(
            fen=position.to_fen(),
            target=ProofTarget.RED,
            max_ply=2,
            node_kind=NodeKind.OR,
            status=ProofStatus.UNKNOWN,
            proof=1,
            disproof=1,
            reason="node_limit",
            children=(first_child, second_child),
        )

        fallback = _proof_aware_fallback_move(
            ordered_moves,
            artifact,
            {move.to_uci(): move for move in ordered_moves},
        )

        self.assertEqual(fallback, second_move)

    def test_searchmoves_limited_result_is_verifier_valid_unknown(self) -> None:
        engine = ProofAssistedUciEngine(max_ply=2, move_orderer=HeuristicMoveOrderer())
        engine.set_position(f"position fen {IMMEDIATE_LOSS_FEN}")
        legal_by_uci = {
            move.to_uci(): move
            for move in engine.position.legal_moves()
            if move.to_uci() == "a6b4"
        }
        raw = BoundedProofSearch(
            ProofTarget.RED,
            max_ply=2,
            move_orderer=_root_limited_move_orderer(
                HeuristicMoveOrderer(),
                legal_by_uci,
                True,
            ),
        ).search(_search_position(engine.position))

        limited = _searchmoves_limited_result(raw, True)

        self.assertEqual(limited.artifact.status, ProofStatus.UNKNOWN)
        self.assertEqual(limited.artifact.proof, 1)
        self.assertEqual(limited.artifact.disproof, 1)
        self.assertEqual(limited.artifact.children, ())
        self.assertTrue(limited.artifact.reason.startswith("searchmoves_limited_"))
        self.assertTrue(ProofVerifier().verify(limited.artifact).valid)
        self.assertEqual(limited.nodes_searched, raw.nodes_searched)

    def test_searchmoves_without_legal_candidates_returns_null_bestmove(self) -> None:
        engine = ProofAssistedUciEngine(max_ply=1)
        engine.set_position("position startpos")

        best = engine.choose_bestmove(searchmoves=["a0a0"])

        self.assertEqual(best.move, "0000")
        self.assertEqual(best.source, "none")
        self.assertEqual(best.reason, "no_searchmoves")

    def test_emergency_bestmove_respects_searchmoves(self) -> None:
        engine = ProofAssistedUciEngine(max_ply=1)
        engine.set_position(f"position fen {RED_WIN_IN_ONE_FEN}")

        best = engine.emergency_bestmove(searchmoves=["a8b8"])

        self.assertEqual(best, "a8b8")

    def test_emergency_bestmove_returns_null_without_legal_searchmoves(self) -> None:
        engine = ProofAssistedUciEngine(max_ply=1)
        engine.set_position("position startpos")

        best = engine.emergency_bestmove(searchmoves=["a0a0"])

        self.assertEqual(best, "0000")

    def test_ordering_error_still_returns_legal_fallback_bestmove(self) -> None:
        class RaisingOrderer:
            def order_moves(self, position, moves):
                raise RuntimeError("ordering exploded")

        engine = ProofAssistedUciEngine(max_ply=1, move_orderer=RaisingOrderer())
        engine.set_position("position startpos")

        best = engine.choose_bestmove()

        self.assertIn(best.move, {move.to_uci() for move in Position.start().legal_moves()})
        self.assertEqual(best.source, "self_fallback")
        self.assertEqual(best.reason, "ordering_error")

    def test_timed_search_skips_orderer_without_ordering_budget(self) -> None:
        class CountingOrderer:
            def __init__(self) -> None:
                self.calls = 0

            def order_moves(self, position, moves):
                self.calls += 1
                return sorted(moves, key=lambda move: move.to_uci())

        orderer = CountingOrderer()
        engine = ProofAssistedUciEngine(max_ply=0, move_orderer=orderer)
        engine.set_position("position startpos")

        best = engine.choose_bestmove(time_limit_ms=349)

        self.assertNotEqual(best.move, "0000")
        self.assertEqual(orderer.calls, 0)
        self.assertEqual(best.external_ordering_elapsed_ms, 0)

    def test_short_budget_uses_external_fallback_when_proof_cannot_start(self) -> None:
        class DirectFallback:
            def __init__(self) -> None:
                self.go_command = None
                self.order_calls = 0

            def bestmove_with_go_command(self, position, moves, go_command):
                self.go_command = go_command
                return next(move for move in moves if move.to_uci() == "b2b9")

            def order_moves(self, position, moves):
                self.order_calls += 1
                return sorted(moves, key=lambda move: move.to_uci())

        fallback = DirectFallback()
        engine = ProofAssistedUciEngine(max_ply=7, move_orderer=fallback)
        engine.set_position("position startpos")

        best = engine.choose_bestmove(
            time_limit_ms=100,
            fallback_go_command="go movetime 100",
        )

        self.assertEqual(best.move, "b2b9")
        self.assertEqual(best.source, "external_fallback")
        self.assertEqual(best.reason, "external_uci")
        self.assertEqual(fallback.go_command, "go movetime 100")
        self.assertEqual(fallback.order_calls, 0)

    def test_direct_external_fallback_uses_legal_bestmove_before_online_search(self) -> None:
        class DirectFallback:
            def __init__(self) -> None:
                self.go_command = None
                self.order_calls = 0

            def bestmove_with_go_command(self, position, moves, go_command):
                self.go_command = go_command
                return next(move for move in moves if move.to_uci() == "a8g8")

            def order_moves(self, position, moves):
                self.order_calls += 1
                return sorted(moves, key=lambda move: move.to_uci())

        fallback = DirectFallback()
        engine = ProofAssistedUciEngine(
            max_ply=1,
            move_orderer=fallback,
            prefer_external_fallback=True,
        )
        engine.set_position(f"position fen {RED_WIN_IN_ONE_FEN}")

        best = engine.choose_bestmove(fallback_go_command="go movetime 500")

        self.assertEqual(best.move, "a8g8")
        self.assertEqual(best.source, "external_fallback")
        self.assertEqual(best.reason, "external_uci")
        self.assertEqual(fallback.go_command, "go movetime 500")
        self.assertEqual(fallback.order_calls, 0)

    def test_cli_direct_fallback_forwards_go_after_new_game_and_store_hit_bypasses_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_path = tmp_path / "commands.txt"
            store_path = tmp_path / "proofs.sqlite"
            fake_engine = _write_fake_uci(
                tmp_path,
                f"""
                import sys
                from pathlib import Path

                log_path = Path({str(log_path)!r})
                for line in sys.stdin:
                    command = line.strip()
                    if command == "ucinewgame" or command.startswith(("position ", "go ")):
                        previous = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
                        log_path.write_text(previous + command + "\\n", encoding="utf-8")
                    if command == "uci":
                        print("id name Fake UCI", flush=True)
                        print("uciok", flush=True)
                    elif command == "isready":
                        print("readyok", flush=True)
                    elif command.startswith("go "):
                        print("bestmove i3i4", flush=True)
                    elif command == "quit":
                        break
                """,
            )
            artifact = BoundedProofSearch("red", max_ply=1).search(
                Position.from_fen(RED_WIN_IN_ONE_FEN)
            ).artifact
            ProofStore(store_path).save(artifact, node_limit=100)
            fallback_command = _shell_command([sys.executable, str(fake_engine)])
            process = subprocess.Popen(
                [
                    sys.executable,
                    "tools/proof_uci.py",
                    "--max-ply",
                    "1",
                    "--proof-store",
                    str(store_path),
                    "--fallback-uci-engine",
                    fallback_command,
                    "--direct-fallback-uci",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, stderr = process.communicate(
                "uci\nisready\nucinewgame\nposition startpos\ngo movetime 123\n"
                f"position fen {RED_WIN_IN_ONE_FEN}\ngo movetime 123\nquit\n",
                timeout=10,
            )
            commands = log_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(process.returncode, 0, stderr)
        self.assertIn("bestmove i3i4", stdout)
        self.assertIn("info string source=proof_store", stdout)
        self.assertEqual(commands[0], "ucinewgame")
        self.assertIn("go movetime 123", commands)
        self.assertEqual(len([command for command in commands if command.startswith("go ")]), 1)
        self.assertFalse(any(RED_WIN_IN_ONE_FEN in command for command in commands))

    def test_proof_search_budget_keeps_deadline_safety_reserve(self) -> None:
        with patch("xiangqi_solver.uci_loop.perf_counter", return_value=10.0):
            self.assertEqual(_proof_search_time_limit_seconds(10.5), 0.35)

    def test_half_second_budget_reserves_time_for_local_ordering(self) -> None:
        self.assertGreater(_ordering_time_limit_ms(time.perf_counter() + 0.5), 0)

    def test_half_second_budget_allows_tactical_local_ordering(self) -> None:
        with patch("xiangqi_solver.uci_loop.perf_counter", return_value=10.0):
            self.assertEqual(_ordering_time_limit_ms(10.5), 210)

    def test_half_second_budget_keeps_tactical_ordering_after_setup_overhead(self) -> None:
        with patch("xiangqi_solver.uci_loop.perf_counter", return_value=10.0):
            self.assertEqual(_ordering_time_limit_ms(10.499), 210)

    def test_orderer_empty_output_is_completed_with_legal_fallback_moves(self) -> None:
        class EmptyOrderer:
            def order_moves(self, position, moves):
                return []

        engine = ProofAssistedUciEngine(max_ply=0, move_orderer=EmptyOrderer())
        engine.set_position("position startpos")

        best = engine.choose_bestmove()

        self.assertEqual(best.move, "a0a1")
        self.assertEqual(best.source, "self_fallback")

    def test_tool_ordering_error_still_returns_legal_fallback_bestmove(self) -> None:
        class RaisingOrderer:
            def order_moves(self, position, moves):
                raise RuntimeError("ordering exploded")

        engine = ProofAssistedUciEngine(max_ply=1, move_orderer=RaisingOrderer())
        output = io.StringIO()

        result = run_uci_loop(
            engine,
            input_stream=io.StringIO("uci\nposition startpos\ngo depth 1\nquit\n"),
            output_stream=output,
        )

        bestmove_lines = [
            line for line in output.getvalue().splitlines() if line.startswith("bestmove ")
        ]
        bestmove = bestmove_lines[-1].split()[1]
        legal = {move.to_uci() for move in Position.start().legal_moves()}
        self.assertEqual(result, 0)
        self.assertNotIn("info string go error: ordering exploded", output.getvalue())
        self.assertIn("info string source=self_fallback", output.getvalue())
        self.assertIn("reason=ordering_error", output.getvalue())
        self.assertIn(bestmove, legal)
        self.assertNotEqual(bestmove, "0000")

    def test_tool_go_searchmoves_returns_allowed_move(self) -> None:
        engine = ProofAssistedUciEngine(max_ply=1)
        output = io.StringIO()

        result = run_uci_loop(
            engine,
            input_stream=io.StringIO(
                f"uci\nposition fen {RED_WIN_IN_ONE_FEN}\ngo depth 1 searchmoves a8g8\nquit\n"
            ),
            output_stream=output,
        )

        self.assertEqual(result, 0)
        self.assertIn("info string source=self_fallback", output.getvalue())
        self.assertIn("bestmove a8g8", output.getvalue())

    def test_tool_emits_phase_timing_telemetry(self) -> None:
        engine = ProofAssistedUciEngine(max_ply=1)
        output = io.StringIO()

        result = run_uci_loop(
            engine,
            input_stream=io.StringIO("uci\nposition startpos\ngo movetime 0\nquit\n"),
            output_stream=output,
        )

        stdout = output.getvalue()
        self.assertEqual(result, 0)
        self.assertRegex(stdout, r"external_ordering_elapsed_ms=\d+")
        self.assertRegex(stdout, r"proof_search_elapsed_ms=\d+")
        self.assertRegex(stdout, r"total_search_elapsed_ms=\d+")

    def test_tool_go_error_emergency_bestmove_respects_searchmoves(self) -> None:
        class RaisingEngine(ProofAssistedUciEngine):
            def choose_bestmove(self, *args, **kwargs):
                raise RuntimeError("search exploded")

        engine = RaisingEngine(max_ply=1)
        output = io.StringIO()

        result = run_uci_loop(
            engine,
            input_stream=io.StringIO(
                f"uci\nposition fen {RED_WIN_IN_ONE_FEN}\ngo depth 1 searchmoves a8b8\nquit\n"
            ),
            output_stream=output,
        )

        stdout = output.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("info string go error: search exploded", stdout)
        self.assertIn("info string source=emergency", stdout)
        self.assertIn("bestmove a8b8", stdout)
        self.assertNotIn("bestmove a8a0", stdout)

    def test_tool_go_parse_error_emergency_bestmove_respects_searchmoves(self) -> None:
        engine = ProofAssistedUciEngine(max_ply=1)
        output = io.StringIO()

        result = run_uci_loop(
            engine,
            input_stream=io.StringIO(
                f"uci\nposition fen {RED_WIN_IN_ONE_FEN}\ngo searchmoves a8b8 depth -1\nquit\n"
            ),
            output_stream=output,
        )

        stdout = output.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("info string go error: go depth must be non-negative", stdout)
        self.assertIn("info string source=emergency", stdout)
        self.assertIn("bestmove a8b8", stdout)
        self.assertNotIn("bestmove a8a0", stdout)

    def test_tool_invalid_position_does_not_reuse_previous_board_for_go(self) -> None:
        output = io.StringIO()

        result = run_uci_loop(
            ProofAssistedUciEngine(max_ply=1),
            input_stream=io.StringIO(
                f"uci\nposition fen {RED_WIN_IN_ONE_FEN}\nposition startpos moves a0a0\ngo depth 1\nquit\n"
            ),
            output_stream=output,
        )

        stdout = output.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("info string position error:", stdout)
        self.assertIn("info string source=none status=unknown reason=invalid_position", stdout)
        self.assertIn("bestmove 0000", stdout)
        self.assertNotIn("bestmove a8a0", stdout)

    def test_tool_valid_position_recovers_after_previous_position_error(self) -> None:
        output = io.StringIO()

        result = run_uci_loop(
            ProofAssistedUciEngine(max_ply=1),
            input_stream=io.StringIO(
                f"uci\nposition startpos moves a0a0\nposition fen {RED_WIN_IN_ONE_FEN}\ngo depth 1\nquit\n"
            ),
            output_stream=output,
        )

        stdout = output.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("info string position error:", stdout)
        self.assertIn("info string source=proof status=proven", stdout)
        self.assertIn("bestmove a8a0", stdout)

    def test_choose_bestmove_can_be_stopped_and_returns_fallback_move(self) -> None:
        stop_event = Event()
        engine = ProofAssistedUciEngine(max_ply=3, node_limit=10_000)
        engine.set_position("position startpos")
        stop_event.set()

        best = engine.choose_bestmove(stop_event=stop_event)

        self.assertEqual(best.source, "self_fallback")
        self.assertEqual(best.reason, "stopped")
        self.assertNotEqual(best.move, "0000")

    def test_tool_go_infinite_stops_and_emits_bestmove(self) -> None:
        class SlowOrderer:
            def order_moves(self, position, moves):
                time.sleep(0.05)
                return sorted(moves, key=lambda move: move.to_uci())

        output = io.StringIO()
        result = run_uci_loop(
            ProofAssistedUciEngine(max_ply=3, node_limit=10_000, move_orderer=SlowOrderer()),
            input_stream=io.StringIO("uci\nposition startpos\ngo infinite\nstop\nquit\n"),
            output_stream=output,
        )

        stdout = output.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("info string source=self_fallback", stdout)
        self.assertIn("reason=stopped", stdout)
        self.assertEqual(len([line for line in stdout.splitlines() if line.startswith("bestmove ")]), 1)

    def test_tool_go_depth_stops_and_emits_one_bestmove(self) -> None:
        class SlowOrderer:
            def order_moves(self, position, moves):
                time.sleep(0.05)
                return sorted(moves, key=lambda move: move.to_uci())

        output = io.StringIO()
        result = run_uci_loop(
            ProofAssistedUciEngine(max_ply=3, node_limit=10_000, move_orderer=SlowOrderer()),
            input_stream=io.StringIO("uci\nposition startpos\ngo depth 3\nstop\nquit\n"),
            output_stream=output,
        )

        stdout = output.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("reason=stopped", stdout)
        self.assertEqual(len([line for line in stdout.splitlines() if line.startswith("bestmove ")]), 1)

    def test_tool_go_infinite_waits_for_stop_before_emitting_bestmove(self) -> None:
        process = subprocess.Popen(
            [sys.executable, "tools/proof_uci.py", "--max-ply", "0"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        stdout_queue: Queue[str] = Queue()

        def read_stdout() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                stdout_queue.put(line.strip())

        reader = Thread(target=read_stdout, daemon=True)
        reader.start()

        def send(command: str) -> None:
            assert process.stdin is not None
            process.stdin.write(command + "\n")
            process.stdin.flush()

        def read_until(predicate, timeout: float = 2.0) -> list[str]:
            deadline = time.time() + timeout
            lines: list[str] = []
            while time.time() < deadline:
                remaining = max(0.01, deadline - time.time())
                try:
                    line = stdout_queue.get(timeout=remaining)
                except Empty:
                    continue
                lines.append(line)
                if predicate(line):
                    return lines
            self.fail(f"timed out waiting for engine output; saw {lines!r}")

        try:
            send("uci")
            read_until(lambda line: line == "uciok")
            send("isready")
            read_until(lambda line: line == "readyok")
            send("position startpos")
            send("go infinite")
            with self.assertRaises(Empty):
                stdout_queue.get(timeout=0.2)
            send("stop")
            lines = read_until(lambda line: line.startswith("bestmove "))
            send("quit")
            stderr = process.communicate(timeout=5)[1]
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)

        self.assertEqual(process.returncode, 0, stderr)
        self.assertTrue(any(line.startswith("info string source=self_fallback") for line in lines))
        self.assertTrue(lines[-1].startswith("bestmove "))

    def test_tool_go_ponder_waits_for_ponderhit_before_emitting_bestmove(self) -> None:
        process = subprocess.Popen(
            [sys.executable, "tools/proof_uci.py", "--max-ply", "0"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        stdout_queue: Queue[str] = Queue()

        def read_stdout() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                stdout_queue.put(line.strip())

        reader = Thread(target=read_stdout, daemon=True)
        reader.start()

        def send(command: str) -> None:
            assert process.stdin is not None
            process.stdin.write(command + "\n")
            process.stdin.flush()

        def read_until(predicate, timeout: float = 2.0) -> list[str]:
            deadline = time.time() + timeout
            lines: list[str] = []
            while time.time() < deadline:
                remaining = max(0.01, deadline - time.time())
                try:
                    line = stdout_queue.get(timeout=remaining)
                except Empty:
                    continue
                lines.append(line)
                if predicate(line):
                    return lines
            self.fail(f"timed out waiting for engine output; saw {lines!r}")

        try:
            send("uci")
            read_until(lambda line: line == "uciok")
            send("isready")
            read_until(lambda line: line == "readyok")
            send("setoption name Ponder value true")
            send("position startpos")
            send("go ponder depth 0")
            with self.assertRaises(Empty):
                stdout_queue.get(timeout=0.2)
            send("ponderhit")
            lines = read_until(lambda line: line.startswith("bestmove "))
            send("quit")
            stderr = process.communicate(timeout=5)[1]
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)

        self.assertEqual(process.returncode, 0, stderr)
        self.assertTrue(any(line.startswith("info string source=self_fallback") for line in lines))
        self.assertTrue(lines[-1].startswith("bestmove "))

    def test_tool_go_ponder_returns_immediately_when_ponder_option_is_disabled(self) -> None:
        output = io.StringIO()

        result = run_uci_loop(
            ProofAssistedUciEngine(max_ply=0),
            input_stream=io.StringIO("uci\nposition startpos\ngo ponder depth 0\nquit\n"),
            output_stream=output,
        )

        stdout = output.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("bestmove ", stdout)
        self.assertIn("info string source=self_fallback", stdout)

    def test_tool_ponder_option_updates_advertised_default(self) -> None:
        output = io.StringIO()

        result = run_uci_loop(
            ProofAssistedUciEngine(),
            input_stream=io.StringIO("setoption name Ponder value true\nuci\nquit\n"),
            output_stream=output,
        )

        self.assertEqual(result, 0)
        self.assertIn("option name Ponder type check default true", output.getvalue())

    def test_tool_isready_keeps_active_search_and_reports_readyok_first(self) -> None:
        class SlowOrderer:
            def order_moves(self, position, moves):
                time.sleep(0.05)
                return sorted(moves, key=lambda move: move.to_uci())

        output = io.StringIO()
        result = run_uci_loop(
            ProofAssistedUciEngine(max_ply=3, node_limit=10_000, move_orderer=SlowOrderer()),
            input_stream=io.StringIO("uci\nposition startpos\ngo infinite\nisready\nstop\nquit\n"),
            output_stream=output,
        )

        lines = output.getvalue().splitlines()
        bestmove_index = next(i for i, line in enumerate(lines) if line.startswith("bestmove "))
        readyok_index = lines.index("readyok")
        self.assertEqual(result, 0)
        self.assertLess(readyok_index, bestmove_index)
        self.assertIn("reason=stopped", output.getvalue())

    def test_external_fallback_orders_bestmove_when_proof_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_engine = _write_fake_uci(
                Path(tmp),
                """
                import sys
                for line in sys.stdin:
                    command = line.strip()
                    if command == "uci":
                        print("id name Fake UCI", flush=True)
                        print("uciok", flush=True)
                    elif command == "isready":
                        print("readyok", flush=True)
                    elif command.startswith("go depth"):
                        print("bestmove i3i4", flush=True)
                    elif command == "quit":
                        break
                """,
            )
            fallback_command = _shell_command([sys.executable, str(fake_engine)])
            process = subprocess.Popen(
                [
                    sys.executable,
                    "tools/proof_uci.py",
                    "--max-ply",
                    "2",
                    "--fallback-uci-engine",
                    fallback_command,
                    "--fallback-uci-depth",
                    "1",
                    "--fallback-uci-timeout",
                    "2",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, stderr = process.communicate(
                "uci\nisready\nposition startpos\ngo depth 1\nquit\n",
                timeout=10,
            )

        self.assertEqual(process.returncode, 0, stderr)
        self.assertIn("info string source=external_fallback", stdout)
        self.assertIn("bestmove i3i4", stdout)

    def test_external_fallback_cannot_override_immediate_loss_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_engine = _write_fake_uci(
                Path(tmp),
                """
                import sys
                for line in sys.stdin:
                    command = line.strip()
                    if command == "uci":
                        print("id name Fake UCI", flush=True)
                        print("uciok", flush=True)
                    elif command == "isready":
                        print("readyok", flush=True)
                    elif command.startswith("go"):
                        print("bestmove a6b4", flush=True)
                    elif command == "quit":
                        break
                """,
            )
            fallback_command = _shell_command([sys.executable, str(fake_engine)])
            process = subprocess.Popen(
                [
                    sys.executable,
                    "tools/proof_uci.py",
                    "--max-ply",
                    "1",
                    "--fallback-uci-engine",
                    fallback_command,
                    "--fallback-uci-depth",
                    "1",
                    "--fallback-uci-timeout",
                    "2",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, stderr = process.communicate(
                "uci\n"
                "position fen 9/9/5k3/N1r2b3/7b1/9/4n4/3A5/9/1p3K3 w - - 0 1\n"
                "go depth 0\n"
                "quit\n",
                timeout=10,
            )

        self.assertEqual(process.returncode, 0, stderr)
        self.assertIn("info string source=external_fallback", stdout)
        bestmove = next(
            line.split()[1] for line in stdout.splitlines() if line.startswith("bestmove ")
        )
        child = Position.from_fen(IMMEDIATE_LOSS_FEN).make_move(Move.from_uci(bestmove))
        self.assertFalse(
            any(child.make_move(reply).game_result() is not None for reply in child.legal_moves())
        )

    def test_external_fallback_ordering_is_cached_within_one_go(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_path = tmp_path / "go_calls.txt"
            fake_engine = _write_fake_uci(
                tmp_path,
                f"""
                import sys
                from pathlib import Path

                log_path = Path({str(log_path)!r})
                for line in sys.stdin:
                    command = line.strip()
                    if command == "uci":
                        print("id name Fake UCI", flush=True)
                        print("uciok", flush=True)
                    elif command == "isready":
                        print("readyok", flush=True)
                    elif command.startswith("go depth"):
                        previous = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
                        log_path.write_text(previous + command + "\\n", encoding="utf-8")
                        print("bestmove i3i4", flush=True)
                    elif command == "quit":
                        break
                """,
            )
            fallback_command = _shell_command([sys.executable, str(fake_engine)])
            process = subprocess.Popen(
                [
                    sys.executable,
                    "tools/proof_uci.py",
                    "--max-ply",
                    "1",
                    "--fallback-uci-engine",
                    fallback_command,
                    "--fallback-uci-depth",
                    "1",
                    "--fallback-uci-timeout",
                    "2",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, stderr = process.communicate(
                "uci\nisready\nposition startpos\ngo depth 1\nquit\n",
                timeout=10,
            )
            go_calls = log_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(process.returncode, 0, stderr)
        self.assertIn("bestmove i3i4", stdout)
        self.assertEqual(go_calls, ["go depth 1"])

    def test_external_fallback_multipv_option_is_forwarded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_path = tmp_path / "commands.txt"
            fake_engine = _write_fake_uci(
                tmp_path,
                f"""
                import sys
                from pathlib import Path

                log_path = Path({str(log_path)!r})
                for line in sys.stdin:
                    command = line.strip()
                    if command.startswith("setoption") or command.startswith("go depth"):
                        previous = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
                        log_path.write_text(previous + command + "\\n", encoding="utf-8")
                    if command == "uci":
                        print("id name Fake UCI", flush=True)
                        print("uciok", flush=True)
                    elif command == "isready":
                        print("readyok", flush=True)
                    elif command.startswith("go depth"):
                        print("info depth 1 multipv 1 pv i3i4", flush=True)
                        print("bestmove i3i4", flush=True)
                    elif command == "quit":
                        break
                """,
            )
            fallback_command = _shell_command([sys.executable, str(fake_engine)])
            process = subprocess.Popen(
                [
                    sys.executable,
                    "tools/proof_uci.py",
                    "--max-ply",
                    "0",
                    "--fallback-uci-engine",
                    fallback_command,
                    "--fallback-uci-depth",
                    "1",
                    "--fallback-uci-multipv",
                    "3",
                    "--fallback-uci-option",
                    "Threads=2",
                    "--fallback-uci-option",
                    "Hash=64",
                    "--fallback-uci-timeout",
                    "2",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, stderr = process.communicate(
                "uci\nisready\nposition startpos\ngo depth 1\nquit\n",
                timeout=10,
            )
            commands = log_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(process.returncode, 0, stderr)
        self.assertIn("bestmove i3i4", stdout)
        self.assertEqual(
            commands,
            [
                "setoption name Threads value 2",
                "setoption name Hash value 64",
                "setoption name MultiPV value 3",
                "go depth 1",
            ],
        )

    def test_runtime_setoption_is_forwarded_to_external_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_path = tmp_path / "commands.txt"
            fake_engine = _write_fake_uci(
                tmp_path,
                f"""
                import sys
                from pathlib import Path

                log_path = Path({str(log_path)!r})
                for line in sys.stdin:
                    command = line.strip()
                    if command.startswith("setoption") or command.startswith("go depth"):
                        previous = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
                        log_path.write_text(previous + command + "\\n", encoding="utf-8")
                    if command == "uci":
                        print("id name Fake UCI", flush=True)
                        print("uciok", flush=True)
                    elif command == "isready":
                        print("readyok", flush=True)
                    elif command.startswith("go depth"):
                        print("bestmove i3i4", flush=True)
                    elif command == "quit":
                        break
                """,
            )
            fallback_command = _shell_command([sys.executable, str(fake_engine)])
            process = subprocess.Popen(
                [
                    sys.executable,
                    "tools/proof_uci.py",
                    "--max-ply",
                    "0",
                    "--fallback-uci-engine",
                    fallback_command,
                    "--fallback-uci-depth",
                    "1",
                    "--fallback-uci-timeout",
                    "2",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, stderr = process.communicate(
                "uci\nsetoption name Hash value 128\nisready\nposition startpos\ngo depth 1\nquit\n",
                timeout=10,
            )
            commands = log_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(process.returncode, 0, stderr)
        self.assertIn("bestmove i3i4", stdout)
        self.assertIn("setoption name Hash value 128", commands)

    def test_external_fallback_uses_current_go_movetime_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_path = tmp_path / "commands.txt"
            fake_engine = _write_fake_uci(
                tmp_path,
                f"""
                import sys
                from pathlib import Path

                log_path = Path({str(log_path)!r})
                for line in sys.stdin:
                    command = line.strip()
                    if command.startswith("go"):
                        previous = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
                        log_path.write_text(previous + command + "\\n", encoding="utf-8")
                    if command == "uci":
                        print("id name Fake UCI", flush=True)
                        print("uciok", flush=True)
                    elif command == "isready":
                        print("readyok", flush=True)
                    elif command.startswith("go"):
                        print("bestmove i3i4", flush=True)
                    elif command == "quit":
                        break
                """,
            )
            fallback_command = _shell_command([sys.executable, str(fake_engine)])
            process = subprocess.Popen(
                [
                    sys.executable,
                    "tools/proof_uci.py",
                    "--max-ply",
                    "2",
                    "--fallback-uci-engine",
                    fallback_command,
                    "--fallback-uci-depth",
                    "1",
                    "--fallback-uci-timeout",
                    "2",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, stderr = process.communicate(
                "uci\nisready\nposition startpos\ngo movetime 1000\nquit\n",
                timeout=10,
            )
            go_calls = log_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(process.returncode, 0, stderr)
        self.assertIn("bestmove i3i4", stdout)
        self.assertEqual(len(go_calls), 1)
        self.assertTrue(go_calls[0].startswith("go depth 1 movetime "))
        movetime = int(go_calls[0].split()[-1])
        self.assertGreater(movetime, 0)
        self.assertLessEqual(movetime, 300)

    def test_external_fallback_receives_searchmoves_and_movetime_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_path = tmp_path / "commands.txt"
            fake_engine = _write_fake_uci(
                tmp_path,
                f"""
                import sys
                from pathlib import Path

                log_path = Path({str(log_path)!r})
                for line in sys.stdin:
                    command = line.strip()
                    if command.startswith("go"):
                        previous = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
                        log_path.write_text(previous + command + "\\n", encoding="utf-8")
                    if command == "uci":
                        print("id name Fake UCI", flush=True)
                        print("uciok", flush=True)
                    elif command == "isready":
                        print("readyok", flush=True)
                    elif command.startswith("go"):
                        print("bestmove a8b8", flush=True)
                    elif command == "quit":
                        break
                """,
            )
            fallback_command = _shell_command([sys.executable, str(fake_engine)])
            process = subprocess.Popen(
                [
                    sys.executable,
                    "tools/proof_uci.py",
                    "--max-ply",
                    "0",
                    "--fallback-uci-engine",
                    fallback_command,
                    "--fallback-uci-depth",
                    "1",
                    "--fallback-uci-timeout",
                    "2",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, stderr = process.communicate(
                f"uci\nisready\nposition fen {RED_WIN_IN_ONE_FEN}\n"
                "go movetime 1000 searchmoves a8b8\nquit\n",
                timeout=10,
            )
            go_calls = log_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(process.returncode, 0, stderr)
        self.assertIn("bestmove a8b8", stdout)
        self.assertEqual(len(go_calls), 1)
        tokens = go_calls[0].split()
        self.assertEqual(tokens[:5], ["go", "searchmoves", "a8b8", "depth", "1"])
        self.assertEqual(tokens[5], "movetime")
        movetime = int(tokens[6])
        self.assertGreater(movetime, 0)
        self.assertLessEqual(movetime, 300)

    def test_illegal_external_fallback_bestmove_uses_deterministic_legal_move(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_engine = _write_fake_uci(
                Path(tmp),
                """
                import sys
                for line in sys.stdin:
                    command = line.strip()
                    if command == "uci":
                        print("id name Fake UCI", flush=True)
                        print("uciok", flush=True)
                    elif command == "isready":
                        print("readyok", flush=True)
                    elif command.startswith("go depth"):
                        print("bestmove a0a9", flush=True)
                    elif command == "quit":
                        break
                """,
            )
            engine = ProofAssistedUciEngine(
                max_ply=1,
                move_orderer=UciBestMoveOrderer([sys.executable, str(fake_engine)], depth=1),
            )
            engine.set_position("position startpos")

            best = engine.choose_bestmove()

        self.assertEqual(best.move, "a0a1")
        self.assertEqual(best.source, "external_fallback")
        self.assertEqual(best.proof_status, ProofStatus.UNKNOWN)

    def test_external_fallback_replays_position_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_engine = _write_fake_uci(
                Path(tmp),
                """
                import sys
                seen_history = False
                for line in sys.stdin:
                    command = line.strip()
                    if command == "uci":
                        print("id name Fake UCI", flush=True)
                        print("uciok", flush=True)
                    elif command == "isready":
                        print("readyok", flush=True)
                    elif command.startswith("position fen") and " moves " in command:
                        seen_history = True
                    elif command.startswith("go depth"):
                        move = "a1a2" if seen_history else "a1a0"
                        print(f"bestmove {move}", flush=True)
                    elif command == "quit":
                        break
                """,
            )
            engine = ProofAssistedUciEngine(
                max_ply=0,
                move_orderer=UciBestMoveOrderer([sys.executable, str(fake_engine)], depth=1),
            )
            engine.set_position(
                "position fen 4k4/9/9/9/9/9/4P4/9/R8/4K4 w - - 0 1 "
                "moves a1a2 e9e8 a2a1 e8e9"
            )

            best = engine.choose_bestmove()

        self.assertEqual(best.move, "a1a2")
        self.assertEqual(best.source, "external_fallback")

    def test_fallback_command_split_handles_quoted_paths(self) -> None:
        parts = [sys.executable, str(Path("engine dir") / "fake uci.py"), "--flag", "two words"]

        self.assertEqual(split_engine_command(_shell_command(parts)), parts)

    def test_tool_speaks_minimal_uci_and_returns_bestmove(self) -> None:
        process = subprocess.Popen(
            [sys.executable, "tools/proof_uci.py", "--max-ply", "0"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = process.communicate(
            "uci\nisready\nposition startpos\ngo depth 1\nquit\n",
            timeout=5,
        )

        self.assertEqual(process.returncode, 0, stderr)
        self.assertIn("uciok", stdout)
        self.assertIn("readyok", stdout)
        bestmove_lines = [line for line in stdout.splitlines() if line.startswith("bestmove ")]
        self.assertEqual(len(bestmove_lines), 1)
        self.assertNotEqual(bestmove_lines[0], "bestmove 0000")

    def test_tool_default_fallback_prefers_local_heuristic_move(self) -> None:
        process = subprocess.Popen(
            [sys.executable, "tools/proof_uci.py", "--max-ply", "0"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = process.communicate(
            "uci\n"
            "position fen 4k4/9/9/9/4p4/9/9/9/8r/4K3R w - - 0 1\n"
            "go depth 0\n"
            "quit\n",
            timeout=5,
        )

        self.assertEqual(process.returncode, 0, stderr)
        self.assertIn("info string source=self_fallback", stdout)
        self.assertIn("bestmove i0i1", stdout)

    def test_tool_default_fallback_avoids_immediate_loss(self) -> None:
        process = subprocess.Popen(
            [sys.executable, "tools/proof_uci.py", "--max-ply", "0"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = process.communicate(
            "uci\n"
            "position fen 9/9/5k3/N1r2b3/7b1/9/4n4/3A5/9/1p3K3 w - - 0 1\n"
            "go depth 0\n"
            "quit\n",
            timeout=5,
        )

        self.assertEqual(process.returncode, 0, stderr)
        self.assertIn("info string source=self_fallback", stdout)
        bestmove = next(
            line.split()[1] for line in stdout.splitlines() if line.startswith("bestmove ")
        )
        child = Position.from_fen(IMMEDIATE_LOSS_FEN).make_move(Move.from_uci(bestmove))
        self.assertFalse(
            any(child.make_move(reply).game_result() is not None for reply in child.legal_moves())
        )

    def test_tool_go_depth_and_nodes_override_single_search_budget(self) -> None:
        process = subprocess.Popen(
            [sys.executable, "tools/proof_uci.py", "--max-ply", "1", "--node-limit", "100"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = process.communicate(
            f"uci\nposition fen {RED_WIN_IN_ONE_FEN}\ngo depth 0 nodes 7\ngo\nquit\n",
            timeout=5,
        )

        self.assertEqual(process.returncode, 0, stderr)
        info_lines = [line for line in stdout.splitlines() if line.startswith("info string ")]
        self.assertEqual(len(info_lines), 2)
        self.assertIn("source=self_fallback", info_lines[0])
        self.assertIn("status=unknown", info_lines[0])
        self.assertIn("max_ply=0", info_lines[0])
        self.assertIn("node_limit=7", info_lines[0])
        self.assertIn("source=proof", info_lines[1])
        self.assertIn("max_ply=1", info_lines[1])
        self.assertIn("node_limit=100", info_lines[1])

    def test_tool_uses_verified_proof_store_before_online_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "proofs.sqlite"
            store = ProofStore(store_path)
            artifact = BoundedProofSearch("red", max_ply=1).search(
                Position.from_fen(RED_WIN_IN_ONE_FEN)
            ).artifact
            store.save(artifact, node_limit=100)

            process = subprocess.Popen(
                [
                    sys.executable,
                    "tools/proof_uci.py",
                    "--max-ply",
                    "1",
                    "--proof-store",
                    str(store_path),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, stderr = process.communicate(
                f"uci\nposition fen {RED_WIN_IN_ONE_FEN}\ngo depth 1\nquit\n",
                timeout=5,
            )

        self.assertEqual(process.returncode, 0, stderr)
        self.assertIn("info string source=proof_store", stdout)
        self.assertIn("status=proven", stdout)
        self.assertIn("nodes=0", stdout)
        self.assertIn("bestmove a8a0", stdout)

    def test_tool_can_save_online_proofs_to_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "proofs.sqlite"

            process = subprocess.Popen(
                [
                    sys.executable,
                    "tools/proof_uci.py",
                    "--max-ply",
                    "1",
                    "--node-limit",
                    "100",
                    "--proof-store",
                    str(store_path),
                    "--save-online-proofs",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, stderr = process.communicate(
                f"uci\nposition fen {RED_WIN_IN_ONE_FEN}\ngo\nquit\n",
                timeout=5,
            )
            stored = ProofStore(store_path).load(RED_WIN_IN_ONE_FEN, "red", 1)

        self.assertEqual(process.returncode, 0, stderr)
        self.assertIn("info string source=proof", stdout)
        self.assertIn("proof_store_saved=1", stdout)
        self.assertIn("proof_store_save_error=none", stdout)
        self.assertIsNotNone(stored)

    def test_tool_go_movetime_limits_proof_search_but_returns_bestmove(self) -> None:
        process = subprocess.Popen(
            [sys.executable, "tools/proof_uci.py", "--max-ply", "1", "--node-limit", "100"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = process.communicate(
            f"uci\nposition fen {RED_WIN_IN_ONE_FEN}\ngo movetime 0\nquit\n",
            timeout=5,
        )

        self.assertEqual(process.returncode, 0, stderr)
        self.assertIn("info string source=self_fallback", stdout)
        self.assertIn("reason=time_limit", stdout)
        self.assertIn("time_limit_ms=0", stdout)
        self.assertIn("time_limit_reached=1", stdout)
        bestmove_lines = [line for line in stdout.splitlines() if line.startswith("bestmove ")]
        self.assertEqual(len(bestmove_lines), 1)
        self.assertNotEqual(bestmove_lines[0], "bestmove 0000")

    def test_go_time_control_allocates_side_to_move_budget(self) -> None:
        red_limits = _parse_go_limits(
            "go wtime 30000 btime 90000 winc 1000 binc 3000 movestogo 10",
            default_max_ply=2,
            default_node_limit=500,
            side_to_move=Color.RED,
        )
        black_limits = _parse_go_limits(
            "go wtime 30000 btime 90000 winc 1000 binc 3000 movestogo 10",
            default_max_ply=2,
            default_node_limit=500,
            side_to_move=Color.BLACK,
        )
        movetime_limits = _parse_go_limits(
            "go movetime 250 depth 3 nodes 17",
            default_max_ply=2,
            default_node_limit=500,
            side_to_move=Color.RED,
        )

        self.assertEqual(red_limits.time_limit_ms, 3500)
        self.assertEqual(black_limits.time_limit_ms, 10500)
        self.assertEqual(movetime_limits.max_ply, 3)
        self.assertEqual(movetime_limits.node_limit, 17)
        self.assertEqual(movetime_limits.time_limit_ms, 250)

    def test_go_limits_parse_searchmoves_before_later_limits(self) -> None:
        limits = _parse_go_limits(
            "go searchmoves a0a1 i0i1 depth 3 nodes 17",
            default_max_ply=2,
            default_node_limit=500,
            side_to_move=Color.RED,
        )

        self.assertEqual(limits.searchmoves, ("a0a1", "i0i1"))
        self.assertEqual(limits.max_ply, 3)
        self.assertEqual(limits.node_limit, 17)

    def test_go_limits_parse_searchmoves_before_later_mate_limit(self) -> None:
        limits = _parse_go_limits(
            "go searchmoves a8b8 mate 1",
            default_max_ply=2,
            default_node_limit=500,
            side_to_move=Color.RED,
        )

        self.assertEqual(limits.searchmoves, ("a8b8",))

    def test_go_limits_parse_ponder(self) -> None:
        limits = _parse_go_limits(
            "go ponder depth 3 movetime 25",
            default_max_ply=2,
            default_node_limit=500,
            side_to_move=Color.RED,
        )

        self.assertTrue(limits.ponder)
        self.assertFalse(limits.infinite)
        self.assertEqual(limits.max_ply, 3)
        self.assertEqual(limits.time_limit_ms, 25)


if __name__ == "__main__":
    unittest.main()
