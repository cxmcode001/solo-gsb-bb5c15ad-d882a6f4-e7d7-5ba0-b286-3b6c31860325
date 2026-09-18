# 风机叶片裂纹复核 API

基于 **Python + FastAPI + SQLite** 的风机叶片裂纹巡检观察复核服务：

- 接收**多次巡检**的裂纹观察：距叶根径向起止位置、**顺时针钟位区间**（允许跨越 12 点）、等级、置信度；
- 将**径向区间与钟位弧同时相交**的观察按连通关系聚为同一连通簇（并查集传递闭包）；
- 每个连通簇合并为一条**裂纹轨迹**：径向/钟位外包范围、等级与置信度汇总、**等级冲突标记**、完整**来源清单**；
- 提供**观察写入、复核结果查询、按批次幂等重算**三类接口；
- 依赖安装、SQLite 建表与数据库初始化均随容器启动自动完成。

## 单条命令启动

```bash
docker compose up -d --build
```

启动后：

| 项目 | 地址 |
| --- | --- |
| API 基址 | `http://localhost:8000` |
| 健康检查 | `GET http://localhost:8000/health` |
| Swagger 交互文档 | `http://localhost:8000/docs` |
| OpenAPI Schema | `http://localhost:8000/openapi.json` |

> 端口可通过环境变量覆盖，例如 `API_PORT=9000 docker compose up -d --build`，则访问 `http://localhost:9000`。

数据持久化在 Docker 命名卷 `crack-data`（容器内 `/data/crack_review.db`），容器重建不丢数据。

## 配置（均为可选项）

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `API_PORT` | `8000` | 宿主机映射端口（compose 层） |
| `CRACK_DB_PATH` | `/data/crack_review.db` | SQLite 数据库文件路径；启动时自动建库建表 |

除环境变量外无需任何额外配置：镜像构建时安装依赖，容器启动（FastAPI startup 钩子）时自动执行 `CREATE TABLE IF NOT EXISTS ...` 初始化。

## 钟位与聚类规则

- 钟面 0–12 小时；`0` 与 `12` 等价，均表示 **12 点**。
- 钟位区间沿**顺时针**从 `clock_start` 到 `clock_end`：
  - `1 → 3` 表示 1 点到 3 点的普通弧；
  - `11 → 1` 表示 **跨越 12 点** 的弧（11 点 → 12 点 → 1 点）；
  - `clock_start == clock_end` 表示单点。
- 两条观察**连通**当且仅当：
  1. 径向区间相交：`max(r_start) ≤ min(r_end)`（**端点相接算相交**，裂纹接续）；
  2. 钟位弧相交（环向，同样端点相接算相交）。
- 连通关系可传递（A–B、B–C 则 A、B、C 同簇）。
- 轨迹合并：径向取最小/最大外包；钟位弧在 [0,12] 上线性合并，首尾在 0/12 相接时还原为一条 `wraps_twelve=true` 的跨 12 点弧，全覆盖时 `full_circle=true`。
- **等级冲突**：簇内出现 2 个及以上不同等级时 `grade_conflict=true`；同时给出各等级的最高置信度 `grade_confidence`，代表等级 `representative_grade` 按“最高置信度优先、并列取更大等级（更保守）”确定。
- 轨迹 ID 由成员观察 ID 集合哈希得到，同输入重算保持稳定。

## API 一览

