# Codex 接入说明

> 本文件供 Codex / FastAPI 开发人员阅读，帮助快速理解项目全貌和接入方式。

---

## 1. 这个项目是做什么的

139云应用平台（plus.buy.139.com）是一个内部管理系统，用于配置和管理移动云应用。目前这些操作（创建渠道、创建应用、修改配置、上线发布）都是人工在网页后台手动操作的。

这个项目把上述人工操作**全部自动化**了——通过连接 Chrome 浏览器，像人一样操作页面：自动登录（含验证码识别）、创建渠道、创建应用、修改应用配置、定位历史应用。

**当前代码包**是自动化执行端，负责实际操作浏览器干活。接入方的工作是编写 FastAPI 编排层，管理批次任务，调用这个执行端，把结果回填到 Excel。

---

## 2. 整体架构

```
业务人员填 Excel → FastAPI 编排层解析 Excel，拆成逐条任务
  → 串行调执行端（这个代码包）
    → 执行端操作 Chrome 浏览器完成实际操作
    → 返回结果（全量数据：渠道记录 / 应用整行 / 长链接 / 修改前原值）
  → FastAPI 把结果存 SQLite + 回填到 Excel
  → 业务人员核对
```

三层跑在同一台 Windows 机器上：
- FastAPI + 执行端代码 + Chrome 浏览器在同一台机器
- FastAPI 直接 import 执行端的 Python 函数，不走网络协议
- Chrome 以调试端口（9222）常驻运行

---

## 3. 代码包里有什么

```
打包传输文件夹/
├── core/                        # 核心层
│   ├── __init__.py              # CDP连接、token读取、签名计算、API调用、渠道查重
│   ├── security.py              # 密码加密/解密（Fernet AES，密钥读环境变量）
│   ├── executor.py              # SQLite持久化、幂等键、单实例锁、执行记录管理
│   ├── error_codes.py           # 错误码定义
│   ├── error_capture.py         # 页面错误抓取（toast/弹窗/表单校验/截图）
│   └── row_reader.py            # 整行全量读取（动态表头，不硬编码列索引）
│
├── ocr/                         # 验证码识别
│   ├── captcha_recognizer.py    # ddddocr 验证码识别（多方法重试）
│   └── image_processor.py       # OpenCV 图像预处理
│
├── actions/                     # 业务动作
│   ├── __init__.py
│   ├── ensure_login.py          # 自动登录（session检测→填账密→OCR验证码→重试）
│   ├── create_channel_v2.py     # 创建渠道（契约版，走幂等+锁+SQLite）
│   ├── create_app_v2.py         # 创建应用（复制模板→6Tab填写→保存→发布→分组）
│   ├── update_app_v2.py         # 修改应用（50字段增量更新+图片上传+下线/上线）
│   ├── locate_app.py            # 定位历史应用（渠道名前缀+应用名消歧义）
│   ├── api.py                   # ★ FastAPI 直接调用入口 ★
│   └── api.md                   # API 文档（函数签名+示例）
│
├── mcp_server.py                # MCP Server（可选，如果不用直接import就走这个）
├── browser_guard.ps1            # 浏览器守护脚本（自动拉起Chrome，找不到用Edge）
├── config.json                  # 登录配置（选择器+加密密码）
├── requirements.txt             # Python 依赖
├── 流程说明.md                   # 整体流程和架构说明
├── HERMES_SETUP.md              # MCP 注册配置（如果走 MCP 方式）
└── DEPLOY.md                    # 新机器部署清单
```

---

## 4. FastAPI 编排层职责

实现 FastAPI 编排层，核心职责：

1. **解析 Excel**：读取业务人员填的待配置链接表，提取每行的业务数据
2. **批次管理**：把多行拆成批次，每批串行执行（执行端有单实例锁，不能并发）
3. **调用执行端**：用 `actions/api.py` 的函数，按顺序调
4. **存 SQLite**：每次调用返回的都是**全量数据**（渠道完整记录 / 应用整行数据），直接存库
5. **回填 Excel**：长链接回填到 Excel，失败原因也回填
6. **提供 HTTP 接口**：给 Hermes / 前端调用

---

## 5. 怎么调

**直接 import，不走 MCP**：

