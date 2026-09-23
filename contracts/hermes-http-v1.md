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
| `input.resource_fallback_page` | 资源不足兜底页 | Excel 中的资源兜底页字段；必填。执行端仅在唯一可见复制对话框中以原生输入填写、框架组件 value/model 回读一致且切换页签往返后仍一致；保存后须先以新增应用 ID 重置筛选；每次重置前安装观察器，且仅本轮同一内存 request 绑定的完成 2xx、结构完整、逐请求可证明 `target_absent`（包括空 `channelName`/`channelNames` 及空 `appStatus`/`appShortUrl`/`appLongUrl`/`placeList`/`categoryList`/`balanceType`/`id`；任一筛选字段非空即为 `target_filtered`；`platformType` 仅可为 0/1/2 或等价十进制字符串的页面分区上下文，不作为筛选；其它字段、非法 `platformType`、`unknown` 或 CDP 请求载荷不可读均拒绝）的列表响应可与两次稳定未筛选 DOM 一致后，才可回到第一页并逐页定位唯一主行；page-event fallback 中仅 GET/HEAD 的无 body 可视为已知空载荷；POST/PUT/PATCH/DELETE 的 body 为 `None`、读取异常或请求状态未知均为 `unknown`，不得读取响应正文或解锁恢复门。若重置后未观察到任何列表请求，刷新预算在调用前即消耗，最多一次且仅在唯一可见主列表 table、唯一 pager 与其共同容器内唯一筛选 form 中点击精确“搜索”触发只读刷新；歧义或异常均不再点击，仍无请求即 fail-closed。门未通过时仅输出以下 allowlist 枚举之一：`no_complete_unfiltered_response`、`unfiltered_response_structure_unparseable`、`unfiltered_response_dom_mismatch`、`unfiltered_dom_unstable`；门通过但完整分页仍未找到 ID 时为 `unfiltered_gate_passed_id_not_found`。`target_absent_2xx` 仅表示匹配到的 target-absent 2xx 请求数，不等于完整结构响应；每个该类响应恰好落入固定 `response_structure` 桶之一：`response_body_missing`、`response_body_read_error`、`base64_decode_error`、`json_unparsable`、`json_non_object`、`required_structure_missing`、`complete`；仅 `complete` 可进入结构记录和后续 DOM 稳定门。当前 TEST 已取证的精确兼容结构可为 `data.totalCount`、`data.appInfoList`、`data.pageCount`，三项仍必须同时有效；`appInfoList` 仅是 `data` 第一层的固定列表 allowlist 项，保留 `list`/`records`/`rows` 原优先级，不接受任意数组、递归路径或其它别名。公共日志、HTTP 回执、数据库和普通诊断始终禁止动态响应字段名；唯一例外是当前环境精确为 `TEST`、`HERMES_LIST_RESPONSE_SCHEMA_OUTLINE_ONCE` 显式开启且 `HERMES_LIST_RESPONSE_SCHEMA_OUTLINE_PATH` 指向本次执行端进程新建的 `/private/tmp` 单层私有 `0700` 目录中的新绝对文件时，对 `required_structure_missing` 响应生成值零泄露的一层 `response_schema_outline_v1` 私有证据。仓库/共享路径拒绝必须在创建目录前完成；目录必须归当前 UID、父目录/祖先/目标均不得为符号链接，文件以同目录 `O_EXCL`/`O_NOFOLLOW` 临时文件、显式 `fchmod(0600)`、`fsync` 和不覆盖安装原子创建；最终以 `lstat` 校验常规文件及精确 `0600`；校验异常或身份/权限不匹配时不得再按目标路径删除，以免误删同 UID 竞争替换文件，保持 fail-closed。临时文件仅在本轮 `O_EXCL` 创建成功后由异常或中断路径清理，且不影响业务结果。每次 `create_app` 保存后未筛选定位阶段的最多三次 gate 尝试共享同一内存 batch，仅在阶段结束的 `finally`（含提前返回）写入一次。正文 1 MiB 限制只适用于私有 outline 捕获，超过时不进入该私有 `json.loads` 或键遍历并形成固定有界未取证状态；公共 `response_structure` 七桶、解析和业务门不因正文大小改变。不安全键计数和其扫描预算均有界，封顶时标记 `unsafe_key_count_capped`。该配置不属于 Hermes 公共接口，私有路径和文件不得进入 Git、回执、日志、HTTP、数据库、模型或回填，提取契约结论后须删除。私有证据最多三条，不参与七桶、解析、成功记录或任何业务门。诊断只含枚举、布尔和有界计数：仅固定 `observer source`（CDP/page-event）、`payload state`（inline/fetched/unavailable/read-error/not-applicable）、`payload shape`（empty/json-object/form-pairs/non-object/unparsable）、`unknown reason`（unsupported-key/unsupported-value-shape/unreadable-payload/unsupported-method/unparsable）及 query/body 可知性计数、固定 `context_relation`（equal/different/unreadable，仅诊断、不参与放行）和保存后渠道窄路径唯一三锚行的固定 `post_save_narrow_row_switch_state`（switch_checked/switch_unchecked/unreadable，仅两次只读 DOM 检查、仅表示 UI 开关 checked 状态，不推导业务已/未启用且不参与任何门或放行）；禁止 request-id、URL、query、headers、body、页面文本、业务原值、响应正文、动态字段名或字段值；ID、应用名、渠道三锚的匹配列必须各唯一，三锚须在同一稳定逻辑行键上连续读取两次且点击复制前再次精确核验。逻辑行键优先主行原生 key；仅单一候选可在内存中用页码、原始行索引和三锚构造，绝不写入日志或回执。仅三锚和行键均不变时，才可从该行打开复制对话框；复制对话框页签必须在唯一真实可见 dialog 中以物理点击确认目标页签 `is-active`、`aria-selected=true`，且其 `aria-controls` 映射 pane 非隐藏；页签发现与点击后激活证明均使用独立有界时限。保存仅允许唯一真实可见 tabbed dialog 内规范化文本精确为“保存”、未禁用且具 Vue click/submit handler 的唯一语义控件；其父级布局几何不可见不构成拒绝理由。同一行 DOM 镜像仅在相同非空原生 key 及三锚完全一致时可折叠，对兜底字段执行两次稳定 DOM/model 回读一致时才可返回成功。复制表单的应用名/渠道不作为持久化身份锚。 |
| `input.settlement_type` | 结算类型 | Excel 中的结算类型字段；执行端仅在唯一真实可见复制 dialog 的唯一控件中以物理交互打开下拉、唯一精确选项中选择，并确认下拉关闭且 DOM/框架 value-model 均与目标一致；任一不可确认时保存前停止；确认后可直接保存，不以无关页签切换作为额外门。 |
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

