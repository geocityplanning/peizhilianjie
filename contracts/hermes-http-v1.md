# Hermes REAL Executor HTTP v1

Hermes 调用本服务的 8001 端口。默认执行真实浏览器自动化；联调时可通过环境变量切换为同契约的 FAKE 假跑模式。

`HERMES_EXECUTOR_FAKE_MODE` 默认为关闭（`false`）。开启后，四个 HTTP 地址、鉴权、请求字段和回执包络都不变，但 `create-channel`、`create-app` 只写入执行记录并返回可测的假成功数据，不启动浏览器。生产和真实验收必须保持关闭。

## 连接

```text
Base URL: http://127.0.0.1:8001
Authorization: Bearer <双方约定的 Token>
X-Contract-Version: http-executor.v1
Content-Type: application/json
```

只开放四个接口：

| 功能 | 方法和路径 |
|---|---|
| 状态 | `POST /v1/exec/info` |
| 创建渠道 | `POST /v1/exec/create-channel` |
| 创建应用、上线、分组 | `POST /v1/exec/create-app` |
| 查询原任务 | `POST /v1/exec/query` |

## GATE-08 探活

调用 `POST /v1/exec/info` 时，执行端在同一层返回以下探活字段：

```json
{
  "status": "SUCCESS",
  "acceptable": true,
  "login_valid": true,
  "unknown_inflight": false
}
```

- `status=SUCCESS` 表示探活请求和数据库检查成功，不代表创建业务成功。
- `acceptable=true` 表示当前可以接收新的创建任务；执行端忙碌或 REAL 登录无效时为 `false`。
- FAKE 模式的 `login_valid` 固定为 `true`；REAL 模式会检查已运行的 139 浏览器会话和只读渠道列表探测，不会自动启动浏览器或执行登录。
- `unknown_inflight=true` 表示 `active_execution_correlation_id` 有值，此时 `acceptable` 必须为 `false`。
- `info` 请求体可以带 `environment`、`run_id`，执行端不因这两个字段返回 400。
- REAL 登录探活使用约 10 秒的端到端总预算，覆盖 Playwright 启动、CDP 连接、页面 Token 读取和心跳请求；任一步骤超时、网络异常或响应解析异常均按登录不可确认处理，返回 HTTP 200、`login_valid=false`、`acceptable=false`，不会让 `info` 无限等待。
## 调用顺序


1. Hermes 调用 `create-channel`。
2. 读取成功回执中的 `data.actual_channel_name`。
3. 将该值作为 `actual_channel_name` 调用 `create-app`。
4. 读取 `data.app_id` 和 `data.app_link`。
5. 请求超时、连接断开或结果为 `UNKNOWN` 时，只调用 `query` 查询原任务，不重新创建。

创建接口同步等待真实自动化结束并返回最终回执。Hermes 应串行发送任务；同一业务动作重试时必须保持原 `task_id`、`snapshot_version` 和 `idempotency_key` 不变。

## 请求

创建渠道：

```json
{
  "task_id": "HERMES-20260908-0001",
  "operation": "create_channel",
  "environment": "TEST",
  "snapshot_version": "20260908-V1",
  "idempotency_key": "HERMES-20260908-0001-create-channel",
  "input": {
    "requested_channel_name": "甘肃体验有礼-0908测试",
    "base_platform": "掌厅"
  }
}
```

创建应用：

```json
{
  "task_id": "HERMES-20260908-0001",
  "operation": "create_app",
  "environment": "TEST",
  "snapshot_version": "20260908-V1",
  "idempotency_key": "HERMES-20260908-0001-create-app",
  "input": {
    "application_type": "云盘",
    "business_object": "中国移动云盘-0908测试",
    "actual_channel_name": "甘肃体验有礼-0908测试-1",
    "jump_address": "mcloud://main/webView?params=test",
    "resource_fallback_page": "https://example.invalid/fallback",
    "settlement_type": "云盘",
    "group_name": "10086",
    "ref_cloud_app_link": "https://plus.buy.139.com/mccloudgame/#/?i=KWcMvfaFlhw="
  }
}
```

`business_object` 是应用名称，不与活动名称拼接。`actual_channel_name` 必须来自创建渠道回执。`ref_cloud_app_link` 可省略，省略时执行端按 `application_type` 使用内置参考应用长链接。
## 字段映射

请求体中的业务字段位于 `input` 下；执行结果中的业务字段位于 `data` 下。

### Hermes 任务字段

| JSON 字段 | 含义 | Excel/业务对应 |
|---|---|---|
| `task_id` | Hermes 业务任务编号，整个业务任务保持稳定 | 一次上架任务的稳定编号 |
| `operation` | 当前执行动作 | `create_channel` 或 `create_app` |
| `environment` | 执行环境 | 当前使用 `TEST` |
| `snapshot_version` | Hermes 本次数据快照或规则版本 | Hermes 生成的批次/版本号 |
| `idempotency_key` | 当前动作的幂等键，重试时必须保持不变 | 同一业务动作的唯一动作编号 |
| `execution_correlation_id` | 执行端生成的执行记录编号，不是应用 ID | 仅在执行端返回后保存，用于 `query` 查询 |