基址 `http://localhost:8000/api/v1`

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/observations/batch` | 按批次写入观察（幂等），默认写入后自动重算 |
| POST | `/reviews/recompute` | 按 `batch_id`（或 `turbine_id`+`blade_id`）幂等重算 |
| GET | `/reviews/{turbine_id}/{blade_id}` | 查询某叶片裂纹轨迹复核结果 |

### 1. 写入观察批次

`POST /api/v1/observations/batch`

```json
{
  "batch_id": "b-2026-09-18-01",
  "turbine_id": "WT-A01",
  "blade_id": "B2",
  "auto_recompute": true,
  "observations": [
    {
      "observation_id": "s1",
      "inspection_id": "drone-0901",
      "inspected_at": "2026-09-01T10:00:00Z",
      "radius_start": 2.0,
      "radius_end": 6.0,
      "clock_start": 11.0,
      "clock_end": 1.0,
      "grade": 2,
      "confidence": 0.82
    },
    {
      "observation_id": "s2",
      "inspection_id": "drone-0901",
      "inspected_at": "2026-09-01T10:00:00Z",
      "radius_start": 5.5,
      "radius_end": 9.0,
      "clock_start": 12.0,
      "clock_end": 2.0,
      "grade": 3,
      "confidence": 0.91
    }
  ]
}
```

字段约束：

| 字段 | 类型/范围 |
| --- | --- |
| `radius_start` / `radius_end` | 数值，米，`>= 0`，且 `end >= start` |
| `clock_start` / `clock_end` | `[0, 12]`，顺时针区间，允许跨 12 点 |
| `grade` | 整数 `1–5` |
| `confidence` | `[0, 1]` |
| `observation_id` | 可空，缺省服务端生成；同一叶片内唯一 |
| `auto_recompute` | 默认 `true` |

幂等语义：

- 相同 `batch_id` + **相同请求内容**重复提交 → `status="idempotent_replay"`，不重复写库（`written=0`）；
- 相同 `batch_id` + **不同内容** → `409 Conflict`。

响应中 `trajectories` 即写入后（可选）重算得到的轨迹，结构同查询接口。

### 2. 幂等重算

`POST /api/v1/reviews/recompute`

```json
{ "batch_id": "b-2026-09-18-01" }
```

或直接指定叶片：

```json
{ "turbine_id": "WT-A01", "blade_id": "B2" }
```

- 以该叶片**当前全部观察**重算（跨批次、跨巡检），事务内整体替换旧结果；
- 输入指纹未变化时不写库，返回 `changed=false`，可安全重试；
- `batch_id` 不存在返回 `404`。

### 3. 查询复核结果

`GET /api/v1/reviews/WT-A01/B2`

```json
{
  "turbine_id": "WT-A01",
  "blade_id": "B2",
  "up_to_date": true,
  "needs_recompute": false,
  "observation_count": 2,
  "trajectory_count": 1,
  "trajectories": [
    {
      "trajectory_id": "trj_...",
      "radius_start": 2.0,
      "radius_end": 9.0,
      "clock": {
        "full_circle": false,
        "span": 3.0,
        "ranges": [
          {
            "start": 11.0,
            "end": 2.0,
            "wraps_twelve": true,
            "start_label": "11点",
            "end_label": "2点"
          }
        ]
      },
      "max_confidence": 0.91,
      "grades": [2, 3],
      "grade_confidence": {"2": 0.82, "3": 0.91},
      "representative_grade": 3,
      "grade_conflict": true,
      "observation_count": 2,
      "sources": [
        { "observation_id": "s1", "inspection_id": "drone-0901", "grade": 2, "confidence": 0.82, "...": "该观察的完整字段与所属 batch_id" },
        { "observation_id": "s2", "inspection_id": "drone-0901", "grade": 3, "confidence": 0.91, "...": "..." }
      ]
    }
  ]
}
```

- `sources` 为构成轨迹的全部来源观察（含批次、巡检、原始范围、等级、置信度）。
- 观察有新增但尚未重算时，接口仍返回基于当前观察**实时计算**的轨迹，并以 `needs_recompute=true` / `up_to_date=false` 提示。

## curl 快速验证

```bash
curl -s http://localhost:8000/health
curl -s -X POST http://localhost:8000/api/v1/observations/batch \
  -H 'Content-Type: application/json' \
  -d @- <<'JSON'
{
  "batch_id": "demo-1",
  "turbine_id": "WT-A01",
  "blade_id": "B2",
  "observations": [
    {"observation_id":"s1","inspection_id":"drone-0901","radius_start":2.0,"radius_end":6.0,"clock_start":11.0,"clock_end":1.0,"grade":2,"confidence":0.82},
    {"observation_id":"s2","inspection_id":"drone-0901","radius_start":5.5,"radius_end":9.0,"clock_start":12.0,"clock_end":2.0,"grade":3,"confidence":0.91}
  ]
}
JSON
curl -s http://localhost:8000/api/v1/reviews/WT-A01/B2
```

## 本地开发（不使用 Docker）

```bash
pip install -r requirements.txt
export CRACK_DB_PATH=data/crack_review.db   # 可选，默认即此值
uvicorn app.main:app --reload --port 8000
```

运行测试（无需额外服务，使用临时 SQLite 文件）：

```bash
python -m unittest discover -s tests -v
```

## 项目结构

```
.
├── app/
│   ├── main.py         # FastAPI 入口、路由、startup 建表
│   ├── schemas.py      # Pydantic 请求/响应模型与字段校验
│   ├── services.py     # 写入幂等、重算、查询业务逻辑
│   ├── clustering.py   # 并查集聚类 + 轨迹合并
│   ├── geometry.py     # 径向/钟位弧相交与合并（含跨 12 点）
│   └── db.py           # SQLite 连接、schema 初始化、数据访问
├── tests/              # 几何 / 聚类 / 端到端 API 测试
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```
