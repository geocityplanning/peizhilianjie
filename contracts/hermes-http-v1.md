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