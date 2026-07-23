from __future__ import annotations

import hashlib
import json
import sys
from contextlib import suppress
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from threading import Event, Lock, Thread
from time import perf_counter
from typing import Protocol, TextIO

from xiangqi_core import Color, GameState, Move, Position

from .proof import ProofArtifact, ProofStatus, ProofTarget
from .search import BoundedProofSearch, MoveOrderer, SearchResult
from .verifier import ProofVerifier


class ProofSearchStore(Protocol):
    def resolve_for_search(
        self,
        fen: str,
        target: ProofTarget,
        max_ply: int,
        history_signature: str = "",
    ) -> ProofArtifact | None:
        ...


@dataclass(frozen=True, slots=True)
class BestMoveResult:
    move: str
    source: str
    proof_status: ProofStatus
    reason: str
    nodes_searched: int
    max_ply: int
    node_limit: int
    time_limit_ms: int | None = None
    time_limit_reached: bool = False
    resolved_store_hits: int = 0
    resolved_store_misses: int = 0
    proof_store_saved: bool = False
    proof_store_save_error: str | None = None
    external_ordering_elapsed_ms: int = 0
    proof_search_elapsed_ms: int = 0
    total_search_elapsed_ms: int = 0
    proof_artifact_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class GoLimits:
    max_ply: int
    node_limit: int
    time_limit_ms: int | None = None
    searchmoves: tuple[str, ...] = ()
    infinite: bool = False
    ponder: bool = False


@dataclass(slots=True)
class _ActiveSearch:
    thread: Thread
    stop_event: Event
    searchmoves: tuple[str, ...] = ()
    ponder: bool = False
    infinite: bool = False
    result: BestMoveResult | None = None
    error: Exception | None = None
    emitted: bool = False
    emission_lock: Lock = field(default_factory=Lock, repr=False)


def _starts_with_uci_token(command: str, token: str) -> bool:
    return command == token or command.startswith(token + " ")


