# Hermes REAL Executor HTTP v1

Hermes 调用本服务的 8001 端口，服务执行真实浏览器自动化。

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

下表同时说明 Hermes 请求字段、自动化返回字段，以及它们与 Excel/业务数据的对应关系。

| JSON 字段 | 含义 | Excel/业务对应 |
|---|---|---|
| `task_id` | Hermes 业务任务编号 | 一次上架任务的稳定编号 |
| `operation` | 当前执行动作 | `create_channel` 或 `create_app` |
| `environment` | 执行环境 | 当前使用 `TEST` |
| `snapshot_version` | 本次数据和规则快照版本 | Hermes 生成的批次/版本号 |
| `idempotency_key` | 当前动作的幂等键 | 同一动作重试时必须保持不变 |
| `input.requested_channel_name` | Hermes 最终确定的渠道名称 | Excel 中活动、业务等字段解析并组装后的渠道名；执行端不再自行拼接 |
| `input.base_platform` | 创建渠道时的平台/底座 | Excel 或 Hermes 的平台配置，可选 |
| `input.application_type` | 应用类型 | `云盘` 或 `掌厅` |
| `input.business_object` | 应用名称 | `中国移动云盘`、`中国移动` 等；不是业务对象和活动名称的拼接值 |
| `input.actual_channel_name` | 应用所属的实际渠道名称 | `create_channel` 返回的 `data.actual_channel_name` |
| `input.jump_address` | 应用配置调起路径/跳转地址 | Excel 中的跳转地址字段 |
| `input.resource_fallback_page` | 资源不足兜底页 | Excel 中的资源兜底页字段 |
| `input.settlement_type` | 结算类型 | Excel 中的结算类型字段 |
| `input.group_name` | 应用上线后的分组 | 例如 `10086` |
| `input.ref_cloud_app_link` | 复制源应用的长链接 | Excel 中的参考应用长链接；可省略，执行端按应用类型使用默认复制源 |
| `data.requested_channel_name` | 原始请求渠道名 | 创建渠道请求中的 `input.requested_channel_name` |
| `data.actual_channel_name` | 创建后实际渠道名 | 渠道查重后的最终名称，可能带后缀；创建应用必须使用此值 |
| `data.app_id` | 新创建应用的后台 ID | 自动化在应用列表中识别到的新应用 ID |
| `data.app_link` | 新创建应用的 HTTP/HTTPS 长链接 | 自动化生成的新应用长链接，不是 Excel 中的参考复制源链接 |
| `data.app_short_link` | 新创建应用的短链接 | `capp://...` 形式的短链接 |
| `data.application_name` | 新创建应用名称 | 通常等于请求中的 `input.business_object` |
| `data.completed_stages` | 已完成的自动化阶段 | `CREATE_SAVE`、`ENABLE`、`SET_GROUP`、`COMPLETED` |
| `data.channel_data` | 创建渠道的底层结果 | 自动化返回的渠道附加信息，Hermes 主要使用 `actual_channel_name` |
| `data.row_data` | 创建应用的底层列表行数据 | 自动化校验证据，Hermes 主要使用 `app_id` 和 `app_link` |
| `execution_correlation_id` | 执行端执行记录编号 | 用于 `query` 查询，不是应用 ID |
| `state` | 执行状态 | `SUCCEEDED`、`FAILED`、`UNKNOWN`、`REJECTED` 等 |
| `status` | 对 Hermes 的业务状态 | `SUCCESS`、`TECH_FAIL`、`BUSINESS_REJECT`、`UNKNOWN`、`NOT_EXECUTED` |
| `activity_name` | 活动名称 | 不直接传给执行端；由 Hermes 用于组装 `requested_channel_name` |
| `ref_app_id` | 旧的参考应用 ID字段 | 当前不使用；应改用 `input.ref_cloud_app_link` |
| `download_link` | 下载链接 | 当前 B01 `create_app` 不接收，不能放进 `input` |
| `fixed_fields` | 任意扩展字段 | 当前 B01 不接收，不能放进 `input` |

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
  "app_short_link": "capp://12008",
  "application_name": "中国移动云盘-0908测试",
  "actual_channel_name": "甘肃体验有礼-0908测试-1",
  "completed_stages": ["CREATE_SAVE", "ENABLE", "SET_GROUP", "COMPLETED"],
  "row_data": {}
}
```

精确结构以 `contracts/hermes-http-v1.openapi.yaml` 为准，契约版本只通过 `X-Contract-Version` 请求头传入。