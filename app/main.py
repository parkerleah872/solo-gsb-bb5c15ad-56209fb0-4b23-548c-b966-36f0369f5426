"""2D 介质光线追迹 API.

提供按顺序排列的介质界面（由界面上一点 + 指向下一介质的法线表示），
服务计算各界面的前向交点，按斯涅尔定律更新方向，遇到全反射（TIR）即停止，
并将每一段（起点/终点/方向/介质折射率）持久化到 SQLite。
"""

from __future__ import annotations

import math
import os
import sqlite3
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

DB_PATH = os.environ.get("RAYTRACER_DB", "/data/raytracer.db")

EPS = 1.0e-9

app = FastAPI(
    title="2D 介质光线追迹 API",
    description="按斯涅尔定律在二维分层介质中追迹光线，结果存入 SQLite，可按编号查询各段。",
    version="1.0.0",
)


# --------------------------------------------------------------------------- #
# 数据模型
# --------------------------------------------------------------------------- #
class Vec2(BaseModel):
    x: float
    y: float


class Surface(BaseModel):
    """一个介质界面：经过 point，法线 normal 指向“下一介质”一侧。"""

    point: Vec2 = Field(..., description="界面上的一点（二维）")
    normal: Vec2 = Field(..., description="界面法线，方向指向下一介质")
    refractive_index: float = Field(..., gt=0, description="越过该界面后下一介质的折射率 n")

    @field_validator("normal")
    @classmethod
    def _normal_nonzero(cls, v: Vec2) -> Vec2:
        if v.x * v.x + v.y * v.y < EPS:
            raise ValueError("法线不能为零向量")
        return v


class TraceRequest(BaseModel):
    origin: Vec2 = Field(..., description="初始光线起点")
    direction: Vec2 = Field(..., description="初始光线方向（无需归一化）")
    initial_refractive_index: float = Field(..., gt=0, description="起始介质折射率 n0")
    surfaces: List[Surface] = Field(..., min_length=1, description="按顺序排列的介质界面")

    @field_validator("direction")
    @classmethod
    def _direction_nonzero(cls, v: Vec2) -> Vec2:
        if v.x * v.x + v.y * v.y < EPS:
            raise ValueError("方向不能为零向量")
        return v


class SegmentOut(BaseModel):
    segment_index: int
    start: Vec2
    end: Optional[Vec2]
    direction: Vec2
    refractive_index: float
    status: str = Field(description="transmitted / total_internal_reflection / no_intersection")


class TraceResponse(BaseModel):
    trace_id: int
    status: str
    reason: str
    segment_count: int
    segments: List[SegmentOut]


# --------------------------------------------------------------------------- #
# 向量与几何
# --------------------------------------------------------------------------- #
def _norm(v: Vec2) -> float:
    return math.hypot(v.x, v.y)


def _normalize(v: Vec2) -> Vec2:
    n = math.hypot(v.x, v.y)
    return Vec2(x=v.x / n, y=v.y / n)


def _dot(a: Vec2, b: Vec2) -> float:
    return a.x * b.x + a.y * b.y


def _add(a: Vec2, b: Vec2) -> Vec2:
    return Vec2(x=a.x + b.x, y=a.y + b.y)


def _scale(a: Vec2, s: float) -> Vec2:
    return Vec2(x=a.x * s, y=a.y * s)


def _sub(a: Vec2, b: Vec2) -> Vec2:
    return Vec2(x=a.x - b.x, y=a.y - b.y)


def _ray_line_intersect(origin: Vec2, direction: Vec2, point: Vec2, normal: Vec2):
    """光线 O + t·d 与过 point、法向 normal 的直线求交。

    返回正的参数 t；光线与界面平行（无交点）返回 None。
    """
    denom = _dot(direction, normal)
    if abs(denom) < EPS:
        return None
    t = _dot(_sub(point, origin), normal) / denom
    if t <= EPS:
        return None
    return t


