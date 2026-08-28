# Hermes HTTP Executor v1

这是第一阶段 Hermes 联调契约。当前只支持 `TEST/FAKE`，不会打开浏览器、登录 139 后台或创建真实渠道和应用。

## 启动

先初始化执行端专用 SQLite：

```powershell
uv run python -m app.http_executor.init_db
```

设置本地联调鉴权令牌后启动独立服务：

```powershell
$env:HERMES_EXECUTOR_TOKEN = "change-this-local-token"
$env:HERMES_EXECUTOR_ENV = "TEST"
$env:HERMES_EXECUTOR_MODE = "FAKE"
uv run uvicorn app.http_executor.main:app --host 127.0.0.1 --port 8001
```

服务不会复用 `app.main`、旧 `job_manager` 或旧的前端接口。

## 通用请求

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

请求头：

```text
Authorization: Bearer change-this-local-token
Content-Type: application/json
```

## 接口

| 能力 | 方法 | 路径 |
|---|---|---|
| `exec.get_info` | GET | `/v1/exec/info` |
| `exec.create_channel` | POST | `/v1/exec/create-channel` |
| `exec.create_app` | POST | `/v1/exec/create-app` |
| `exec.query_execution` | POST | `/v1/exec/query` |

创建渠道的 `input`：

```json
{
  "requested_channel_name": "甘肃体验有礼掌厅瀑布流"
}
```

创建应用的 `input`：

```json
{
  "application_type": "云盘",
  "business_object": "云盘",
  "actual_channel_name": "甘肃体验有礼掌厅瀑布流-1",
  "jump_address": "mcloud://...",
  "resource_fallback_page": "https://...",
  "settlement_type": "云盘",
  "download_link": "https://...",
  "fixed_fields": {}
}
```

查询可以使用 `execution_id`，也可以使用 `idempotency_key`，或者使用 `task_id + operation + environment`。

## 状态规则

```text
ACCEPTED -> RUNNING -> SUCCEEDED
                     -> FAILED
                     -> UNKNOWN
```

请求超时或连接断开时，Hermes 必须查询原任务，不能重新提交创建请求。

`TEST/FAKE` 成功会返回 `business_status=SIMULATED_SUCCESS`，且不会生成真实长链接。

