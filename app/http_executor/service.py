from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any, Dict, Optional

from .models import ExecutionRequest, QueryRequest
from .settings import Settings
from .store import CONTRACT_VERSION, ExecutorStore


TERMINAL_STATES = {"SUCCEEDED", "FAILED", "UNKNOWN", "REJECTED"}


def _fingerprint(request: ExecutionRequest) -> str:
    payload = {
        "contract_version": request.contract_version,
        "task_id": request.task_id,
        "operation": request.operation,
        "environment": request.environment,
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
    def __init__(self, settings: Settings, store: ExecutorStore):
        self.settings = settings
        self.store = store

    def _validate_common(self, request: ExecutionRequest, operation: str) -> None:
        if request.contract_version != CONTRACT_VERSION:
            raise ServiceError("UNSUPPORTED_VERSION", "VALIDATE", "不支持的 contract_version")
        if request.operation != operation:
            raise ServiceError("OPERATION_MISMATCH", "VALIDATE", f"operation 必须是 {operation}")
        if request.environment != self.settings.environment:
            raise ServiceError("ENVIRONMENT_MISMATCH", "VALIDATE", "请求环境与执行端环境不一致")
        if not request.task_id.strip() or not request.idempotency_key.strip():
            raise ServiceError("MISSING_EXECUTION_IDENTITY", "VALIDATE", "task_id 和 idempotency_key 不能为空")

    @staticmethod
    def _validate_input(request: ExecutionRequest) -> None:
        required = {
            "exec.create_channel": ["requested_channel_name"],
            "exec.create_app": [
                "application_type",
                "business_object",
                "actual_channel_name",
                "jump_address",
                "resource_fallback_page",
                "settlement_type",
            ],
        }[request.operation]
        missing = [field for field in required if not str(request.input.get(field, "")).strip()]
        if missing:
            raise ServiceError("MISSING_REQUIRED_FIELD", "VALIDATE", f"缺少必填字段: {', '.join(missing)}")

    def create(self, request: ExecutionRequest, operation: str) -> Dict[str, Any]:
        self._validate_common(request, operation)
        self._validate_input(request)
        record, should_run = self.store.reserve(
            task_id=request.task_id,
            operation=request.operation,
            environment=request.environment,
            idempotency_key=request.idempotency_key,
            request_fingerprint=_fingerprint(request),
            request=request.model_dump() if hasattr(request, "model_dump") else request.dict(),
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
            threading.Thread(
                target=self._run_fake,
                args=(record["execution_id"], request),
                daemon=True,
                name=f"hermes-exec-{record['execution_id']}",
            ).start()
        return self._envelope(record)

    def _run_fake(self, execution_id: str, request: ExecutionRequest) -> None:
        try:
            self.store.mark_running(execution_id)
            if self.settings.fake_delay_ms:
                time.sleep(self.settings.fake_delay_ms / 1000)
            if request.operation == "exec.create_channel":
                requested = str(request.input["requested_channel_name"]).strip()
                suffix = self.settings.fake_channel_suffix
                actual = requested if not suffix or requested.endswith(suffix) else requested + suffix
                data = {
                    "simulated": True,
                    "requested_channel_name": requested,
                    "actual_channel_name": actual,
                    "base": "FAKE_BASE",
                    "next_operator_action": "确认实际渠道名后再提交 exec.create_app",
                }
            else:
                app_id = f"FAKE-APP-{execution_id[-6:]}"
                data = {
                    "simulated": True,
                    "application_id": app_id,
                    "application_name": request.input.get("application_name") or app_id,
                    "actual_channel_name": request.input["actual_channel_name"],
                    "long_link": None,
                    "short_link": None,
                    "note": "Fake 模式不会创建真实应用，也不会生成真实长链接",
                }
            self.store.finish(
                execution_id=execution_id,
                execution_state="SUCCEEDED",
                business_status="SIMULATED_SUCCESS",
                data=data,
                error=None,
                evidence_ref=[],
            )
        except Exception as exc:
            self.store.finish(
                execution_id=execution_id,
                execution_state="FAILED",
                business_status="FAILED",
                data=None,
                error={
                    "error_code": "FAKE_EXECUTION_FAILED",
                    "error_stage": "EXECUTE",
                    "message": str(exc),
                    "next_action": "QUERY",
                },
                evidence_ref=[],
            )

    def query(self, request: QueryRequest) -> Dict[str, Any]:
        if request.contract_version != CONTRACT_VERSION:
            raise ServiceError("UNSUPPORTED_VERSION", "VALIDATE", "不支持的 contract_version")
        if request.execution_id:
            record = self.store.get_by_execution_id(request.execution_id)
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
                    f"查询字段 {field} 与 execution_id 关联记录不一致",
                    "MANUAL_CHECK",
                )
        return self._envelope(record)

    def info(self) -> Dict[str, Any]:
        self.store.assert_ready()
        return {
            "contract_version": CONTRACT_VERSION,
            "service": "hermes-http-executor",
            "environment": self.settings.environment,
            "mode": self.settings.mode,
            "capabilities": [
                "exec.get_info",
                "exec.create_channel",
                "exec.create_app",
                "exec.query_execution",
            ],
            "browser_status": "NOT_ATTACHED",
            "login_status": "NOT_CHECKED",
            "active_execution_id": self.store.active_execution_id(),
            "database_status": "AVAILABLE",
        }

    @staticmethod
    def _envelope(record: Dict[str, Any]) -> Dict[str, Any]:
        error = record.get("error")
        next_action = error.get("next_action") if isinstance(error, dict) else None
        if not next_action:
            next_action = "QUERY" if record["execution_state"] in {"ACCEPTED", "RUNNING"} else "STOP"
        return {
            "contract_version": CONTRACT_VERSION,
            "accepted": record["execution_state"] != "REJECTED",
            "execution_id": record.get("execution_id"),
            "task_id": record["task_id"],
            "operation": record["operation"],
            "environment": record["environment"],
            "idempotency_key": record["idempotency_key"],
            "execution_state": record["execution_state"],
            "business_status": record["business_status"],
            "data": record.get("data"),
            "error": error,
            "evidence_ref": record.get("evidence_ref", []),
            "next_action": next_action,
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
        }
