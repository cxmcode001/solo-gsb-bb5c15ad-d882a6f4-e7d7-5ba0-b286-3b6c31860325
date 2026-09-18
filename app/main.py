"""FastAPI application: wind-turbine blade crack review service."""
from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from typing import AsyncIterator, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, status
from typing_extensions import Annotated

from . import db
from .schemas import (
    BatchInfo,
    BatchWrite,
    BatchWriteResult,
    CrackTrajectory,
    ObservationOut,
    RecomputeResult,
)
from .db import utc_now


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Tables are created on startup; dependencies need no other provisioning.
    conn = db.connect()
    try:
        db.init_db(conn)
    finally:
        conn.close()
    yield


app = FastAPI(
    title="风机叶片裂纹复核 API",
    version="1.0.0",
    lifespan=lifespan,
    description=(
        "接收多批次巡检观察，按径向区间与顺时针钟位区间（可跨 12 点）"
        "的相交关系进行连通簇聚类，合并为带来源清单的裂纹轨迹并标出等级冲突。"
    ),
)


def get_conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


ConnDep = Annotated[sqlite3.Connection, Depends(get_conn)]


@app.get("/health", tags=["meta"], summary="健康检查")
def health() -> dict:
    return {"status": "ok"}


@app.put(
    "/api/batches/{batch_id}/observations",
    response_model=BatchWriteResult,
    tags=["batches"],
    status_code=status.HTTP_200_OK,
    summary="按批次幂等写入观察并自动重算受影响叶片",
)
def put_batch_observations(
    batch_id: str,
    payload: BatchWrite,
    conn: ConnDep,
) -> BatchWriteResult:
    observations = []
    for pos, obs in enumerate(payload.observations):
        observations.append(
            {
                "obs_key": obs.obs_key,
                "turbine": obs.turbine,
                "blade": obs.blade,
                "r_start": obs.r_start,
                "r_end": obs.r_end,
                "clock_start": obs.clock_start,
                "clock_end": obs.clock_end,
                "grade": obs.grade,
                "confidence": obs.confidence,
                "observed_at": (
                    obs.observed_at.isoformat()
                    if obs.observed_at
                    else utc_now()
                ),
                "source_code": obs.source_code or f"#{pos + 1:04d}",
            }
        )
    result = db.replace_batch(conn, batch_id, observations)
    return BatchWriteResult(**result)


@app.post(
    "/api/batches/{batch_id}/recompute",
    response_model=RecomputeResult,
    tags=["batches"],
    summary="按批次幂等重算复核结果",
)
def post_batch_recompute(batch_id: str, conn: ConnDep) -> RecomputeResult:
    result = db.recompute_batch(conn, batch_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"batch '{batch_id}' not found",
        )
    return RecomputeResult(**result)


@app.post(
    "/api/recompute",
    response_model=RecomputeResult,
    tags=["batches"],
    summary="重算全部叶片的复核结果",
)
def post_recompute_all(conn: ConnDep) -> RecomputeResult:
    return RecomputeResult(**db.recompute_all(conn))


@app.get(
    "/api/batches/{batch_id}",
    response_model=BatchInfo,
    tags=["batches"],
    summary="查询批次元信息",
)
def get_batch(batch_id: str, conn: ConnDep) -> BatchInfo:
    info = db.get_batch(conn, batch_id)
    if info is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"batch '{batch_id}' not found",
        )
    return BatchInfo(**info)


@app.get(
    "/api/observations",
    response_model=List[ObservationOut],
    tags=["observations"],
    summary="查询已写入的观察（可按风机/叶片/批次过滤）",
)
def get_observations(
    conn: ConnDep,
    turbine: Annotated[Optional[str], Query()] = None,
    blade: Annotated[Optional[str], Query()] = None,
    batch_id: Annotated[Optional[str], Query()] = None,
) -> List[ObservationOut]:
    rows = db.list_observations(conn, turbine, blade, batch_id)
    return [ObservationOut(**row) for row in rows]


@app.get(
    "/api/cracks",
    response_model=List[CrackTrajectory],
    tags=["review"],
    summary="查询复核结果：合并后的裂纹轨迹（带来源清单与等级冲突标记）",
)
def get_cracks(
    conn: ConnDep,
    turbine: Annotated[Optional[str], Query(description="风机编号")] = None,
    blade: Annotated[Optional[str], Query(description="叶片编号")] = None,
    only_conflicts: Annotated[
        bool, Query(description="仅返回存在等级冲突的轨迹")
    ] = False,
) -> List[CrackTrajectory]:
    rows = db.list_trajectories(conn, turbine, blade, only_conflicts)
    return [CrackTrajectory(**row) for row in rows]
