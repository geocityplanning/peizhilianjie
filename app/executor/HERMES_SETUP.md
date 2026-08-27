# Hermes 接入指南

## 1. 运行环境

本项目的 MCP Server 通过 CDP（Chrome DevTools Protocol）连接本机浏览器（端口 9222），自动探测 Chrome（优先）或 Edge（兜底）。**MCP Server 必须运行在装有浏览器的 Windows 机器上**，不能跑在云端。

如果 Hermes 部署在同一台 Windows 机器上，直接注册 MCP Server 即可。
如果 Hermes 部署在云端/其他机器，需要在本机起一个 MCP SSE/HTTP 桥接，或用隧道暴露 stdio MCP Server。

## 2. 安装

```powershell
cd <项目目录>
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

> 不需要 `playwright install`，本项目连接系统已装的浏览器。

## 3. 环境变量

```
AMOO_SECRET_KEY=<Fernet 密钥，44 字符 base64>
```

从本机获取：`Get-Content data\.secret_key`（或从系统环境变量读）。

## 4. 前置条件

浏览器以调试端口启动（已注册计划任务 AmooBrowserGuard 自动维护）：

```
chrome.exe --remote-debugging-port=9222 --user-data-dir=...\browser-profile
```

`browser_guard.ps1` 自动探测 Chrome 或 Edge，优先 Chrome。

## 5. MCP Server 注册

在 Hermes 的 MCP 配置中添加：

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

环境变量 `AMOO_SECRET_KEY` 需对该进程可见。

## 6. 可用 Tool（6 个）

| Tool | 参数 | 返回 |
|------|------|------|
| `get_executor_info` | （无） | 环境状态 + 支持的操作列表 |
| `create_channel_tool` | `request_json: str` | `{"success":true, "data":{"actual_channel_name":"..."}}` |
| `create_app_tool` | `request_json: str` | `{"success":true, "data":{"cloud_app_link":"..."}}` |
| `update_app_tool` | `request_json: str` | `{"success":true, "data":{"updated_fields":[...], "previous_values":{...}}}` |
| `locate_app_tool` | `request_json: str` | `{"success":true, "data":{"platform_id":"...", "app_name":"...", ...}}` |
| `get_execution` | `query_json: str` | 原执行结果（幂等回放） |

> 如果 FastAPI 直接 import 使用（`from actions.api import ...`），则不需要走 MCP。

## 7. 失败时的输出

所有 Tool 失败时返回：

```json
{
  "success": false,
  "message": "保存失败(对话框未关闭) | 页面提示: xxx | 表单校验错误: yyy",
  "error_code": "SAVE_FAILED",
  "error_stage": "SAVE",
  "next_action": "MANUAL_CHECK"
}
```

Hermes 可直接将 `message` 转发给用户，`error_code` 供程序判断。

## 8. 不需要上传的文件

- `venv/` — 目标机器自己建
- `browser-profile/` / `edge-profile/` — 浏览器 session 缓存，绑机器
- `output/` — 测试截图和结果
- `data/` — SQLite 和临时文件
- `__pycache__/` — Python 缓存

## 9. 需要上传的文件

```
amoo-automation/
├── core/
│   ├── __init__.py          # CDP连接、token、签名、API调用、渠道查重
│   ├── security.py          # 密码加密/解密
│   ├── executor.py          # SQLite、幂等、锁、执行记录
│   ├── error_codes.py       # 错误码定义
│   └── error_capture.py     # 页面错误抓取
├── ocr/
│   ├── __init__.py
│   ├── captcha_recognizer.py
│   └── image_processor.py
├── actions/
│   ├── __init__.py
│   ├── ensure_login.py      # 自动登录
│   ├── create_channel_v2.py # 创建渠道
│   ├── create_app_v2.py     # 创建应用
│   ├── update_app_v2.py     # 修改应用
│   ├── locate_app.py        # 定位应用
│   ├── api.py               # 直接调用入口（给 FastAPI）
│   └── api.md               # API 文档
├── mcp_server.py            # MCP Server（6 个 Tool）
├── browser_guard.ps1        # 浏览器守护脚本
├── config.json              # 登录配置
├── requirements.txt
├── 流程说明.md
├── HERMES_SETUP.md          （本文件）
└── DEPLOY.md
```
