# 山火事件指挥与离线人员调度

维护火线、风向、资源和任务区，合并离线现场记录并防止人员重复分配。

## 模块结构

- `app.py`：参数解析、依赖组装和HTTP服务启动。
- `src/domain.py`：数据结构、错误、状态和基础校验。
- `src/rules.py`：状态机、角色矩阵、优先级、期限和关闭不变量；资源分配重算（`recalculate_response`按最新观测输出优先级、响应时限和升级标志）。
- `src/offline_merge.py`：离线补录合并——观测校验、同编号幂等、乱序旧观测归档、现值推进决策。
- `src/audit.py`：审计写入——UTC时间和SHA-256审计事件。
- `src/repository.py`：SQLite建表、事务、版本控制和审计链。
- `src/service.py`：权限检查、用例编排、并发控制和审计。
- `src/http_api.py`：JSON路由和统一错误响应。
- `static/index.html`：最小演示页。
- `tests/`：完整流程、规则、失败和离线合并测试。

资源分配、离线合并和审计写入分别落在`src/rules.py`、`src/offline_merge.py`和`src/audit.py`三个业务源码中。

## 初始化与启动

```bash
python3 app.py --db ./data.db --port 8319
```

默认端口为`8319`，首次启动自动建库。使用`X-Actor`和`X-Role`请求头传递身份。

## 主要接口

- `GET /health`
- `GET /api/items`
- `POST /api/items`
- `GET /api/items/{id}`
- `POST /api/items/{id}/records`
- `POST /api/items/{id}/offline-merge`，离线补录合并
- `POST /api/items/{id}/transition`，必须提交`expected_version`
- `GET /api/audit`

允许角色：field_commander, incident_commander, logistics, viewer。火线长度、风向变化和离线记录数量影响风险等级；同一资源不能同时出现在多个活动任务中。

## 离线补录合并

断网期间记在本机的观测回到指挥车后通过`POST /api/items/{id}/offline-merge`集中补录，请求体为`{"records": [...]}`，每条观测带：

- `ref`：补录编号（同一事件内唯一，兼容`external_ref`字段名）
- `segment`：火线段
- `observed_at`：观测时刻（ISO-8601，归一化为UTC）
- `risk_summary`：风险摘要
- 可选`kind`、`status`（`open`/`closed`，默认`open`）、`severity`、`quantity`（观测到的最新风险等级和火线长度）

合并规则：

- 同编号重放返回第一次受理结果（原`record_id`和`outcome`，`replayed=true`），不重复写入、不重复计入风险。
- 观测时刻早于当前最新观测的旧观测只归档（`outcome=archived`），不改现值；最新观测推进`severity`、`quantity`和`last_observed_at`（`outcome=applied`）。
- 合并后按最新观测重算优先级和响应时限，并写入审计事件；未闭合事项（`status=open`）继续挡住关闭。

## 测试

```bash
python3 -m unittest discover -s tests -v
```
