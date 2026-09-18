"""Pydantic 请求/响应模型。"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ObservationIn(BaseModel):
    """单条巡检观察输入。"""

    model_config = ConfigDict(extra="forbid")

    observation_id: Optional[str] = Field(
        default=None, max_length=64,
        description="观察外部编号；缺省时由服务端生成",
    )
    inspection_id: str = Field(..., max_length=64, description="巡检批次/航次编号")
    inspected_at: Optional[str] = Field(
        default=None,
        description="巡检时间 ISO-8601；缺省使用写入时刻",
    )
    radius_start: float = Field(..., ge=0.0, description="距叶根起始位置（米，沿展向）")
    radius_end: float = Field(..., ge=0.0, description="距叶根终止位置（米，沿展向）")
    clock_start: float = Field(..., ge=0.0, le=12.0, description="起始钟位（0/12 均为 12 点）")
    clock_end: float = Field(..., ge=0.0, le=12.0, description="终止钟位，沿顺时针；起止相等为单点")
    grade: int = Field(..., ge=1, le=5, description="裂纹等级（1-5）")
    confidence: float = Field(..., ge=0.0, le=1.0, description="置信度（0-1）")

    @field_validator("radius_end")
    @classmethod
    def _radius_order(cls, v, info):
        start = info.data.get("radius_start")
        if start is not None and v < start:
            raise ValueError("radius_end 必须 >= radius_start")
        return v

    @field_validator("inspected_at")
    @classmethod
    def _strip(cls, v):
        if v is not None and not v.strip():
            return None
        return v


class BatchWriteRequest(BaseModel):
    """观察写入（按批次幂等）。"""

    model_config = ConfigDict(extra="forbid")

    batch_id: str = Field(..., max_length=64, description="写入批次号；同号重放为幂等")
    turbine_id: str = Field(..., max_length=64, description="风机编号")
    blade_id: str = Field(..., max_length=64, description="叶片编号")
    observations: List[ObservationIn] = Field(..., min_length=1)
    auto_recompute: bool = Field(
        default=True,
        description="写入后是否立即对该叶片重算复核（默认开启）",
    )


class SourceItem(BaseModel):
    observation_id: str
    inspection_id: str
    inspected_at: Optional[str] = None
    batch_id: str
    radius_start: float
    radius_end: float
    clock_start: float
    clock_end: float
    grade: int
    confidence: float


class TrajectoryOut(BaseModel):
    trajectory_id: str
    turbine_id: str
    blade_id: str
    radius_start: float
    radius_end: float
    clock: dict
    max_confidence: float
    grades: List[int]
    grade_confidence: dict
    representative_grade: Optional[int] = None
    grade_conflict: bool
    observation_count: int
    sources: List[SourceItem]


class BatchWriteResponse(BaseModel):
    batch_id: str
    turbine_id: str
    blade_id: str
    written: int
    skipped_duplicates: int
    recomputed: bool
    changed: Optional[bool] = None
    trajectories: List[TrajectoryOut] = []
    status: Literal["created", "idempotent_replay", "duplicate_batch_conflict"]


class RecomputeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: Optional[str] = Field(
        default=None,
        description="按批次重算（解析到所属风机/叶片）；与 turbine_id+blade_id 二选一",
    )
    turbine_id: Optional[str] = None
    blade_id: Optional[str] = None


class RecomputeResponse(BaseModel):
    turbine_id: str
    blade_id: str
    changed: bool
    trajectory_count: int
    trajectories: List[TrajectoryOut]


class ReviewResponse(BaseModel):
    turbine_id: str
    blade_id: str
    up_to_date: bool
    observation_count: int
    trajectory_count: int
    needs_recompute: bool
    trajectories: List[TrajectoryOut]
