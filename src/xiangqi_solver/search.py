from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from time import perf_counter
from typing import Protocol
from threading import Event

from xiangqi_core import Move, Position

from .pns import INF, ProofNumbers, combine_proof_numbers
from .proof import (
    ProofArtifact,
    ProofStatus,
    ProofTarget,
    game_result_and_reason,
    node_kind_for,
    status_refutes_target,
)


@dataclass(frozen=True, slots=True)
class SearchResult:
    artifact: ProofArtifact
    nodes_searched: int
    node_limit_reached: bool
    time_limit_reached: bool = False
    threshold_reached: bool = False
    cache_hits: int = 0
    resolved_cache_hits: int = 0
    resolved_store_hits: int = 0
    resolved_store_misses: int = 0


class MoveOrderer(Protocol):
    def order_moves(self, position: Position, moves: Sequence[Move]) -> list[Move]:
        ...


class ProofResolver(Protocol):
    def resolve_for_search(
        self,
        fen: str,
        target: ProofTarget,
        max_ply: int,
        history_signature: str = "",
    ) -> ProofArtifact | None:
        ...


def _capture_first_order(position: Position, moves: Sequence[Move]) -> list[Move]:
    board = getattr(position, "position", position)
    return sorted(
        moves,
        key=lambda move: (
            board.piece_at(move.to_square) is None,
            move.to_uci(),
        ),
    )