def _refract(incident: Vec2, normal: Vec2, n1: float, n2: float):
    """斯涅尔定律折射。

    normal 指向光线将要进入的一侧。返回 (折射方向单位向量, None)；
    若发生全反射，返回 (None, 临界原因描述)。
    """
    # 保证 normal 与入射方向反向（即指向入射侧），便于套用标准公式
    n = normal
    cos_i = -_dot(incident, n)
    if cos_i < 0.0:
        n = Vec2(x=-normal.x, y=-normal.y)
        cos_i = -_dot(incident, n)
    if cos_i > 1.0:
        cos_i = 1.0

    ratio = n1 / n2
    sin2_t = ratio * ratio * (1.0 - cos_i * cos_i)
    if sin2_t > 1.0:
        if n1 <= n2:
            # 数值误差导致的轻微越界
            sin2_t = 1.0
        else:
            critical = math.degrees(math.asin(n2 / n1)) if n2 < n1 else float("nan")
            return None, (
                f"在界面处发生全反射：n1={n1:g} -> n2={n2:g}，"
                f"入射角超过临界角 {critical:.4f}°"
            )

    cos_t = math.sqrt(max(0.0, 1.0 - sin2_t))
    # t = η·i + (η·cos_i − cos_t)·n ，其中 n 指向入射侧
    refracted = _add(
        _scale(incident, ratio),
        _scale(n, ratio * cos_i - cos_t),
    )
    return _normalize(refracted), None


# --------------------------------------------------------------------------- #
# SQLite
# --------------------------------------------------------------------------- #
def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS traces (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                status      TEXT NOT NULL,
                reason      TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS segments (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id         INTEGER NOT NULL,
                segment_index    INTEGER NOT NULL,
                start_x          REAL NOT NULL,
                start_y          REAL NOT NULL,
                end_x            REAL,
                end_y            REAL,
                dir_x            REAL NOT NULL,
                dir_y            REAL NOT NULL,
                refractive_index REAL NOT NULL,
                status           TEXT NOT NULL,
                UNIQUE (trace_id, segment_index),
                FOREIGN KEY (trace_id) REFERENCES traces (id) ON DELETE CASCADE
            );
            """
        )


def get_db():
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# 追迹核心
# --------------------------------------------------------------------------- #
def _build_segment(row: sqlite3.Row) -> SegmentOut:
    end = None
    if row["end_x"] is not None and row["end_y"] is not None:
        end = Vec2(x=row["end_x"], y=row["end_y"])
    return SegmentOut(
        segment_index=row["segment_index"],
        start=Vec2(x=row["start_x"], y=row["start_y"]),
        end=end,
        direction=Vec2(x=row["dir_x"], y=row["dir_y"]),
        refractive_index=row["refractive_index"],
        status=row["status"],
    )


def run_trace(req: TraceRequest, conn: sqlite3.Connection) -> TraceResponse:
    origin = req.origin
    direction = _normalize(req.direction)
    n_current = req.initial_refractive_index

    segments: list[dict] = []
    status = "transmitted"
    reason = "光线穿过全部界面"

    for idx, surface in enumerate(req.surfaces):
        normal = _normalize(surface.normal)
        n_next = surface.refractive_index

        t = _ray_line_intersect(origin, direction, surface.point, normal)
        if t is None:
            # 与该界面无“前向”交点：沿当前方向无限延伸后停止
            segments.append(
                dict(
                    segment_index=idx,
                    start=origin,
                    end=None,
                    direction=direction,
                    refractive_index=n_current,
                    status="no_intersection",
                )
            )
            status = "no_intersection"
            reason = f"光线与第 {idx} 号界面在前进方向上没有交点（平行或交点位于光线后方）"
            break

        hit = Vec2(x=origin.x + t * direction.x, y=origin.y + t * direction.y)

        refracted, tir_reason = _refract(direction, normal, n_current, n_next)
        if refracted is None:
            # 全反射：记录到该界面的入射段后停止（不继续追迹反射光）
            segments.append(
                dict(
                    segment_index=idx,
                    start=origin,
                    end=hit,
                    direction=direction,
                    refractive_index=n_current,
                    status="total_internal_reflection",
                )
            )
            status = "total_internal_reflection"
            reason = f"追迹在第 {idx} 号界面停止：{tir_reason}"
            break

        segments.append(
            dict(
                segment_index=idx,
                start=origin,
                end=hit,
                direction=direction,
                refractive_index=n_current,
                status="transmitted",
            )
        )
        origin = hit
        direction = refracted
        n_current = n_next
    else:
        # 穿过所有界面：记录最终介质中的开放段
        segments.append(
            dict(
                segment_index=len(req.surfaces),
                start=origin,
                end=None,
                direction=direction,
                refractive_index=n_current,
                status="transmitted",
            )
        )

    cur = conn.execute(
        "INSERT INTO traces (status, reason) VALUES (?, ?)", (status, reason)
    )
    trace_id = cur.lastrowid
    conn.executemany(
        """
        INSERT INTO segments
            (trace_id, segment_index, start_x, start_y, end_x, end_y,
             dir_x, dir_y, refractive_index, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                trace_id,
                s["segment_index"],
                s["start"].x,
                s["start"].y,
                None if s["end"] is None else s["end"].x,
                None if s["end"] is None else s["end"].y,
                s["direction"].x,
                s["direction"].y,
                s["refractive_index"],
                s["status"],
            )
            for s in segments
        ],
    )
    conn.commit()

    return TraceResponse(
        trace_id=trace_id,
        status=status,
        reason=reason,
        segment_count=len(segments),
        segments=[
            SegmentOut(
                segment_index=s["segment_index"],
                start=s["start"],
                end=s["end"],
                direction=s["direction"],
                refractive_index=s["refractive_index"],
                status=s["status"],
            )
            for s in segments
        ],
    )


