# 山火事件指挥与离线人员调度

维护火线、风向、资源和任务区，合并离线现场记录并防止人员重复分配。

## 模块结构

- `app.py`：参数解析、依赖组装和HTTP服务启动。
- `src/domain.py`：数据结构、错误、状态和基础校验。
- `src/rules.py`：状态机、角色矩阵、优先级、期限和关闭不变量。
- `src/offline.py`：离线补录合并，观测时刻判定、重放处理和批次汇总。
- `src/allocation.py`：资源分配规则和活动任务唯一性约束。
- `src/repository.py`：SQLite建表、事务、版本控制和审计链。
- `src/service.py`：权限检查、用例编排、并发控制和审计。
- `src/http_api.py`：JSON路由和统一错误响应。
- `src/audit.py`：UTC时间和SHA-256审计事件。
- `static/index.html`：最小演示页。
- `tests/`：完整流程、规则、失败、离线合并和资源分配测试。

## 初始化与启动

```bash
python3 app.py --db ./data.db --port 8319
```

默认端口为`8319`，首次启动自动建库，老库自动补列迁移。使用`X-Actor`和`X-Role`请求头传递身份。

## 主要接口

- `GET /health`
- `GET /api/items`
- `POST /api/items`
- `GET /api/items/{id}`
- `POST /api/items/{id}/records`
- `POST /api/items/{id}/records/merge`，离线补录合并
- `POST /api/items/{id}/transition`，必须提交`expected_version`
- `GET /api/items/{id}/allocations`
- `POST /api/items/{id}/allocations`
- `POST /api/items/{id}/allocations/{id}/release`
- `GET /api/audit`

允许角色：field_commander, incident_commander, logistics, viewer。火线长度、风向变化和离线记录数量影响风险等级；同一资源不能同时出现在多个活动任务中。

## 离线补录合并

断网期间巡线员在本机记录观测，回到指挥车后通过`POST /api/items/{id}/records/merge`批量补录。每条记录必须带`record_no`（编号）、`segment`（火线段）、`observed_at`（观测时刻，ISO-8601）和`risk_summary`（风险摘要），可选`kind`、`status`、`severity`、`quantity`。

- 同编号重放返回第一次受理结果（`outcome=replayed`），不重复写入、不重复计入风险。
- 观测时刻不晚于当前最新观测的记录只归档（`outcome=archived`），不改现值。
- 更新的观测受理后（`outcome=applied`）刷新事件现值，并按最新观测重算优先级和响应时限。
- 未闭合事项（`status=open`）继续挡住事件关闭，每次合并逐条写入审计链。

## 测试

```bash
python3 -m unittest discover -s tests -v
```