`execution_correlation_id` 不属于创建请求字段。Hermes 提交创建请求后保存返回值；超时、断线或收到 `UNKNOWN` 时，使用它查询原任务，不能重新创建。

### 创建渠道字段

| JSON 字段 | 含义 | Excel/业务对应 |
|---|---|---|
| `input.requested_channel_name` | Hermes 最终确定的渠道名称 | Excel 中活动、业务等字段解析并组装后的渠道名；执行端不再自行拼接 |
| `input.base_platform` | 创建渠道时的平台/底座 | Excel 或 Hermes 的平台配置，可选 |

`activity_name` 是活动名称，不直接传给执行端。Hermes 使用它参与组装 `requested_channel_name`，然后将最终渠道名传给 `create-channel`。

创建渠道成功后，执行端返回：

```json
{
  "data": {
    "requested_channel_name": "甘肃体验有礼-0909测试",
    "actual_channel_name": "甘肃体验有礼-0909测试-1",
    "channel_data": {}
  }
}
```

`actual_channel_name` 可能因渠道查重增加后缀。Hermes 创建应用时必须使用返回的 `data.actual_channel_name`，不能继续使用原始渠道名。

### 创建应用字段

| JSON 字段 | 含义 | Excel/业务对应 |
|---|---|---|
| `input.application_type` | 应用类型 | 云盘、掌厅 |
| `input.business_object` | 应用名称 | 中国移动云盘、中国移动等；不是业务对象和活动名称的拼接值 |
| `input.actual_channel_name` | 实际渠道名称 | 来自创建渠道接口返回的 `data.actual_channel_name`，不是直接取 Excel |
| `input.jump_address` | 应用配置调起路径/跳转地址 | Excel 中的跳转地址字段 |
| `input.resource_fallback_page` | 资源不足兜底页 | Excel 中的资源兜底页字段；必填。执行端仅在唯一可见复制对话框中以原生输入填写、框架组件 value/model 回读一致且切换页签往返后仍一致，并在保存后按新增应用 ID 重新打开对话框、核验应用名/渠道身份及两次稳定回读一致时才可返回成功。 |
| `input.settlement_type` | 结算类型 | Excel 中的结算类型字段 |
| `input.group_name` | 应用上线后的分组，可选 | 有值时例如 `10086`；省略或为空时不修改复制件原分组 |
| `input.ref_cloud_app_link` | 复制源应用的长链接 | Excel 中的参考应用长链接/复制源长链接，可选 |

创建应用请求结构：

```json
{
  "operation": "create_app",
  "input": {
    "application_type": "云盘",
    "business_object": "中国移动云盘-0909测试",
    "actual_channel_name": "甘肃体验有礼-0909测试-1",
    "jump_address": "mcloud://main/webView?params=test",
    "resource_fallback_page": "https://example.invalid/fallback",
    "settlement_type": "云盘",
    "group_name": "10086",
    "ref_cloud_app_link": "https://plus.buy.139.com/mccloudgame/#/?i=KWcMvfaFlhw="
  }
}
```

保存后资源不足兜底页无法重新打开、对象身份/页签/控件不可核验、两次稳定回读不一致或验证对话框无法确认关闭时，执行端返回现有错误信封：`code=RESOURCE_FALLBACK_VERIFY_FAILED`、`stage=VERIFY`、`next_action=MANUAL_CHECK`；该次保存可能已发生，业务状态在 HTTP 原单中也必须保持 `UNKNOWN`，不得更换幂等键重建，只能人工核验原单。

### 应用执行结果字段

| JSON 字段 | 含义 | Excel/业务对应 |
|---|---|---|
| `data.app_id` | 新创建应用的后台 ID | 自动化在应用列表中识别到的新应用 ID |
| `data.app_link` | 新创建应用后台“长连接”列的 HTTP/HTTPS 值 | 自动化从应用列表“长连接”列读取；用于回填待配置链接表 |
| `data.application_name` | 新创建应用名称 | 通常等于请求中的 `input.business_object` |
| `data.actual_channel_name` | 新应用所属的实际渠道名 | 创建渠道接口返回的最终渠道名 |
| `data.completed_stages` | 已完成的自动化阶段 | `CREATE_SAVE`、`ENABLE`、`COMPLETED`；只有传入非空 `group_name` 时才包含 `SET_GROUP` |
| `data.row_data` | 创建应用的后台列表行数据 | 自动化校验证据，Hermes 主要使用 `app_id` 和 `app_link` |
| `data.channel_data` | 创建渠道的底层结果 | 自动化返回的渠道附加信息，Hermes 主要使用 `actual_channel_name` |

应用链接的区别：

```text
Excel 参考应用长链接
  → input.ref_cloud_app_link
  → 自动化搜索并复制参考应用
  → 创建新应用
  → data.app_link              http:// 或 https:// 后台“长连接”列的值，用于回填
  → 后台“应用链接”列              仅内部读取，不放入 8001 HTTP 回执
```

