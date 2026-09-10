# Hermes REAL 专用电脑部署说明

适用环境：Hermes、FastAPI 执行端和浏览器运行在同一台 Windows 专用电脑。

## 1. 拉取代码

首次部署：

```powershell
git clone -b feat/hermes-real-executor https://github.com/geocityplanning/peizhilianjie.git
cd peizhilianjie
```

已有仓库：

```powershell
git fetch origin
git switch feat/hermes-real-executor
git pull --ff-only
```

## 2. 安装依赖

电脑需安装 Git、uv，以及 Chrome 或 Edge。然后在仓库根目录执行：

```powershell
uv sync
```

## 3. 配置自动化登录

创建本机配置：

```powershell
Copy-Item app\executor\config.example.json app\executor\config.json
```

生成一次 Fernet 密钥：

```powershell
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

安全保存该密钥，并在当前 PowerShell 设置：

```powershell
$env:AMOO_SECRET_KEY = "<刚生成的Fernet密钥>"
$env:PYTHONPATH = (Resolve-Path app\executor).Path
```

加密登录密码：

```powershell
uv run python app\executor\actions\ensure_login.py encrypt "<登录密码>"
```

编辑 `app\executor\config.json`：

- `username` 填登录账号。
- `password_encrypted` 填上一步输出的密文。

不要提交 `config.json`。以后启动服务必须继续使用同一个 `AMOO_SECRET_KEY`，不能重新生成。

## 4. 初始化数据库

```powershell
uv run python -m app.http_executor.init_db
```

只需首次部署时执行一次。

## 5. 启动 REAL 执行端

设置由 Hermes 和执行端共同约定的 Token：

```powershell
$env:HERMES_EXECUTOR_TOKEN = "<双方约定的Bearer Token>"
$env:HERMES_EXECUTOR_ENV = "TEST"
$env:AMOO_SECRET_KEY = "<第3步保存的Fernet密钥>"
uv run uvicorn app.http_executor.main:app --host 127.0.0.1 --port 8001
```

保持此 PowerShell 窗口和浏览器运行。

## 5.1 Hermes 联调 FAKE 模式

联调时仍使用同一个 8001 和同一套四个接口，只将假跑开关打开。默认值是关闭，生产和真实验收必须保持关闭：

```powershell
$env:HERMES_EXECUTOR_TOKEN = "<双方约定的Bearer Token>"
$env:HERMES_EXECUTOR_ENV = "TEST"
$env:HERMES_EXECUTOR_FAKE_MODE = "true"
uv run uvicorn app.http_executor.main:app --host 127.0.0.1 --port 8001
```

此时先调用 `POST /v1/exec/info`，确认返回 `mode = FAKE`。`create-channel` 和 `create-app` 会写入现有执行记录并返回契约形状的假成功回执，不启动浏览器，也不调用真实后台。测试结束后关闭该 PowerShell 窗口，或重新启动并设置：

```powershell
$env:HERMES_EXECUTOR_FAKE_MODE = "false"
```

## 6. 验证服务

另开一个 PowerShell：

```powershell
$token = "<双方约定的Bearer Token>"
$headers = @{
    Authorization = "Bearer $token"
    "X-Contract-Version" = "http-executor.v1"
}
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8001/v1/exec/info" -Headers $headers
```

成功结果应包含：

```text
service = hermes-real-executor
mode = REAL
database_status = AVAILABLE
status = SUCCESS
acceptable = true/false（取决于 REAL 登录状态和是否有未决任务）
login_valid = true（REAL 会话有效时）
unknown_inflight = false（当前无未决任务时）
```

其中，`status = SUCCESS` 只表示执行端探活和数据库检查成功，不代表创建渠道或创建应用已经成功。`acceptable = true` 才表示当前可以接受新的创建请求；REAL 模式下还必须满足浏览器已有有效登录会话，且不存在未决执行。若 `unknown_inflight = true`，应先用 `active_execution_correlation_id` 查询原任务，不要提交新的创建请求。
## 7. 配置 Hermes

```text
Base URL: http://127.0.0.1:8001
Authorization: Bearer <双方约定的Token>
X-Contract-Version: http-executor.v1
Content-Type: application/json
```

调用顺序：

```text
POST /v1/exec/create-channel
POST /v1/exec/create-app
POST /v1/exec/query
```

`create-app` 的 `actual_channel_name` 必须使用 `create-channel` 返回的 `data.actual_channel_name`。超时、断线或返回 `UNKNOWN` 时调用 `query` 查询原任务，不能重新创建。

请求字段见：

```text
contracts/examples/create-channel-request.json
contracts/examples/create-app-request.json
contracts/examples/query-request.json
contracts/hermes-http-v1.openapi.yaml
```

## 8. 正式验收

使用带日期和“测试”后缀的唯一数据，依次执行：

1. 一条云盘渠道和应用。
2. 一条掌厅渠道和应用。

每条应用的成功条件：

```text
state = SUCCEEDED
status = SUCCESS
data.app_id 有值
data.app_link 有值
data.completed_stages 包含 CREATE_SAVE、ENABLE、COMPLETED；传入非空 group_name 时还必须包含 SET_GROUP
```