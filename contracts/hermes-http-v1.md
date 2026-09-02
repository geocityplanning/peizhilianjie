# Hermes HTTP Executor v1

这是 Hermes HTTP 执行契约。执行端支持 `FAKE` 和 `REAL`：`FAKE` 只验证接口，`REAL` 会调用本项目现有浏览器自动化创建真实渠道和应用。

## 启动

先初始化执行端专用 SQLite：

```powershell
uv run python -m app.http_executor.init_db
```

设置本地联调鉴权令牌后启动独立服务：

```powershell
$env:HERMES_EXECUTOR_TOKEN = "change-this-local-token"
$env:HERMES_EXECUTOR_ENV = "TEST"
$env:HERMES_EXECUTOR_MODE = "REAL"
uv run uvicorn app.http_executor.main:app --host 127.0.0.1 --port 8001
```

需要纯接口联调时可将 `HERMES_EXECUTOR_MODE` 改为 `FAKE`。服务不会复用 `app.main`、旧 `job_manager` 或旧的前端接口；REAL 模式通过适配层调用同一套自动化 action。

## Hermes 电脑首次部署

在 Windows 上拉取 `feat/hermes-http-integration` 后，从仓库根目录执行：

```powershell
uv sync
Copy-Item app\executor\config.example.json app\executor\config.json
```

编辑本机 `app\executor\config.json`，填写 `username`。密码不要写明文：先设置双方约定的 `AMOO_SECRET_KEY`，再生成密文并填入 `password_encrypted`：

```powershell
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
$env:AMOO_SECRET_KEY = "上一步生成并由执行端安全保管的Fernet密钥"
uv run python app\executor\actions\ensure_login.py encrypt "真实密码"
```

`config.json`、`browser-profile`、数据库和日志均被 Git 忽略，不得提交。每次启动 REAL 服务的终端都要设置同一个 `AMOO_SECRET_KEY`。机器需安装 Chrome 或 Edge；首次运行会创建该机器自己的 `browser-profile`。

然后初始化 8001 专用数据库并启动：

```powershell
uv run python -m app.http_executor.init_db
$env:HERMES_EXECUTOR_TOKEN = "Hermes与执行端约定的Bearer Token"
$env:HERMES_EXECUTOR_ENV = "TEST"
$env:HERMES_EXECUTOR_MODE = "REAL"
uv run uvicorn app.http_executor.main:app --host 127.0.0.1 --port 8001
```

Hermes 与执行端运行在同一台电脑时使用 `http://127.0.0.1:8001`。首次真实任务前先访问 `/v1/exec/info`，确认返回的 `mode` 为 `REAL`；浏览器首次出现时允许操作人员完成必要的登录确认。

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
  "business_object": "中国移动云盘",
  "actual_channel_name": "甘肃体验有礼掌厅瀑布流-1",
  "jump_address": "mcloud://...",
  "resource_fallback_page": "https://...",
  "settlement_type": "云盘",
  "group_name": "10086",
  "download_link": "https://...",
  "fixed_fields": {}
}
```

`business_object` 是应用名称（例如“中国移动云盘”或“中国移动”）；`actual_channel_name` 必须使用创建渠道接口返回的实际名称；`group_name` 是应用上线后的分组值。活动名称已由 Hermes 用于组装 `requested_channel_name`，不属于创建应用输入。

查询可以使用 `execution_id`，也可以使用 `idempotency_key`，或者使用 `task_id + operation + environment`。

## 状态规则

```text
ACCEPTED -> RUNNING -> SUCCEEDED
                     -> FAILED
                     -> UNKNOWN
```

请求超时或连接断开时，Hermes 必须查询原任务，不能重新提交创建请求。

`FAKE` 成功返回 `business_status=SIMULATED_SUCCESS`，且不会生成真实长链接；`REAL` 成功返回 `business_status=SUCCESS`。
