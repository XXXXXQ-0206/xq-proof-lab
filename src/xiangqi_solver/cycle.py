from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from time import perf_counter
from typing import Any

from xiangqi_core import GameState, Position

from .frontier import collect_frontier
from .merge import merge_resolved_frontier
from .pns import INF
from .proof import ProofArtifact
from .dfpn import DfpnLimits, DfpnSearch, run_iterative_dfpn
from .search import BoundedProofSearch
from .store import ProofStore
from .verifier import ProofVerifier


class ProofCycleError(RuntimeError):
    def __init__(self, errors: tuple[str, ...]) -> None:
        super().__init__("proof cycle produced an invalid proof artifact")
        self.errors = errors


@dataclass(frozen=True, slots=True)
class _CycleSearchRun:
    result: Any
    iterations: tuple[dict[str, Any], ...] = ()
    total_nodes_searched: int = 0
    threshold_reached: bool = False
    total_cache_hits: int = 0
    total_resolved_cache_hits: int = 0
    total_resolved_store_hits: int = 0
    total_resolved_store_misses: int = 0


def run_proof_cycle(
    root_state,
    *,
    target: str,
    store_path: str | Path,
    artifact_path: str | Path,
    initial_ply: int = 0,
    extra_ply: int = 1,
    cycles: int = 1,
    frontier_limit: int = 10,
    frontier_reasons: tuple[str, ...] | list[str] = (),
    frontier_max_attempts: int | None = None,
    frontier_min_remaining_ply: int | None = None,
    frontier_max_remaining_ply: int | None = None,
    frontier_max_proof: int | None = None,
    frontier_max_disproof: int | None = None,
    node_limit: int = 100_000,
    resume_artifact: bool = False,
    searcher: str = "bounded",
    reuse_store: bool = False,
    proof_threshold: int = INF,
    disproof_threshold: int = INF,
    dfpn_iterative: bool = False,
    dfpn_iterations: int = 4,
    threshold_growth: int = 2,
    move_orderer: Any | None = None,
    time_limit_seconds: float | None = None,
    node_budget: int | None = None,
    reset_running_max_age_seconds: float | None = None,
) -> dict[str, Any]:
    if node_budget is not None and node_budget < 0:
        raise ProofCycleError(("node_budget must be non-negative",))
    if reset_running_max_age_seconds is not None and reset_running_max_age_seconds < 0:
        raise ProofCycleError(("reset_running_max_age_seconds must be non-negative",))
    started = perf_counter()
    store = ProofStore(store_path)
    verifier = ProofVerifier()
    time_limit_reached = False
    node_budget_reached = False
    running_frontier_reset = 0

    if resume_artifact:
        root_artifact = _load_artifact(Path(artifact_path))
        initial_nodes_searched = 0
        initial_total_nodes_searched = 0
        initial_threshold_reached = False
        initial_dfpn_iterations = []
    else:
        if root_state is None:
            raise ProofCycleError(("root state is required unless resume_artifact is enabled",))
        root_run = _search(
            root_state,
            target,
            initial_ply,
            node_limit,
            searcher,
            store,
            reuse_store,
            proof_threshold,
            disproof_threshold,
            dfpn_iterative,
            dfpn_iterations,
            threshold_growth,
            move_orderer,
            _remaining_time_limit_seconds(time_limit_seconds, started),
        )
        root_result = root_run.result
        root_artifact = root_result.artifact
        initial_nodes_searched = root_result.nodes_searched
        initial_total_nodes_searched = root_run.total_nodes_searched
        initial_threshold_reached = root_run.threshold_reached
        initial_dfpn_iterations = list(root_run.iterations)
        time_limit_reached = root_result.time_limit_reached
    nodes_consumed = initial_total_nodes_searched
    _verify_or_raise(verifier, root_artifact)
    store.save(root_artifact, node_limit)
    store.enqueue_frontier(collect_frontier(root_artifact))

    processed = []
    for _ in range(cycles):
        running_frontier_reset += store.reset_running_frontier(
            reset_running_max_age_seconds
        )
        jobs = store.pending_frontier(
            frontier_limit,
            reasons=frontier_reasons,
            max_attempts=frontier_max_attempts,
            min_remaining_ply=frontier_min_remaining_ply,
            max_remaining_ply=frontier_max_remaining_ply,
            max_proof=frontier_max_proof,
            max_disproof=frontier_max_disproof,
        )
        if not jobs:
            break
        for job in jobs:
            if _time_limit_reached(time_limit_seconds, started):
                time_limit_reached = True
                break
            if _node_budget_reached(node_budget, nodes_consumed):
                node_budget_reached = True
                break
            store.mark_frontier_running(job.id)
            max_ply = max(0, job.remaining_ply + extra_ply)
            run = _search(
                _frontier_state(job),
                job.target,
                max_ply,
                node_limit,
                searcher,
                store,
                reuse_store,
                proof_threshold,
                disproof_threshold,
                dfpn_iterative,
                dfpn_iterations,
                threshold_growth,
                move_orderer,
                _remaining_time_limit_seconds(time_limit_seconds, started),
            )
            nodes_consumed += run.total_nodes_searched
            result = run.result
            time_limit_reached = time_limit_reached or result.time_limit_reached
            artifact = _with_frontier_history(
                result.artifact,
                job.history_signature,
                job.position_command,
            )
            _verify_or_raise(verifier, artifact)
            store.save(artifact, node_limit)
            frontier = collect_frontier(artifact)
            store.enqueue_frontier(frontier)
            split_frontier = artifact.status.value == "unknown" and bool(artifact.children)
            store.finish_frontier(
                job.id,
                artifact.status.value,
                proof=artifact.proof,
                disproof=artifact.disproof,
                reason=artifact.reason,
                split=split_frontier,
            )
            processed.append(
                {
                    "job_id": job.id,
                    "status": artifact.status.value,
                    "split_frontier": split_frontier,
                    "frontier_proof": job.proof,
                    "frontier_disproof": job.disproof,
                    "result_proof": artifact.proof,
                    "result_disproof": artifact.disproof,
                    "result_reason": artifact.reason,
                    "nodes_searched": result.nodes_searched,
                    "total_nodes_searched": run.total_nodes_searched,
                    "cache_hits": result.cache_hits,
                    "total_cache_hits": run.total_cache_hits,
                    "resolved_cache_hits": result.resolved_cache_hits,
                    "total_resolved_cache_hits": run.total_resolved_cache_hits,
                    "resolved_store_hits": result.resolved_store_hits,
                    "total_resolved_store_hits": run.total_resolved_store_hits,
                    "resolved_store_misses": result.resolved_store_misses,
                    "total_resolved_store_misses": run.total_resolved_store_misses,
                    "threshold_reached": run.threshold_reached,
                    "final_threshold_reached": result.threshold_reached,
                    "dfpn_iterations": list(run.iterations),
                    "max_ply": max_ply,
                    "nodes_consumed_after": nodes_consumed,
                }
            )
            if _node_budget_reached(node_budget, nodes_consumed):
                node_budget_reached = True

        root_artifact = merge_resolved_frontier(root_artifact, store)
        _verify_or_raise(verifier, root_artifact)
        store.save(root_artifact, node_limit)
        if root_artifact.status.value != "unknown" or time_limit_reached or node_budget_reached:
            break

    artifact_output = Path(artifact_path)
    artifact_output.parent.mkdir(parents=True, exist_ok=True)
    artifact_output.write_text(
        json.dumps(root_artifact.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    return {
        "status": root_artifact.status.value,
        "proof": root_artifact.proof,
        "disproof": root_artifact.disproof,
        "max_ply": root_artifact.max_ply,
        "initial_nodes_searched": initial_nodes_searched,
        "initial_total_nodes_searched": initial_total_nodes_searched,
        "initial_cache_hits": root_result.cache_hits if not resume_artifact else 0,
        "initial_total_cache_hits": root_run.total_cache_hits if not resume_artifact else 0,
        "initial_resolved_cache_hits": root_result.resolved_cache_hits if not resume_artifact else 0,
        "initial_total_resolved_cache_hits": (
            root_run.total_resolved_cache_hits if not resume_artifact else 0
        ),
        "initial_resolved_store_hits": root_result.resolved_store_hits if not resume_artifact else 0,
        "initial_total_resolved_store_hits": (
            root_run.total_resolved_store_hits if not resume_artifact else 0
        ),
        "initial_resolved_store_misses": (
            root_result.resolved_store_misses if not resume_artifact else 0
        ),
        "initial_total_resolved_store_misses": (
            root_run.total_resolved_store_misses if not resume_artifact else 0
        ),
        "initial_threshold_reached": initial_threshold_reached,
        "initial_dfpn_iterations": initial_dfpn_iterations,
        "resumed": resume_artifact,
        "searcher": searcher,
        "reuse_store": reuse_store,
        "dfpn_iterative": dfpn_iterative,
        "time_limit_reached": time_limit_reached,
        "node_budget": node_budget,
        "node_budget_reached": node_budget_reached,
        "nodes_consumed": nodes_consumed,
        "reset_running_max_age_seconds": reset_running_max_age_seconds,
        "running_frontier_reset": running_frontier_reset,
        "frontier_filters": {
            "reasons": list(frontier_reasons),
            "max_attempts": frontier_max_attempts,
            "min_remaining_ply": frontier_min_remaining_ply,
            "max_remaining_ply": frontier_max_remaining_ply,
            "max_proof": frontier_max_proof,
            "max_disproof": frontier_max_disproof,
        },
        "processed": processed,
        "database": store.database_summary(),
        "frontier_metrics": store.frontier_metrics(),
    }


def _verify_or_raise(verifier: ProofVerifier, artifact) -> None:
    verification = verifier.verify(artifact)
    if not verification.valid:
        raise ProofCycleError(verification.errors)


def _load_artifact(path: Path) -> ProofArtifact:
    try:
        return ProofArtifact.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:
        raise ProofCycleError((f"failed to load artifact {path}: {exc}",)) from exc


def _frontier_state(job):
    if job.position_command:
        return GameState.from_uci_position(job.position_command)
    return Position.from_fen(job.fen)


def _search(
    state,
    target,
    max_ply: int,
    node_limit: int,
    searcher: str,
    store: ProofStore,
    reuse_store: bool,
    proof_threshold: int,
    disproof_threshold: int,
    dfpn_iterative: bool,
    dfpn_iterations: int,
    threshold_growth: int,
    move_orderer: Any | None,
    time_limit_seconds: float | None,
):
    if searcher == "bounded":
        resolver = store if reuse_store else None
        result = BoundedProofSearch(
            target,
            max_ply,
            node_limit,
            move_orderer=move_orderer,
            time_limit_seconds=time_limit_seconds,
            resolver=resolver,
        ).search(state)
        return _CycleSearchRun(
            result,
            total_nodes_searched=result.nodes_searched,
            total_cache_hits=result.cache_hits,
            total_resolved_cache_hits=result.resolved_cache_hits,
            total_resolved_store_hits=result.resolved_store_hits,
            total_resolved_store_misses=result.resolved_store_misses,
        )
    if searcher == "dfpn":
        resolver = store if reuse_store else None
        limits = DfpnLimits(
            proof_threshold=proof_threshold,
            disproof_threshold=disproof_threshold,
            node_limit=node_limit,
            time_limit_seconds=time_limit_seconds,
        )
        if dfpn_iterative:
            iterative = run_iterative_dfpn(
                state,
                target,
                max_ply,
                initial_limits=limits,
                max_iterations=dfpn_iterations,
                threshold_growth=threshold_growth,
                move_orderer=move_orderer,
                resolver=resolver,
            )
            iterations = tuple(_dfpn_iteration_dict(iteration) for iteration in iterative.iterations)
            return _CycleSearchRun(
                iterative.result,
                iterations=iterations,
                total_nodes_searched=iterative.total_nodes_searched,
                threshold_reached=any(
                    iteration["threshold_reached"] for iteration in iterations
                ),
                total_cache_hits=iterative.total_cache_hits,
                total_resolved_cache_hits=iterative.total_resolved_cache_hits,
                total_resolved_store_hits=iterative.total_resolved_store_hits,
                total_resolved_store_misses=iterative.total_resolved_store_misses,
            )
        result = DfpnSearch(
            target,
            max_ply,
            limits=limits,
            move_orderer=move_orderer,
            resolver=resolver,
        ).search(state)
        return _CycleSearchRun(
            result,
            total_nodes_searched=result.nodes_searched,
            threshold_reached=result.threshold_reached,
            total_cache_hits=result.cache_hits,
            total_resolved_cache_hits=result.resolved_cache_hits,
            total_resolved_store_hits=result.resolved_store_hits,
            total_resolved_store_misses=result.resolved_store_misses,
        )
    raise ProofCycleError((f"unsupported searcher: {searcher}",))


def _dfpn_iteration_dict(iteration) -> dict[str, Any]:
    return {
        "index": iteration.index,
        "proof_threshold": iteration.proof_threshold,
        "disproof_threshold": iteration.disproof_threshold,
        "status": iteration.status.value,
        "proof": iteration.proof,
        "disproof": iteration.disproof,
        "reason": iteration.reason,
        "nodes_searched": iteration.nodes_searched,
        "node_limit_reached": iteration.node_limit_reached,
        "threshold_reached": iteration.threshold_reached,
        "cache_hits": iteration.cache_hits,
        "resolved_cache_hits": iteration.resolved_cache_hits,
        "resolved_store_hits": iteration.resolved_store_hits,
        "resolved_store_misses": iteration.resolved_store_misses,
    }


def _with_frontier_history(artifact, history_signature: str, position_command: str):
    if (
        (not history_signature or artifact.history_signature == history_signature)
        and (not position_command or artifact.position_command == position_command)
    ):
        return artifact
    return replace(
        artifact,
        history_signature=artifact.history_signature or history_signature,
        position_command=artifact.position_command or position_command,
    )


def _time_limit_reached(limit_seconds: float | None, started: float) -> bool:
    return limit_seconds is not None and perf_counter() - started >= limit_seconds


def _remaining_time_limit_seconds(
    limit_seconds: float | None,
    started: float,
) -> float | None:
    if limit_seconds is None:
        return None
    return max(0.0, limit_seconds - (perf_counter() - started))


def _node_budget_reached(node_budget: int | None, nodes_consumed: int) -> bool:
    return node_budget is not None and nodes_consumed >= node_budget