从保存按钮成功点击开始，保存窗口必须唯一归因至一个同 request-id 完成响应且业务信封明确成功的同源写请求；HTTP 2xx 本身不构成业务成功。仅当该唯一 CDP 候选正文为 `missing`/`read_error` 时，page-response 回退才可对当前观察窗口内同源非列表写 response 的 `response.request` 即时投影 method/path-hash/status；仅恰好一个与候选三项投影一致、无溢出的同一响应可作为 page 证据源。`loadingFinished` 回调仅登记 completed/pending，正文读取必须在回调外进行；从首次实际正文读取即将发生时起，CDP `Network.getResponseBody` 与该同一 page response 的 `finished()`/`text()` 序列合计最多三次、共享 450ms 证据窗口，每一次实际 CDP 读取或 page 序列均消耗一次。该读取级重试仅为同一响应的证据可读性重试，不得触发第二次保存、启用、搜索、重建、换幂等键或任何业务重试；成功或共享预算耗尽才终态，耗尽仍保持 `SAVE_FAILED` / `UNKNOWN` / `QUERY`。不得依赖 Python wrapper 对象身份，不得保留 URL、query、headers、request-id、正文或业务原值；零个、多个、异常、非2xx、不可解析或非成功信封均保持既有 `SAVE_FAILED` / `UNKNOWN` / `QUERY`。保存观察等待结束后、进入判定分支前，执行端恰好输出一条机器可读的脱敏诊断：唯一候选仅可含 method、path 哈希、HTTP 状态、固定 `response_body.fetch_state`（available/missing/read_error/base64_decoded/base64_decode_error）、固定 `response_body.parse_state`（not_applicable/json_unparsable/json_non_object/envelope_missing/envelope_classified）及业务 outcome/code/message 的 present/length/hash；零或多候选仅可含 outcome/candidate_count，严禁 URL、query、headers、body 或原文。若候选不唯一、无响应/超时、非2xx、业务拒绝或业务信封无法判断，以及对话框未关闭、新应用 ID 无法唯一确认、主表 ID/应用名/渠道三锚不可读或不匹配、无法从已核验主表行打开复制对话框、资源不足兜底页不可核验、两次稳定回读不一致或验证对话框无法确认关闭，执行端均保留原错误码和阶段、返回 `next_action=QUERY`，并使 HTTP 原单保持 `UNKNOWN` / `UNKNOWN` / `adjudicated=false`。不得更换幂等键重建，只能查询或人工核验原单。保存点击前的失败仍是普通失败，不被扩张为 UNKNOWN。

