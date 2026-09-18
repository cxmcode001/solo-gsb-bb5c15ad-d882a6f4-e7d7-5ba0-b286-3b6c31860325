"""业务逻辑：观察写入、幂等重算、复核查询。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from . import db
from .clustering import Observation, build_all
from .schemas import BatchWriteRequest, ObservationIn


class ApiError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_inspected_at(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        datetime.fromisoformat(text)
    except ValueError as exc:
        raise ApiError(422, f"inspected_at 不是合法 ISO-8601 时间: {value}") from exc
    return value.strip()


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def content_hash(req: BatchWriteRequest) -> str:
    """请求内容指纹：同内容重放视为幂等。"""
    payload = {
        "turbine_id": req.turbine_id,
        "blade_id": req.blade_id,
        "observations": [
            {
                "observation_id": o.observation_id,
                "inspection_id": o.inspection_id,
                "inspected_at": _parse_inspected_at(o.inspected_at),
                "radius_start": o.radius_start,
                "radius_end": o.radius_end,
                "clock_start": _normalize_clock_input(o.clock_start),
                "clock_end": _normalize_clock_input(o.clock_end),
                "grade": o.grade,
                "confidence": o.confidence,
            }
            for o in req.observations
        ],
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _row_to_observation(row: sqlite3.Row) -> Observation:
    return Observation(
        observation_id=row["observation_id"],
        inspection_id=row["inspection_id"],
        inspected_at=row["inspected_at"],
        batch_id=row["batch_id"],
        turbine_id=row["turbine_id"],
        blade_id=row["blade_id"],
        radius_start=row["radius_start"],
        radius_end=row["radius_end"],
        clock_start=row["clock_start"],
        clock_end=row["clock_end"],
        grade=row["grade"],
        confidence=row["confidence"],
    )


def blade_input_hash(conn: sqlite3.Connection,
                     turbine_id: str, blade_id: str) -> Tuple[str, int]:
    """叶片当前全部观察的指纹与条数。"""
    rows = db.list_observations(conn, turbine_id, blade_id)
    payload = [
        {
            "observation_id": r["observation_id"],
            "inspection_id": r["inspection_id"],
            "inspected_at": r["inspected_at"],
            "radius_start": r["radius_start"],
            "radius_end": r["radius_end"],
            "clock_start": r["clock_start"],
            "clock_end": r["clock_end"],
            "grade": r["grade"],
            "confidence": r["confidence"],
        }
        for r in rows
    ]
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return digest, len(rows)


def _compute_live(conn: sqlite3.Connection,
                  turbine_id: str, blade_id: str) -> List[Dict[str, Any]]:
    rows = db.list_observations(conn, turbine_id, blade_id)
    observations = [_row_to_observation(r) for r in rows]
    return build_all(turbine_id, blade_id, observations)


# ------------------------------------------------------------------ write

def write_batch(req: BatchWriteRequest) -> Dict[str, Any]:
    """写入观察批次。

    - 相同 batch_id + 相同内容：幂等重放，不重复写入；
    - 相同 batch_id + 不同内容：409；
    - observation_id 与已有观察冲突（跨批次）：409。
    """
    now = utcnow()
    fingerprint = content_hash(req)

    # 提前校验时间格式
    for o in req.observations:
        _parse_inspected_at(o.inspected_at)

    with db.get_conn() as conn:
        existing = db.get_batch(conn, req.batch_id)
        if existing is not None:
            if existing["content_hash"] != fingerprint or \
                    existing["turbine_id"] != req.turbine_id or \
                    existing["blade_id"] != req.blade_id:
                raise ApiError(
                    409,
                    f"batch_id={req.batch_id} 已存在但内容与本次请求不一致",
                )
            trajectories = db.list_trajectories(
                conn, req.turbine_id, req.blade_id)
            return {
                "batch_id": req.batch_id,
                "turbine_id": req.turbine_id,
                "blade_id": req.blade_id,
                "written": 0,
                "skipped_duplicates": len(req.observations),
                "recomputed": False,
                "changed": None,
                "trajectories": trajectories,
                "status": "idempotent_replay",
            }

        # 同一 blade 内的观察外部编号不得重复
        existing_ids = {
            r["observation_id"]
            for r in db.list_observations(conn, req.turbine_id, req.blade_id)
        }
        seen: set[str] = set()
        prepared: List[Dict[str, Any]] = []
        for seq, o in enumerate(req.observations):
            obs_id = o.observation_id or f"obs_{uuid.uuid4().hex[:16]}"
            if obs_id in seen:
                raise ApiError(422, f"批次内 observation_id 重复: {obs_id}")
            if obs_id in existing_ids:
                raise ApiError(409,
                               f"observation_id 已存在: {obs_id}")
            seen.add(obs_id)
            prepared.append({
                "observation_id": obs_id,
                "batch_id": req.batch_id,
                "turbine_id": req.turbine_id,
                "blade_id": req.blade_id,
                "inspection_id": o.inspection_id,
                "inspected_at": _parse_inspected_at(o.inspected_at),
                "radius_start": o.radius_start,
                "radius_end": o.radius_end,
                "clock_start": _normalize_clock_input(o.clock_start),
                "clock_end": _normalize_clock_input(o.clock_end),
                "grade": o.grade,
                "confidence": o.confidence,
                "seq": seq,
                "created_at": now,
            })

        try:
            with conn:
                db.insert_batch(conn, req.batch_id, req.turbine_id,
                                req.blade_id, fingerprint, now)
                for row in prepared:
                    db.insert_observation(conn, row)
        except sqlite3.IntegrityError as exc:
            raise ApiError(409, f"写入冲突（可能为并发重复提交）: {exc}") from exc

        recomputed = False
        changed: Optional[bool] = None
        trajectories: List[Dict[str, Any]] = []
        if req.auto_recompute:
            changed, trajectories = _recompute_conn(
                conn, req.turbine_id, req.blade_id)
            recomputed = True

        return {
            "batch_id": req.batch_id,
            "turbine_id": req.turbine_id,
            "blade_id": req.blade_id,
            "written": len(prepared),
            "skipped_duplicates": 0,
            "recomputed": recomputed,
            "changed": changed,
            "trajectories": trajectories,
            "status": "created",
        }


def _normalize_clock_input(v: float) -> float:
    # 12.0 统一存为 0.0，几何层按同一语义处理
    return 0.0 if abs(v - 12.0) < 1e-9 else v


# -------------------------------------------------------------- recompute

def _resolve_blade(conn: sqlite3.Connection, batch_id: Optional[str],
                   turbine_id: Optional[str], blade_id: Optional[str]
                   ) -> Tuple[str, str]:
    if batch_id:
        row = db.get_batch(conn, batch_id)
        if row is None:
            raise ApiError(404, f"batch_id 不存在: {batch_id}")
        return row["turbine_id"], row["blade_id"]
    if turbine_id and blade_id:
        return turbine_id, blade_id
    raise ApiError(422, "必须提供 batch_id，或同时提供 turbine_id 与 blade_id")


def _recompute_conn(conn: sqlite3.Connection,
                    turbine_id: str, blade_id: str
                    ) -> Tuple[bool, List[Dict[str, Any]]]:
    count = db.blade_observation_count(conn, turbine_id, blade_id)
    if count == 0:
        raise ApiError(404,
                       f"叶片无观察记录: turbine={turbine_id} blade={blade_id}")

    input_hash, _ = blade_input_hash(conn, turbine_id, blade_id)
    state = db.get_review_state(conn, turbine_id, blade_id)
    if state is not None and state["input_hash"] == input_hash:
        return False, db.list_trajectories(conn, turbine_id, blade_id)

    trajectories = _compute_live(conn, turbine_id, blade_id)
    with conn:
        db.replace_review(conn, turbine_id, blade_id, input_hash,
                          trajectories, utcnow())
    return True, trajectories


def recompute(batch_id: Optional[str],
              turbine_id: Optional[str],
              blade_id: Optional[str]) -> Dict[str, Any]:
    with db.get_conn() as conn:
        turbine_id, blade_id = _resolve_blade(conn, batch_id,
                                              turbine_id, blade_id)
        changed, trajectories = _recompute_conn(conn, turbine_id, blade_id)
        return {
            "turbine_id": turbine_id,
            "blade_id": blade_id,
            "changed": changed,
            "trajectory_count": len(trajectories),
            "trajectories": trajectories,
        }


# ------------------------------------------------------------------ query

def get_review(turbine_id: str, blade_id: str) -> Dict[str, Any]:
    with db.get_conn() as conn:
        count = db.blade_observation_count(conn, turbine_id, blade_id)
        if count == 0:
            raise ApiError(404,
                           f"叶片无观察记录: turbine={turbine_id} blade={blade_id}")

        input_hash, _ = blade_input_hash(conn, turbine_id, blade_id)
        state = db.get_review_state(conn, turbine_id, blade_id)
        up_to_date = state is not None and state["input_hash"] == input_hash

        if up_to_date:
            trajectories = db.list_trajectories(conn, turbine_id, blade_id)
        else:
            # 不落库地实时计算，保证查询始终返回当前观察对应的轨迹
            trajectories = _compute_live(conn, turbine_id, blade_id)

        return {
            "turbine_id": turbine_id,
            "blade_id": blade_id,
            "up_to_date": up_to_date,
            "observation_count": count,
            "trajectory_count": len(trajectories),
            "needs_recompute": not up_to_date,
            "trajectories": trajectories,
        }
