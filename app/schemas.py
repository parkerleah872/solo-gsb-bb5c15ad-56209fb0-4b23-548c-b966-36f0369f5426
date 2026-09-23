"""请求 / 响应数据模型。"""
from __future__ import annotations

import math

from pydantic import BaseModel, Field, field_validator

_EPS = 1e-12


class Vec2(BaseModel):
    x: float
    y: float

    def as_tuple(self) -> tuple[float, float]:
        return (self.x, self.y)


class InterfaceIn(BaseModel):
    """一个界面：线上一点、指向下一介质的法线、下一介质折射率。"""

    point: Vec2
    normal: Vec2
    n_next: float = Field(gt=0, description="下一介质折射率，必须为正")

    @field_validator("normal")
    @classmethod
    def normal_nonzero(cls, v: Vec2) -> Vec2:
        if math.hypot(v.x, v.y) < _EPS:
            raise ValueError("法线不能为零向量")
        return v


class TraceRequest(BaseModel):
    start: Vec2
    direction: Vec2
    n_start: float = Field(gt=0, description="起始介质折射率，必须为正")
    interfaces: list[InterfaceIn] = Field(default_factory=list)

    @field_validator("direction")
    @classmethod
    def direction_nonzero(cls, v: Vec2) -> Vec2:
        if math.hypot(v.x, v.y) < _EPS:
            raise ValueError("方向不能为零向量")
        return v


class SegmentOut(BaseModel):
    seg_index: int
    origin: Vec2
    direction: Vec2
    n_medium: float
    hit_point: Vec2 | None
    event: str | None
    n_after: float | None


class TraceOut(BaseModel):
    id: int
    start: Vec2
    direction: Vec2
    n_start: float
    status: str
    reason: str | None
    created_at: str
    segments: list[SegmentOut]


class TraceSummary(BaseModel):
    id: int
    status: str
    reason: str | None
    n_start: float
    created_at: str
