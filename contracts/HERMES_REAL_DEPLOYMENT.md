# Hermes REAL 执行端跨平台部署说明

适用环境：Hermes、FastAPI 执行端和浏览器运行在同一台 Windows 或 macOS 专用电脑。

HTTP 地址、端口、认证方式和四个 Hermes POST 接口在两个系统上完全一致：

```text
http://127.0.0.1:8001
```

## 1. 拉取代码

Windows PowerShell 和 macOS Terminal 都可以执行：

```text
git clone -b feat/hermes-real-executor https://github.com/geocityplanning/peizhilianjie.git
cd peizhilianjie
```

已有仓库：

```text
git fetch origin
git switch feat/hermes-real-executor
git pull --ff-only
```

## 2. 安装依赖

电脑需安装 Git、uv，以及 Chrome、Edge 或 Chromium。

Windows：

```powershell
uv sync
```

macOS：

```bash
uv sync
```

项目使用 Python 跨平台浏览器守护器 `app/executor/browser_guard.py`，不要求 macOS 安装 PowerShell。Windows 仍保留旧的 `browser_guard.ps1`，但 REAL HTTP 执行路径不再依赖它。

## 3. 配置自动化登录

Windows：

```powershell
Copy-Item app\executor\config.example.json app\executor\config.json
```

macOS：

```bash
cp app/executor/config.example.json app/executor/config.json
```

生成一次 Fernet 密钥（两个系统命令相同）：

```text
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Windows PowerShell 设置：

```powershell
$env:AMOO_SECRET_KEY = "<刚生成的Fernet密钥>"
```

macOS Terminal 设置：

```bash
export AMOO_SECRET_KEY='<刚生成的Fernet密钥>'
```

加密登录密码：

Windows：

```powershell
uv run python app\executor\actions\ensure_login.py encrypt "<登录密码>"
```

macOS：

```bash
uv run python app/executor/actions/ensure_login.py encrypt '<登录密码>'
```

将输出的密文写入对应的 `app/executor/config.json`：

- `username` 填登录账号。
- `password_encrypted` 填加密密码。

不要提交 `config.json`。以后启动服务必须继续使用同一个 `AMOO_SECRET_KEY`，不能重新生成。
根目录 `.env` 可以同时放置 HTTP 执行端和自动化模块的环境变量，例如 `HERMES_EXECUTOR_TOKEN`、`HERMES_EXECUTOR_ENV`、`HERMES_EXECUTOR_FAKE_MODE`、`HERMES_EXECUTOR_DB` 和 `AMOO_SECRET_KEY`。配置类按自身字段分域读取，会忽略其他子系统的合法变量；不需要为了消除冲突删除这些启动变量。

执行端发生未知异常时，只返回稳定错误码和脱敏说明，不会把 Bearer Token、Fernet 密钥、登录密码、密文或 Pydantic 原始 `input_value` 写入 HTTP 回执和 SQLite。

## 4. 浏览器和 CDP

REAL 执行端使用本机浏览器的 CDP `9222` 端口。每次真实创建前，Python 浏览器守护器会：

1. 检查 `127.0.0.1:9222` 是否已可用。
2. 如果不可用，按系统寻找 Chrome、Edge 或 Chromium 并启动。
3. 使用独立的 `app/executor/browser-profile` 保存登录会话。
4. 打开测试环境后台地址，并等待 139 页面可用。

如浏览器安装在非默认路径，可手动配置：

Windows PowerShell：

```powershell
$env:HERMES_BROWSER_EXECUTABLE = "D:\Apps\Chrome\chrome.exe"
$env:HERMES_BROWSER_PROFILE_DIR = "D:\hermes-browser-profile"
```

macOS：

```bash
export HERMES_BROWSER_EXECUTABLE="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
export HERMES_BROWSER_PROFILE_DIR="$HOME/hermes-browser-profile"
```

也可以设置自定义目标地址或 CDP 地址：

```text
HERMES_BROWSER_TARGET_URL=https://uat-cloud.139.com/cloudappadmin/#/cloudAppChannelManager
HERMES_CDP_URL=http://127.0.0.1:9222
```

## 5. 初始化数据库

Windows 和 macOS 命令相同：

```text
uv run python -m app.http_executor.init_db
```

只需首次部署时执行一次。

## 6. 启动 REAL 执行端

Windows PowerShell：

```powershell
$env:HERMES_EXECUTOR_TOKEN = "<双方约定的Bearer Token>"
$env:HERMES_EXECUTOR_ENV = "TEST"
$env:HERMES_EXECUTOR_FAKE_MODE = "false"
uv run uvicorn app.http_executor.main:app --host 127.0.0.1 --port 8001
```

macOS Terminal：

```bash
export HERMES_EXECUTOR_TOKEN='<双方约定的Bearer Token>'
export HERMES_EXECUTOR_ENV='TEST'
export HERMES_EXECUTOR_FAKE_MODE='false'
uv run uvicorn app.http_executor.main:app --host 127.0.0.1 --port 8001
```

REAL 模式不要设置为 `true`。Hermes、FastAPI 和浏览器必须运行在同一台电脑，Hermes 调用地址固定为 `127.0.0.1:8001`。

## 7. FAKE 联调模式

FAKE 仅用于联调，不执行网页自动化。Windows 和 macOS 只需设置相同的环境变量后启动：

Windows PowerShell：

```powershell
$env:HERMES_EXECUTOR_TOKEN = "<双方约定的Bearer Token>"
$env:HERMES_EXECUTOR_ENV = "TEST"
$env:HERMES_EXECUTOR_FAKE_MODE = "true"
uv run uvicorn app.http_executor.main:app --host 127.0.0.1 --port 8001
```

macOS Terminal：

```bash
export HERMES_EXECUTOR_TOKEN='<双方约定的Bearer Token>'
export HERMES_EXECUTOR_ENV='TEST'
export HERMES_EXECUTOR_FAKE_MODE='true'
uv run uvicorn app.http_executor.main:app --host 127.0.0.1 --port 8001
```

FAKE 探活应返回：

```text
mode = FAKE
status = SUCCESS
acceptable = true
login_valid = true
unknown_inflight = false
```

## 8. 探活和 REAL 验收

另开一个终端调用：

```bash
curl -X POST http://127.0.0.1:8001/v1/exec/info \
  -H "Authorization: Bearer <双方约定的Bearer Token>" \
  -H "X-Contract-Version: http-executor.v1" \
  -H "Content-Type: application/json" \
  -d '{"environment":"TEST","run_id":"C2-INFO-001"}'
