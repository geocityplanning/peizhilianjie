from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .models import ExecutionRequest


ExecutorCaller = Callable[..., dict[str, Any]]


def run_real_request(
    request: ExecutionRequest,
    caller: ExecutorCaller | None = None,
) -> dict[str, Any]:
    if caller is None:
        from app.services.executor_client import call_executor

        caller = call_executor

    input_data = dict(request.input)
    if request.operation == "exec.create_channel":
        kwargs: dict[str, Any] = {
            "channel_base_name": str(input_data["requested_channel_name"]).strip(),
            "task_id": request.task_id,
        }
        base_platform = str(input_data.get("base_platform") or "").strip()
        if base_platform:
            kwargs["base_platform"] = base_platform
        return caller("create_channel", **kwargs)

    fixed_fields = input_data.get("fixed_fields")
    kwargs = {
        "business_object": str(input_data["business_object"]).strip(),
        "activity_name": "",
        "actual_channel_name": str(input_data["actual_channel_name"]).strip(),
        "application_type": str(input_data["application_type"]).strip(),
        "jump_address": str(input_data["jump_address"]).strip(),
        "resource_fallback_page": str(input_data["resource_fallback_page"]).strip(),
        "settlement_type": str(input_data["settlement_type"]).strip(),
        "group_name": str(input_data["group_name"]).strip(),
        "task_id": request.task_id,
    }
    ref_cloud_app_link = str(input_data.get("ref_cloud_app_link") or "").strip()
    if ref_cloud_app_link:
        kwargs["ref_cloud_app_link"] = ref_cloud_app_link
    if isinstance(fixed_fields, dict):
        for key, value in fixed_fields.items():
            kwargs.setdefault(str(key), value)
    return caller("create_app", **kwargs)


def map_real_success(request: ExecutionRequest, result: dict[str, Any]) -> dict[str, Any]:
    if request.operation == "exec.create_channel":
        return {
            "simulated": False,
            "requested_channel_name": request.input["requested_channel_name"],
            "actual_channel_name": result["actual_channel_name"],
            "channel_data": result.get("channel_data") or {},
        }

    row_data = result.get("row_data") or {}
    app_id = result.get("app_id") or row_data.get("ID")
    return {
        "simulated": False,
        "application_id": app_id,
        "application_name": result.get("app_name") or request.input["business_object"],
        "actual_channel_name": request.input["actual_channel_name"],
        "long_link": result.get("cloud_app_link"),
        "short_link": result.get("cloud_app_short_link"),
        "completed_stages": result.get("completed_stages") or [],
        "row_data": row_data,
    }


def map_real_failure(result: dict[str, Any]) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    next_action = str(result.get("next_action") or "MANUAL_CHECK").upper()
    execution_state = "UNKNOWN" if next_action == "QUERY" else "FAILED"
    business_status = "UNKNOWN" if execution_state == "UNKNOWN" else "FAILED"
    data = {
        "completed_stages": result.get("completed_stages") or [],
        "failed_stage": result.get("failed_stage") or result.get("error_stage"),
    }
    error = {
        "error_code": result.get("error_code") or "AUTOMATION_FAILED",
        "error_stage": result.get("error_stage") or result.get("failed_stage") or "EXECUTE",
        "message": result.get("message") or "真实自动化执行失败",
        "next_action": next_action,
    }
    return execution_state, business_status, data, error
