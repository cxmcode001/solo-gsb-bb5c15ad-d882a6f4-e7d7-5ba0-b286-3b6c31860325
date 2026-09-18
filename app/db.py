"""SQLite persistence and (re)computation of crack trajectories."""
from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import config
from .clustering import cluster_observations

_SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    batch_id    TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS observations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id      TEXT NOT NULL REFERENCES batches(batch_id) ON DELETE CASCADE,
    obs_key       TEXT NOT NULL,
    turbine       TEXT NOT NULL,
    blade         TEXT NOT NULL,
    r_start       REAL NOT NULL,
    r_end         REAL NOT NULL,
    clock_start   REAL NOT NULL,
    clock_end     REAL NOT NULL,
    grade         INTEGER NOT NULL,
    confidence    REAL NOT NULL,
    observed_at   TEXT,
    source_code   TEXT NOT NULL,
    UNIQUE (batch_id, obs_key)
);

CREATE TABLE IF NOT EXISTS clusters (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_key        TEXT NOT NULL,
    turbine            TEXT NOT NULL,
    blade              TEXT NOT NULL,
    r_start            REAL NOT NULL,
    r_end              REAL NOT NULL,
    grade              INTEGER NOT NULL,
    grade_conflict     INTEGER NOT NULL,
    clock_arcs_json    TEXT NOT NULL,
    grade_conf_json    TEXT NOT NULL,
    computed_at        TEXT NOT NULL,
    UNIQUE (turbine, blade, cluster_key)
);

CREATE TABLE IF NOT EXISTS cluster_members (
    cluster_id      INTEGER NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,
    observation_id  INTEGER NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
    position        INTEGER NOT NULL,
    PRIMARY KEY (cluster_id, observation_id)
);

CREATE INDEX IF NOT EXISTS idx_obs_blade
    ON observations (turbine, blade);
CREATE INDEX IF NOT EXISTS idx_obs_batch
    ON observations (batch_id);
CREATE INDEX IF NOT EXISTS idx_clusters_blade
    ON clusters (turbine, blade);
CREATE INDEX IF NOT EXISTS idx_members_obs
    ON cluster_members (observation_id);
"""

_OBS_COLUMNS = (
    "id",
    "batch_id",
    "obs_key",
    "turbine",
    "blade",
    "r_start",
    "r_end",
    "clock_start",
    "clock_end",
    "grade",
    "confidence",
    "observed_at",
    "source_code",
)

# Serialise all writes: SQLite handles one writer, and recomputation must be
# atomic relative to new batch submissions.
_write_lock = threading.RLock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(
        config.DB_PATH,
        timeout=30.0,
        detect_types=sqlite3.PARSE_DECLTYPES,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(config.DB_PATH)), exist_ok=True)
    conn.executescript(_SCHEMA)
    conn.commit()


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def replace_batch(
    conn: sqlite3.Connection,
    batch_id: str,
    observations: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """Idempotently replace *batch_id* with the given observations.

    Re-submitting the same batch deletes its previous payload and recomputes
    every blade the batch touches (old and new). Returns a summary dict.
    """
    with _write_lock:
        now = utc_now()
        existed = conn.execute(
            "SELECT 1 FROM batches WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        action = "replaced" if existed else "created"

        old_blades = [
            tuple(row)
            for row in conn.execute(
                "SELECT DISTINCT turbine, blade FROM observations "
                "WHERE batch_id = ?",
                (batch_id,),
            )
        ]

        conn.execute(
            "INSERT INTO batches (batch_id, created_at, updated_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(batch_id) DO UPDATE SET updated_at = excluded.updated_at",
            (batch_id, now, now),
        )
        conn.execute("DELETE FROM observations WHERE batch_id = ?", (batch_id,))

        for pos, obs in enumerate(observations):
            conn.execute(
                "INSERT INTO observations (batch_id, obs_key, turbine, blade, "
                "r_start, r_end, clock_start, clock_end, grade, confidence, "
                "observed_at, source_code) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    batch_id,
                    obs["obs_key"],
                    obs["turbine"],
                    obs["blade"],
                    obs["r_start"],
                    obs["r_end"],
                    obs["clock_start"],
                    obs["clock_end"],
                    obs["grade"],
                    obs["confidence"],
                    obs["observed_at"],
                    obs.get("source_code") or f"#{pos + 1:04d}",
                ),
            )

        new_blades = {(obs["turbine"], obs["blade"]) for obs in observations}
        blades = sorted(set(old_blades) | new_blades)
        blade_counts = {}
        cluster_count = 0
        for turbine, blade in blades:
            blade_counts[f"{turbine}/{blade}"] = recompute_blade(
                conn, turbine, blade, now=now
            )
            cluster_count += blade_counts[f"{turbine}/{blade}"]
        conn.commit()

        return {
            "batch_id": batch_id,
            "action": action,
            "observations_written": len(observations),
            "blades_recomputed": len(blades),
            "clusters": cluster_count,
            "recomputed_at": now,
        }


def recompute_blade(
    conn: sqlite3.Connection,
    turbine: str,
    blade: str,
    now: Optional[str] = None,
) -> int:
    """Recompute all crack trajectories for one blade. Returns cluster count."""
    now = now or utc_now()
    rows = conn.execute(
        "SELECT " + ", ".join(_OBS_COLUMNS) + " FROM observations "
        "WHERE turbine = ? AND blade = ? ORDER BY id",
        (turbine, blade),
    ).fetchall()
    trajectories = cluster_observations([dict(r) for r in rows])

    conn.execute(
        "DELETE FROM clusters WHERE turbine = ? AND blade = ?",
        (turbine, blade),
    )
    import json

    for traj in trajectories:
        cur = conn.execute(
            "INSERT INTO clusters (cluster_key, turbine, blade, r_start, r_end, "
            "grade, grade_conflict, clock_arcs_json, grade_conf_json, computed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                traj["cluster_key"],
                turbine,
                blade,
                traj["r_start"],
                traj["r_end"],
                traj["grade"],
                1 if traj["grade_conflict"] else 0,
                json.dumps(traj["clock_arcs"], ensure_ascii=False),
                json.dumps(traj["grade_confidence"], ensure_ascii=False),
                now,
            ),
        )
        cluster_id = cur.lastrowid
        for pos, src in enumerate(traj["sources"]):
            conn.execute(
                "INSERT INTO cluster_members (cluster_id, observation_id, position) "
                "VALUES (?, ?, ?)",
                (cluster_id, src["observation_id"], pos),
            )
    return len(trajectories)


def recompute_batch(conn: sqlite3.Connection, batch_id: str) -> Optional[Dict[str, Any]]:
    """Recompute every blade a batch touches. None if the batch is unknown."""
    with _write_lock:
        if not conn.execute(
            "SELECT 1 FROM batches WHERE batch_id = ?", (batch_id,)
        ).fetchone():
            return None
        blades = [
            tuple(row)
            for row in conn.execute(
                "SELECT DISTINCT turbine, blade FROM observations "
                "WHERE batch_id = ? ORDER BY turbine, blade",
                (batch_id,),
            )
        ]
        now = utc_now()
        cluster_count = 0
        per_blade = {}
        for turbine, blade in blades:
            n = recompute_blade(conn, turbine, blade, now=now)
            per_blade[f"{turbine}/{blade}"] = n
            cluster_count += n
        conn.commit()
        return {
            "batch_id": batch_id,
            "scope": "batch",
            "blades_recomputed": len(blades),
            "clusters": cluster_count,
            "recomputed_at": now,
        }


def recompute_all(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Recompute every blade in the database."""
    with _write_lock:
        blades = [
            tuple(row)
            for row in conn.execute(
                "SELECT DISTINCT turbine, blade FROM observations "
                "ORDER BY turbine, blade"
            )
        ]
        now = utc_now()
        cluster_count = 0
        per_blade = {}
        for turbine, blade in blades:
            n = recompute_blade(conn, turbine, blade, now=now)
            per_blade[f"{turbine}/{blade}"] = n
            cluster_count += n
        conn.commit()
        return {
            "scope": "all",
            "blades_recomputed": len(blades),
            "clusters": cluster_count,
            "recomputed_at": now,
        }


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def get_batch(conn: sqlite3.Connection, batch_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT b.batch_id, b.created_at, b.updated_at, "
        "COUNT(o.id) AS observation_count "
        "FROM batches b LEFT JOIN observations o ON o.batch_id = b.batch_id "
        "WHERE b.batch_id = ? GROUP BY b.batch_id",
        (batch_id,),
    ).fetchone()
    return dict(row) if row else None


