from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

from .models import ExecutionRequest


ExecutorCaller = Callable[..., dict[str, Any]]


def run_fake_request(request: ExecutionRequest) -> dict[str, Any]:
    """Return a deterministic contract-shaped receipt without touching a browser."""
    seed = f"{request.task_id}:{request.idempotency_key}:{request.operation}".encode("utf-8")
    token = hashlib.sha256(seed).hexdigest()[:10].upper()

    if request.operation == "create_channel":
        requested_name = request.input["requested_channel_name"].strip()
        return {
            "success": True,
            "actual_channel_name": f"{requested_name}-FAKE-{token[:6]}",
            "channel_data": {"source": "fake", "mode": "FAKE", "task_id": request.task_id},
        }

    app_id = f"FAKE-{token}"
    return {
        "success": True,
        "app_id": app_id,
        "app_name": request.input["business_object"].strip(),
        "cloud_app_link": f"https://fake.invalid/cloudapp/#/?i={app_id}",
        "cloud_app_short_link": f"capp://{app_id}",
        "completed_stages": ["CREATE_SAVE", "ENABLE", "SET_GROUP", "COMPLETED"],
        "row_data": {"ID": app_id, "mode": "FAKE"},
    }


def run_real_request(
    request: ExecutionRequest,
    caller: ExecutorCaller | None = None,
) -> dict[str, Any]:
    if caller is None:
        from app.services.executor_client import call_hermes_executor

        caller = call_hermes_executor

    input_data = request.input
    if request.operation == "create_channel":
        kwargs: dict[str, Any] = {
            "channel_base_name": input_data["requested_channel_name"].strip(),
            "task_id": request.task_id,
        }
        base_platform = str(input_data.get("base_platform") or "").strip()
        if base_platform:
            kwargs["base_platform"] = base_platform
        return caller("create_channel", **kwargs)

    kwargs = {
        "business_object": input_data["business_object"].strip(),
        "activity_name": "",
        "actual_channel_name": input_data["actual_channel_name"].strip(),
        "application_type": input_data["application_type"].strip(),
        "jump_address": input_data["jump_address"].strip(),
        "resource_fallback_page": input_data["resource_fallback_page"].strip(),
        "settlement_type": input_data["settlement_type"].strip(),
        "group_name": input_data["group_name"].strip(),
        "task_id": request.task_id,
    }
    ref_cloud_app_link = str(input_data.get("ref_cloud_app_link") or "").strip()
    if ref_cloud_app_link:
        kwargs["ref_cloud_app_link"] = ref_cloud_app_link
    return caller("create_app", **kwargs)


def map_real_success(request: ExecutionRequest, result: dict[str, Any]) -> dict[str, Any]:
    if request.operation == "create_channel":
        return {
            "requested_channel_name": request.input["requested_channel_name"],
            "actual_channel_name": result["actual_channel_name"],
            "channel_data": result.get("channel_data") or {},
        }

    row_data = result.get("row_data") or {}
    return {
        "app_id": result.get("app_id") or row_data.get("ID"),
        "app_link": result.get("cloud_app_link"),
        "app_short_link": result.get("cloud_app_short_link"),
        "application_name": result.get("app_name") or request.input["business_object"],
        "actual_channel_name": request.input["actual_channel_name"],
        "completed_stages": result.get("completed_stages") or [],
        "row_data": row_data,
    }


def map_real_failure(result: dict[str, Any]) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    next_action = str(result.get("next_action") or "MANUAL_CHECK").upper()
    declared_status = str(result.get("status") or result.get("business_status") or "").upper()
    if next_action == "QUERY":
        execution_state = "UNKNOWN"
        business_status = "UNKNOWN"
    else:
        execution_state = "FAILED"
        business_status = "BUSINESS_REJECT" if declared_status == "BUSINESS_REJECT" else "TECH_FAIL"

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
