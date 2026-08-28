# Codex / Hermes HTTP 执行端交接报告

## 1. 交接目标

本文档用于把当前项目的 Hermes HTTP 联调能力交给 Codex、Hermes 或其他接入 Agent 使用。

本阶段目标是先验证以下交互链路：

```text
Hermes 接收客户邮件
  -> Hermes 解析邮件并展示待确认信息
  -> 调用创建渠道接口
  -> 获取实际渠道名并回填
  -> Hermes 再次询问是否继续创建应用
  -> 调用创建应用接口
  -> 查询原始执行任务
  -> 向操作人员返回执行结果
```

当前执行端为 `TEST/FAKE` 模式，不会打开浏览器，不会登录 139 后台，也不会创建真实渠道、应用或真实长链接。

## 2. Git 交接位置

仓库：

```text
https://github.com/geocityplanning/peizhilianjie.git
```

联调分支：

```text
feat/hermes-http-integration
```

分支地址：

```text
https://github.com/geocityplanning/peizhilianjie/tree/feat/hermes-http-integration
```

Codex/Hermes 应从上述分支读取，不要默认使用 `main`，也不要调用当前旧前端接口作为 Hermes 执行接口。

## 3. 当前代码位置

HTTP 服务：

```text
app/http_executor/main.py
app/http_executor/api.py
app/http_executor/service.py
app/http_executor/store.py
app/http_executor/models.py
app/http_executor/settings.py
```

契约和样例：

```text
contracts/hermes-http-v1.md
contracts/hermes-http-v1.openapi.yaml
contracts/examples/create-channel-request.json
contracts/examples/create-app-request.json
contracts/examples/query-request.json
```

测试：

```text
tests/test_http_executor.py
```

真实浏览器自动化仍位于：

```text
app/executor/
```

第一阶段 HTTP 联调层不会直接修改或调用 `create_app_v2.py`。

## 4. 对外只开放四个能力

| 能力 | HTTP 方法 | 路径 |
|---|---|---|
| `exec.get_info` | GET | `/v1/exec/info` |
| `exec.create_channel` | POST | `/v1/exec/create-channel` |
| `exec.create_app` | POST | `/v1/exec/create-app` |
| `exec.query_execution` | POST | `/v1/exec/query` |

不要通过 HTTP 暴露任意 Python 函数、任意脚本、任意页面操作、释放锁、登录、定位应用或修改应用接口。

当前旧接口，例如 `/api/parse-email`、`/api/channels`、`/api/apps`，属于操作人员前端，不属于本交接协议。

## 5. 启动方式

工作目录：

```text
F:\卓望\配链接项目\codex
```

初始化执行端专用 SQLite：

```powershell
uv run python -m app.http_executor.init_db
```

设置联调环境并启动：

```powershell
$env:HERMES_EXECUTOR_TOKEN = "change-this-local-token"
$env:HERMES_EXECUTOR_ENV = "TEST"
$env:HERMES_EXECUTOR_MODE = "FAKE"

uv run uvicorn app.http_executor.main:app --host 127.0.0.1 --port 8001
```

Hermes 使用以下连接信息：

```text
Base URL: http://127.0.0.1:8001
Authorization: Bearer change-this-local-token
Content-Type: application/json
```

当前服务绑定 `127.0.0.1`，只适合 Hermes 和执行端在同一台电脑上的联调。不要把 `127.0.0.1` 当成其他电脑可以访问的地址。

如果 SQLite 文件不存在，服务应报数据库不可用，而不是把执行端错误地判断为空闲。必须先执行初始化命令。

## 6. 通用请求信封

创建渠道和创建应用都必须使用以下字段：

```json
{
  "contract_version": "http-executor.v1",
  "task_id": "HERMES-20260828-0001",
  "operation": "exec.create_channel",
  "environment": "TEST",
  "idempotency_key": "HERMES-20260828-0001-create-channel",
  "input": {}
}
```

字段规则：

| 字段 | 责任方 | 规则 |
|---|---|---|
| `contract_version` | 双方 | 当前固定为 `http-executor.v1` |
| `task_id` | Hermes | 一个业务任务的稳定编号 |
| `operation` | Hermes | 必须和实际接口匹配 |
| `environment` | Hermes/执行端 | 当前固定为 `TEST` |
| `idempotency_key` | Hermes | 同一业务动作重复请求必须保持不变 |
| `input` | Hermes | 经过 Hermes 解析和人工确认的结构化字段 |

Hermes 不能在每次重试时重新生成 `task_id` 或 `idempotency_key`。

## 7. 创建渠道

请求：

```http
POST /v1/exec/create-channel
```

