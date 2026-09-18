"""SQLite 持久化：schema 初始化与数据访问。

数据库文件路径由环境变量 ``CRACK_DB_PATH`` 指定，随应用启动自动建表。
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Sequence

DB_PATH = os.environ.get("CRACK_DB_PATH", "data/crack_review.db")

SCHEMA: Sequence[str] = (
    """
    CREATE TABLE IF NOT EXISTS batches (
        batch_id   TEXT PRIMARY KEY,
        turbine_id TEXT NOT NULL,
        blade_id   TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS observations (
        observation_id TEXT PRIMARY KEY,
        batch_id    TEXT NOT NULL REFERENCES batches(batch_id),
        turbine_id  TEXT NOT NULL,
        blade_id    TEXT NOT NULL,
        inspection_id TEXT NOT NULL,
        inspected_at TEXT,
        radius_start REAL NOT NULL,
        radius_end   REAL NOT NULL,
        clock_start  REAL NOT NULL,
        clock_end    REAL NOT NULL,
        grade        INTEGER NOT NULL,
        confidence   REAL NOT NULL,
        seq          INTEGER NOT NULL,
        created_at   TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_obs_blade ON observations(turbine_id, blade_id)",
    "CREATE INDEX IF NOT EXISTS idx_obs_batch ON observations(batch_id)",
    """
    CREATE TABLE IF NOT EXISTS review_state (
        turbine_id TEXT NOT NULL,
        blade_id   TEXT NOT NULL,
        input_hash TEXT NOT NULL,
        trajectory_count INTEGER NOT NULL,
        computed_at TEXT NOT NULL,
        PRIMARY KEY (turbine_id, blade_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trajectories (
        turbine_id TEXT NOT NULL,
        blade_id   TEXT NOT NULL,
        trajectory_id TEXT NOT NULL,
        ord INTEGER NOT NULL,
        data_json TEXT NOT NULL,
        PRIMARY KEY (turbine_id, blade_id, trajectory_id)
    )
    """,
)

_init_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    db_dir = os.path.dirname(os.path.abspath(DB_PATH))
    os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db() -> None:
    """建表（幂等），应用启动时执行。"""
    with _init_lock:
        conn = _connect()
        try:
            with conn:
                for stmt in SCHEMA:
                    conn.execute(stmt)
        finally:
            conn.close()


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------- batches

def get_batch(conn: sqlite3.Connection, batch_id: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM batches WHERE batch_id = ?", (batch_id,)
    ).fetchone()


def insert_batch(conn: sqlite3.Connection, batch_id: str, turbine_id: str,
                 blade_id: str, content_hash: str, created_at: str) -> None:
    conn.execute(
        "INSERT INTO batches(batch_id, turbine_id, blade_id, content_hash, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (batch_id, turbine_id, blade_id, content_hash, created_at),
    )


def count_observations(conn: sqlite3.Connection, batch_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM observations WHERE batch_id = ?", (batch_id,)
    ).fetchone()
    return int(row["c"])


def insert_observation(conn: sqlite3.Connection, obs: Dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO observations(
            observation_id, batch_id, turbine_id, blade_id, inspection_id,
            inspected_at, radius_start, radius_end, clock_start, clock_end,
            grade, confidence, seq, created_at)
        VALUES (:observation_id, :batch_id, :turbine_id, :blade_id, :inspection_id,
                :inspected_at, :radius_start, :radius_end, :clock_start, :clock_end,
                :grade, :confidence, :seq, :created_at)
        """,
        obs,
    )


def get_observation(conn: sqlite3.Connection, observation_id: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM observations WHERE observation_id = ?", (observation_id,)
    ).fetchone()


# ------------------------------------------------------------- blade data

def list_observations(conn: sqlite3.Connection,
                      turbine_id: str, blade_id: str) -> List[sqlite3.Row]:
    return list(conn.execute(
        """
        SELECT * FROM observations
        WHERE turbine_id = ? AND blade_id = ?
        ORDER BY radius_start, clock_start, seq, observation_id
        """,
        (turbine_id, blade_id),
    ).fetchall())


def blade_observation_count(conn: sqlite3.Connection,
                            turbine_id: str, blade_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM observations WHERE turbine_id = ? AND blade_id = ?",
        (turbine_id, blade_id),
    ).fetchone()
    return int(row["c"])


# ----------------------------------------------------------- review state

def get_review_state(conn: sqlite3.Connection,
                     turbine_id: str, blade_id: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM review_state WHERE turbine_id = ? AND blade_id = ?",
        (turbine_id, blade_id),
    ).fetchone()


def replace_review(conn: sqlite3.Connection, turbine_id: str, blade_id: str,
                   input_hash: str, trajectories: List[Dict[str, Any]],
                   computed_at: str) -> None:
    """在同一事务内整体替换叶片复核结果（幂等重算的核心）。"""
    conn.execute(
        "DELETE FROM trajectories WHERE turbine_id = ? AND blade_id = ?",
        (turbine_id, blade_id),
    )
    for ord_, trj in enumerate(trajectories):
        import json
        conn.execute(
            "INSERT INTO trajectories(turbine_id, blade_id, trajectory_id, ord, data_json)"
            " VALUES (?, ?, ?, ?, ?)",
            (turbine_id, blade_id, trj["trajectory_id"], ord_,
             json.dumps(trj, ensure_ascii=False, sort_keys=True)),
        )
    conn.execute(
        """
        INSERT INTO review_state(turbine_id, blade_id, input_hash,
                                 trajectory_count, computed_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(turbine_id, blade_id) DO UPDATE SET
            input_hash=excluded.input_hash,
            trajectory_count=excluded.trajectory_count,
            computed_at=excluded.computed_at
        """,
        (turbine_id, blade_id, input_hash, len(trajectories), computed_at),
    )


def list_trajectories(conn: sqlite3.Connection,
                      turbine_id: str, blade_id: str) -> List[Dict[str, Any]]:
    import json
    rows = conn.execute(
        "SELECT data_json FROM trajectories"
        " WHERE turbine_id = ? AND blade_id = ? ORDER BY ord",
        (turbine_id, blade_id),
    ).fetchall()
    return [json.loads(r["data_json"]) for r in rows]
