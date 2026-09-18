"""FastAPI 应用入口与路由。

启动即完成：依赖检查、SQLite 建表（见 :func:`app.db.init_db`）。
"""

from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import db, services
from .schemas import (
    BatchWriteRequest,
    BatchWriteResponse,
    RecomputeRequest,
    RecomputeResponse,
    ReviewResponse,
)

app = FastAPI(
    title="风机叶片裂纹复核 API",
    description=(
        "接收多次巡检观察（距叶根径向区间 + 顺时针钟位区间 + 等级 + 置信度），"
        "将径向与钟位范围相交的观察聚为连通簇，合并为带来源清单的裂纹轨迹，"
        "并标注等级冲突。支持观察写入、复核查询与按批次幂等重算。"
    ),
    version="1.0.0",
)


@app.on_event("startup")
def _startup() -> None:
    db.DB_PATH = os.environ.get("CRACK_DB_PATH", db.DB_PATH)
    db.init_db()


@app.exception_handler(services.ApiError)
async def _api_error_handler(_request: Request, exc: services.ApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code,
                        content={"detail": exc.detail})


@app.get("/health", tags=["系统"], summary="健康检查")
def health() -> dict:
    return {"status": "ok"}


@app.post(
    "/api/v1/observations/batch",
    tags=["观察写入"],
    response_model=BatchWriteResponse,
    summary="按批次写入巡检观察（幂等）",
)
def write_batch(payload: BatchWriteRequest) -> dict:
    return services.write_batch(payload)


@app.post(
    "/api/v1/reviews/recompute",
    tags=["复核重算"],
    response_model=RecomputeResponse,
    summary="按批次（或风机+叶片）幂等重算裂纹轨迹",
)
def recompute(payload: RecomputeRequest) -> dict:
    return services.recompute(payload.batch_id,
                              payload.turbine_id, payload.blade_id)


@app.get(
    "/api/v1/reviews/{turbine_id}/{blade_id}",
    tags=["复核查询"],
    response_model=ReviewResponse,
    summary="查询某叶片的裂纹轨迹复核结果",
)
def get_review(turbine_id: str, blade_id: str) -> dict:
    return services.get_review(turbine_id, blade_id)
