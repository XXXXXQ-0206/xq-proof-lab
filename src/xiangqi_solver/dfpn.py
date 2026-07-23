from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from time import perf_counter
from typing import Protocol

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
from .search import MoveOrderer, SearchResult, _capture_first_order, _proof_outcome_name
from .verifier import ProofVerifier


@dataclass(frozen=True, slots=True)
class DfpnLimits:
    proof_threshold: int = INF
    disproof_threshold: int = INF
    node_limit: int = 100_000
    time_limit_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class DfpnIteration:
    index: int
    proof_threshold: int
    disproof_threshold: int
    status: ProofStatus
    proof: int
    disproof: int
    reason: str
    nodes_searched: int
    node_limit_reached: bool
    threshold_reached: bool
    cache_hits: int = 0
    resolved_cache_hits: int = 0
    resolved_store_hits: int = 0
    resolved_store_misses: int = 0


@dataclass(frozen=True, slots=True)
class IterativeDfpnResult:
    result: SearchResult
    iterations: tuple[DfpnIteration, ...]

    @property
    def total_nodes_searched(self) -> int:
        return sum(iteration.nodes_searched for iteration in self.iterations)

    @property
    def total_cache_hits(self) -> int:
        return sum(iteration.cache_hits for iteration in self.iterations)

    @property
    def total_resolved_cache_hits(self) -> int:
        return sum(iteration.resolved_cache_hits for iteration in self.iterations)

    @property
    def total_resolved_store_hits(self) -> int:
        return sum(iteration.resolved_store_hits for iteration in self.iterations)

    @property
    def total_resolved_store_misses(self) -> int:
        return sum(iteration.resolved_store_misses for iteration in self.iterations)


class DfpnResolver(Protocol):
    def resolve_for_search(
        self,
        fen: str,
        target: ProofTarget,
        max_ply: int,
        history_signature: str = "",
    ) -> ProofArtifact | None:
        ...


