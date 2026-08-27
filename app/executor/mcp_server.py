# -*- coding: utf-8 -*-
"""
MCP Server — 契约版（v0.1-draft）。

暴露 5 个 Tool，完全对齐《MCP 执行端接口契约草案》：

  get_executor_info   — 调用前检查（浏览器/登录/锁/版本/环境）
  create_channel      — CommonWriteRequest → ExecutionResult
  create_app           — CommonWriteRequest → ExecutionResult
  update_app           — CommonWriteRequest → ExecutionResult（增量更新）
  get_execution        — 超时/恢复时只读查询原执行

运行: python mcp_server.py
依赖: 浏览器常驻调试端口 9222 + 环境变量 AMOO_SECRET_KEY
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mcp.server.mcpserver.server import MCPServer
from core import executor as ex
from core.error_codes import err, NEXT_QUERY, NEXT_MANUAL
from actions.ensure_login import ensure_login, is_logged_in
from actions.create_channel_v2 import execute_create_channel
from actions.create_app_v2 import execute_create_app
from actions.update_app_v2 import execute_update_app
from actions.locate_app import execute_locate_app

mcp = MCPServer("amoo-139-executor")


# ---- Tool 1: get_executor_info ----

@mcp.tool()
def get_executor_info() -> str:
    """调用前检查：浏览器是否可连、是否已登录、环境、契约版本、执行锁状态。"""
    import requests
    # 浏览器 CDP 检查
    edge_ok = False
    reason = None
    try:
        r = requests.get("http://127.0.0.1:9222/json/version", timeout=3)
        edge_ok = r.status_code == 200
    except Exception:
        reason = "BROWSER_NOT_READY"

    # 登录态检查
    login_ok = False
    if edge_ok:
        try:
            from core import get_browser_page
            pw, browser, page = get_browser_page()
            login_ok = is_logged_in(page)
            pw.stop()
        except Exception:
            reason = "LOGIN_CHECK_FAILED"

    # 锁状态
    locked = ex.is_locked()
    active_exec = ex.get_active_execution_id() if locked else None

    state = ex.EXEC_NOT_READY
    if edge_ok and login_ok and not locked:
        state = ex.EXEC_READY
    elif locked:
        state = ex.EXEC_BUSY

    if not edge_ok and not reason:
        reason = "BROWSER_NOT_READY"
    elif not login_ok and not reason:
        reason = "NOT_LOGGED_IN"
    elif locked and not reason:
        reason = "EXECUTOR_BUSY"

    result = {
        "contract_version": ex.CONTRACT_VERSION,
        "environment": ex.ENVIRONMENT,
        "state": state,
        "supported_operations": ["CREATE_CHANNEL", "CREATE_APP", "UPDATE_APP", "LOCATE_APP"],
        "server_instance_id": f"executor-{ex.ENVIRONMENT.lower()}-01",
        "active_execution_id": active_exec,
        "reason_code": reason,
    }
    return json.dumps(result, ensure_ascii=False)


# ---- Tool 2: create_channel ----

@mcp.tool()
def create_channel_tool(request_json: str) -> str:
    """创建渠道。request_json 为 CommonWriteRequest JSON 字符串。"""
    try:
        req = json.loads(request_json)
    except Exception as e:
        return json.dumps(ex.build_result("", "CREATE_CHANNEL", "", accepted=False,
                          error=err("FIELD_VALIDATION", "PARSE", f"JSON 解析失败: {e}", "STOP")),
                          ensure_ascii=False)

    # 契约版本检查
    if req.get("contract_version") != ex.CONTRACT_VERSION:
        return json.dumps(ex.build_result(req.get("task_id", ""), "CREATE_CHANNEL",
                          req.get("idempotency_key", ""), accepted=False,
                          error=err("CONTRACT_VERSION_MISMATCH", "VALIDATE",
                          f"请求版本={req.get('contract_version')}, 服务端={ex.CONTRACT_VERSION}", "STOP")),
                          ensure_ascii=False)

    # 环境检查
    if req.get("environment") != ex.ENVIRONMENT:
        return json.dumps(ex.build_result(req.get("task_id", ""), "CREATE_CHANNEL",
                          req.get("idempotency_key", ""), accepted=False,
                          error=err("ENVIRONMENT_MISMATCH", "VALIDATE",
                          f"请求环境={req.get('environment')}, 实际={ex.ENVIRONMENT}", "STOP")),
                          ensure_ascii=False)

    result = execute_create_channel(req)
    return json.dumps(result, ensure_ascii=False)


# ---- Tool 3: create_app ----

@mcp.tool()
def create_app_tool(request_json: str) -> str:
    """创建应用。request_json 为 CommonWriteRequest JSON 字符串。"""
    try:
        req = json.loads(request_json)
    except Exception as e:
        return json.dumps(ex.build_result("", "CREATE_APP", "", accepted=False,
                          error=err("FIELD_VALIDATION", "PARSE", f"JSON 解析失败: {e}", "STOP")),
                          ensure_ascii=False)

    if req.get("contract_version") != ex.CONTRACT_VERSION:
        return json.dumps(ex.build_result(req.get("task_id", ""), "CREATE_APP",
                          req.get("idempotency_key", ""), accepted=False,
                          error=err("CONTRACT_VERSION_MISMATCH", "VALIDATE",
                          f"请求版本={req.get('contract_version')}, 服务端={ex.CONTRACT_VERSION}", "STOP")),
                          ensure_ascii=False)

    if req.get("environment") != ex.ENVIRONMENT:
        return json.dumps(ex.build_result(req.get("task_id", ""), "CREATE_APP",
                          req.get("idempotency_key", ""), accepted=False,
                          error=err("ENVIRONMENT_MISMATCH", "VALIDATE",
                          f"请求环境={req.get('environment')}, 实际={ex.ENVIRONMENT}", "STOP")),
                          ensure_ascii=False)

    result = execute_create_app(req)
    return json.dumps(result, ensure_ascii=False)


# ---- Tool 4: update_app ----

@mcp.tool()
def update_app_tool(request_json: str) -> str:
    """修改应用配置（增量更新）。request_json 为 CommonWriteRequest JSON 字符串。
    data.fields_to_update 中的字段会被修改，未列出的字段保留原值。"""
    try:
        req = json.loads(request_json)
    except Exception as e:
        return json.dumps(ex.build_result("", "UPDATE_APP", "", accepted=False,
                          error=err("FIELD_VALIDATION", "PARSE", f"JSON 解析失败: {e}", "STOP")),
                          ensure_ascii=False)

    if req.get("contract_version") != ex.CONTRACT_VERSION:
        return json.dumps(ex.build_result(req.get("task_id", ""), "UPDATE_APP",
                          req.get("idempotency_key", ""), accepted=False,
                          error=err("CONTRACT_VERSION_MISMATCH", "VALIDATE",
                          f"请求版本={req.get('contract_version')}, 服务端={ex.CONTRACT_VERSION}", "STOP")),
                          ensure_ascii=False)

    if req.get("environment") != ex.ENVIRONMENT:
        return json.dumps(ex.build_result(req.get("task_id", ""), "UPDATE_APP",
                          req.get("idempotency_key", ""), accepted=False,
                          error=err("ENVIRONMENT_MISMATCH", "VALIDATE",
                          f"请求环境={req.get('environment')}, 实际={ex.ENVIRONMENT}", "STOP")),
                          ensure_ascii=False)

    result = execute_update_app(req)
    return json.dumps(result, ensure_ascii=False)


# ---- Tool 5: locate_app ----

@mcp.tool()
def locate_app_tool(request_json: str) -> str:
    """定位已存在应用（历史手动创建的）。通过渠道名前缀匹配+应用名消歧义。
    request_json: CommonWriteRequest, data 含 channel_name 和/或 app_name。"""
    try:
        req = json.loads(request_json)
    except Exception as e:
        return json.dumps(ex.build_result("", "LOCATE_APP", "", accepted=False,
                          error=err("FIELD_VALIDATION", "PARSE", f"JSON 解析失败: {e}", "STOP")),
                          ensure_ascii=False)

    if req.get("contract_version") != ex.CONTRACT_VERSION:
        return json.dumps(ex.build_result(req.get("task_id", ""), "LOCATE_APP",
                          req.get("idempotency_key", ""), accepted=False,
                          error=err("CONTRACT_VERSION_MISMATCH", "VALIDATE",
                          f"请求版本={req.get('contract_version')}, 服务端={ex.CONTRACT_VERSION}", "STOP")),
                          ensure_ascii=False)

    if req.get("environment") != ex.ENVIRONMENT:
        return json.dumps(ex.build_result(req.get("task_id", ""), "LOCATE_APP",
                          req.get("idempotency_key", ""), accepted=False,
                          error=err("ENVIRONMENT_MISMATCH", "VALIDATE",
                          f"请求环境={req.get('environment')}, 实际={ex.ENVIRONMENT}", "STOP")),
                          ensure_ascii=False)

    result = execute_locate_app(req)
    return json.dumps(result, ensure_ascii=False)


# ---- Tool 6: get_execution ----

@mcp.tool()
def get_execution(query_json: str) -> str:
    """只读查询原执行记录。query_json: {execution_id?, idempotency_key?}"""
    try:
        q = json.loads(query_json)
    except Exception as e:
        return json.dumps({"contract_version": ex.CONTRACT_VERSION,
                          "accepted": False, "error": err("FIELD_VALIDATION", "PARSE",
                          f"JSON 解析失败: {e}", "STOP")}, ensure_ascii=False)

    eid = q.get("execution_id")
    ikey = q.get("idempotency_key")
    if not eid and not ikey:
        return json.dumps({"contract_version": ex.CONTRACT_VERSION, "accepted": False,
                          "error": err("FIELD_VALIDATION", "VALIDATE",
                          "execution_id 和 idempotency_key 至少提供一个", "STOP")}, ensure_ascii=False)

    record = None
    if eid:
        record = ex.find_by_execution_id(eid)
    if not record and ikey:
        record = ex.find_by_idempotency(ikey)

    if not record:
        return json.dumps({"contract_version": ex.CONTRACT_VERSION, "accepted": False,
                          "error": err("EXECUTION_NOT_FOUND", "QUERY",
                          "未找到执行记录", "MANUAL_CHECK")}, ensure_ascii=False)

    # 如果同时提供了 eid 和 ikey，校验一致性
    if eid and ikey and record.get("idempotency_key") != ikey:
        return json.dumps({"contract_version": ex.CONTRACT_VERSION, "accepted": True,
                          "error": err("IDEMPOTENCY_CONFLICT", "QUERY",
                          "execution_id 与 idempotency_key 不匹配", "STOP")}, ensure_ascii=False)

    result = ex.build_result(
        record["task_id"], record["operation"], record["idempotency_key"],
        execution=record, accepted=True,
        data=json.loads(record.get("output_json") or "{}"),
        error=json.loads(record.get("error_json") or "null") if record.get("error_json") else None,
        environment=record.get("environment"),
    )
    return json.dumps(result, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run(transport="stdio")
