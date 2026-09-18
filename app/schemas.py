"""Pydantic request/response models."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from . import config


class ObservationIn(BaseModel):
    obs_key: str = Field(
        ..., min_length=1, max_length=128, description="批内唯一观察编号"
    )
    turbine: str = Field(..., min_length=1, max_length=64, description="风机编号")
    blade: str = Field(..., min_length=1, max_length=64, description="叶片编号")
    r_start: float = Field(..., ge=0, description="距叶根起始位置（米，闭区间）")
    r_end: float = Field(..., ge=0, description="距叶根结束位置（米，闭区间）")
    clock_start: float = Field(
        ..., ge=0, le=12, description="起始顺时针钟位（0 与 12 均表示 12 点）"
    )
    clock_end: float = Field(
        ..., ge=0, le=12, description="结束顺时针钟位；可越过 12 点"
    )
    grade: int = Field(..., description="裂纹等级")
    confidence: float = Field(..., ge=0, le=1, description="置信度，区间 [0, 1]")
    observed_at: Optional[datetime] = Field(
        None, description="巡检时间（ISO 8601）；缺省取服务端写入时刻"
    )
    source_code: Optional[str] = Field(
        None,
        max_length=128,
        description="观察来源编码（如巡检员/设备）；缺省按提交顺序生成 #0001",
    )

    @field_validator("obs_key", "turbine", "blade", "source_code")
    @classmethod
    def _strip(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        return v or None

    @field_validator("grade")
    @classmethod
    def _grade_range(cls, v: int) -> int:
        if not config.GRADE_MIN <= v <= config.GRADE_MAX:
            raise ValueError(
                f"grade must be between {config.GRADE_MIN} and {config.GRADE_MAX}"
            )
        return v

    @model_validator(mode="after")
    def _check_ranges(self) -> "ObservationIn":
        if self.r_end < self.r_start:
            raise ValueError("r_end must be greater than or equal to r_start")
        return self


class BatchWrite(BaseModel):
    observations: List[ObservationIn] = Field(
        ..., min_length=1, description="本批次提交的全部巡检观察（整批覆盖）"
    )

    @model_validator(mode="after")
    def _unique_keys(self) -> "BatchWrite":
        keys = [o.obs_key for o in self.observations]
        if len(set(keys)) != len(keys):
            raise ValueError("obs_key must be unique within a batch")
        return self


class ClockArc(BaseModel):
    start: float
    end: float
    wraps: bool = Field(..., description="True 表示该区间跨越 12 点")


class GradeConfidence(BaseModel):
    grade: int
    confidence: float


class ObservationSource(BaseModel):
    observation_id: int
    batch_id: str
    obs_key: str
    r_start: float
    r_end: float
    clock_start: float
    clock_end: float
    grade: int
    confidence: float
    observed_at: Optional[str]
    source_code: str


class CrackTrajectory(BaseModel):
    turbine: str
    blade: str
    cluster_key: str
    r_start: float
    r_end: float
    clock_arcs: List[ClockArc]
    grade: int = Field(..., description="轨迹采用等级（置信度合计最高者）")
    grade_conflict: bool = Field(..., description="簇内是否存在多个等级")
    grade_confidence: List[GradeConfidence]
    computed_at: str
    sources: List[ObservationSource]


class BatchWriteResult(BaseModel):
    batch_id: str
    action: str
    observations_written: int
    blades_recomputed: int
    clusters: int
    recomputed_at: str


class RecomputeResult(BaseModel):
    scope: str
    batch_id: Optional[str] = None
    blades_recomputed: int
    clusters: int
    recomputed_at: str


class ObservationOut(ObservationIn):
    id: int
    batch_id: str

    class Config:
        from_attributes = True


class BatchInfo(BaseModel):
    batch_id: str
    created_at: str
    updated_at: str
    observation_count: int
