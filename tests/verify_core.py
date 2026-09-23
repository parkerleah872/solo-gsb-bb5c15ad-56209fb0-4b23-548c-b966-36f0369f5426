"""不依赖 fastapi/pydantic 安装包，使用桩模块导入 app.main 验证核心逻辑。"""
import importlib
import math
import os
import sys
import tempfile
import types

# ---- pydantic 桩 ----
pyd = types.ModuleType("pydantic")


class BaseModel:
    def __init__(self, *args, **kw):
        if args:
            fields = []
            for klass in type(self).__mro__:
                fields.extend(getattr(klass, "__annotations__", {}).keys())
            for k, v in zip(fields, args):
                kw[k] = v
        for k, v in kw.items():
            setattr(self, k, v)

    def __repr__(self):
        return f"{type(self).__name__}({self.__dict__})"


def Field(default=None, **kw):
    return default


def field_validator(*a, **kw):
    def deco(fn):
        return classmethod(fn)
    return deco


pyd.BaseModel = BaseModel
pyd.Field = Field
pyd.field_validator = field_validator
sys.modules["pydantic"] = pyd

# ---- fastapi 桩 ----
fa = types.ModuleType("fastapi")


class HTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail


def Depends(fn):
    return fn


class _Router:
    def __getattr__(self, name):
        def deco(path, *a, **kw):
            def wrap(fn):
                return fn
            return wrap
        return deco


class FastAPI(_Router):
    def __init__(self, *a, **kw):
        pass

    def on_event(self, event):
        def wrap(fn):
            return fn
        return wrap


fa.FastAPI = FastAPI
fa.Depends = Depends
fa.HTTPException = HTTPException
sys.modules["fastapi"] = fa