class DfpnSearch:
    """A first threshold-controlled proof-number search.

    This is intentionally conservative: thresholds may stop expansion early and
    return UNKNOWN, but PROVEN/DISPROVEN artifacts must still be verifier-valid.
    """

    def __init__(
        self,
        target: str | ProofTarget,
        max_ply: int,
        limits: DfpnLimits | None = None,
        move_orderer: MoveOrderer | None = None,
        resolver: DfpnResolver | None = None,
    ) -> None:
        if max_ply < 0:
            raise ValueError("max_ply must be non-negative")
        self.target = ProofTarget.parse(target)
        self.max_ply = max_ply
        self.limits = limits or DfpnLimits()
        if self.limits.node_limit <= 0:
            raise ValueError("node_limit must be positive")
        if self.limits.time_limit_seconds is not None and self.limits.time_limit_seconds < 0:
            raise ValueError("time_limit_seconds must be non-negative")
        self.move_orderer = move_orderer
        self.nodes_searched = 0
        self.node_limit_reached = False
        self.time_limit_reached = False
        self.threshold_reached = False
        self.cache_hits = 0
        self.resolved_cache_hits = 0
        self.resolved_store_hits = 0
        self.resolved_store_misses = 0
        self._cache: dict[tuple[str, ProofTarget, int, str, int, int], ProofArtifact] = {}
        self._resolved_cache: dict[tuple[str, ProofTarget, int, str], ProofArtifact | None] = {}
        self.resolver = resolver
        self._verifier = ProofVerifier()
        self.deadline = (
            perf_counter() + self.limits.time_limit_seconds
            if self.limits.time_limit_seconds is not None
            else None
        )

    def search(self, position: Position) -> SearchResult:
        prepare = getattr(position, "for_search", None)
        if callable(prepare):
            position = prepare()
        artifact = self._search(
            position,
            self.max_ply,
            move=None,
            proof_threshold=self.limits.proof_threshold,
            disproof_threshold=self.limits.disproof_threshold,
        )
        return SearchResult(
            artifact=artifact,
            nodes_searched=self.nodes_searched,
            node_limit_reached=self.node_limit_reached,
            time_limit_reached=self.time_limit_reached,
            threshold_reached=self.threshold_reached,
            cache_hits=self.cache_hits,
            resolved_cache_hits=self.resolved_cache_hits,
            resolved_store_hits=self.resolved_store_hits,
            resolved_store_misses=self.resolved_store_misses,
        )

    def _search(
        self,
        position: Position,
        max_ply: int,
        move: str | None,
        proof_threshold: int,
        disproof_threshold: int,
    ) -> ProofArtifact:
        key = _search_cache_key(
            position,
            self.target,
            max_ply,
            proof_threshold,
            disproof_threshold,
        )
        cached = self._cache.get(key)
        if cached is not None:
            self.cache_hits += 1
            return _with_move(cached, move)

        legal_moves = position.legal_moves()
        result, reason = game_result_and_reason(position, legal_moves)
        if result is not None:
            artifact = ProofArtifact.terminal(position, self.target, max_ply, move, result, reason)
            self._cache[key] = artifact
            return artifact

        resolved = self._resolve(position, max_ply, move)
        if resolved is not None:
            self._cache[key] = resolved
            return resolved

        if max_ply == 0:
            artifact = self._unknown(position, max_ply, move, "ply_bound")
            self._cache[key] = artifact
            return artifact

        if self._time_limit_reached():
            return self._unknown(position, max_ply, move, "time_limit")

        if self.nodes_searched >= self.limits.node_limit:
            self.node_limit_reached = True
            return self._unknown(position, max_ply, move, "node_limit")

        self.nodes_searched += 1
        if self.nodes_searched >= self.limits.node_limit:
            self.node_limit_reached = True
            artifact = self._unknown(position, max_ply, move, "node_limit")
            self._cache[key] = artifact
            return artifact

        children: list[ProofArtifact] = []
        node_kind = node_kind_for(position, self.target)
        ordered_moves = self._order_moves(position, max_ply, legal_moves)
        if self._time_limit_reached():
            return self._unknown(position, max_ply, move, "time_limit")
        for index, legal_move in enumerate(ordered_moves):
            child_proof_threshold, child_disproof_threshold = _child_thresholds(
                node_kind,
                proof_threshold,
                disproof_threshold,
                children,
                unexpanded_siblings=len(ordered_moves) - index - 1,
            )
            child = self._search(
                position.make_move(legal_move),
                max_ply - 1,
                move=legal_move.to_uci(),
                proof_threshold=child_proof_threshold,
                disproof_threshold=child_disproof_threshold,
            )
            children.append(child)
            if self.time_limit_reached:
                artifact = self._unknown(position, max_ply, move, "time_limit")
                self._cache[key] = artifact
                return artifact
            partial = self._combine(
                position,
                max_ply,
                move,
                children,
                reason="dfpn_partial",
                all_moves_covered=False,
            )

            if node_kind.value == "or" and child.status is ProofStatus.PROVEN:
                artifact = self._combine(
                    position,
                    max_ply,
                    move,
                    children,
                    reason="dfpn_or_proven",
                    all_moves_covered=False,
                )
                self._cache[key] = artifact
                return artifact
            if node_kind.value == "and" and status_refutes_target(child.status):
                artifact = self._combine(
                    position,
                    max_ply,
                    move,
                    children,
                    reason="dfpn_and_disproven",
                    all_moves_covered=False,
                )
                self._cache[key] = artifact
                return artifact
            finite_proof_threshold_reached = (
                proof_threshold < INF and partial.proof >= proof_threshold
            )
            finite_disproof_threshold_reached = (
                disproof_threshold < INF and partial.disproof >= disproof_threshold
            )
            if finite_proof_threshold_reached or finite_disproof_threshold_reached:
                self.threshold_reached = True
                artifact = self._unknown_with_children(
                    position,
                    max_ply,
                    move,
                    children,
                    "threshold",
                    partial.proof,
                    partial.disproof,
                )
                self._cache[key] = artifact
                return artifact

        artifact = self._combine(
            position,
            max_ply,
            move,
            children,
            reason="dfpn_complete",
            all_moves_covered=True,
        )
        self._cache[key] = artifact
        return artifact

    def _order_moves(
        self,
        position: Position,
        max_ply: int,
        legal_moves: list[Move] | None = None,
    ) -> list[Move]:
        if legal_moves is None:
            legal_moves = position.legal_moves()
        if self.move_orderer is None:
            ordered = _capture_first_order(position, legal_moves)
        else:
            timed_order = getattr(self.move_orderer, "order_moves_with_time_limit", None)
            if self.deadline is not None and callable(timed_order):
                ordered = timed_order(position, legal_moves, self._remaining_time_ms() or 0)
            else:
                ordered = self.move_orderer.order_moves(position, legal_moves)
        if self.resolver is None or max_ply <= 0:
            return ordered

        node_kind = node_kind_for(position, self.target)
        ranked = []
        for index, move in enumerate(ordered):
            child = position.make_move(move)
            resolved = self._lookup_resolved(child, max_ply - 1)
            ranked.append((_resolved_sort_key(node_kind, resolved, index), move))
        return [move for _key, move in sorted(ranked, key=lambda item: item[0])]

    def _time_limit_reached(self) -> bool:
        if self.deadline is None or perf_counter() < self.deadline:
            return False
        self.time_limit_reached = True
        return True

    def _remaining_time_ms(self) -> int:
        if self.deadline is None:
            return 0
        return max(0, int((self.deadline - perf_counter()) * 1000))

    def _resolve(self, position: Position, max_ply: int, move: str | None) -> ProofArtifact | None:
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
        self.resolved_store_hits += 1
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
        all_moves_covered: bool | None = None,
    ) -> ProofArtifact:
        node_kind = node_kind_for(position, self.target)
        if not children:
            return self._unknown(position, max_ply, move, "no_children")
        combined = combine_proof_numbers(
            node_kind,
            [ProofNumbers(child.proof, child.disproof, _proof_outcome_name(child.status)) for child in children],
        )

        covered = (
            _all_legal_moves_covered(position, children)
            if all_moves_covered is None
            else all_moves_covered
        )
        if node_kind.value == "or":
            if any(child.status is ProofStatus.PROVEN for child in children):
                status = ProofStatus.PROVEN
            elif covered and all(
                status_refutes_target(child.status) for child in children
            ):
                status = ProofStatus.DISPROVEN
            else:
                status = ProofStatus.UNKNOWN
        else:
            if any(status_refutes_target(child.status) for child in children):
                status = ProofStatus.DISPROVEN
            elif covered and all(
                child.status is ProofStatus.PROVEN for child in children
            ):
                status = ProofStatus.PROVEN
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

    def _unknown(
        self,
        position: Position,
        max_ply: int,
        move: str | None,
        reason: str,
    ) -> ProofArtifact:
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

    def _unknown_with_children(
        self,
        position: Position,
        max_ply: int,
        move: str | None,
        children: list[ProofArtifact],
        reason: str,
        proof: int,
        disproof: int,
    ) -> ProofArtifact:
        return ProofArtifact(
            fen=position.to_fen(),
            target=self.target,
            max_ply=max_ply,
            node_kind=node_kind_for(position, self.target),
            status=ProofStatus.UNKNOWN,
            proof=proof,
            disproof=disproof,
            move=move,
            reason=reason,
            history_signature=_history_signature(position),
            position_command=_position_command(position),
            children=tuple(children),
        )


