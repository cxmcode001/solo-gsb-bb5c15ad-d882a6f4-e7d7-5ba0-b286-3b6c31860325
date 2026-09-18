# 风机叶片裂纹复核 API

基于 **Python + FastAPI + SQLite** 的单容器服务：接收风机/叶片的多次巡检观察，
把径向位置与顺时针钟位范围均相交的观察归入同一连通簇，合并成带完整来源清单的
裂纹轨迹，并自动标出等级冲突。钟位区间支持跨越 12 点（例如 11 → 1）。

## 一条命令启动

需要本机已安装 Docker 与 Docker Compose Plugin（`docker compose`）。

```bash
docker compose up -d --build
```

启动即自动完成：安装依赖、创建 SQLite 数据库及全部表结构（首次启动建库）。
数据持久化在 Docker 命名卷 `blade-crack-data`（容器内 `/data/cracks.db`）中，
重复启动数据保留；`docker compose down -v` 会连数据卷一并删除。

服务地址与端口：

* API Base URL：`http://localhost:8000`
* Swagger 交互文档：`http://localhost:8000/docs`
* ReDoc 文档：`http://localhost:8000/redoc`
* 健康检查：`http://localhost:8000/health`

## 必要配置（均可缺省）

通过 `docker-compose.yml` 的 `environment` 或环境变量配置，全部为可选项：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DB_PATH` | `./data/cracks.db`（容器内为 `/data/cracks.db`） | SQLite 数据库文件路径，启动时自动建库建表 |
| `GRADE_MIN` | `1` | 允许的最小裂纹等级 |
| `GRADE_MAX` | `4` | 允许的最大裂纹等级 |

容器固定监听 **8000** 端口；如需改用宿主机其他端口，修改
`docker-compose.yml` 中的端口映射（如 `"9000:8000"`）即可。

本地裸机运行（可选，不用 Docker）：

```bash
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 数据模型与聚类规则

每条观察包含：

* `turbine` 风机编号、`blade` 叶片编号、`obs_key` **批内唯一**观察编号；
* `r_start` / `r_end`：距叶根的起止位置（米，闭区间，且 `r_end >= r_start`）；
* `clock_start` / `clock_end`：顺时针钟位区间，取值 `[0, 12]`，`0` 与 `12`
  都表示 12 点。区间按顺时针方向解释，允许跨越 12 点；两端相同表示一个点；
* `grade`：裂纹等级（默认 1~4，可由 `GRADE_MIN/MAX` 调整）；
* `confidence`：置信度，`[0, 1]`；
* `observed_at`（可选，ISO 8601；缺省取服务端写入时刻）、`source_code`
  （可选，缺省按提交顺序生成 `#0001`、`#0002` ……）。

**连通簇判定（同一风机 + 同一叶片内）**：观察 A、B 的径向闭区间相交，
**且**顺时针钟位弧在圆环上相交（端点相接、跨 12 点、点落于区间内均算相交），
则两观察相连；对该二元关系求连通分量（并查集）得到裂纹簇。

**轨迹合并**：每个连通簇输出一条裂纹轨迹——

* 径向范围：所有来源观察径向范围的最小起点与最大终点；
* 钟位范围：所有来源钟位弧在圆环上求并，输出最简弧段列表，每段含
  `start`、`end`、`wraps`；`wraps=true` 表示跨越 12 点，此时 `end` 取下一圈
  数值（如 11 → 1 输出 `{"start": 11.0, "end": 13.0, "wraps": true}`）；
  覆盖整圈时输出 `{"start": 0, "end": 12, "wraps": false}`；
* `grade_conflict`：簇内出现多于一个等级即为 `true`；
* `grade_confidence`：各等级的置信度合计；轨迹 `grade` 取合计置信度最高的
  等级（并列时取较小等级），供人工复核参考；
* `sources`：贡献该轨迹的全部观察清单（观察 ID、批次、径向/钟位范围、
  等级、置信度、巡检时间、来源编码）；