```python
from actions.api import get_status, login, create_channel, create_app, update_app, locate_app, get_field_list

# 检查环境
status = get_status()
# {"ready": True, "browser": "Chrome/152", "logged_in": True, "locked": False}

# 创建渠道 → 返回渠道完整记录
ch = create_channel(channel_base_name="he0820")
# 不传 base_platform 默认华为底座2.0
# 也可指定: create_channel("he0820", base_platform="蜂助手底座")
# {"success": True, "actual_channel_name": "he0820",
#  "channel_data": {"id": "969", "channel": "he0820", "basePlatform": "2",
#                    "creator": "weiweitest", "createTime": "...", ...}}
# ★ 存 channel_data.id 到 SQLite，下次直接用

# 创建应用 → 返回整行全量数据
app = create_app(
    business_object="中国移动云盘",
    activity_name="活动1",
    actual_channel_name=ch["actual_channel_name"],  # ← 从渠道返回值拿
    application_type="云盘",
    jump_address="mcloud://main/tab?params=xxx&tk=",
    resource_fallback_page="https://m.mcloud.139.com/portal/...",
    settlement_type="云盘",
    group_name="10028",
)
# {"success": True,
#  "cloud_app_link": "https://plus.buy.139.com/mccloudgame/#/?i=...",  ← 长链接，回填Excel
#  "cloud_app_short_link": "https://l.yun.139.com/m/a/s/...",          ← 短链接
#  "row_data": {"ID": "11945", "应用名称": "...", "所属渠道": "he0820",
#               "应用链接": "...", "长连接": "...", "底座": "华为底座2.0",
#               "_status": "ON", ...17个字段全量}}
# ★ 存 row_data.ID 到 SQLite，下次改应用直接用

# 定位历史应用 → 返回整行全量数据
loc = locate_app(channel_name="he0818", app_name="中国移动云盘-测试")
# {"success": True, "cloud_app_link": "...", "cloud_app_short_link": "...",
#  "match_confidence": "HIGH", "row_data": {"ID": "11945", ...}}
# ★ 存 row_data.ID，以后不用再模糊匹配

# 修改应用 → 返回修改前原值 + 修改后整行
upd = update_app(
    app_name="中国移动云盘-活动1",
    fields_to_update={
        "short_intro": "新简介",
        "icon": "http://127.0.0.1:8000/files/icons/icon_001.png",
        "share_text": "",  # 空字符串=清空
    }
)
# {"success": True,
#  "updated_fields": ["一句话简介", "应用图标", "分享文案"],
#  "previous_values": {"一句话简介": "旧值", "应用图标": "old.png"},  ← 用于回撤
#  "final_status": "ONLINE",
#  "row_data": {"ID": "11945", ..., "_status": "ON", ...}}            ← 修改后最新整行
```

**重要注意**：
- 所有函数**同步阻塞**，单次10-60秒，放线程池或后台任务跑
- 内部有**单实例锁**，同时只能一个在执行，第二个会返回 EXECUTOR_BUSY
- 不需要传 task_id / contract_version 等协议字段，api.py 内部自动生成

---

## 6. 返回值统一格式

### 成功

```json
{
  "success": true,
  "message": "操作成功",
  // + 各函数特有的业务字段（见下方）
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

---

## 7. 各函数返回的全量数据

**核心设计**：不管是创建、修改还是定位，调用方拿到的都是该应用/渠道在后台的**最新完整信息**，可直接存库。

### create_channel 返回

| 字段 | 类型 | 说明 |
|------|------|------|
| actual_channel_name | str | 实际渠道名（重名自动加后缀） |
| channel_data | dict | 后台API返回的完整渠道记录 |
| channel_data.id | str | **渠道ID**，存库后精确匹配用 |
| channel_data.channel | str | 渠道名 |
| channel_data.basePlatform | str | 底座ID（2=华为底座2.0） |
| channel_data.creator | str | 创建人 |
| channel_data.createTime | str | 创建时间 |
| channel_data.appNum | int | 该渠道下应用数量 |

### create_app 返回

| 字段 | 类型 | 说明 |
|------|------|------|
| cloud_app_link | str | **长链接**，回填Excel用 |
| cloud_app_short_link | str | 短链接 |
| row_data | dict | **整行全量数据**（17个字段，动态读表头） |
| row_data.ID | str | **平台ID**，存库后精确匹配用 |
| row_data.应用名称 | str | |
| row_data.所属渠道 | str | |
| row_data.应用链接 | str | 短链接 |
| row_data.长连接 | str | 长链接 |
| row_data.底座 | str | |
| row_data.结算类型 | str | |
| row_data.创建时间 | str | |
| row_data.创建人 | str | |
| row_data._status | str | ON/OFF |

### update_app 返回

| 字段 | 类型 | 说明 |
|------|------|------|
| updated_fields | list | 实际修改了哪些字段（后台label） |
| previous_values | dict | **修改前原值**，可用于回撤 |
| final_status | str | 修改后状态（ONLINE/OFFLINE） |
| row_data | dict | **修改后整行全量数据**，格式同 create_app |

### locate_app 返回

| 字段 | 类型 | 说明 |
|------|------|------|
| cloud_app_link | str | 长链接 |
| cloud_app_short_link | str | 短链接 |
| match_confidence | str | HIGH / MEDIUM / AMBIGUOUS |
| row_data | dict | **整行全量数据**，格式同 create_app |
| row_data.ID | str | **平台ID**，存库后下次精确匹配 |

> row_data 是动态读取后台表头的，139后台增加/调整列不影响，新列自动包含。

---

## 8. 图片处理约定

修改应用时，图片字段传 HTTP URL：

```
http://127.0.0.1:8000/files/icons/icon_001.png
```

执行端内部行为：下载图片 → 上传到139后台 → 删临时文件。

**FastAPI 需要做的**：
1. 从 Excel 提取图片
2. 存到本地目录（如 `data/icons/`）
3. 用 FastAPI StaticFiles 暴露 HTTP 服务
4. 在 JSON 中传 URL 给执行端

---

## 9. 幂等和执行锁

执行端内部有 SQLite，每次执行会记录：
- execution_id（唯一执行ID）
- idempotency_key（防重复）
- execution_state（ACCEPTED → RUNNING → FINAL）
- business_status（SUCCESS / FAILED / UNKNOWN）
- output_json（完整返回值，含 row_data / channel_data）
- previous_values（修改前的值，用于回撤）

同一个 idempotency_key 重复调用只返回原结果。同时只有一个写操作能执行。

**不需要重新实现这些**，api.py 已经封装好了。但可以通过 `get_execution` 查历史记录，拿回完整数据。

---

## 10. 典型批次流程

```
FastAPI 收到一批 Excel（10行）

