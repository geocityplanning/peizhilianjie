# 直接调用 API 说明（给 Codex / FastAPI 用）

> 本文件说明 `actions/api.py` 暴露的函数，FastAPI 可直接 import 调用，无需 MCP 协议。

---

## 1. 环境要求

- Python 3.11+，项目 venv
- Chrome 或 Edge 以 `--remote-debugging-port=9222` 常驻
- 环境变量 `AMOO_SECRET_KEY` 已设置
- `config.json` 中已配置加密密码

---

## 2. 函数列表

| 函数 | 作用 | 返回 |
|------|------|------|
| `get_status()` | 检查环境状态 | `{"ready": bool, "browser": str, "logged_in": bool, "locked": bool}` |
| `login()` | 触发自动登录 | `{"success": bool, "message": str, "already_logged_in": bool}` |
| `create_channel(channel_base_name)` | 创建渠道 | `{"success": bool, "actual_channel_name": str, "message": str}` |
| `create_app(...)` | 创建应用 | `{"success": bool, "cloud_app_link": str, "message": str}` |
| `update_app(app_name, fields_to_update)` | 修改应用配置 | `{"success": bool, "updated_fields": list, "previous_values": dict, "message": str}` |
| `locate_app(channel_name, app_name)` | 定位历史应用 | `{"success": bool, "app_name": str, "channel": str, "cloud_link": str, "match_confidence": str}` |
| `get_field_list()` | 获取所有可改字段 | `{"fields": [...], "total": int}` |

---

## 3. 函数签名与示例

### get_status()

```python
from actions.api import get_status

status = get_status()
# {"ready": True, "browser": "Chrome/152.0.7977.65", "logged_in": True, "locked": False}
```

### login()

```python
from actions.api import login

result = login()
# {"success": True, "message": "登录成功", "already_logged_in": False}
```

### create_channel()

```python
from actions.api import create_channel

result = create_channel(channel_base_name="he0820")
# 成功: {"success": True, "actual_channel_name": "he0820", "message": "操作成功"}
# 重名: {"success": True, "actual_channel_name": "he08201", "message": "操作成功"}
# 失败: {"success": False, "message": "创建渠道异常: ...", "error_code": "CHANNEL_CREATE_FAILED"}
```

### create_app()

```python
from actions.api import create_app

result = create_app(
    business_object="中国移动云盘",        # 用于匹配复制模板
    activity_name="",                      # 兼容保留，不参与应用名称生成
    actual_channel_name="he0820",          # 从 create_channel 返回值拿
    application_type="云盘",               # "云盘" / "掌厅"
    jump_address="mcloud://main/tab?params=xxx&tk=",
    resource_fallback_page="https://m.mcloud.139.com/portal/...",
    settlement_type="云盘",
    group_name="10028",                   # 空则跳过分组设置
)
# 成功: {"success": True, "cloud_app_link": "https://l.yun.139.com/m/a/s/2HNYG", "message": "操作成功"}
# 失败: {"success": False, "message": "...", "error_code": "SAVE_FAILED"}
```

### update_app()

```python
from actions.api import update_app

result = update_app(
    app_name="中国移动云盘-活动1",
    fields_to_update={
        "short_intro": "全新云盘体验",       # 有值 = 设新值
        "share_text": "",                  # 空字符串 = 清空
        "icon": "http://127.0.0.1:8000/files/icons/icon_001.png",  # 图片 URL
        "show_floating_ball": False,        # 开关
        "settlement_type": "在线",          # 下拉选择
    },
    app_id="EXEC-F57DB3A6981A",  # 可选，创建应用时的 execution_id
)
# 成功: {"success": True,
#        "updated_fields": ["一句话简介", "分享文案", "应用图标", "显示悬浮球", "结算类型"],
#        "previous_values": {"一句话简介": "旧值", "结算类型": "云盘"},
#        "final_status": "ONLINE",
#        "message": "操作成功"}
# 失败: {"success": False, "message": "...", "error_code": "SAVE_FAILED"}
```

### get_field_list()

```python
from actions.api import get_field_list

fields = get_field_list()
# {"fields": [{"key": "app_name", "tab": "体验配置", "label": "应用名称",
#              "type": "input", "required": True}, ...],
#  "total": 50}
```

前端可用此接口渲染审查表：按 tab 分组、标红星必填、根据 type 渲染不同输入控件。

---

## 4. 返回值统一格式

### 成功

```json
{
  "success": true,
  "message": "操作成功",
  // + 各函数特有的业务字段
}
```

### 失败

```json
{
  "success": false,
  "message": "保存失败(对话框未关闭) | 表单校验错误: 应用名称不能为空",
  "error_code": "SAVE_FAILED",
  "error_stage": "SAVE",
  "next_action": "MANUAL_CHECK"
}
```

| 字段 | 说明 |
|------|------|
| success | 是否成功 |
| message | 人话说明，可直接展示给业务人员 |
| error_code | 机器错误码（失败时） |
| error_stage | 出错阶段（失败时） |
| next_action | STOP / QUERY / MANUAL_CHECK（失败时） |

---

## 5. 注意事项

- 所有函数**同步阻塞**，单次操作约 10-60 秒，FastAPI 应放到后台任务或线程池执行
- 内部有**单实例锁**，同时只有一个函数在操作浏览器，第二个调用会被拒绝
- token 过期时 `ensure_login` 会自动检测并重新登录（OCR 识别验证码，约 3-5 秒）
- 图片字段传 HTTP URL，MCP 内部自动下载+上传，不传文件路径
- `previous_values` 可用于"回撤"：把它当 fields_to_update 再调一次 update_app

---

## 6. FastAPI 集成示例

```python
from fastapi import FastAPI, BackgroundTasks
from actions.api import create_channel, create_app, update_app, get_status

app = FastAPI()

@app.get("/api/status")
def status():
    return get_status()

@app.post("/api/channel")
async def create_channel_api(data: dict, bg: BackgroundTasks):
    # 同步调用（阻塞），生产环境建议用线程池
    result = create_channel(data["channel_base_name"])
    return result

@app.post("/api/app")
async def create_app_api(data: dict):
    result = create_app(**data)
    return result

@app.patch("/api/app")
async def update_app_api(data: dict):
    result = update_app(
        app_name=data["app_name"],
        fields_to_update=data["fields_to_update"],
    )
    return result
```