* `cluster_key`：由成员身份（`batch_id:obs_key`）哈希得到，幂等重算后稳定。

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| PUT | `/api/batches/{batch_id}/observations` | **观察写入（幂等）**：整批覆盖该批次，并自动重算所有受影响叶片 |
| POST | `/api/batches/{batch_id}/recompute` | **按批次幂等重算**（批次不存在返回 404） |
| POST | `/api/recompute` | 重算数据库中全部叶片 |
| GET | `/api/cracks` | **复核结果查询**，可按 `turbine`、`blade` 过滤，`only_conflicts=true` 只看冲突 |
| GET | `/api/observations` | 查询已写入观察，可按 `turbine` / `blade` / `batch_id` 过滤 |
| GET | `/api/batches/{batch_id}` | 批次元信息（创建/更新时间、观察数） |
| GET | `/health` | 健康检查 |

### 1）写入观察（整批幂等）

重复提交同一个 `batch_id` 会**整体替换**该批次内容，并对新旧批次涉及的全部
叶片重算；其他批次的数据不受影响。

```bash
curl -X PUT http://localhost:8000/api/batches/flight-2026-09-18/observations \
  -H 'Content-Type: application/json' \
  -d '{
    "observations": [
      {"obs_key":"o1","turbine":"WT-07","blade":"B1",
       "r_start":1.0,"r_end":3.2,"clock_start":11.4,"clock_end":0.6,
       "grade":2,"confidence":0.70,"source_code":"drone-A"},
      {"obs_key":"o2","turbine":"WT-07","blade":"B1",
       "r_start":2.8,"r_end":4.5,"clock_start":0.2,"clock_end":0.5,
       "grade":4,"confidence":0.95,"source_code":"drone-A"},
      {"obs_key":"o3","turbine":"WT-07","blade":"B1",
       "r_start":8.0,"r_end":9.0,"clock_start":3,"clock_end":4,
       "grade":1,"confidence":0.60,"source_code":"drone-B"}
    ]
  }'
```

返回示例（o1 跨 12 点的弧与 o2 相交且径向重叠，归为一簇；o3 独立成簇）：

```json
{
  "batch_id": "flight-2026-09-18",
  "action": "created",
  "observations_written": 3,
  "blades_recomputed": 1,
  "clusters": 2,
  "recomputed_at": "2026-09-18T17:03:35.836055+00:00"
}
```

### 2）查询复核结果

```bash
curl 'http://localhost:8000/api/cracks?turbine=WT-07&blade=B1'
```

返回（节选）：

```json
[
  {
    "turbine": "WT-07",
    "blade": "B1",
    "cluster_key": "b31d008637188a4c",
    "r_start": 1.0,
    "r_end": 4.5,
    "clock_arcs": [{"start": 11.4, "end": 12.6, "wraps": true}],
    "grade": 4,
    "grade_conflict": true,
    "grade_confidence": [
      {"grade": 2, "confidence": 0.7},
      {"grade": 4, "confidence": 0.95}
    ],
    "sources": [ {"obs_key": "o1", "...": "..."}, {"obs_key": "o2", "...": "..."} ]
  }
]
```

只看存在等级冲突的轨迹：`GET /api/cracks?only_conflicts=true`。

### 3）按批次 / 全量幂等重算

```bash
curl -X POST http://localhost:8000/api/batches/flight-2026-09-18/recompute
curl -X POST http://localhost:8000/api/recompute
```

重算是确定性的：只要观察集合不变，聚类结果与 `cluster_key` 保持稳定；
重复调用安全。写入批次时也会自动重算受影响叶片，一般无需手动调用。

## 项目结构

```
app/
  config.py       # 环境变量配置（DB_PATH、等级范围等）
  geometry.py     # 钟位/径向几何：跨 12 点弧相交、圆环弧合并
  clustering.py   # 并查集连通簇、轨迹合并、等级冲突判定
  db.py           # SQLite 表结构、批次幂等写入、重算与查询
  schemas.py      # 请求/响应模型与参数校验
  main.py         # FastAPI 路由（启动时自动建表）
tests/            # 几何、聚类与端到端 API 测试（22 个用例）
Dockerfile
docker-compose.yml
requirements.txt
```

## 测试

```bash
pip install -r requirements.txt pytest httpx
python -m pytest -q
```
