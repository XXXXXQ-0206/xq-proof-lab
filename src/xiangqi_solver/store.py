from __future__ import annotations

import json
import hashlib
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from xiangqi_core import Position, Zobrist

from .frontier import FrontierNode
from .proof import ProofArtifact, ProofTarget
from .verifier import ProofVerifier


SCHEMA = """
CREATE TABLE IF NOT EXISTS proof_results (
    position_key TEXT NOT NULL,
    fen TEXT NOT NULL,
    target TEXT NOT NULL,
    max_ply INTEGER NOT NULL,
    node_limit INTEGER NOT NULL,
    status TEXT NOT NULL,
    proof INTEGER NOT NULL,
    disproof INTEGER NOT NULL,
    artifact_json TEXT NOT NULL,
    artifact_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (position_key, target, max_ply)
);

CREATE TABLE IF NOT EXISTS frontier_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_key TEXT NOT NULL,
    fen TEXT NOT NULL,
    target TEXT NOT NULL,
    remaining_ply INTEGER NOT NULL,
    reason TEXT NOT NULL,
    history_signature TEXT NOT NULL DEFAULT '',
    position_command TEXT NOT NULL DEFAULT '',
    proof INTEGER NOT NULL DEFAULT 1,
    disproof INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_result_status TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(position_key, target, remaining_ply)
);
"""


@dataclass(frozen=True, slots=True)
class StoredProof:
    position_key: str
    fen: str
    target: ProofTarget
    max_ply: int
    node_limit: int
    artifact: ProofArtifact


@dataclass(frozen=True, slots=True)
class FrontierJob:
    id: int
    position_key: str
    fen: str
    target: ProofTarget
    remaining_ply: int
    reason: str
    history_signature: str
    position_command: str
    proof: int
    disproof: int
    status: str
    attempts: int
    last_result_status: str | None


class ProofStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._zobrist = Zobrist()
        self._verifier = ProofVerifier()
        self._init_schema()

    def save(self, artifact: ProofArtifact, node_limit: int, verify: bool = True) -> str:
        if verify:
            verification = self._verifier.verify(artifact)
            if not verification.valid:
                raise ValueError(
                    "proof artifact failed verification: " + "; ".join(verification.errors)
                )
        position_key = self.artifact_key(artifact)
        now = datetime.now(UTC).isoformat()
        payload = json.dumps(artifact.to_dict(), ensure_ascii=False, sort_keys=True)
        payload_hash = _artifact_hash(payload)
        with closing(sqlite3.connect(self.path)) as con:
            con.execute(
                """
                INSERT INTO proof_results (
                    position_key, fen, target, max_ply, node_limit, status,
                    proof, disproof, artifact_json, artifact_hash, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(position_key, target, max_ply) DO UPDATE SET
                    fen=excluded.fen,
                    node_limit=excluded.node_limit,
                    status=excluded.status,
                    proof=excluded.proof,
                    disproof=excluded.disproof,
                    artifact_json=excluded.artifact_json,
                    artifact_hash=excluded.artifact_hash,
                    updated_at=excluded.updated_at
                """,
                (
                    position_key,
                    artifact.fen,
                    artifact.target.value,
                    artifact.max_ply,
                    node_limit,
                    artifact.status.value,
                    artifact.proof,
                    artifact.disproof,
                    payload,
                    payload_hash,
                    now,
                    now,
                ),
            )
            con.commit()
        return position_key

    def load(self, fen: str, target: str | ProofTarget, max_ply: int) -> StoredProof | None:
        return self.load_with_history(fen, "", target, max_ply)

    def load_with_history(
        self,
        fen: str,
        history_signature: str,
        target: str | ProofTarget,
        max_ply: int,
    ) -> StoredProof | None:
        parsed_target = ProofTarget.parse(target)
        position_key = self.state_key(fen, history_signature)
        with closing(sqlite3.connect(self.path)) as con:
            row = con.execute(
                """
                SELECT position_key, fen, target, max_ply, node_limit, artifact_json, artifact_hash
                FROM proof_results
                WHERE position_key = ? AND target = ? AND max_ply = ?
                """,
                (position_key, parsed_target.value, max_ply),
            ).fetchone()
        if row is None:
            return None
        artifact = _artifact_from_payload(row[5], row[6])
        return StoredProof(row[0], row[1], ProofTarget.parse(row[2]), row[3], row[4], artifact)

    def iter_proofs(self, limit: int | None = None) -> list[StoredProof]:
        sql = """
            SELECT position_key, fen, target, max_ply, node_limit, artifact_json, artifact_hash
            FROM proof_results
            ORDER BY updated_at DESC, position_key
        """
        params: tuple[int, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        with closing(sqlite3.connect(self.path)) as con:
            rows = con.execute(sql, params).fetchall()
        return [
            StoredProof(
                row[0],
                row[1],
                ProofTarget.parse(row[2]),
                row[3],
                row[4],
                _artifact_from_payload(row[5], row[6]),
            )
            for row in rows
        ]

    def resolve(
        self,
        fen: str,
        target: ProofTarget,
        min_ply: int,
        history_signature: str = "",
    ) -> ProofArtifact | None:
        position_key = self.state_key(fen, history_signature)
        with closing(sqlite3.connect(self.path)) as con:
            row = con.execute(
                """
                SELECT artifact_json, artifact_hash
                FROM proof_results
                WHERE position_key = ?
                  AND target = ?
                  AND max_ply >= ?
                  AND status != 'unknown'
                ORDER BY max_ply ASC
                LIMIT 1
                """,
                (position_key, target.value, min_ply),
            ).fetchone()
        if row is None:
            return None
        return _artifact_from_payload(row[0], row[1])

    def resolve_for_merge(
        self,
        fen: str,
        target: ProofTarget,
        min_ply: int,
        history_signature: str = "",
    ) -> ProofArtifact | None:
        resolved = self.resolve(fen, target, min_ply, history_signature)
        if resolved is not None:
            return resolved

        position_key = self.state_key(fen, history_signature)
        with closing(sqlite3.connect(self.path)) as con:
            rows = con.execute(
                """
                SELECT artifact_json, artifact_hash
                FROM proof_results
                WHERE position_key = ?
                  AND target = ?
                  AND max_ply >= ?
                  AND status = 'unknown'
                ORDER BY max_ply ASC
                """,
                (position_key, target.value, min_ply),
            ).fetchall()
        for row in rows:
            artifact = _artifact_from_payload(row[0], row[1])
            if artifact.children:
                return artifact
        return None

    def resolve_for_search(
        self,
        fen: str,
        target: ProofTarget,
        max_ply: int,
        history_signature: str = "",
    ) -> ProofArtifact | None:
        position_key = self.state_key(fen, history_signature)
        with closing(sqlite3.connect(self.path)) as con:
            exact = con.execute(
                """
                SELECT artifact_json, artifact_hash
                FROM proof_results
                WHERE position_key = ?
                  AND target = ?
                  AND max_ply = ?
                  AND status != 'unknown'
                LIMIT 1
                """,
                (position_key, target.value, max_ply),
            ).fetchone()
            if exact is not None:
                return _artifact_from_payload(exact[0], exact[1])

            proven = con.execute(
                """
                SELECT artifact_json, artifact_hash
                FROM proof_results
                WHERE position_key = ?
                  AND target = ?
                  AND max_ply <= ?
                  AND status = 'proven'
                ORDER BY max_ply ASC
                LIMIT 1
                """,
                (position_key, target.value, max_ply),
            ).fetchone()
            if proven is not None:
                return _artifact_from_payload(proven[0], proven[1])

            disproven = con.execute(
                """
                SELECT artifact_json, artifact_hash
                FROM proof_results
                WHERE position_key = ?
                  AND target = ?
                  AND max_ply >= ?
                  AND status IN ('disproven', 'draw')
                ORDER BY max_ply ASC
                LIMIT 1
                """,
                (position_key, target.value, max_ply),
            ).fetchone()
        if disproven is None:
            return None
        return _artifact_from_payload(disproven[0], disproven[1])

    def resolve_proven(
        self,
        fen: str,
        target: str | ProofTarget,
        max_ply: int | None = None,
        history_signature: str = "",
    ) -> ProofArtifact | None:
        parsed_target = ProofTarget.parse(target)
        position_key = self.state_key(fen, history_signature)
        where = [
            "position_key = ?",
            "target = ?",
            "status = 'proven'",
        ]
        params: list[str | int] = [position_key, parsed_target.value]
        if max_ply is not None:
            where.append("max_ply <= ?")
            params.append(max_ply)
        with closing(sqlite3.connect(self.path)) as con:
            row = con.execute(
                f"""
                SELECT artifact_json, artifact_hash
                FROM proof_results
                WHERE {" AND ".join(where)}
                ORDER BY max_ply ASC
                LIMIT 1
                """,
                tuple(params),
            ).fetchone()
        if row is None:
            return None
        return _artifact_from_payload(row[0], row[1])

    def enqueue_frontier(self, nodes: list[FrontierNode] | tuple[FrontierNode, ...]) -> int:
        now = datetime.now(UTC).isoformat()
        inserted_or_updated = 0
        with closing(sqlite3.connect(self.path)) as con:
            for node in nodes:
                position_key = self.state_key(node.fen, node.history_signature)
                con.execute(
                    """
                    INSERT INTO frontier_jobs (
                        position_key, fen, target, remaining_ply, reason,
                        history_signature, position_command, proof, disproof,
                        status, attempts, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?)
                    ON CONFLICT(position_key, target, remaining_ply) DO UPDATE SET
                        reason=excluded.reason,
                        history_signature=excluded.history_signature,
                        position_command=excluded.position_command,
                        proof=excluded.proof,
                        disproof=excluded.disproof,
                        status=CASE
                            WHEN frontier_jobs.status = 'done' THEN frontier_jobs.status
                            ELSE 'pending'
                        END,
                        updated_at=excluded.updated_at
                    """,
                    (
                        position_key,
                        node.fen,
                        node.target.value,
                        node.remaining_ply,
                        node.reason,
                        node.history_signature,
                        node.position_command,
                        node.proof,
                        node.disproof,
                        now,
                        now,
                    ),
                )
                inserted_or_updated += 1
            con.commit()
        return inserted_or_updated

    def pending_frontier(
        self,
        limit: int = 10,
        *,
        reasons: tuple[str, ...] | list[str] = (),
        max_attempts: int | None = None,
        min_remaining_ply: int | None = None,
        max_remaining_ply: int | None = None,
        max_proof: int | None = None,
        max_disproof: int | None = None,
    ) -> list[FrontierJob]:
        where = ["status = 'pending'"]
        params: list[str | int] = []
        if reasons:
            where.append("reason IN (" + ", ".join("?" for _ in reasons) + ")")
            params.extend(reasons)
        if max_attempts is not None:
            where.append("attempts <= ?")
            params.append(max_attempts)
        if min_remaining_ply is not None:
            where.append("remaining_ply >= ?")
            params.append(min_remaining_ply)
        if max_remaining_ply is not None:
            where.append("remaining_ply <= ?")
            params.append(max_remaining_ply)
        if max_proof is not None:
            where.append("proof <= ?")
            params.append(max_proof)
        if max_disproof is not None:
            where.append("disproof <= ?")
            params.append(max_disproof)
        params.append(limit)
        with closing(sqlite3.connect(self.path)) as con:
            rows = con.execute(
                f"""
                SELECT id, position_key, fen, target, remaining_ply, reason,
                       history_signature, position_command, proof, disproof,
                       status, attempts, last_result_status
                FROM frontier_jobs
                WHERE {" AND ".join(where)}
                ORDER BY
                    attempts ASC,
                    CASE reason
                        WHEN 'ply_bound' THEN 0
                        WHEN 'threshold' THEN 1
                        WHEN 'node_limit' THEN 2
                        WHEN 'unknown_rule_state' THEN 3
                        ELSE 4
                    END ASC,
                    proof ASC,
                    disproof ASC,
                    remaining_ply ASC,
                    updated_at ASC,
                    id ASC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [self._frontier_job_from_row(row) for row in rows]

    def iter_frontier(
        self,
        limit: int | None = None,
        status: str | None = None,
    ) -> list[FrontierJob]:
        sql = """
            SELECT id, position_key, fen, target, remaining_ply, reason,
                   history_signature, position_command, proof, disproof,
                   status, attempts, last_result_status
            FROM frontier_jobs
        """
        params: list[str | int] = []
        if status is not None:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY id"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with closing(sqlite3.connect(self.path)) as con:
            rows = con.execute(sql, tuple(params)).fetchall()
        return [self._frontier_job_from_row(row) for row in rows]

    def mark_frontier_running(self, job_id: int) -> None:
        now = datetime.now(UTC).isoformat()
        with closing(sqlite3.connect(self.path)) as con:
            con.execute(
                """
                UPDATE frontier_jobs
                SET status = 'running', attempts = attempts + 1, updated_at = ?
                WHERE id = ?
                """,
                (now, job_id),
            )
            con.commit()

    def finish_frontier(
        self,
        job_id: int,
        result_status: str,
        *,
        proof: int | None = None,
        disproof: int | None = None,
        reason: str | None = None,
        split: bool = False,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        status = "done" if result_status != "unknown" or split else "pending"
        with closing(sqlite3.connect(self.path)) as con:
            con.execute(
                """
                UPDATE frontier_jobs
                SET status = ?,
                    last_result_status = ?,
                    proof = COALESCE(?, proof),
                    disproof = COALESCE(?, disproof),
                    reason = COALESCE(?, reason),
                    updated_at = ?
                WHERE id = ?
                """,
                (status, result_status, proof, disproof, reason, now, job_id),
            )
            con.commit()

    def reset_running_frontier(self, max_age_seconds: float | None = None) -> int:
        if max_age_seconds is not None and max_age_seconds < 0:
            raise ValueError("max_age_seconds must be non-negative")
        now = datetime.now(UTC).isoformat()
        where = "status = 'running'"
        params: list[str | float] = [now]
        if max_age_seconds is not None:
            cutoff = (datetime.now(UTC) - timedelta(seconds=max_age_seconds)).isoformat()
            where += " AND updated_at <= ?"
            params.append(cutoff)
        with closing(sqlite3.connect(self.path)) as con:
            cursor = con.execute(
                f"""
                UPDATE frontier_jobs
                SET status = 'pending', updated_at = ?
                WHERE {where}
                """,
                tuple(params),
            )
            con.commit()
            return cursor.rowcount

    def proof_summary(self) -> dict[str, int]:
        with closing(sqlite3.connect(self.path)) as con:
            rows = con.execute(
                """
                SELECT status, COUNT(*)
                FROM proof_results
                GROUP BY status
                ORDER BY status
                """
            ).fetchall()
        return {row[0]: row[1] for row in rows}

    def frontier_summary(self) -> dict[str, int]:
        with closing(sqlite3.connect(self.path)) as con:
            rows = con.execute(
                """
                SELECT status, COUNT(*)
                FROM frontier_jobs
                GROUP BY status
                ORDER BY status
                """
            ).fetchall()
        return {row[0]: row[1] for row in rows}

    def frontier_metrics(self, status: str | None = None) -> dict[str, list[dict[str, int | str]]]:
        where = ""
        params: tuple[str, ...] = ()
        if status is not None:
            where = "WHERE status = ?"
            params = (status,)
        with closing(sqlite3.connect(self.path)) as con:
            by_reason = con.execute(
                f"""
                SELECT status, reason, COUNT(*),
                       MIN(remaining_ply), MAX(remaining_ply),
                       MIN(attempts), MAX(attempts),
                       MIN(proof), MAX(proof),
                       MIN(disproof), MAX(disproof)
                FROM frontier_jobs
                {where}
                GROUP BY status, reason
                ORDER BY
                    status ASC,
                    CASE reason
                        WHEN 'ply_bound' THEN 0
                        WHEN 'threshold' THEN 1
                        WHEN 'node_limit' THEN 2
                        WHEN 'unknown_rule_state' THEN 3
                        ELSE 4
                    END ASC,
                    reason ASC
                """,
                params,
            ).fetchall()
            by_attempts = con.execute(
                f"""
                SELECT status, attempts, COUNT(*),
                       MIN(remaining_ply), MAX(remaining_ply),
                       MIN(proof), MAX(proof),
                       MIN(disproof), MAX(disproof)
                FROM frontier_jobs
                {where}
                GROUP BY status, attempts
                ORDER BY status ASC, attempts ASC
                """,
                params,
            ).fetchall()
        return {
            "by_status_reason": [
                {
                    "status": row[0],
                    "reason": row[1],
                    "count": row[2],
                    "min_remaining_ply": row[3],
                    "max_remaining_ply": row[4],
                    "min_attempts": row[5],
                    "max_attempts": row[6],
                    "min_proof": row[7],
                    "max_proof": row[8],
                    "min_disproof": row[9],
                    "max_disproof": row[10],
                }
                for row in by_reason
            ],
            "by_status_attempts": [
                {
                    "status": row[0],
                    "attempts": row[1],
                    "count": row[2],
                    "min_remaining_ply": row[3],
                    "max_remaining_ply": row[4],
                    "min_proof": row[5],
                    "max_proof": row[6],
                    "min_disproof": row[7],
                    "max_disproof": row[8],
                }
                for row in by_attempts
            ],
        }

    def database_summary(self) -> dict[str, dict[str, int]]:
        return {
            "proof_results": self.proof_summary(),
            "frontier_jobs": self.frontier_summary(),
        }

    def delete_proof(self, position_key: str, target: ProofTarget, max_ply: int) -> int:
        with closing(sqlite3.connect(self.path)) as con:
            cursor = con.execute(
                """
                DELETE FROM proof_results
                WHERE position_key = ? AND target = ? AND max_ply = ?
                """,
                (position_key, target.value, max_ply),
            )
            con.commit()
            return cursor.rowcount

    def position_key(self, position: Position) -> str:
        return f"{self._zobrist.hash_position(position):016x}"

    def artifact_key(self, artifact: ProofArtifact) -> str:
        return self.state_key(artifact.fen, artifact.history_signature)

    def state_key(self, fen: str, history_signature: str = "") -> str:
        if not history_signature:
            return self.position_key(Position.from_fen(fen))
        payload = f"{fen}\n{history_signature}"
        return f"history:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"

    def _init_schema(self) -> None:
        with closing(sqlite3.connect(self.path)) as con:
            con.executescript(SCHEMA)
            self._ensure_columns(con)
            con.commit()

    def _ensure_columns(self, con: sqlite3.Connection) -> None:
        proof_columns = {row[1] for row in con.execute("PRAGMA table_info(proof_results)").fetchall()}
        if "artifact_hash" not in proof_columns:
            con.execute("ALTER TABLE proof_results ADD COLUMN artifact_hash TEXT")
        frontier_columns = {row[1] for row in con.execute("PRAGMA table_info(frontier_jobs)").fetchall()}
        if "history_signature" not in frontier_columns:
            con.execute("ALTER TABLE frontier_jobs ADD COLUMN history_signature TEXT NOT NULL DEFAULT ''")
        if "position_command" not in frontier_columns:
            con.execute("ALTER TABLE frontier_jobs ADD COLUMN position_command TEXT NOT NULL DEFAULT ''")
        if "proof" not in frontier_columns:
            con.execute("ALTER TABLE frontier_jobs ADD COLUMN proof INTEGER NOT NULL DEFAULT 1")
        if "disproof" not in frontier_columns:
            con.execute("ALTER TABLE frontier_jobs ADD COLUMN disproof INTEGER NOT NULL DEFAULT 1")

    def _frontier_job_from_row(self, row) -> FrontierJob:
        return FrontierJob(
            id=row[0],
            position_key=row[1],
            fen=row[2],
            target=ProofTarget.parse(row[3]),
            remaining_ply=row[4],
            reason=row[5],
            history_signature=row[6],
            position_command=row[7],
            proof=row[8],
            disproof=row[9],
            status=row[10],
            attempts=row[11],
            last_result_status=row[12],
        )


def _artifact_hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _artifact_from_payload(payload: str, expected_hash: str | None) -> ProofArtifact:
    if expected_hash and _artifact_hash(payload) != expected_hash:
        raise ValueError("stored proof artifact hash mismatch")
    return ProofArtifact.from_dict(json.loads(payload))