# --------------------------------------------------------------------------- #
# 路由
# --------------------------------------------------------------------------- #
@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/traces", response_model=TraceResponse, status_code=201, tags=["traces"])
def create_trace(req: TraceRequest, conn: sqlite3.Connection = Depends(get_db)):
    return run_trace(req, conn)


@app.get("/api/traces", response_model=List[int], tags=["traces"])
def list_traces(conn: sqlite3.Connection = Depends(get_db)):
    rows = conn.execute("SELECT id FROM traces ORDER BY id").fetchall()
    return [r["id"] for r in rows]


@app.get("/api/traces/{trace_id}", response_model=TraceResponse, tags=["traces"])
def get_trace(trace_id: int, conn: sqlite3.Connection = Depends(get_db)):
    trace = conn.execute(
        "SELECT * FROM traces WHERE id = ?", (trace_id,)
    ).fetchone()
    if trace is None:
        raise HTTPException(status_code=404, detail=f"追迹编号 {trace_id} 不存在")
    rows = conn.execute(
        "SELECT * FROM segments WHERE trace_id = ? ORDER BY segment_index",
        (trace_id,),
    ).fetchall()
    return TraceResponse(
        trace_id=trace["id"],
        status=trace["status"],
        reason=trace["reason"],
        segment_count=len(rows),
        segments=[_build_segment(r) for r in rows],
    )


@app.get(
    "/api/traces/{trace_id}/segments/{segment_index}",
    response_model=SegmentOut,
    tags=["traces"],
)
def get_segment(trace_id: int, segment_index: int, conn: sqlite3.Connection = Depends(get_db)):
    if conn.execute("SELECT 1 FROM traces WHERE id = ?", (trace_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail=f"追迹编号 {trace_id} 不存在")
    row = conn.execute(
        "SELECT * FROM segments WHERE trace_id = ? AND segment_index = ?",
        (trace_id, segment_index),
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"追迹 {trace_id} 中不存在段编号 {segment_index}",
        )
    return _build_segment(row)
