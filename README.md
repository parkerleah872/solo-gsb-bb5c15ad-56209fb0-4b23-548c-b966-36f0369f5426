# 2D 介质光线追迹 API

基于 **Python + FastAPI + SQLite** 的二维光线追迹服务：提交初始光线（起点、方向）、起始介质折射率，以及按顺序排列的界面（界面上一点、指向下一介质的法线、下一介质折射率），服务逐界面计算前向交点并依斯涅尔定律更新方向；遇到全反射（或光线与界面平行 / 交点在反方向）即停止并返回原因。追迹结果保存到 SQLite，可按编号查询各段交点与方向。

## 启动

```bash
docker compose up --build
```

启动时会安装依赖并自动建库建表（SQLite 文件存于命名卷 `raytrace-data` 中）。

- **API 地址**：http://localhost:8000
- **交互式文档（Swagger UI）**：http://localhost:8000/docs
- **端口**：`8000`

本地开发（不用 Docker）：

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/traces` | 提交光线与界面序列，执行追迹、保存并返回完整结果 |
| GET | `/api/traces` | 列出所有已保存追迹的摘要 |
| GET | `/api/traces/{trace_id}` | 按编号查询完整追迹（含各段交点与方向） |
| GET | `/api/traces/{trace_id}/segments/{seg_index}` | 按编号查询某一段的交点与方向 |

### 请求示例

```bash
curl -X POST http://localhost:8000/api/traces \
  -H "Content-Type: application/json" \
  -d '{
    "start": {"x": 0, "y": 0},
    "direction": {"x": 1, "y": 0.5},
    "n_start": 1.0,
    "interfaces": [
      {"point": {"x": 2, "y": 0}, "normal": {"x": 1, "y": 0}, "n_next": 1.5},
      {"point": {"x": 4, "y": 0}, "normal": {"x": 1, "y": 0}, "n_next": 1.0}
    ]
  }'
```

### 响应说明

- `status`：`completed`（穿过所有界面）、`total_internal_reflection`（全反射停止）、`no_intersection`（与界面平行或交点在反方向）
- `reason`：停止原因（完成时为 `null`）
- `segments[]`：每段包含 `origin`（段起点）、`direction`（段方向）、`n_medium`（所在介质折射率）、`hit_point`（与下一界面的交点，出射段为 `null`）、`event`（`refraction` / `total_internal_reflection` / `no_intersection` / `null`）

### 全反射示例

```bash
curl -X POST http://localhost:8000/api/traces \
  -H "Content-Type: application/json" \
  -d '{
    "start": {"x": 0, "y": 0},
    "direction": {"x": 1, "y": 2},
    "n_start": 1.5,
    "interfaces": [
      {"point": {"x": 2, "y": 0}, "normal": {"x": 1, "y": 0}, "n_next": 1.0}
    ]
  }'
# status = "total_internal_reflection"，reason 说明入射角超过临界角
```

## 项目结构

```
app/
  main.py       FastAPI 路由与生命周期（启动时建库）
  geometry.py   二维求交与斯涅尔折射计算
  db.py         SQLite 持久化（traces / segments 两表）
  schemas.py    Pydantic 请求 / 响应模型
Dockerfile
docker-compose.yml
requirements.txt
```