```json
{
  "contract_version": "http-executor.v1",
  "task_id": "HERMES-20260828-0001",
  "operation": "exec.create_channel",
  "environment": "TEST",
  "idempotency_key": "HERMES-20260828-0001-create-channel",
  "input": {
    "requested_channel_name": "甘肃体验有礼掌厅瀑布流"
  }
}
```

当前 Fake 结果会模拟渠道重名后缀：

```json
{
  "contract_version": "http-executor.v1",
  "accepted": true,
  "execution_id": "EXEC-001",
  "task_id": "HERMES-20260828-0001",
  "operation": "exec.create_channel",
  "environment": "TEST",
  "idempotency_key": "HERMES-20260828-0001-create-channel",
  "execution_state": "SUCCEEDED",
  "business_status": "SIMULATED_SUCCESS",
  "data": {
    "simulated": true,
    "requested_channel_name": "甘肃体验有礼掌厅瀑布流",
    "actual_channel_name": "甘肃体验有礼掌厅瀑布流-1",
    "base": "FAKE_BASE",
    "next_operator_action": "确认实际渠道名后再提交 exec.create_app"
  },
  "error": null,
  "evidence_ref": [],
  "next_action": "STOP"
}
```

Hermes 必须使用 `data.actual_channel_name`，不能继续使用原始渠道名，也不能再次重复创建渠道。

## 8. 创建应用

请求：

```http
POST /v1/exec/create-app
```

当前必填字段：

```text
application_type
business_object
actual_channel_name
jump_address
resource_fallback_page
settlement_type
```

示例：

```json
{
  "contract_version": "http-executor.v1",
  "task_id": "HERMES-20260828-0001",
  "operation": "exec.create_app",
  "environment": "TEST",
  "idempotency_key": "HERMES-20260828-0001-create-app",
  "input": {
    "application_type": "云盘",
    "business_object": "云盘",
    "actual_channel_name": "甘肃体验有礼掌厅瀑布流-1",
    "jump_address": "mcloud://main/webView?params=fake",
    "resource_fallback_page": "https://example.invalid/fallback",
    "settlement_type": "云盘",
    "download_link": "https://example.invalid/download",
    "fixed_fields": {}
  }
}
```

当前 Fake 结果示例：

```json
{
  "execution_state": "SUCCEEDED",
  "business_status": "SIMULATED_SUCCESS",
  "data": {
    "simulated": true,
    "application_id": "FAKE-APP-001",
    "application_name": "FAKE-APP-001",
    "actual_channel_name": "甘肃体验有礼掌厅瀑布流-1",
    "long_link": null,
    "short_link": null,
    "note": "Fake 模式不会创建真实应用，也不会生成真实长链接"
  }
}
```

`SIMULATED_SUCCESS` 不能被解释为真实后台创建成功。

## 9. 查询原始任务

请求：

```http
POST /v1/exec/query
```

优先使用 `execution_id`：

```json
{
  "contract_version": "http-executor.v1",
  "execution_id": "EXEC-001"
}
```

也可以使用原始幂等键：

```json
{
  "contract_version": "http-executor.v1",
  "idempotency_key": "HERMES-20260828-0001-create-app"
}
```

请求超时、连接断开或 Hermes 没有收到响应时，必须查询原任务，不能再次调用创建接口。

查询时如果同时提供 `execution_id` 和 `task_id`、`operation`、`environment`、`idempotency_key`，这些字段必须属于同一条记录，否则返回 `QUERY_ASSOCIATION_CONFLICT`。

## 10. 状态和错误处理

执行状态：

```text
ACCEPTED
RUNNING
SUCCEEDED
FAILED
UNKNOWN
REJECTED
```

推荐处理方式：

| 状态 | Hermes 行为 |
|---|---|
| `ACCEPTED` | 保存 `execution_id`，按策略查询 |
| `RUNNING` | 展示执行阶段或继续查询 |
| `SUCCEEDED` | 读取 `data` 并结束任务 |
| `FAILED` | 展示错误，进入人工检查或后续策略 |
| `UNKNOWN` | 查询原任务，不得自动重做 |
| `REJECTED` | 不产生真实副作用，处理输入、鉴权或占用问题 |

常见错误码：

```text
SCHEMA_INVALID
UNSUPPORTED_VERSION
OPERATION_MISMATCH
ENVIRONMENT_MISMATCH
MISSING_EXECUTION_IDENTITY
MISSING_REQUIRED_FIELD
EXECUTOR_BUSY
IDEMPOTENCY_CONFLICT
EXECUTION_NOT_FOUND
QUERY_ASSOCIATION_CONFLICT
EXECUTOR_DATABASE_UNAVAILABLE
```