class ProofAssistedUciEngine:
    def __init__(
        self,
        max_ply: int = 1,
        node_limit: int = 10_000,
        move_orderer: MoveOrderer | None = None,
        proof_store: ProofSearchStore | None = None,
        ponder_enabled: bool = False,
        save_online_proofs: bool = False,
        prefer_external_fallback: bool = False,
        external_move_ordering: bool = False,
        proof_enabled: bool = True,
    ) -> None:
        if max_ply < 0:
            raise ValueError("max_ply must be non-negative")
        if node_limit <= 0:
            raise ValueError("node_limit must be positive")
        self.max_ply = max_ply
        self.node_limit = node_limit
        self.move_orderer = move_orderer
        self.proof_store = proof_store
        self.proof_enabled = proof_enabled
        self.ponder_enabled = ponder_enabled
        self.save_online_proofs = save_online_proofs
        self.prefer_external_fallback = prefer_external_fallback
        self.external_move_ordering = external_move_ordering or callable(
            getattr(move_orderer, "bestmove_with_go_command", None)
        ) or bool(getattr(move_orderer, "uses_external_engine", False))
        self._proof_verifier = ProofVerifier()
        self.position: Position | GameState = Position.start()
        self._position_error: str | None = None
        self._verified_store_proofs: dict[tuple[str, ProofTarget, int, str], ProofArtifact] = {}

    def new_game(self) -> None:
        self.position = Position.start()
        self._position_error = None
        self._verified_store_proofs.clear()
        if self.proof_enabled:
            self._preload_stored_proof()
        new_game = getattr(self.move_orderer, "new_game", None)
        if callable(new_game):
            new_game()

    def close(self) -> None:
        close = getattr(self.move_orderer, "close", None)
        if callable(close):
            with suppress(Exception):
                close()

    def set_position(self, command: str) -> None:
        self.position = GameState.from_uci_position(command)
        self._position_error = None
        if self.proof_enabled:
            self._preload_stored_proof()

    def invalidate_position(self, reason: str) -> None:
        self._position_error = reason

    def set_option(self, name: str, value: str | None) -> None:
        normalized = name.replace(" ", "").lower()
        if normalized == "maxply":
            if value is None:
                raise ValueError("MaxPly requires a value")
            max_ply = int(value)
            if max_ply < 0:
                raise ValueError("MaxPly must be non-negative")
            self.max_ply = max_ply
        elif normalized == "nodelimit":
            if value is None:
                raise ValueError("NodeLimit requires a value")
            node_limit = int(value)
            if node_limit <= 0:
                raise ValueError("NodeLimit must be positive")
            self.node_limit = node_limit
        elif normalized == "clearhash":
            clear_cache = getattr(self.move_orderer, "clear_cache", None)
            if callable(clear_cache):
                clear_cache()
        elif normalized == "ponder":
            if value is None:
                raise ValueError("Ponder requires a value")
            self.ponder_enabled = _parse_uci_bool(value)
        elif normalized == "saveonlineproofs":
            if value is None:
                raise ValueError("SaveOnlineProofs requires a value")
            self.save_online_proofs = _parse_uci_bool(value)
        else:
            set_option = getattr(self.move_orderer, "set_option", None)
            if callable(set_option):
                set_option(name, value)

    def uci_options(self) -> tuple[str, ...]:
        option_lines = [
            "option name Clear Hash type button",
            f"option name Ponder type check default {_format_uci_bool(self.ponder_enabled)}",
            f"option name SaveOnlineProofs type check default {_format_uci_bool(self.save_online_proofs)}",
            f"option name MaxPly type spin default {self.max_ply} min 0 max 64",
            f"option name NodeLimit type spin default {self.node_limit} min 1 max 100000000",
        ]
        advertised_names = {_uci_option_name(line) for line in option_lines}
        move_orderer_options = getattr(self.move_orderer, "uci_options", None)
        if callable(move_orderer_options):
            for line in move_orderer_options():
                option_name = _uci_option_name(line)
                if option_name and option_name not in advertised_names:
                    option_lines.append(line)
                    advertised_names.add(option_name)
        return tuple(option_lines)

    def choose_bestmove(
        self,
        max_ply: int | None = None,
        node_limit: int | None = None,
        time_limit_ms: int | None = None,
        searchmoves: Sequence[str] | None = None,
        stop_event: Event | None = None,
        fallback_go_command: str | None = None,
    ) -> BestMoveResult:
        search_started = perf_counter()
        external_ordering_elapsed_ms = 0
        proof_search_elapsed_ms = 0
        search_max_ply = self.max_ply if max_ply is None else max_ply
        search_node_limit = self.node_limit if node_limit is None else node_limit
        if search_max_ply < 0:
            raise ValueError("max_ply must be non-negative")
        if search_node_limit <= 0:
            raise ValueError("node_limit must be positive")
        if time_limit_ms is not None and time_limit_ms < 0:
            raise ValueError("time_limit_ms must be non-negative")
        deadline = _search_deadline(time_limit_ms)
        if self._position_error is not None:
            return BestMoveResult(
                "0000",
                "none",
                ProofStatus.UNKNOWN,
                "invalid_position",
                0,
                search_max_ply,
                search_node_limit,
                time_limit_ms,
                False,
                0,
                0,
            )

        legal_moves = self.position.legal_moves()
        if not legal_moves:
            return BestMoveResult(
                "0000",
                "none",
                ProofStatus.UNKNOWN,
                "no_legal_moves",
                0,
                search_max_ply,
                search_node_limit,
                time_limit_ms,
                False,
                0,
                0,
            )

        legal_by_uci = {move.to_uci(): move for move in legal_moves}
        if searchmoves:
            searchmove_set = set(searchmoves)
            legal_by_uci = {
                uci: move for uci, move in legal_by_uci.items() if uci in searchmove_set
            }
            legal_moves = list(legal_by_uci.values())
            if not legal_moves:
                return BestMoveResult(
                    "0000",
                    "none",
                    ProofStatus.UNKNOWN,
                    "no_searchmoves",
                    0,
                    search_max_ply,
                    search_node_limit,
                    time_limit_ms,
                    False,
                    0,
                    0,
                )
        if stop_event is not None and stop_event.is_set():
            return self._stopped_result(
                sorted(legal_moves, key=lambda move: move.to_uci())[0],
                search_max_ply,
                search_node_limit,
                time_limit_ms,
                search_started,
            )
        target = ProofTarget.RED if self.position.side_to_move is Color.RED else ProofTarget.BLACK
        proof_position = _search_position(self.position)
        stored = (
            self._stored_proof_bestmove(
                target,
                legal_by_uci,
                sorted(legal_by_uci.values(), key=lambda move: move.to_uci()),
                search_max_ply,
                search_node_limit,
                time_limit_ms,
            )
            if self.proof_enabled
            else None
        )
        if stored is not None:
            return replace(
                stored,
                total_search_elapsed_ms=_elapsed_ms_since(search_started),
            )
        if self.prefer_external_fallback:
            fallback_started = perf_counter()
            external_move = self._external_fallback_move(
                proof_position,
                legal_by_uci,
                fallback_go_command,
            )
            external_ordering_elapsed_ms = _elapsed_ms_since(fallback_started)
            if external_move is not None:
                return self._fallback_result(
                    external_move,
                    ProofStatus.UNKNOWN,
                    "external_uci",
                    0,
                    search_max_ply,
                    search_node_limit,
                    time_limit_ms,
                    time_limit_reached=False,
                    resolved_store_hits=0,
                    resolved_store_misses=0,
                    external_ordering_elapsed_ms=external_ordering_elapsed_ms,
                    proof_search_elapsed_ms=proof_search_elapsed_ms,
                    total_search_elapsed_ms=_elapsed_ms_since(search_started),
                )
        ordering_error = False
        used_external_ordering = False
        ordering_time_limit_ms = _ordering_time_limit_ms(deadline)
        if self.external_move_ordering and ordering_time_limit_ms is not None:
            ordering_time_limit_ms = min(
                ordering_time_limit_ms,
                _MAX_EXTERNAL_ORDERING_TIME_MS,
            )
        if ordering_time_limit_ms == 0:
            ordered_legal_moves = sorted(legal_moves, key=lambda move: move.to_uci())
        else:
            ordering_started = perf_counter()
            try:
                ordered_legal_moves = self._ordered_legal_moves(
                    legal_moves,
                    proof_position,
                    ordering_time_limit_ms,
                    stop_event,
                )
                used_external_ordering = self.external_move_ordering
            except Exception:
                ordered_legal_moves = sorted(legal_moves, key=lambda move: move.to_uci())
                ordering_error = True
            external_ordering_elapsed_ms = _elapsed_ms_since(ordering_started)
        if stop_event is not None and stop_event.is_set():
            return self._stopped_result(
                ordered_legal_moves[0],
                search_max_ply,
                search_node_limit,
                time_limit_ms,
                search_started,
            )
        if ordering_error:
            return self._fallback_result(
                ordered_legal_moves[0],
                ProofStatus.UNKNOWN,
                "ordering_error",
                0,
                search_max_ply,
                search_node_limit,
                time_limit_ms,
                time_limit_reached=False,
                resolved_store_hits=0,
                resolved_store_misses=0,
                external_ordering_elapsed_ms=external_ordering_elapsed_ms,
                proof_search_elapsed_ms=proof_search_elapsed_ms,
                total_search_elapsed_ms=_elapsed_ms_since(search_started),
            )
        if not self.proof_enabled:
            return self._fallback_result(
                ordered_legal_moves[0],
                ProofStatus.UNKNOWN,
                "local_only",
                0,
                search_max_ply,
                search_node_limit,
                time_limit_ms,
                time_limit_reached=False,
                resolved_store_hits=0,
                resolved_store_misses=0,
                external_ordering_elapsed_ms=external_ordering_elapsed_ms,
                proof_search_elapsed_ms=0,
                total_search_elapsed_ms=_elapsed_ms_since(search_started),
            )
        if not _can_start_proof_search(deadline):
            if used_external_ordering:
                fallback_status, fallback_reason = _searchmoves_limited_fallback_status(
                    ProofStatus.UNKNOWN,
                    "time_limit",
                    bool(searchmoves),
                )
                return self._fallback_result(
                    ordered_legal_moves[0],
                    fallback_status,
                    fallback_reason,
                    0,
                    search_max_ply,
                    search_node_limit,
                    time_limit_ms,
                    time_limit_reached=True,
                    resolved_store_hits=0,
                    resolved_store_misses=0,
                    external_ordering_elapsed_ms=external_ordering_elapsed_ms,
                    proof_search_elapsed_ms=proof_search_elapsed_ms,
                    total_search_elapsed_ms=_elapsed_ms_since(search_started),
                )
            fallback_started = perf_counter()
            external_move = self._external_fallback_move(
                proof_position,
                legal_by_uci,
                fallback_go_command,
            )
            external_fallback_elapsed_ms = _elapsed_ms_since(fallback_started)
            if external_move is not None:
                return self._fallback_result(
                    external_move,
                    ProofStatus.UNKNOWN,
                    "external_uci",
                    0,
                    search_max_ply,
                    search_node_limit,
                    time_limit_ms,
                    time_limit_reached=True,
                    resolved_store_hits=0,
                    resolved_store_misses=0,
                    external_ordering_elapsed_ms=external_fallback_elapsed_ms,
                    proof_search_elapsed_ms=proof_search_elapsed_ms,
                    total_search_elapsed_ms=_elapsed_ms_since(search_started),
                )
            fallback_status, fallback_reason = _searchmoves_limited_fallback_status(
                ProofStatus.UNKNOWN,
                "time_limit",
                bool(searchmoves),
            )
            return self._fallback_result(
                ordered_legal_moves[0],
                fallback_status,
                fallback_reason,
                0,
                search_max_ply,
                search_node_limit,
                time_limit_ms,
                time_limit_reached=True,
                resolved_store_hits=0,
                resolved_store_misses=0,
                external_ordering_elapsed_ms=external_ordering_elapsed_ms,
                proof_search_elapsed_ms=proof_search_elapsed_ms,
                total_search_elapsed_ms=_elapsed_ms_since(search_started),
            )
        proof_search_started = perf_counter()
        try:
            proof_move_orderer = _frozen_root_move_orderer(
                ordered_legal_moves,
            )
            result = BoundedProofSearch(
                target,
                max_ply=search_max_ply,
                node_limit=search_node_limit,
                move_orderer=proof_move_orderer,
                time_limit_seconds=_proof_search_time_limit_seconds(deadline),
                resolver=_root_skipping_resolver(self.proof_store, bool(searchmoves)),
                stop_event=stop_event,
            ).search(proof_position)
        except Exception:
            if self.proof_store is not None:
                try:
                    result = BoundedProofSearch(
                        target,
                        max_ply=search_max_ply,
                        node_limit=search_node_limit,
                        move_orderer=_frozen_root_move_orderer(
                            ordered_legal_moves,
                        ),
                        time_limit_seconds=_proof_search_time_limit_seconds(deadline),
                        stop_event=stop_event,
                    ).search(proof_position)
                except Exception:
                    proof_search_elapsed_ms = _elapsed_ms_since(proof_search_started)
                    return self._fallback_result(
                        ordered_legal_moves[0],
                        ProofStatus.UNKNOWN,
                        "search_error",
                        0,
                        search_max_ply,
                        search_node_limit,
                        time_limit_ms,
                        time_limit_reached=False,
                        resolved_store_hits=0,
                        resolved_store_misses=0,
                        external_ordering_elapsed_ms=external_ordering_elapsed_ms,
                        proof_search_elapsed_ms=proof_search_elapsed_ms,
                        total_search_elapsed_ms=_elapsed_ms_since(search_started),
                    )
            else:
                proof_search_elapsed_ms = _elapsed_ms_since(proof_search_started)
                return self._fallback_result(
                    ordered_legal_moves[0],
                    ProofStatus.UNKNOWN,
                    "search_error",
                    0,
                    search_max_ply,
                    search_node_limit,
                    time_limit_ms,
                    time_limit_reached=False,
                    resolved_store_hits=0,
                    resolved_store_misses=0,
                    external_ordering_elapsed_ms=external_ordering_elapsed_ms,
                    proof_search_elapsed_ms=proof_search_elapsed_ms,
                    total_search_elapsed_ms=_elapsed_ms_since(search_started),
                )
        proof_search_elapsed_ms = _elapsed_ms_since(proof_search_started)

        raw_result = result
        result = _searchmoves_limited_result(raw_result, bool(searchmoves))
        if result.artifact.status is ProofStatus.PROVEN:
            for child in result.artifact.children:
                if child.status is ProofStatus.PROVEN and child.move in legal_by_uci:
                    if used_external_ordering:
                        return self._fallback_result(
                            legal_by_uci[child.move],
                            ProofStatus.UNKNOWN,
                            "external_ordering",
                            result.nodes_searched,
                            search_max_ply,
                            search_node_limit,
                            time_limit_ms,
                            result.time_limit_reached,
                            result.resolved_store_hits,
                            result.resolved_store_misses,
                            external_ordering_elapsed_ms=external_ordering_elapsed_ms,
                            proof_search_elapsed_ms=proof_search_elapsed_ms,
                            total_search_elapsed_ms=_elapsed_ms_since(search_started),
                        )
                    proof_store_saved, proof_store_save_error = self._save_online_proof(
                        result.artifact,
                        search_node_limit,
                    )
                    return BestMoveResult(
                        child.move,
                        "proof",
                        result.artifact.status,
                        result.artifact.reason,
                        result.nodes_searched,
                        search_max_ply,
                        search_node_limit,
                        time_limit_ms,
                        result.time_limit_reached,
                        result.resolved_store_hits,
                        result.resolved_store_misses,
                        proof_store_saved,
                        proof_store_save_error,
                        external_ordering_elapsed_ms,
                        proof_search_elapsed_ms,
                        _elapsed_ms_since(search_started),
                        _proof_artifact_sha256(result.artifact),
                    )

        fallback_status, fallback_reason = _searchmoves_limited_fallback_status(
            result.artifact.status,
            result.artifact.reason,
            bool(searchmoves),
        )
        fallback_move = _proof_aware_fallback_move(
            ordered_legal_moves,
            raw_result.artifact,
            legal_by_uci,
        )
        return self._fallback_result(
            fallback_move,
            fallback_status,
            fallback_reason,
            result.nodes_searched,
            search_max_ply,
            search_node_limit,
            time_limit_ms,
            result.time_limit_reached,
            result.resolved_store_hits,
            result.resolved_store_misses,
            external_ordering_elapsed_ms=external_ordering_elapsed_ms,
            proof_search_elapsed_ms=proof_search_elapsed_ms,
            total_search_elapsed_ms=_elapsed_ms_since(search_started),
        )

    def _stored_proof_bestmove(
        self,
        target: ProofTarget,
        legal_by_uci: dict[str, Move],
        ordered_legal_moves: Sequence[Move],
        max_ply: int,
        node_limit: int,
        time_limit_ms: int | None,
    ) -> BestMoveResult | None:
        if self.proof_store is None:
            return None
        artifact = self._resolve_stored_proven(target, max_ply)
        if artifact is None:
            return None
        if (
            artifact.target is not target
            or artifact.fen != self.position.to_fen()
            or artifact.history_signature != _history_signature(self.position)
        ):
            return None
        proven_child_moves = {
            child.move
            for child in artifact.children
            if child.status is ProofStatus.PROVEN and child.move in legal_by_uci
        }
        for move in ordered_legal_moves:
            if move.to_uci() in proven_child_moves:
                return BestMoveResult(
                    move.to_uci(),
                    "proof_store",
                    artifact.status,
                    artifact.reason,
                    0,
                    artifact.max_ply,
                    node_limit,
                    time_limit_ms,
                    False,
                    1,
                    0,
                    proof_artifact_sha256=_proof_artifact_sha256(artifact),
                )
        return None

    def _resolve_stored_proven(
        self,
        target: ProofTarget,
        max_ply: int,
    ) -> ProofArtifact | None:
        assert self.proof_store is not None
        fen = self.position.to_fen()
        history_signature = _history_signature(self.position)
        cache_key = (fen, target, max_ply, history_signature)
        cached = self._verified_store_proofs.get(cache_key)
        if cached is not None:
            return cached
        try:
            resolve_proven = getattr(self.proof_store, "resolve_proven", None)
            if callable(resolve_proven):
                artifact = resolve_proven(
                    fen,
                    target,
                    max_ply=max_ply,
                    history_signature=history_signature,
                )
            else:
                artifact = self.proof_store.resolve_for_search(
                    fen,
                    target,
                    max_ply,
                    history_signature,
                )
        except Exception:
            return None
        if artifact is None or artifact.status is not ProofStatus.PROVEN:
            return None
        verification = self._proof_verifier.verify(artifact)
        if not verification.valid:
            return None
        self._verified_store_proofs[cache_key] = artifact
        return artifact

    def _preload_stored_proof(self) -> None:
        if self.proof_store is None:
            return
        target = ProofTarget.RED if self.position.side_to_move is Color.RED else ProofTarget.BLACK
        self._resolve_stored_proven(target, self.max_ply)

    def _ordered_legal_moves(
        self,
        legal_moves: Sequence[Move] | None = None,
        position: Position | GameState | None = None,
        time_limit_ms: int | None = None,
        stop_event: Event | None = None,
    ) -> list[Move]:
        position = self.position if position is None else position
        legal_moves = self.position.legal_moves() if legal_moves is None else legal_moves
        if self.move_orderer is None:
            return sorted(legal_moves, key=lambda move: move.to_uci())
        cancellable_order = getattr(self.move_orderer, "order_moves_with_stop_event", None)
        if stop_event is not None and callable(cancellable_order):
            ordered = cancellable_order(position, legal_moves, stop_event, time_limit_ms)
            return _sanitize_ordered_moves(legal_moves, ordered)
        timed_order = getattr(self.move_orderer, "order_moves_with_time_limit", None)
        if time_limit_ms is not None and callable(timed_order):
            ordered = timed_order(position, legal_moves, time_limit_ms)
        else:
            ordered = self.move_orderer.order_moves(position, legal_moves)
        return _sanitize_ordered_moves(legal_moves, ordered)

    def _stopped_result(
        self,
        move: Move,
        max_ply: int,
        node_limit: int,
        time_limit_ms: int | None,
        search_started: float,
    ) -> BestMoveResult:
        return BestMoveResult(
            move.to_uci(),
            "self_fallback",
            ProofStatus.UNKNOWN,
            "stopped",
            0,
            max_ply,
            node_limit,
            time_limit_ms,
            False,
            0,
            0,
            total_search_elapsed_ms=_elapsed_ms_since(search_started),
        )

    def _external_fallback_move(
        self,
        position: Position | GameState,
        legal_by_uci: dict[str, Move],
        go_command: str | None,
    ) -> Move | None:
        if self.move_orderer is None or not go_command:
            return None
        bestmove = getattr(self.move_orderer, "bestmove_with_go_command", None)
        if not callable(bestmove):
            return None
        try:
            move = bestmove(position, list(legal_by_uci.values()), go_command)
        except Exception:
            return None
        if isinstance(move, Move):
            return legal_by_uci.get(move.to_uci())
        if isinstance(move, str):
            return legal_by_uci.get(move)
        return None

    def _fallback_result(
        self,
        move: Move,
        proof_status: ProofStatus,
        reason: str,
        nodes_searched: int,
        max_ply: int,
        node_limit: int,
        time_limit_ms: int | None,
        time_limit_reached: bool,
        resolved_store_hits: int,
        resolved_store_misses: int,
        external_ordering_elapsed_ms: int = 0,
        proof_search_elapsed_ms: int = 0,
        total_search_elapsed_ms: int = 0,
    ) -> BestMoveResult:
        return BestMoveResult(
            move.to_uci(),
            "external_fallback"
            if reason == "external_uci" or self.external_move_ordering
            else "self_fallback",
            proof_status,
            reason,
            nodes_searched,
            max_ply,
            node_limit,
            time_limit_ms,
            time_limit_reached,
            resolved_store_hits,
            resolved_store_misses,
            False,
            None,
            external_ordering_elapsed_ms,
            proof_search_elapsed_ms,
            total_search_elapsed_ms,
        )

    def _save_online_proof(
        self,
        artifact: ProofArtifact,
        node_limit: int,
    ) -> tuple[bool, str | None]:
        if not self.save_online_proofs or self.proof_store is None:
            return False, None
        if artifact.status is not ProofStatus.PROVEN:
            return False, None
        save = getattr(self.proof_store, "save", None)
        if not callable(save):
            return False, "save_unavailable"
        try:
            save(artifact, node_limit=node_limit)
        except Exception as exc:
            return False, _uci_info_token(str(exc) or exc.__class__.__name__)
        return True, None

    def emergency_bestmove(self, searchmoves: Sequence[str] | None = None) -> str:
        if self._position_error is not None:
            return "0000"
        legal_moves = self.position.legal_moves()
        if searchmoves:
            searchmove_set = set(searchmoves)
            legal_moves = [
                move for move in legal_moves if move.to_uci() in searchmove_set
            ]
        if not legal_moves:
            return "0000"
        return min(legal_moves, key=lambda move: move.to_uci()).to_uci()


