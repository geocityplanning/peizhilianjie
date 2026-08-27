# 新机器部署清单

> 适用场景：FastAPI + MCP Server + Chrome 浏览器 全部装在同一台 Windows 电脑上。

---

## 1. 安装基础软件

- Python 3.11+（勾选 Add to PATH）
- Google Chrome（推荐）或 Microsoft Edge（系统自带）

## 2. 拷贝项目文件

将打包传输文件夹里的所有内容拷到新机器（如 `C:\Users\<用户名>\Documents\amoo-automation\`）。

**不要拷：** `venv/`、`browser-profile/`、`edge-profile/`、`output/`、`data/`、`__pycache__/`

## 3. 创建虚拟环境 + 装依赖

```powershell
cd <项目目录>
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

> 注：本项目通过 CDP 连接系统已装的 Chrome/Edge，不需要 `playwright install` 下载浏览器。

## 4. 设置环境变量

```powershell
[Environment]::SetEnvironmentVariable("AMOO_SECRET_KEY", "_xNZOWsB21aZtBB37d2jMybiKCn9IpAoahzGpoOhOHw=", "User")
```

> 密钥与加密密码配套使用，两台机器必须一致才能解密 config.json 里的 password_encrypted。

## 5. 注册浏览器守护任务

```powershell
$script = "<项目目录>\browser_guard.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`""
$triggerLogon = New-ScheduledTaskTrigger -AtLogOn
$triggerRecur = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
Register-ScheduledTask -TaskName "AmooBrowserGuard" -Action $action -Trigger @($triggerLogon, $triggerRecur) -Settings $settings -Description "AMOO 139 automation browser CDP guard" -Force
# 手动触发一次，启动浏览器
Start-ScheduledTask -TaskName "AmooBrowserGuard"
```

`browser_guard.ps1` 会自动探测 Chrome（优先）或 Edge（兜底），不需要手动指定浏览器路径。

## 6. 注册 MCP Server（如果走 MCP 方式）

在 Hermes / Claude Desktop 的 MCP 配置中添加：

```json
{
  "mcpServers": {
    "amoo-139": {
      "command": "<项目目录>\\venv\\Scripts\\python.exe",
      "args": ["<项目目录>\\mcp_server.py"]
    }
  }
}
```

> 如果 FastAPI 直接 import 使用，则不需要这步。

## 7. 首次验证

```powershell
# 确认浏览器 CDP 在跑
Invoke-WebRequest -Uri "http://127.0.0.1:9222/json/version" -UseBasicParsing -TimeoutSec 5

# 测试直接调用
.\venv\Scripts\python.exe -c "from actions.api import get_status; print(get_status())"

# 测试 MCP（如果走 MCP 方式）
.\venv\Scripts\python.exe test_mcp.py
```

首次调用会自动登录（填账号 weiweitest + 解密密码 + OCR 验证码），无需手动登录。

## 8. 验证成功/失败的标准

所有函数返回 JSON，`success: true` = 成功，`success: false` 时 `message` 字段含人话原因，`error_code` 标明错误类别，`next_action` 建议下一步。