def list_observations(
    conn: sqlite3.Connection,
    turbine: Optional[str] = None,
    blade: Optional[str] = None,
    batch_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    sql = "SELECT " + ", ".join(_OBS_COLUMNS) + " FROM observations WHERE 1=1"
    params: List[Any] = []
    if turbine is not None:
        sql += " AND turbine = ?"
        params.append(turbine)
    if blade is not None:
        sql += " AND blade = ?"
        params.append(blade)
    if batch_id is not None:
        sql += " AND batch_id = ?"
        params.append(batch_id)
    sql += " ORDER BY turbine, blade, id"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def list_trajectories(
    conn: sqlite3.Connection,
    turbine: Optional[str] = None,
    blade: Optional[str] = None,
    only_conflicts: bool = False,
) -> List[Dict[str, Any]]:
    import json

    sql = "SELECT * FROM clusters WHERE 1=1"
    params: List[Any] = []
    if turbine is not None:
        sql += " AND turbine = ?"
        params.append(turbine)
    if blade is not None:
        sql += " AND blade = ?"
        params.append(blade)
    if only_conflicts:
        sql += " AND grade_conflict = 1"
    sql += " ORDER BY turbine, blade, r_start, id"

    result = []
    for cluster_row in conn.execute(sql, params).fetchall():
        cluster_row = dict(cluster_row)
        members = conn.execute(
            "SELECT o.*, cm.position FROM cluster_members cm "
            "JOIN observations o ON o.id = cm.observation_id "
            "WHERE cm.cluster_id = ? ORDER BY cm.position",
            (cluster_row["id"],),
        ).fetchall()
        sources = []
        for m in members:
            m = dict(m)
            sources.append(
                {
                    "observation_id": m["id"],
                    "batch_id": m["batch_id"],
                    "obs_key": m["obs_key"],
                    "r_start": m["r_start"],
                    "r_end": m["r_end"],
                    "clock_start": m["clock_start"],
                    "clock_end": m["clock_end"],
                    "grade": m["grade"],
                    "confidence": m["confidence"],
                    "observed_at": m["observed_at"],
                    "source_code": m["source_code"],
                }
            )
        result.append(
            {
                "turbine": cluster_row["turbine"],
                "blade": cluster_row["blade"],
                "cluster_key": cluster_row["cluster_key"],
                "r_start": cluster_row["r_start"],
                "r_end": cluster_row["r_end"],
                "clock_arcs": json.loads(cluster_row["clock_arcs_json"]),
                "grade": cluster_row["grade"],
                "grade_conflict": bool(cluster_row["grade_conflict"]),
                "grade_confidence": json.loads(cluster_row["grade_conf_json"]),
                "computed_at": cluster_row["computed_at"],
                "sources": sources,
            }
        )
    return result