def run_iterative_dfpn(
    position: Position,
    target: str | ProofTarget,
    max_ply: int,
    *,
    initial_limits: DfpnLimits | None = None,
    max_iterations: int = 4,
    threshold_growth: int = 2,
    move_orderer: MoveOrderer | None = None,
    resolver: DfpnResolver | None = None,
) -> IterativeDfpnResult:
    if max_iterations <= 0:
        raise ValueError("max_iterations must be positive")
    if threshold_growth <= 1:
        raise ValueError("threshold_growth must be greater than one")

    limits = initial_limits or DfpnLimits()
    proof_threshold = limits.proof_threshold
    disproof_threshold = limits.disproof_threshold
    iterations: list[DfpnIteration] = []
    latest: SearchResult | None = None

    for index in range(1, max_iterations + 1):
        searcher = DfpnSearch(
            target,
            max_ply,
            limits=DfpnLimits(
                proof_threshold=proof_threshold,
                disproof_threshold=disproof_threshold,
                node_limit=limits.node_limit,
                time_limit_seconds=limits.time_limit_seconds,
            ),
            move_orderer=move_orderer,
            resolver=resolver,
        )
        latest = searcher.search(position)
        artifact = latest.artifact
        iterations.append(
            DfpnIteration(
                index=index,
                proof_threshold=proof_threshold,
                disproof_threshold=disproof_threshold,
                status=artifact.status,
                proof=artifact.proof,
                disproof=artifact.disproof,
                reason=artifact.reason,
                nodes_searched=latest.nodes_searched,
                node_limit_reached=latest.node_limit_reached,
                threshold_reached=latest.threshold_reached,
                cache_hits=latest.cache_hits,
                resolved_cache_hits=latest.resolved_cache_hits,
                resolved_store_hits=latest.resolved_store_hits,
                resolved_store_misses=latest.resolved_store_misses,
            )
        )

        if artifact.status is not ProofStatus.UNKNOWN:
            break
        if latest.node_limit_reached or latest.time_limit_reached or not latest.threshold_reached:
            break

        next_proof, next_disproof = _next_iteration_thresholds(
            proof_threshold,
            disproof_threshold,
            artifact.proof,
            artifact.disproof,
            threshold_growth,
        )
        if next_proof == proof_threshold and next_disproof == disproof_threshold:
            break
        proof_threshold = next_proof
        disproof_threshold = next_disproof

    assert latest is not None
    return IterativeDfpnResult(latest, tuple(iterations))