class BoundedProofSearch:
    def __init__(
        self,
        target: str | ProofTarget,
        max_ply: int,
        node_limit: int = 100_000,
        move_orderer: MoveOrderer | None = None,
        time_limit_seconds: float | None = None,
        resolver: ProofResolver | None = None,
        stop_event: Event | None = None,
    ) -> None:
        if max_ply < 0:
            raise ValueError("max_ply must be non-negative")
        if node_limit <= 0:
            raise ValueError("node_limit must be positive")
        if time_limit_seconds is not None and time_limit_seconds < 0:
            raise ValueError("time_limit_seconds must be non-negative")
        self.target = ProofTarget.parse(target)
        self.max_ply = max_ply
        self.node_limit = node_limit
        self.nodes_searched = 0
        self.node_limit_reached = False
        self.time_limit_reached = False
        self.deadline = (
            perf_counter() + time_limit_seconds if time_limit_seconds is not None else None
        )
        self.cache_hits = 0
        self.resolved_cache_hits = 0
        self.resolved_store_hits = 0
        self.resolved_store_misses = 0
        self._cache: dict[tuple[str, ProofTarget, int, str], ProofArtifact] = {}
        self._resolved_cache: dict[tuple[str, ProofTarget, int, str], ProofArtifact | None] = {}
        self.move_orderer = move_orderer
        self.resolver = resolver
        self.stop_event = stop_event
        if resolver is None:
            self._verifier = None
        else:
            from .verifier import ProofVerifier

            self._verifier = ProofVerifier()

    def search(self, position: Position) -> SearchResult:
        prepare = getattr(position, "for_search", None)
        if callable(prepare):
            position = prepare()
        artifact = self._search(position, self.max_ply, move=None)
        return SearchResult(
            artifact=artifact,
            nodes_searched=self.nodes_searched,
            node_limit_reached=self.node_limit_reached,
            time_limit_reached=self.time_limit_reached,
            cache_hits=self.cache_hits,
            resolved_cache_hits=self.resolved_cache_hits,
            resolved_store_hits=self.resolved_store_hits,
            resolved_store_misses=self.resolved_store_misses,
        )

    def _search(self, position: Position, max_ply: int, move: str | None) -> ProofArtifact:
        key = _state_cache_key(position, self.target, max_ply)
        if key in self._cache:
            self.cache_hits += 1
            cached = self._cache[key]
            if cached.move == move:
                return cached
            return ProofArtifact(
                fen=cached.fen,
                target=cached.target,
                max_ply=cached.max_ply,
                node_kind=cached.node_kind,
                status=cached.status,
                proof=cached.proof,
                disproof=cached.disproof,
                move=move,
                reason=cached.reason,
                history_signature=cached.history_signature,
                position_command=cached.position_command,
                children=cached.children,
            )

        resolved = self._resolve(position, max_ply, move)
        if resolved is not None:
            self._cache[key] = resolved
            return resolved

        if self._stop_requested():
            return self._unknown(position, max_ply, move, "stopped")

        if self._time_limit_reached():
            return self._unknown(position, max_ply, move, "time_limit")

        if self.nodes_searched >= self.node_limit:
            self.node_limit_reached = True
            return self._unknown(position, max_ply, move, "node_limit")

        self.nodes_searched += 1
        result, reason = game_result_and_reason(position)
        if result is not None:
            artifact = ProofArtifact.terminal(position, self.target, max_ply, move, result, reason)
            self._cache[key] = artifact
            return artifact

        if max_ply == 0:
            artifact = self._unknown(position, max_ply, move, "ply_bound")
            self._cache[key] = artifact
            return artifact

        node_kind = node_kind_for(position, self.target)
        legal_moves = self._order_moves(position, max_ply)
        children: list[ProofArtifact] = []

        if node_kind.value == "or":
            for legal_move in legal_moves:
                child = self._child(position, legal_move, max_ply)
                children.append(child)
                if self.time_limit_reached:
                    artifact = self._unknown(position, max_ply, move, "time_limit")
                    self._cache[key] = artifact
                    return artifact
                if child.status is ProofStatus.PROVEN:
                    artifact = self._combine(position, max_ply, move, children, "target_move_proves")
                    self._cache[key] = artifact
                    return artifact
            artifact = self._combine(position, max_ply, move, children, "all_target_moves_exhausted")
            self._cache[key] = artifact
            return artifact

        for legal_move in legal_moves:
            child = self._child(position, legal_move, max_ply)
            children.append(child)
            if self.time_limit_reached:
                artifact = self._unknown(position, max_ply, move, "time_limit")
                self._cache[key] = artifact
                return artifact
            if status_refutes_target(child.status):
                artifact = self._combine(position, max_ply, move, children, "opponent_refutation")
                self._cache[key] = artifact
                return artifact
            if child.status is ProofStatus.UNKNOWN:
                artifact = self._combine(position, max_ply, move, children, "opponent_line_unknown")
                self._cache[key] = artifact
                return artifact

        artifact = self._combine(position, max_ply, move, children, "all_opponent_replies_proven")
        self._cache[key] = artifact
        return artifact

    def _child(self, position: Position, move: Move, max_ply: int) -> ProofArtifact:
        next_position = position.make_move(move)
        return self._search(next_position, max_ply - 1, move=move.to_uci())

    def _order_moves(self, position: Position, max_ply: int) -> list[Move]:
        legal_moves = position.legal_moves()
        if self.move_orderer is None:
            ordered = _capture_first_order(position, legal_moves)
        else:
            ordered = _order_moves_with_optional_time_limit(
                self.move_orderer,
                position,
                legal_moves,
                self._remaining_time_ms(),
            )
        if self.resolver is None or max_ply <= 0:
            return ordered

        node_kind = node_kind_for(position, self.target)
        ranked = []
        for index, move in enumerate(ordered):
            child = position.make_move(move)
            resolved = self._lookup_resolved(child, max_ply - 1)
            ranked.append((_resolved_sort_key(node_kind, resolved, index), move))
        return [move for _key, move in sorted(ranked, key=lambda item: item[0])]

    def _resolve(
        self,
        position: Position,
        max_ply: int,
        move: str | None,
    ) -> ProofArtifact | None:
        resolved = self._lookup_resolved(position, max_ply)
        if resolved is None:
            return None
        return _with_move(resolved, move)

    def _lookup_resolved(self, position: Position, max_ply: int) -> ProofArtifact | None:
        if self.resolver is None:
            return None
        key = _state_cache_key(position, self.target, max_ply)
        if key in self._resolved_cache:
            self.resolved_cache_hits += 1
            return self._resolved_cache[key]
        resolved = self.resolver.resolve_for_search(
            position.to_fen(),
            self.target,
            max_ply,
            _history_signature(position),
        )
        if resolved is None:
            self.resolved_store_misses += 1
            self._resolved_cache[key] = None
            return None
        if resolved.status is ProofStatus.UNKNOWN:
            self.resolved_store_misses += 1
            self._resolved_cache[key] = None
            return None
        if (
            resolved.target is not self.target
            or resolved.fen != position.to_fen()
            or resolved.history_signature != _history_signature(position)
        ):
            raise ValueError("resolved proof artifact does not match requested state")
        self.resolved_store_hits += 1
        assert self._verifier is not None
        verification = self._verifier.verify(resolved)
        if not verification.valid:
            raise ValueError(
                "resolved proof artifact failed verification: "
                + "; ".join(verification.errors)
            )
        self._resolved_cache[key] = resolved
        return resolved

    def _combine(
        self,
        position: Position,
        max_ply: int,
        move: str | None,
        children: list[ProofArtifact],
        reason: str,
    ) -> ProofArtifact:
        node_kind = node_kind_for(position, self.target)
        child_numbers = [
            ProofNumbers(child.proof, child.disproof, _proof_outcome_name(child.status))
            for child in children
        ]
        combined = combine_proof_numbers(node_kind, child_numbers)

        if combined.proof == 0:
            status = ProofStatus.PROVEN
        elif combined.disproof == 0:
            status = ProofStatus.DISPROVEN
        elif any(child.status is ProofStatus.UNKNOWN for child in children):
            status = ProofStatus.UNKNOWN
        else:
            status = ProofStatus.UNKNOWN

        return ProofArtifact(
            fen=position.to_fen(),
            target=self.target,
            max_ply=max_ply,
            node_kind=node_kind,
            status=status,
            proof=combined.proof,
            disproof=combined.disproof,
            move=move,
            reason=reason,
            history_signature=_history_signature(position),
            position_command=_position_command(position),
            children=tuple(children),
        )

    def _unknown(self, position: Position, max_ply: int, move: str | None, reason: str) -> ProofArtifact:
        return ProofArtifact(
            fen=position.to_fen(),
            target=self.target,
            max_ply=max_ply,
            node_kind=node_kind_for(position, self.target),
            status=ProofStatus.UNKNOWN,
            proof=1,
            disproof=1,
            move=move,
            reason=reason,
            history_signature=_history_signature(position),
            position_command=_position_command(position),
        )

    def _time_limit_reached(self) -> bool:
        if self.deadline is None:
            return False
        if perf_counter() < self.deadline:
            return False
        self.time_limit_reached = True
        return True

    def _remaining_time_ms(self) -> int | None:
        if self.deadline is None:
            return None
        return max(0, int((self.deadline - perf_counter()) * 1000))

    def _stop_requested(self) -> bool:
        return self.stop_event is not None and self.stop_event.is_set()