```

REAL 空闲且已登录时应包含：

```text
mode = REAL
database_status = AVAILABLE
status = SUCCESS
login_valid = true
unknown_inflight = false
acceptable = true
```

`status=SUCCESS` 只表示探活和数据库检查成功，不代表业务创建成功。`acceptable=true` 才表示当前可以接受新的创建任务。登录失效、数据库不可用或存在未决任务时不要创建。
REAL 登录心跳有约 10 秒的有限超时。浏览器接口挂起、网络异常或响应解析失败时，`info` 会安全返回 `login_valid=false`、`acceptable=false`，不会无限等待。

## 9. Hermes 调用顺序

```text
POST /v1/exec/info
POST /v1/exec/create-channel
POST /v1/exec/create-app
POST /v1/exec/query
```

`create-app` 的 `actual_channel_name` 必须使用 `create-channel` 返回的 `data.actual_channel_name`。

超时、断线或返回 `UNKNOWN` 时，必须使用原 `execution_correlation_id` 调用 `query`，不能重新创建。

## 10. 正式验收

使用带日期和“测试”后缀的唯一数据，依次执行：

1. 一条云盘渠道和应用。
2. 一条掌厅渠道和应用。

创建应用成功最低条件：

```text
state = SUCCEEDED
status = SUCCESS
data.app_id 有值
data.app_link 有值，且为 http:// 或 https:// 开头
```

第一轮 C1/C2 真跑按双方冻结口径，以后台“长连接”对应的 `data.app_link` 为成功依据；不把短链接或 `completed_stages` 完整性作为第一轮最低成功门槛。