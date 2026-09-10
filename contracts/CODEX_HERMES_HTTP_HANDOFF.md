# Hermes REAL 执行端部署与调用

## Git

```text
Repository: https://github.com/geocityplanning/peizhilianjie.git
Branch: feat/hermes-real-executor
```

此分支交付 Hermes 所需的 8001 HTTP 执行端和浏览器自动化。默认是 REAL；联调时可通过 `HERMES_EXECUTOR_FAKE_MODE=true` 在同一端口启用假跑。

## 首次部署

在 Windows 执行电脑上：

```powershell
git clone -b feat/hermes-real-executor https://github.com/geocityplanning/peizhilianjie.git
cd peizhilianjie
uv sync
Copy-Item app\executor\config.example.json app\executor\config.json
```

编辑本机 `app\executor\config.json`，填写登录账号和加密密码配置。`config.json`、浏览器 Profile、数据库和日志不得提交 Git。

初始化 8001 专用数据库：

```powershell
uv run python -m app.http_executor.init_db
```

## 启动

每次启动服务前设置执行端 Token 和自动化登录密钥：

```powershell
$env:HERMES_EXECUTOR_TOKEN = "<Hermes与执行端约定的Bearer Token>"
$env:HERMES_EXECUTOR_ENV = "TEST"
$env:AMOO_SECRET_KEY = "<执行电脑保存的Fernet密钥>"
uv run uvicorn app.http_executor.main:app --host 127.0.0.1 --port 8001
```

服务未配置 `HERMES_EXECUTOR_TOKEN` 时会拒绝启动。

## Hermes 调用

所有请求都带：

```text
Authorization: Bearer <Token>
X-Contract-Version: http-executor.v1
Content-Type: application/json
```

调用顺序：

```text
POST /v1/exec/info
POST /v1/exec/create-channel
POST /v1/exec/create-app
POST /v1/exec/query   # 仅在需要查询原任务时
```

请求样例位于 `contracts/examples/`，完整字段规则见 `contracts/hermes-http-v1.openapi.yaml`。

## 约束

- Hermes 串行提交创建任务，一个渠道对应一个应用。
- 创建应用必须使用创建渠道返回的 `actual_channel_name`。
- 同一动作重试必须复用原 `idempotency_key`。
- 超时、断线或 `UNKNOWN` 时查询原任务，禁止直接再次创建。
- 对外只认 `execution_correlation_id`、`app_id`、`app_link`。
- 自动化页面或按钮调整只影响执行实现；四个 HTTP 路径和请求回执契约保持不变。