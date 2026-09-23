# 二维介质光线追迹 API

基于 **Python + FastAPI + SQLite** 的二维光线追迹服务：给定光线起点、方向与起始介质折射率，
以及一组按顺序排列的介质界面（界面上一点、指向下一介质的法线、下一介质折射率），
服务依次计算光线与各界面的**前向交点**，按**斯涅尔定律**更新方向；
一旦发生**全反射（TIR）**立即停止并返回原因。每次追迹及各段（交点、方向、折射率）均存入 SQLite，支持按编号查询。

## 一条命令启动

```bash
docker compose up --build
```

启动时自动安装依赖并创建 SQLite 数据库（容器内 `/data/raytracer.db`，通过命名卷持久化）。

- API 地址与端口： **http://localhost:8000**
- 交互文档（Swagger UI）： **http://localhost:8000/docs**
- 健康检查： http://localhost:8000/health

> 本地（无 Docker）运行：`pip install -r requirements.txt && uvicorn app.main:app --port 8000`，
> 数据库路径可用环境变量 `RAYTRACER_DB` 覆盖（默认 `/data/raytracer.db`）。

## API

| 方法 | 路径 | 说明 |
| ---- | ---- | ---- |
| `POST` | `/api/traces` | 发起一次追迹并入库 |
| `GET` | `/api/traces` | 列出全部追迹编号 |
| `GET` | `/api/traces/{trace_id}` | 查询某次追迹的全部段 |
| `GET` | `/api/traces/{trace_id}/segments/{segment_index}` | 按段编号查询该段交点与方向 |

### 请求字段

- `origin`：初始光线起点 `{x, y}`
- `direction`：初始光线方向 `{x, y}`（无需归一化）
- `initial_refractive_index`：起始介质折射率 `n0`（>0）
- `surfaces`：按追迹顺序排列的界面数组
  - `point`：界面上一点 `{x, y}`（界面为经过该点的直线）
  - `normal`：界面法线，**方向指向下一介质**
  - `refractive_index`：越过界面后下一介质的折射率

每段记录：`segment_index`、`start`、`end`（到达的交点；最终开放段或无交点时为 `null`）、
`direction`（该段内传播方向）、`refractive_index`（所在介质折射率）、
`status`（`transmitted` / `total_internal_reflection` / `no_intersection`）。

### 示例：全反射

光在玻璃（n=1.5）中掠射向空气（n=1.0）界面，将发生全反射：

```bash
curl -s -X POST http://localhost:8000/api/traces \
  -H 'Content-Type: application/json' \
  -d '{
    "origin": {"x": -1.0, "y": -0.5},
    "direction": {"x": 1.0, "y": 1.0},
    "initial_refractive_index": 1.5,
    "surfaces": [
      {"point": {"x": 0.0, "y": 0.0},
       "normal": {"x": 0.0, "y": 1.0},
       "refractive_index": 1.0}
    ]
  }'
```

按编号查询第 0 段的交点与方向：

```bash
curl -s http://localhost:8000/api/traces/1/segments/0
```

普通折射（空气 → 玻璃）示例见 `/docs`，将请求体折射率换成 1.5、起点置于界面另一侧即可。

## 几何约定

- 界面为无限长直线：经过 `point`、法向为 `normal`。
- 只接受光线**前进方向**上的交点（参数 t>0）；平行或交点位于后方时该界面记为 `no_intersection` 并停止。
- 法线方向同时用于判断入射侧；服务内部会自动翻转法线以保证折射公式一致，因此只需保证法线指向“下一介质”。