def _all_legal_moves_covered(position: Position, children: list[ProofArtifact]) -> bool:
    return {move.to_uci() for move in position.legal_moves()} == {child.move for child in children}


def _child_thresholds(
    node_kind,
    proof_threshold: int,
    disproof_threshold: int,
    searched_siblings: list[ProofArtifact],
    unexpanded_siblings: int,
) -> tuple[int, int]:
    proof = proof_threshold
    disproof = disproof_threshold
    unknown_siblings = max(0, unexpanded_siblings)

    if node_kind.value == "or":
        if proof_threshold < INF:
            sibling_proofs = [child.proof for child in searched_siblings] + [1] * unknown_siblings
            second_best = min(sibling_proofs) if sibling_proofs else INF
            proof = min(proof_threshold, _saturating_add(second_best, 1))
        if disproof_threshold < INF:
            sibling_disproof = _saturating_sum(
                [child.disproof for child in searched_siblings],
                unknown_siblings,
            )
            disproof = max(1, disproof_threshold - sibling_disproof)
        return proof, disproof

    if proof_threshold < INF:
        sibling_proof = _saturating_sum(
            [child.proof for child in searched_siblings],
            unknown_siblings,
        )
        proof = max(1, proof_threshold - sibling_proof)
    if disproof_threshold < INF:
        sibling_disproofs = [child.disproof for child in searched_siblings] + [1] * unknown_siblings
        second_best = min(sibling_disproofs) if sibling_disproofs else INF
        disproof = min(disproof_threshold, _saturating_add(second_best, 1))
    return proof, disproof


def _saturating_add(value: int, delta: int) -> int:
    return min(INF, value + delta)


def _saturating_sum(values: list[int], unknown_count: int) -> int:
    total = min(INF, unknown_count)
    for value in values:
        total = min(INF, total + value)
    return total


def _grow_threshold(value: int, growth: int) -> int:
    if value >= INF:
        return INF
    return min(INF, max(value + 1, value * growth))


def _next_iteration_thresholds(
    proof_threshold: int,
    disproof_threshold: int,
    proof: int,
    disproof: int,
    growth: int,
) -> tuple[int, int]:
    next_proof = proof_threshold
    next_disproof = disproof_threshold
    if proof >= proof_threshold:
        next_proof = _advance_reached_threshold(proof_threshold, proof, growth)
    if disproof >= disproof_threshold:
        next_disproof = _advance_reached_threshold(disproof_threshold, disproof, growth)
    return next_proof, next_disproof


def _advance_reached_threshold(threshold: int, value: int, growth: int) -> int:
    if threshold >= INF:
        return INF
    return min(INF, max(_grow_threshold(threshold, growth), value + 1))


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


def _resolved_sort_key(node_kind, resolved: ProofArtifact | None, index: int) -> tuple[int, int, int, int]:
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


def _search_cache_key(
    position,
    target: ProofTarget,
    max_ply: int,
    proof_threshold: int,
    disproof_threshold: int,
) -> tuple[str, ProofTarget, int, str, int, int]:
    return (
        *_state_cache_key(position, target, max_ply),
        proof_threshold,
        disproof_threshold,
    )


def _history_signature(position) -> str:
    signature = getattr(position, "history_signature", None)
    return signature() if callable(signature) else ""


def _position_command(position) -> str:
    command = getattr(position, "to_uci_position", None)
    return command() if callable(command) else ""