保存业务成功后，执行端先在**当前渠道窄视图**从第一页跨页定位唯一主表行，要求 ID、应用名、渠道三锚、页码、行位及稳定逻辑键连续两次一致；以该仅内存 handle 打开复制对话框完成资源兜底页 DOM/model 连续双读，关闭后再次取得完全相同 handle。随后仅可对该行唯一未启用、未禁用开关执行一次原子点击：点击前可见 `.el-message-box__wrapper` 基线必须为零，观察器仅接受唯一同源非列表写请求的完整 2xx 响应和明确业务成功信封。确认框仅可为本次动作后唯一新出现的框，且其中唯一可见可用、规范化精确为“确定”的按钮最多点击一次；无框直发必须在整个有界窗口均无确认框。已启用/不可读开关、确认框歧义、零或多写请求、非 2xx、正文/信封不可判定均不得再次点击，返回 `UNKNOWN` / `QUERY`。写成功后丢弃所有窄视图 locator、响应和 DOM 观察；D-044 方案 B 随即安装全新有界列表观察器，在唯一可见主列表所属唯一筛选表单内填入本任务完整渠道名和完整应用名，原子复核后仅触发一次精确“搜索”。只有本轮观察器捕获且可证明同时携带这两个精确筛选值、无未登记动态字段、完成、HTTP 2xx、结构完整的唯一列表响应，才可与当前精确视图 DOM 对齐；DOM 必须连续两次稳定读到恰好一条同 ID、应用名、渠道三锚记录且逻辑行键不变，方可分组或返回成功。旧 locator、旧观察器、旧响应和旧 DOM 不可复用；该终验不得重置筛选、不得未筛选跨页扫描或回退放行。筛选值不可读、缺任一筛选项、多个请求/候选、响应—DOM 不一致或任一稳定性失败同样保持原单 `UNKNOWN` / `QUERY`，不得二次启用、二次搜索、重试、换幂等键、重建、删除或处理历史对象。启用前开关必须由本次原子点击内唯一可读未启用状态拒绝性核验；一旦写请求已唯一确认完整 2xx 和业务成功，立即丢弃旧开关 DOM、locator、观察器和响应证据，不再作写后开关回读。稳定 handle 不写日志、HTTP 回执、数据库或外部信封。

启用动作窗口的 `no_candidate` 脱敏诊断按 CDP 与 page-event 分别记录有界 `accepted_write`、`method_rejected`、`list_rejected`、`origin_rejected`、`missing_request_id`、`observer_event_unseen`，并仅允许 same/cross-origin 计数、固定 HTTP method 枚举及最多三个 16 位 path 哈希；确认框仅记录固定轨迹、唯一确认按钮是否点击/关闭和窗口结束后的开关枚举。不得记录 URL、host、query、headers、body 或业务原值。该诊断不参与成功判定：`no_candidate`、观察器异常、不可读响应、非 2xx、业务拒绝或多候选仍为既有 `PUBLISH_FAILED` / `PUBLISH` / `UNKNOWN` / `QUERY`。

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
