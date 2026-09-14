from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any, Dict

from .models import ExecutionRequest, QueryRequest
from .settings import Settings
from .store import CONTRACT_VERSION, ExecutorStore


TERMINAL_STATES = {"SUCCEEDED", "FAILED", "UNKNOWN", "REJECTED"}
CHANNEL_FIELDS = {"requested_channel_name", "base_platform"}
APP_FIELDS = {
    "application_type",
    "business_object",
    "actual_channel_name",
    "jump_address",
    "resource_fallback_page",
    "settlement_type",
    "group_name",
    "ref_cloud_app_link",
}
REQUIRED_FIELDS = {
    "create_channel": {"requested_channel_name"},
    "create_app": {
        "application_type",
        "business_object",
        "actual_channel_name",
        "jump_address",
        "resource_fallback_page",
        "settlement_type",
    },
}


def _fingerprint(request: ExecutionRequest) -> str:
    payload = {
        "contract_version": CONTRACT_VERSION,
        "task_id": request.task_id,
        "operation": request.operation,
        "environment": request.environment,
        "snapshot_version": request.snapshot_version,
        "input": request.input,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ServiceError(Exception):
    def __init__(self, code: str, stage: str, message: str, next_action: str = "STOP"):
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.message = message
        self.next_action = next_action


class ExecutionService:
    def __init__(
        self,
        settings: Settings,
        store: ExecutorStore,
        real_caller: Callable[..., dict[str, Any]] | None = None,
        login_checker: Callable[[], bool] | None = None,
    ):
        self.settings = settings
        self.store = store
        self.real_caller = real_caller
        self.login_checker = login_checker

    def _validate_common(self, request: ExecutionRequest, operation: str) -> None:
        if request.operation != operation:
            raise ServiceError("OPERATION_MISMATCH", "VALIDATE", f"operation 必须是 {operation}")
        request.environment = request.environment.upper()
        if request.environment != self.settings.environment:
            raise ServiceError("ENVIRONMENT_MISMATCH", "VALIDATE", "请求环境与执行端环境不一致")

    @staticmethod
    def _validate_input(request: ExecutionRequest) -> None:
        allowed = CHANNEL_FIELDS if request.operation == "create_channel" else APP_FIELDS
        unknown = sorted(set(request.input) - allowed)
        if unknown:
            raise ServiceError("UNKNOWN_INPUT_FIELD", "VALIDATE", f"不支持的输入字段: {', '.join(unknown)}")

        missing = [
            field
            for field in sorted(REQUIRED_FIELDS[request.operation])
            if not isinstance(request.input.get(field), str) or not request.input[field].strip()
        ]
        if missing:
            raise ServiceError("MISSING_REQUIRED_FIELD", "VALIDATE", f"缺少必填字段: {', '.join(missing)}")
        if request.operation == "create_app" and request.input["application_type"] not in {"云盘", "掌厅"}:
            raise ServiceError("INVALID_APPLICATION_TYPE", "VALIDATE", "application_type 只能是 云盘 或 掌厅")

    def create(self, request: ExecutionRequest, operation: str) -> Dict[str, Any]:
        self._validate_common(request, operation)
        self._validate_input(request)
        request_data = request.model_dump() if hasattr(request, "model_dump") else request.dict()
        record, should_run = self.store.reserve(
            task_id=request.task_id,
            operation=request.operation,
            environment=request.environment,
            idempotency_key=request.idempotency_key,
            request_fingerprint=_fingerprint(request),
            request=request_data,
        )
        if not should_run and record["execution_state"] == "REJECTED":
            error = record.get("error") or {}
            raise ServiceError(
                error.get("error_code", "EXECUTION_REJECTED"),
                error.get("error_stage", "VALIDATE"),
                error.get("message", "执行请求被拒绝"),
                error.get("next_action", "STOP"),
            )
        if should_run:
            if self.settings.fake_mode:
                self._run_fake(record["execution_id"], request)
            else:
                self._run_real(record["execution_id"], request)
            record = self.store.get_by_execution_id(record["execution_id"]) or record
        return self._envelope(record)

    def _run_fake(self, execution_id: str, request: ExecutionRequest) -> None:
        from .real_runner import map_real_success, run_fake_request

        try:
            self.store.mark_running(execution_id)
            result = run_fake_request(request)
            self.store.finish(
                execution_id=execution_id,
                execution_state="SUCCEEDED",
                business_status="SUCCESS",
                data=map_real_success(request, result),
                error=None,
                evidence_ref=[],
            )
        except Exception as exc:
            self.store.finish(
                execution_id=execution_id,
                execution_state="UNKNOWN",
                business_status="UNKNOWN",
                data=None,
                error={
                    "error_code": "FAKE_EXECUTION_ERROR",
                    "error_stage": "EXECUTE",
                    "message": "执行自动化异常，结果未知，请查询原任务",
                    "next_action": "QUERY",
                },
                evidence_ref=[],
            )

    def _run_real(self, execution_id: str, request: ExecutionRequest) -> None:
        from .real_runner import map_real_failure, map_real_success, run_real_request

        try:
            self.store.mark_running(execution_id)
            result = run_real_request(request, self.real_caller)
            if result.get("success"):
                self.store.finish(
                    execution_id=execution_id,
                    execution_state="SUCCEEDED",
                    business_status="SUCCESS",
                    data=map_real_success(request, result),
                    error=None,
                    evidence_ref=[],
                )
                return

            execution_state, business_status, data, error = map_real_failure(result)
            self.store.finish(
                execution_id=execution_id,
                execution_state=execution_state,
                business_status=business_status,
                data=data,
                error=error,
                evidence_ref=[],
            )
        except Exception as exc:
            self.store.finish(
                execution_id=execution_id,
                execution_state="UNKNOWN",
                business_status="UNKNOWN",
                data=None,
                error={
                    "error_code": "REAL_EXECUTION_UNKNOWN",
                    "error_stage": "EXECUTE",
                    "message": "执行自动化异常，结果未知，请查询原任务",
                    "next_action": "QUERY",
                },
                evidence_ref=[],
            )

    def query(self, request: QueryRequest) -> Dict[str, Any]:
        if request.environment:
            request.environment = request.environment.upper()
        if request.execution_correlation_id:
            record = self.store.get_by_execution_id(request.execution_correlation_id)
        else:
            record = self.store.get_by_lookup(
                task_id=request.task_id,
                operation=request.operation,
                environment=request.environment,
                idempotency_key=request.idempotency_key,
            )
        if record is None:
            raise ServiceError("EXECUTION_NOT_FOUND", "QUERY", "找不到原始执行任务", "MANUAL_CHECK")

        for field in ("task_id", "operation", "environment", "idempotency_key"):
            requested_value = getattr(request, field)
            if requested_value is not None and requested_value != record[field]:
                raise ServiceError(
                    "QUERY_ASSOCIATION_CONFLICT",
                    "QUERY",
                    f"查询字段 {field} 与原执行记录不一致",
                    "MANUAL_CHECK",
                )

        requested_snapshot = str(request.snapshot_version or "").strip()
        recorded_snapshot = str((record.get("request") or {}).get("snapshot_version") or "").strip()
        if requested_snapshot and requested_snapshot != recorded_snapshot:
            raise ServiceError(
                "QUERY_ASSOCIATION_CONFLICT",
                "QUERY",
                "查询字段 snapshot_version 与原执行记录不一致",
                "MANUAL_CHECK",
            )
        return self._envelope(record)

    def _login_valid(self) -> bool:
        if self.settings.fake_mode:
            return True
        checker = self.login_checker
        if checker is None:
            from .login_probe import probe_real_login

            checker = probe_real_login
        try:
            return bool(checker())
        except Exception:
            return False

    def info(self) -> Dict[str, Any]:
        self.store.assert_ready()
        active_execution_id = self.store.active_execution_id()
        login_valid = self._login_valid()
        unknown_inflight = active_execution_id is not None
        return {
            "contract_version": CONTRACT_VERSION,
            "service": "hermes-real-executor",
            "environment": self.settings.environment,
            "mode": "FAKE" if self.settings.fake_mode else "REAL",
            "capabilities": ["get_info", "create_channel", "create_app", "query_execution"],
            "active_execution_correlation_id": active_execution_id,
            "database_status": "AVAILABLE",
            "status": "SUCCESS",
            "acceptable": not unknown_inflight and login_valid,
            "login_valid": login_valid,
            "unknown_inflight": unknown_inflight,
        }

    @staticmethod
    def _envelope(record: Dict[str, Any]) -> Dict[str, Any]:
        error = record.get("error")
        state = record["execution_state"]
        business_status = record["business_status"]
        if state in {"ACCEPTED", "RUNNING"}:
            status = "RUNNING"
        elif state == "SUCCEEDED":
            status = "SUCCESS"
        elif state == "FAILED" and business_status == "BUSINESS_REJECT":
            status = "BUSINESS_REJECT"
        elif state == "FAILED":
            status = "TECH_FAIL"
        elif state == "REJECTED":
            status = "NOT_EXECUTED"
        else:
            status = "UNKNOWN"

        next_action = error.get("next_action") if isinstance(error, dict) else None
        if not next_action:
            next_action = "QUERY" if state in {"ACCEPTED", "RUNNING", "UNKNOWN"} else "STOP"
        request_data = record.get("request") or {}
        return {
            "contract_version": CONTRACT_VERSION,
            "accepted": state != "REJECTED",
            "execution_correlation_id": record.get("execution_id"),
            "task_id": record["task_id"],
            "operation": record["operation"],
            "environment": record["environment"],
            "snapshot_version": request_data.get("snapshot_version"),
            "idempotency_key": record["idempotency_key"],
            "state": state,
            "status": status,
            "late": False,
            "adjudicated": state in TERMINAL_STATES and state != "UNKNOWN",
            "data": record.get("data"),
            "error": error,
            "evidence_ref": record.get("evidence_ref", []),
            "next_action": next_action,
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
        }