步骤1: get_status() → 确认 READY

步骤2: 逐行处理：

  【新建场景】
  行1: create_channel("he0820") → 存 channel_data.id=969
  行1: create_app(actual_channel_name="he0820", ...) → 存 row_data.ID=11945
  行1: 回填 Excel（cloud_app_link 长链接）

  【修改场景 - 自动化创建过的】
  行2: 从 SQLite 查到 row_data.ID=11945 → 直接 update_app()
  行2: 回填 Excel

  【修改场景 - 历史手动创建的】
  行3: locate_app(channel_name="he0818", app_name="中国移动云盘-测试")
       → 返回 row_data.ID=11945 → 存库
  行3: update_app(app_name="中国移动云盘-测试", fields_to_update={...})
  行3: 回填 Excel

步骤3: 全部完成，通知业务人员
```

---

## 11. SQLite 存储建议

FastAPI 自己的 SQLite（和执行端的 SQLite 是两个独立的库）建议存：

| 表 | 字段 | 说明 |
|----|------|------|
| apps | platform_id (PK) | 平台ID（来自 row_data.ID） |
| | app_name | 应用名称 |
| | channel_id | 渠道ID（来自 channel_data.id） |
| | channel_name | 渠道名 |
| | cloud_app_link | 长链接 |
| | cloud_app_short_link | 短链接 |
| | base | 底座 |
| | settlement_type | 结算类型 |
| | status | 状态（ON/OFF） |
| | row_data_json | 整行数据JSON（完整快照） |
| | created_at | 首次创建时间 |
| | updated_at | 最后更新时间 |
| channels | channel_id (PK) | 渠道ID（来自 channel_data.id） |
| | channel_name | 渠道名 |
| | base_platform | 底座 |
| | channel_data_json | 渠道完整记录JSON |
| operations | id (PK) | 操作记录ID |
| | batch_id | 批次ID |
| | app_id | 关联应用 |
| | operation_type | CREATE_CHANNEL / CREATE_APP / UPDATE_APP / LOCATE_APP |
| | execution_id | 执行端返回的 execution_id |
| | success | 是否成功 |
| | previous_values_json | 修改前原值（用于回撤） |
| | row_data_json | 操作后的完整行数据 |
| | error_message | 失败原因 |
| | created_at | 操作时间 |

---

## 12. 环境要求

- Python 3.11+（项目自带 venv）
- Chrome 或 Edge 浏览器
- 环境变量 `AMOO_SECRET_KEY`（密码解密用）
- Chrome 以 `--remote-debugging-port=9222` 常驻（browser_guard.ps1 自动维护）

---

## 13. 常用错误码速查

| error_code | 含义 | 建议处理方式 |
|------------|------|----------------|
| EXECUTOR_BUSY | 执行端忙 | 等几秒重试 |
| NOT_LOGGED_IN | 登录失败 | 调 login() 再试一次 |
| FIELD_VALIDATION | 字段校验失败 | 检查传入参数 |
| CHANNEL_VERIFY_FAILED | 渠道创建后没验证到 | 转人工，看截图 |
| SAVE_FAILED | 保存失败 | 看 message 里的表单校验错误 |
| PUBLISH_FAILED | 上线失败 | 转 MANUAL_CHECK |
| APP_NOT_FOUND | 没找到应用 | 检查渠道名/应用名是否正确 |
| APP_AMBIGUOUS | 匹配到多个 | 看 matches 数组，人工确认 |