HTTP 200 不等于业务成功，必须同时读取 `execution_state` 和 `business_status`。

## 11. Hermes 和执行端的职责边界

Hermes 负责：

- 接收原始客户邮件；
- 解析渠道名、应用类型、长链接、兜底链接等；
- 生成候选结果；
- 请求操作人员确认；
- 处理渠道重名确认；
- 决定是否继续创建应用；
- 组织批量任务；
- 在断线或超时时查询原始任务。

HTTP 执行端负责：

- 请求结构校验；
- 鉴权；
- 幂等判断；
- SQLite 执行记录；
- 单执行锁；
- Fake 或真实执行器调用；
- 返回完整执行信封。

浏览器自动化执行端负责：

- 进入页面；
- 操作 Tab、输入框、下拉框和按钮；
- 保存、发布、分组；
- 读取真实应用结果；
- 产生截图和诊断证据。

## 12. 对压缩包 E01-E10 的响应

压缩包要求的 63 个技术用例和 21 个业务场景，当前不能写整体通过。

| 分项 | 当前响应 | 当前状态 |
|---|---|---|
| E01 模板/新建入口 | HTTP 层可传 `application_type`，但真实模板规则尚未接入；后续直接新建后需重新定义 E01 | `NOT_RUN` |
| E02 应用创建控制 | Fake 层已校验当前必填字段；真实 DOM 填写、保存和读回尚未接入 | `NOT_RUN` |
| E03 字段映射 | 已预留 `fixed_fields`，云盘和中国移动固定字段、token 和快照规则尚未冻结 | `NOT_RUN` |
| E04 执行入口隔离 | 新 HTTP 入口不启动旧 `app.main`、旧队列或旧自动重试；真实重启、超时和未知状态仍未验收 | `部分实现，NOT_RUN` |
| E05 返回协议 | 已有完整响应信封、执行 ID、幂等键和查询；真实执行端返回尚未接入 | `部分实现，NOT_RUN` |
| E06 幂等与并发 | 已有 SQLite 唯一幂等键、原子预留和基础测试；跨进程压力测试未完成 | `部分实现，NOT_RUN` |
| E07 环境稳定性 | Windows 本机 Fake HTTP 已启动验证；Mac、真实浏览器和 Profile 尚未测试 | `NOT_RUN，Mac BLOCKED` |
| E08 SQLite 一致性 | 新 HTTP 服务要求显式初始化数据库且不把缺库当空闲；旧前端数据库与执行库边界仍需后续验收 | `部分实现，NOT_RUN` |
| E09 权限与能力边界 | 已有 Bearer Token 和四能力路由；生产鉴权、并发信息查询和完整脱敏尚未验收 | `部分实现，NOT_RUN` |
| E10 HTTP 稳定性 | 已完成本机 socket 的 info、create-channel、query 联调；断线、坏 JSON、长任务和服务重启矩阵未完成 | `部分实现，NOT_RUN` |

## 13. 已完成的证据

已执行：

```powershell
uv run python -m pytest -q
```

结果：

```text
9 passed
```

已完成一次本机真实 HTTP 进程联调：

```text
GET  /v1/exec/info              200 OK
POST /v1/exec/create-channel    200 OK
POST /v1/exec/query             200 OK
```

该证据只证明 Hermes HTTP Fake 联调层可用，不证明真实浏览器自动化或 E01-E10 验收通过。

## 14. 当前明确未完成项

- 没有接入真实 `app/executor` 浏览器操作；
- 没有打开真实浏览器或检查真实登录状态；
- 没有创建真实渠道和应用；
- 没有生成真实长链接；
- 没有完成云盘和中国移动固定字段配置；
- 没有完成 Mac 环境验证；
- 没有完成 Hermes 实际 Agent 调用验证；
- 没有完成 Excel 批量任务；
- 新增联调代码尚需独立提交并持续维护；
- 真实执行接入前必须重新执行 E01-E10 对应用例。

## 15. 后续实施顺序

```text
1. Hermes 使用 TEST/FAKE 接口完成对话联调
2. 确认 Hermes 任务 ID、幂等键和确认状态管理
3. 接入真实创建渠道自动化
4. 自动化同事完成直接新建应用流程
5. 接入真实创建应用并补充阶段证据
6. 执行 E04-E10 的 HTTP、幂等、并发和环境测试
7. 执行 E01-E03 的真实业务验收
8. 最后接入 Excel 批量编排
```

任何 Agent 在修改真实自动化之前，必须先保留当前 Hermes HTTP 契约，除非同步更新版本号、OpenAPI、示例和测试。