### 当前不使用的字段

| JSON 字段 | 含义 | Excel/业务对应 |
|---|---|---|
| `activity_name` | 活动名称 | 由 Hermes 内部用于组装渠道名，不直接放进 `create_app.input` |
| `ref_app_id` | 旧的参考应用 ID | 当前不使用，应改用 `input.ref_cloud_app_link` |
| `download_link` | 下载链接 | 当前 B01 `create_app` 不接收，不能放进 `input` |
| `fixed_fields` | 任意扩展字段 | 当前 B01 不接收，不能放进 `input` |
| `original_link` | 原链接 | 本轮不接收，不能放进 `input` |
| `app_download_link` | 应用下载链接 | 本轮不接收，不能放进 `input` |
| `settlement_province` | 结算省份 | 本轮不接收，不能放进 `input` |
查询：

```json
{
  "operation": "create_app",
  "environment": "TEST",
  "snapshot_version": "20260908-V1",
  "execution_correlation_id": "EXEC-REPLACE-ME"
}
```

也可只用原 `idempotency_key` 查询，或使用 `task_id + operation + environment` 查询。

## 回执

判断顺序：先看 HTTP 状态，再看 `state` 和 `status`。HTTP 200 不等于执行成功。
主要判断规则：

| `state` | `status` | 含义 | 是否已调用真实自动化 | Hermes 处理规则 |
|---|---|---|---|---|
| `SUCCEEDED` | `SUCCESS` | 自动化完整成功 | 是 | 读取 `data`，结束当前动作；创建应用时必须确认 `data.app_id`、`data.app_link` 和 `data.completed_stages` 完整 |
| `FAILED` | `TECH_FAIL` | 技术执行失败，例如页面、浏览器、网络或自动化步骤异常 | 是，可能已完成部分阶段 | 读取 `data.completed_stages` 和 `data.failed_stage`，转人工检查；不得直接重新创建 |
| `FAILED` | `BUSINESS_REJECT` | 业务规则或后台校验拒绝 | 可能已调用，具体以阶段数据为准 | 展示 `error` 信息并修正业务数据；确认原任务状态前不得重新创建 |
| `UNKNOWN` | `UNKNOWN` | 执行结果不确定，例如服务中断、浏览器异常或返回链路断开 | 可能已调用，不能判断最终结果 | 只能使用 `execution_correlation_id` 调用 `query`；禁止再次调用创建接口 |
| `REJECTED` | `NOT_EXECUTED` | 当前请求在执行前被拒绝 | 否，本次请求没有进入真实自动化 | 根据 `error.error_code` 修正请求、等待执行端空闲或停止；修正后才可提交新的动作 |

状态字段不能单独判断，必须同时读取 `state` 和 `status`。例如：

- `SUCCEEDED + SUCCESS` 才代表自动化完整成功；只有 HTTP 200 不能代表成功。
- `FAILED + TECH_FAIL` 不是“没有创建任何数据”的保证，必须查看 `completed_stages` 和后台实际状态。
- `UNKNOWN + UNKNOWN` 不能当作失败重试，因为原操作可能已经在浏览器中生效。
- `REJECTED + NOT_EXECUTED` 表示本次请求未进入真实自动化，但同一个幂等键是否已有原任务，仍应根据错误码和原执行记录判断。

常用 `next_action`：

| `next_action` | Hermes 行为 |
|---|---|
| `STOP` | 结束当前动作，不自动重试 |
| `QUERY` | 使用原 `execution_correlation_id` 查询，不创建新任务 |
| `MANUAL_CHECK` | 转人工检查浏览器、后台页面和执行证据 |

创建应用完整成功的最低判断条件：

```text
HTTP 状态为 200
state = SUCCEEDED
status = SUCCESS
data.app_id 有值
data.app_link 有值，且为 http:// 或 https:// 开头
data.completed_stages 包含 CREATE_SAVE、ENABLE、COMPLETED；传入非空 group_name 时还必须包含 SET_GROUP
```

- `state=SUCCEEDED` 且 `status=SUCCESS`：成功。
- `state=FAILED`：自动化已明确失败。
- `state=UNKNOWN`：结果不确定，只查询原任务，不自动重做。
- `state=REJECTED`：未执行，按错误信息修正请求或等待执行端空闲。

创建渠道成功数据：

```json
{
  "requested_channel_name": "甘肃体验有礼-0908测试",
  "actual_channel_name": "甘肃体验有礼-0908测试-1",
  "channel_data": {}
}
```

创建应用成功数据：

```json
{
  "app_id": "12008",
  "app_link": "https://example.invalid/#/?i=12008",
  "application_name": "中国移动云盘-0908测试",
  "actual_channel_name": "甘肃体验有礼-0908测试-1",
  "completed_stages": ["CREATE_SAVE", "ENABLE", "SET_GROUP", "COMPLETED"],
  "row_data": {}
}
```

精确结构以 `contracts/hermes-http-v1.openapi.yaml` 为准，契约版本只通过 `X-Contract-Version` 请求头传入。