def _uci_option_name(option_line: str) -> str:
    if not option_line.startswith("option name "):
        return ""
    remainder = option_line[len("option name ") :]
    name, separator, _ = remainder.partition(" type ")
    if not separator:
        return ""
    return name.replace(" ", "").lower()


def run_uci_loop(
    engine: ProofAssistedUciEngine | None = None,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> int:
    engine = engine or ProofAssistedUciEngine()
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout

    def send(line: str) -> None:
        print(line, file=output_stream, flush=True)

    active_search: _ActiveSearch | None = None

    def emit_bestmove(best: BestMoveResult) -> None:
        send(
            "info string "
            f"source={best.source} status={best.proof_status.value} "
            f"reason={best.reason} nodes={best.nodes_searched} "
            f"max_ply={best.max_ply} node_limit={best.node_limit} "
            f"time_limit_ms={best.time_limit_ms or 0} "
            f"time_limit_reached={int(best.time_limit_reached)} "
            f"resolved_store_hits={best.resolved_store_hits} "
            f"resolved_store_misses={best.resolved_store_misses} "
            f"proof_store_saved={int(best.proof_store_saved)} "
                    f"proof_store_save_error={best.proof_store_save_error or 'none'} "
                    f"proof_artifact_sha256={best.proof_artifact_sha256 or 'none'} "
                    f"external_ordering_elapsed_ms={best.external_ordering_elapsed_ms} "
            f"proof_search_elapsed_ms={best.proof_search_elapsed_ms} "
            f"total_search_elapsed_ms={best.total_search_elapsed_ms}"
        )
        send(f"bestmove {best.move}")

    def emit_go_error(
        exc: Exception,
        searchmoves: Sequence[str] | None = None,
    ) -> None:
        send(f"info string go error: {exc}")
        emergency_move = engine.emergency_bestmove(searchmoves)
        send(
            "info string "
            f"source=emergency status=unknown reason=go_error "
            f"nodes=0 max_ply={engine.max_ply} node_limit={engine.node_limit} "
            "time_limit_ms=0 time_limit_reached=0 "
            "resolved_store_hits=0 resolved_store_misses=0 "
            "proof_store_saved=0 proof_store_save_error=none "
            "proof_artifact_sha256=none "
            "external_ordering_elapsed_ms=0 proof_search_elapsed_ms=0 "
            "total_search_elapsed_ms=0"
        )
        send(f"bestmove {emergency_move}")

    def emit_active_search(active: _ActiveSearch) -> None:
        with active.emission_lock:
            if active.emitted:
                return
            if active.error is not None:
                emit_go_error(active.error, active.searchmoves)
            elif active.result is not None:
                emit_bestmove(active.result)
            else:
                return
            active.emitted = True

    def start_search(limits: GoLimits, go_command: str) -> _ActiveSearch:
        stop_event = Event()
        active = _ActiveSearch(
            thread=Thread(),
            stop_event=stop_event,
            searchmoves=limits.searchmoves,
            ponder=limits.ponder,
            infinite=limits.infinite,
        )

        def worker() -> None:
            try:
                active.result = engine.choose_bestmove(
                    max_ply=limits.max_ply,
                    node_limit=limits.node_limit,
                    time_limit_ms=limits.time_limit_ms,
                    searchmoves=limits.searchmoves,
                    stop_event=stop_event,
                    fallback_go_command=go_command,
                )
            except Exception as exc:  # pragma: no cover - exercised through loop behavior
                active.error = exc
            if active.infinite and not active.stop_event.is_set():
                return
            if active.ponder and not active.stop_event.is_set():
                return
            emit_active_search(active)

        active.thread = Thread(target=worker, daemon=True)
        active.thread.start()
        return active

    def finish_search(*, stop: bool, emit: bool) -> None:
        nonlocal active_search
        if active_search is None:
            return
        if stop:
            active_search.stop_event.set()
        active_search.thread.join()
        if emit:
            emit_active_search(active_search)
        active_search = None

    try:
        for raw_line in input_stream:
            command = raw_line.strip()
            if not command:
                continue

            if command == "uci":
                finish_search(stop=True, emit=True)
                send("id name XQ Proof Lab")
                send("id author xiangqi-proof-tree")
                for option_line in engine.uci_options():
                    send(option_line)
                send("uciok")
            elif command == "isready":
                send("readyok")
            elif command == "ucinewgame":
                finish_search(stop=True, emit=True)
                engine.new_game()
            elif command.startswith("setoption "):
                finish_search(stop=True, emit=True)
                try:
                    name, value = _parse_setoption(command)
                    engine.set_option(name, value)
                except Exception as exc:
                    send(f"info string setoption error: {exc}")
            elif command.startswith("position "):
                finish_search(stop=True, emit=True)
                try:
                    engine.set_position(command)
                except Exception as exc:
                    engine.invalidate_position(str(exc))
                    send(f"info string position error: {exc}")
            elif _starts_with_uci_token(command, "go"):
                finish_search(stop=True, emit=True)
                limits: GoLimits | None = None
                searchmoves = _extract_go_searchmoves(command)
                try:
                    limits = _parse_go_limits(
                        command,
                        engine.max_ply,
                        engine.node_limit,
                        engine.position.side_to_move,
                    )
                    searchmoves = limits.searchmoves
                    active_search = start_search(limits, command)
                except Exception as exc:
                    emit_go_error(exc, searchmoves)
            elif command == "stop":
                finish_search(stop=True, emit=True)
            elif command == "ponderhit":
                if active_search is None or not active_search.ponder:
                    continue
                active_search.ponder = False
                if not active_search.thread.is_alive():
                    emit_active_search(active_search)
            elif command == "quit":
                finish_search(
                    stop=bool(
                        active_search
                        and (active_search.infinite or active_search.ponder)
                    ),
                    emit=True,
                )
                return 0
            else:
                finish_search(stop=True, emit=True)
                send(f"info string unsupported command: {command}")

        finish_search(stop=True, emit=True)
        return 0
    finally:
        engine.close()


def _parse_setoption(command: str) -> tuple[str, str | None]:
    tokens = command.split()
    lowered = [token.lower() for token in tokens]
    if len(tokens) < 3 or lowered[0] != "setoption" or lowered[1] != "name":
        raise ValueError("expected 'setoption name <name> [value <value>]'")
    if "value" in lowered:
        value_index = lowered.index("value")
        name = " ".join(tokens[2:value_index]).strip()
        value = " ".join(tokens[value_index + 1 :]).strip()
        if not name or not value:
            raise ValueError("setoption name and value must be non-empty")
        return name, value
    name = " ".join(tokens[2:]).strip()
    if not name:
        raise ValueError("setoption name must be non-empty")
    return name, None


def _parse_uci_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise ValueError(f"expected boolean value, got {value!r}")


def _format_uci_bool(value: bool) -> str:
    return "true" if value else "false"


_UCI_RESPONSE_RESERVE_MS = 100
_MIN_PROOF_SEARCH_TIME_MS = 250
_TACTICAL_ORDERING_MIN_TIME_MS = 450
_TACTICAL_ORDERING_PROOF_RESERVE_MS = 189
_MAX_LOCAL_ORDERING_TIME_MS = 210
_MAX_EXTERNAL_ORDERING_TIME_MS = 150
_PROOF_SEARCH_OVERRUN_RESERVE_MS = 150


def _search_deadline(time_limit_ms: int | None) -> float | None:
    if time_limit_ms is None:
        return None
    return perf_counter() + time_limit_ms / 1000.0


def _remaining_time_ms(deadline: float | None) -> int | None:
    if deadline is None:
        return None
    return max(0, int((deadline - perf_counter()) * 1000))


def _elapsed_ms_since(started: float) -> int:
    return max(0, int((perf_counter() - started) * 1000))


def _remaining_time_seconds(deadline: float | None) -> float | None:
    remaining_ms = _remaining_time_ms(deadline)
    if remaining_ms is None:
        return None
    return remaining_ms / 1000.0


def _proof_search_time_limit_seconds(deadline: float | None) -> float | None:
    remaining_ms = _remaining_time_ms(deadline)
    if remaining_ms is None:
        return None
    return max(0, remaining_ms - _PROOF_SEARCH_OVERRUN_RESERVE_MS) / 1000.0


def _ordering_time_limit_ms(deadline: float | None) -> int | None:
    remaining_ms = _remaining_time_ms(deadline)
    if remaining_ms is None:
        return None
    proof_reserve_ms = _MIN_PROOF_SEARCH_TIME_MS
    if remaining_ms >= _TACTICAL_ORDERING_MIN_TIME_MS:
        proof_reserve_ms = _TACTICAL_ORDERING_PROOF_RESERVE_MS
    available_ms = remaining_ms - _UCI_RESPONSE_RESERVE_MS - proof_reserve_ms
    return min(_MAX_LOCAL_ORDERING_TIME_MS, max(0, available_ms))


def _can_start_proof_search(deadline: float | None) -> bool:
    remaining_ms = _remaining_time_ms(deadline)
    return remaining_ms is None or remaining_ms >= _MIN_PROOF_SEARCH_TIME_MS


def _parse_go_limits(
    command: str,
    default_max_ply: int,
    default_node_limit: int,
    side_to_move: Color,
) -> GoLimits:
    tokens = command.split()
    if not tokens or tokens[0] != "go":
        raise ValueError("go command must start with 'go'")
    max_ply = default_max_ply
    node_limit = default_node_limit
    movetime_ms: int | None = None
    wtime_ms: int | None = None
    btime_ms: int | None = None
    winc_ms = 0
    binc_ms = 0
    movestogo: int | None = None
    searchmoves: list[str] = []
    infinite = False
    ponder = False
    index = 1
    while index < len(tokens):
        token = tokens[index].lower()
        if token == "depth":
            value = _go_int_value(tokens, index, "depth")
            if value < 0:
                raise ValueError("go depth must be non-negative")
            max_ply = value
            index += 2
        elif token == "nodes":
            value = _go_int_value(tokens, index, "nodes")
            if value <= 0:
                raise ValueError("go nodes must be positive")
            node_limit = value
            index += 2
        elif token == "movetime":
            value = _go_int_value(tokens, index, "movetime")
            if value < 0:
                raise ValueError("go movetime must be non-negative")
            movetime_ms = value
            index += 2
        elif token == "wtime":
            value = _go_int_value(tokens, index, "wtime")
            if value < 0:
                raise ValueError("go wtime must be non-negative")
            wtime_ms = value
            index += 2
        elif token == "btime":
            value = _go_int_value(tokens, index, "btime")
            if value < 0:
                raise ValueError("go btime must be non-negative")
            btime_ms = value
            index += 2
        elif token == "winc":
            value = _go_int_value(tokens, index, "winc")
            if value < 0:
                raise ValueError("go winc must be non-negative")
            winc_ms = value
            index += 2
        elif token == "binc":
            value = _go_int_value(tokens, index, "binc")
            if value < 0:
                raise ValueError("go binc must be non-negative")
            binc_ms = value
            index += 2
        elif token == "movestogo":
            value = _go_int_value(tokens, index, "movestogo")
            if value <= 0:
                raise ValueError("go movestogo must be positive")
            movestogo = value
            index += 2
        elif token == "searchmoves":
            index += 1
            while index < len(tokens) and tokens[index].lower() not in _GO_KEYWORDS_AFTER_SEARCHMOVES:
                searchmoves.append(tokens[index])
                index += 1
        elif token == "infinite":
            infinite = True
            index += 1
        elif token == "ponder":
            ponder = True
            index += 1
        else:
            index += 1
    return GoLimits(
        max_ply=max_ply,
        node_limit=node_limit,
        time_limit_ms=_search_time_limit_ms(
            side_to_move,
            movetime_ms,
            wtime_ms,
            btime_ms,
            winc_ms,
            binc_ms,
            movestogo,
        ),
        searchmoves=tuple(searchmoves),
        infinite=infinite,
        ponder=ponder,
    )


def _extract_go_searchmoves(command: str) -> tuple[str, ...]:
    tokens = command.split()
    if not tokens or tokens[0] != "go":
        return ()
    searchmoves: list[str] = []
    index = 1
    while index < len(tokens):
        token = tokens[index].lower()
        if token == "searchmoves":
            index += 1
            while index < len(tokens) and tokens[index].lower() not in _GO_KEYWORDS_AFTER_SEARCHMOVES:
                searchmoves.append(tokens[index])
                index += 1
        else:
            index += 1
    return tuple(searchmoves)


_GO_KEYWORDS_AFTER_SEARCHMOVES = {
    "depth",
    "nodes",
    "movetime",
    "wtime",
    "btime",
    "winc",
    "binc",
    "movestogo",
    "mate",
    "ponder",
    "infinite",
}


def _search_time_limit_ms(
    side_to_move: Color,
    movetime_ms: int | None,
    wtime_ms: int | None,
    btime_ms: int | None,
    winc_ms: int,
    binc_ms: int,
    movestogo: int | None,
) -> int | None:
    if movetime_ms is not None:
        return movetime_ms

    remaining = wtime_ms if side_to_move is Color.RED else btime_ms
    increment = winc_ms if side_to_move is Color.RED else binc_ms
    if remaining is None:
        return None
    if remaining <= 0:
        return 0

    moves = movestogo or 30
    base = remaining // max(1, moves)
    budget = base + increment // 2

    reserve = min(remaining, max(50, remaining // 20))
    capped = min(budget, max(0, remaining - reserve))
    return max(0, capped)


def _go_int_value(tokens: list[str], index: int, name: str) -> int:
    if index + 1 >= len(tokens):
        raise ValueError(f"go {name} is missing a value")
    try:
        return int(tokens[index + 1])
    except ValueError as exc:
        raise ValueError(f"go {name} value must be an integer") from exc


def _history_signature(position) -> str:
    if hasattr(position, "moves") and not getattr(position, "moves"):
        return ""
    signature = getattr(position, "history_signature", None)
    return signature() if callable(signature) else ""


def _proof_artifact_sha256(artifact: ProofArtifact) -> str:
    payload = json.dumps(
        artifact.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _uci_info_token(value: str) -> str:
    token = "".join("_" if char.isspace() or char == "=" else char for char in value)
    return token[:160] or "error"


def _search_position(position):
    if hasattr(position, "moves") and not getattr(position, "moves"):
        bare_position = getattr(position, "position", None)
        if bare_position is not None:
            return bare_position
    return position


def _sanitize_ordered_moves(
    legal_moves: Sequence[Move],
    ordered_moves: Sequence[Move],
) -> list[Move]:
    legal_by_uci = {move.to_uci(): move for move in legal_moves}
    sanitized: list[Move] = []
    seen: set[str] = set()
    for move in ordered_moves:
        uci = move.to_uci()
        if uci not in legal_by_uci or uci in seen:
            continue
        sanitized.append(legal_by_uci[uci])
        seen.add(uci)
    for uci in sorted(set(legal_by_uci) - seen):
        sanitized.append(legal_by_uci[uci])
    return sanitized


def _root_limited_move_orderer(
    move_orderer: MoveOrderer | None,
    legal_by_uci: dict[str, Move],
    enabled: bool,
) -> MoveOrderer | None:
    if not enabled:
        return move_orderer
    return _RootSearchMovesOrderer(move_orderer, set(legal_by_uci))


def _frozen_root_move_orderer(ordered_root_moves: Sequence[Move]) -> MoveOrderer:
    return _FrozenRootMoveOrderer(ordered_root_moves)


def _root_skipping_resolver(
    resolver: ProofSearchStore | None,
    enabled: bool,
) -> ProofSearchStore | None:
    if resolver is None or not enabled:
        return resolver
    return _RootSkippingResolver(resolver)


def _searchmoves_limited_fallback_status(
    status: ProofStatus,
    reason: str,
    searchmoves_enabled: bool,
) -> tuple[ProofStatus, str]:
    if not searchmoves_enabled or status is ProofStatus.PROVEN:
        return status, reason
    if reason.startswith("searchmoves_limited_"):
        return ProofStatus.UNKNOWN, reason
    return ProofStatus.UNKNOWN, f"searchmoves_limited_{reason}"


def _proof_aware_fallback_move(
    ordered_legal_moves: Sequence[Move],
    artifact: ProofArtifact,
    legal_by_uci: dict[str, Move],
) -> Move:
    if not ordered_legal_moves:
        raise ValueError("ordered_legal_moves must not be empty")
    if getattr(artifact.node_kind, "value", artifact.node_kind) != "or":
        return ordered_legal_moves[0]
    child_by_move = {
        child.move: child
        for child in artifact.children
        if child.move is not None and child.move in legal_by_uci
    }
    if not child_by_move:
        return ordered_legal_moves[0]
    fallback_index = {move.to_uci(): index for index, move in enumerate(ordered_legal_moves)}
    return min(
        ordered_legal_moves,
        key=lambda move: _proof_fallback_sort_key(
            child_by_move.get(move.to_uci()),
            fallback_index[move.to_uci()],
        ),
    )


def _proof_fallback_sort_key(
    artifact: ProofArtifact | None,
    fallback_index: int,
) -> tuple[int, int, int, int]:
    if artifact is None:
        return (1, 1, -1, fallback_index)
    if artifact.status is ProofStatus.PROVEN:
        return (0, artifact.proof, -artifact.disproof, fallback_index)
    if artifact.status is ProofStatus.UNKNOWN:
        return (1, artifact.proof, -artifact.disproof, fallback_index)
    if artifact.status is ProofStatus.DRAW:
        return (2, artifact.proof, -artifact.disproof, fallback_index)
    if artifact.status is ProofStatus.DISPROVEN:
        return (3, artifact.proof, -artifact.disproof, fallback_index)
    return (4, artifact.proof, -artifact.disproof, fallback_index)


def _searchmoves_limited_result(
    result: SearchResult,
    searchmoves_enabled: bool,
) -> SearchResult:
    if not searchmoves_enabled or result.artifact.status is ProofStatus.PROVEN:
        return result
    artifact = result.artifact
    reason = artifact.reason
    if not reason.startswith("searchmoves_limited_"):
        reason = f"searchmoves_limited_{reason}"
    return replace(
        result,
        artifact=ProofArtifact(
            fen=artifact.fen,
            target=artifact.target,
            max_ply=artifact.max_ply,
            node_kind=artifact.node_kind,
            status=ProofStatus.UNKNOWN,
            proof=1,
            disproof=1,
            move=artifact.move,
            reason=reason,
            history_signature=artifact.history_signature,
            position_command=artifact.position_command,
            children=(),
        ),
    )


class _FrozenRootMoveOrderer:
    def __init__(self, ordered_root_moves: Sequence[Move]) -> None:
        self.ordered_root_moves = tuple(move.to_uci() for move in ordered_root_moves)
        self._root_pending = True

    def order_moves(self, position: Position, moves: Sequence[Move]) -> list[Move]:
        return self._order_moves(moves)

    def order_moves_with_time_limit(
        self,
        position: Position,
        moves: Sequence[Move],
        time_limit_ms: int,
    ) -> list[Move]:
        return self._order_moves(moves)

    def _order_moves(
        self,
        moves: Sequence[Move],
    ) -> list[Move]:
        if not self._root_pending:
            return sorted(moves, key=lambda move: move.to_uci())
        self._root_pending = False
        legal_by_uci = {move.to_uci(): move for move in moves}
        return [
            legal_by_uci[uci]
            for uci in self.ordered_root_moves
            if uci in legal_by_uci
        ]


class _RootSearchMovesOrderer:
    def __init__(
        self,
        move_orderer: MoveOrderer | None,
        root_moves: set[str],
    ) -> None:
        self.move_orderer = move_orderer
        self.root_moves = root_moves
        self._root_pending = True

    def order_moves(self, position: Position, moves: Sequence[Move]) -> list[Move]:
        return self._order_moves(position, moves, None)

    def order_moves_with_time_limit(
        self,
        position: Position,
        moves: Sequence[Move],
        time_limit_ms: int,
    ) -> list[Move]:
        return self._order_moves(position, moves, time_limit_ms)

    def _order_moves(
        self,
        position: Position,
        moves: Sequence[Move],
        time_limit_ms: int | None,
    ) -> list[Move]:
        legal_moves = list(moves)
        if self._root_pending:
            self._root_pending = False
            legal_moves = [
                move for move in legal_moves if move.to_uci() in self.root_moves
            ]
        if self.move_orderer is None:
            return sorted(legal_moves, key=lambda move: move.to_uci())
        timed_order = getattr(self.move_orderer, "order_moves_with_time_limit", None)
        if time_limit_ms is not None and callable(timed_order):
            ordered = timed_order(position, legal_moves, time_limit_ms)
        else:
            ordered = self.move_orderer.order_moves(position, legal_moves)
        return _sanitize_ordered_moves(legal_moves, ordered)


class _RootSkippingResolver:
    def __init__(self, resolver: ProofSearchStore) -> None:
        self.resolver = resolver
        self._root_pending = True

    def resolve_for_search(
        self,
        fen: str,
        target: ProofTarget,
        max_ply: int,
        history_signature: str = "",
    ) -> ProofArtifact | None:
        if self._root_pending:
            self._root_pending = False
            return None
        return self.resolver.resolve_for_search(
            fen,
            target,
            max_ply,
            history_signature,
        )