# ---- 导入被测模块 ----
tmpdir = tempfile.mkdtemp()
os.environ["RAYTRACER_DB"] = os.path.join(tmpdir, "test.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
main = importlib.import_module("app.main")
main.init_db()

fails = []


def check(name, cond, extra=""):
    print(("PASS" if cond else "FAIL"), name, extra)
    if not cond:
        fails.append(name)


V = main.Vec2

# 1) 法线入射不偏折（空气->玻璃, n=1 -> 1.5）
i = V(0, -1)
n = V(0, 1)  # 指向下一介质（下方）
t, err = main._refract(i, n, 1.0, 1.5)
check("normal incidence no bend", err is None and abs(t.x) < 1e-12 and abs(t.y + 1) < 1e-12,
      f"t=({t.x},{t.y})")

# 2) 45度 空气->玻璃: sin_t = sin45/1.5
t, err = main._refract(main._normalize(V(1, -1)), V(0, 1), 1.0, 1.5)
sin_t_expected = math.sin(math.pi / 4) / 1.5
# 折射方向与法线夹角
cos_t = -t.y  # t 朝下
sin_t = t.x
check("air->glass snell", err is None and abs(sin_t - sin_t_expected) < 1e-9,
      f"sin_t={sin_t:.9f} expected={sin_t_expected:.9f}")
check("refracted toward normal", cos_t > math.cos(math.pi / 4))

# 3) 玻璃->空气 45度入射角 => TIR (临界角 asin(1/1.5)=41.81)
t, err = main._refract(main._normalize(V(1, 1)), V(0, 1), 1.5, 1.0)
check("TIR at 45 deg glass->air", t is None and "全反射" in err, err or "")

# 4) 玻璃->空气 30度入射角 => 折射，sin_t = 1.5*0.5 = 0.75
t, err = main._refract(main._normalize(V(math.sin(math.pi/6), math.cos(math.pi/6))),
                       V(0, 1), 1.5, 1.0)
check("glass->air 30deg refracts", err is None and abs(t.x - 0.75) < 1e-9 and abs(t.y - math.sqrt(1 - 0.75**2)) < 1e-9,
      f"t=({t.x},{t.y})")

# 5) 法线朝向另一侧也应自动处理（入射方向与法线同向时翻转）
t2, err2 = main._refract(main._normalize(V(1, -1)), V(0, -1), 1.0, 1.5)
check("normal auto-flip", err2 is None
      and abs(t2.x - sin_t_expected) < 1e-12
      and abs(t2.y + cos_t) < 1e-12)

# 6) 交点: 从 (-1,-0.5) 沿 (1,1)/sqrt2 到 y=0 界面
hit_t = main._ray_line_intersect(V(-1, -0.5), main._normalize(V(1, 1)), V(0, 0), V(0, 1))
check("intersection t", abs(hit_t - 0.5 * math.sqrt(2)) < 1e-12, f"t={hit_t}")

# 7) 平行光线无交点
check("parallel no intersection",
      main._ray_line_intersect(V(0, -1), V(1, 0), V(0, 0), V(0, 1)) is None)

# 8) 交点在光线后方 => None
check("behind no intersection",
      main._ray_line_intersect(V(0, 1), V(0, 1), V(0, 0), V(0, 1)) is None)

# ---- 端到端：全反射追迹入库 ----
conn = main._connect()
req = main.TraceRequest(
    origin=V(-1.0, -0.5),
    direction=V(1.0, 1.0),
    initial_refractive_index=1.5,
    surfaces=[
        main.Surface(point=V(0, 0), normal=V(0, 1), refractive_index=1.0),
    ],
)
resp = main.run_trace(req, conn)
check("TIR trace status", resp.status == "total_internal_reflection", resp.status)
check("TIR stops with 1 segment", resp.segment_count == 1)
seg = resp.segments[0]
# 交点应为 (-0.5, 0)
check("TIR hit point", abs(seg.end.x + 0.5) < 1e-9 and abs(seg.end.y) < 1e-9,
      f"end=({seg.end.x},{seg.end.y})")
check("TIR segment status", seg.status == "total_internal_reflection")

# 按编号查询
seg_q = main.get_segment(resp.trace_id, 0, conn)
check("query segment by index", seg_q.end.x == seg.end.x and seg_q.direction.x == seg.direction.x)

# 不存在的追迹 / 段
try:
    main.get_trace(9999, conn)
    check("404 missing trace", False)
except main.HTTPException as e:
    check("404 missing trace", e.status_code == 404)
try:
    main.get_segment(resp.trace_id, 7, conn)
    check("404 missing segment", False)
except main.HTTPException as e:
    check("404 missing segment", e.status_code == 404)

# ---- 端到端：普通折射，穿过后有开放段 ----
req2 = main.TraceRequest(
    origin=V(0, 1),
    direction=V(1, -1),
    initial_refractive_index=1.0,
    surfaces=[
        main.Surface(point=V(0, 0), normal=V(0, -1), refractive_index=1.5),
    ],
)
resp2 = main.run_trace(req2, conn)
check("transmission status", resp2.status == "transmitted")
check("2 segments (hit + open)", resp2.segment_count == 2)
s0, s1 = resp2.segments
check("hit at (1, 0)", abs(s0.end.x - 1.0) < 1e-12 and abs(s0.end.y) < 1e-12,
      f"end=({s0.end.x},{s0.end.y})")
check("open segment end null", s1.end is None and s1.refractive_index == 1.5)
check("bend toward normal", abs(s1.direction.x) < abs(s0.direction.x))
# 斯涅尔数值
check("transmitted sin", abs(s1.direction.x - math.sin(math.pi/4)/1.5) < 1e-9,
      f"dx={s1.direction.x}")

# ---- 多界面往返: 空气->玻璃->空气，出射方向应平行于入射方向 ----
req3 = main.TraceRequest(
    origin=V(0, 2),
    direction=V(1, -1),
    initial_refractive_index=1.0,
    surfaces=[
        main.Surface(point=V(0, 0), normal=V(0, -1), refractive_index=1.5),  # y=0, 法线指向下一介质(下方)
        main.Surface(point=V(0, -1), normal=V(0, -1), refractive_index=1.0),  # y=-1, 回到空气
    ],
)
resp3 = main.run_trace(req3, conn)
check("parallel slab status", resp3.status == "transmitted", resp3.reason)
last = resp3.segments[-1]
d0 = main._normalize(V(1, -1))
check("slab exit parallel to entry",
      abs(last.direction.x - d0.x) < 1e-9 and abs(last.direction.y - d0.y) < 1e-9,
      f"last dir=({last.direction.x},{last.direction.y})")

# ---- 平行界面无交点 ----
req4 = main.TraceRequest(
    origin=V(0, 1),
    direction=V(1, 0),
    initial_refractive_index=1.0,
    surfaces=[main.Surface(point=V(0, 0), normal=V(0, 1), refractive_index=1.5)],
)
resp4 = main.run_trace(req4, conn)
check("no_intersection status", resp4.status == "no_intersection", resp4.status)
check("no_intersection open end null", resp4.segments[0].end is None)

# ---- 列表接口 ----
ids = main.list_traces(conn)
check("list traces ids", set([resp.trace_id, resp2.trace_id, resp3.trace_id, resp4.trace_id]) <= set(ids))

print()
print("FAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