def _proof_outcome_name(status: ProofStatus):
    from .pns import ProofOutcome

    if status is ProofStatus.PROVEN:
        return ProofOutcome.PROVEN
    if status is ProofStatus.DISPROVEN:
        return ProofOutcome.DISPROVEN
    if status is ProofStatus.DRAW:
        return ProofOutcome.DRAW
    if status is ProofStatus.ILLEGAL:
        return ProofOutcome.ILLEGAL
    return ProofOutcome.UNKNOWN


def _with_move(artifact: ProofArtifact, move: str | None) -> ProofArtifact:
    if artifact.move == move:
        return artifact
    return ProofArtifact(
        fen=artifact.fen,
        target=artifact.target,
        max_ply=artifact.max_ply,
        node_kind=artifact.node_kind,
        status=artifact.status,
        proof=artifact.proof,
        disproof=artifact.disproof,
        move=move,
        reason=artifact.reason,
        history_signature=artifact.history_signature,
        position_command=artifact.position_command,
        children=artifact.children,
    )


def _resolved_sort_key(
    node_kind,
    resolved: ProofArtifact | None,
    index: int,
) -> tuple[int, int, int, int]:
    if resolved is None or resolved.status is ProofStatus.UNKNOWN:
        return (1, 1, -1, index)
    if node_kind.value == "or":
        if resolved.status is ProofStatus.PROVEN:
            priority = 0
        elif resolved.status in {ProofStatus.DISPROVEN, ProofStatus.DRAW}:
            priority = 2
        else:
            priority = 1
        return (priority, resolved.proof, -resolved.disproof, index)

    if resolved.status in {ProofStatus.DISPROVEN, ProofStatus.DRAW}:
        priority = 0
    elif resolved.status is ProofStatus.PROVEN:
        priority = 2
    else:
        priority = 1
    return (priority, resolved.disproof, -resolved.proof, index)


def _state_cache_key(position, target: ProofTarget, max_ply: int) -> tuple[str, ProofTarget, int, str]:
    history_signature = (
        position.history_signature() if hasattr(position, "history_signature") else ""
    )
    return (position.to_fen(), target, max_ply, history_signature)


def _history_signature(position) -> str:
    signature = getattr(position, "history_signature", None)
    return signature() if callable(signature) else ""


def _order_moves_with_optional_time_limit(
    orderer: MoveOrderer,
    position: Position,
    moves: Sequence[Move],
    time_limit_ms: int | None,
) -> list[Move]:
    timed_order = getattr(orderer, "order_moves_with_time_limit", None)
    if time_limit_ms is not None and callable(timed_order):
        return timed_order(position, moves, time_limit_ms)
    return orderer.order_moves(position, moves)


def _position_command(position) -> str:
    command = getattr(position, "to_uci_position", None)
    return command() if callable(command) else ""
