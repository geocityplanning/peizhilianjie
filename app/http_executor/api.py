from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .models import ExecutionRequest, QueryRequest
from .service import ExecutionService, ServiceError
from .store import CONTRACT_VERSION, DatabaseUnavailable


def create_app(service: ExecutionService) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        service.store.assert_ready()
        if not service.settings.auth_token:
            raise RuntimeError("HERMES_EXECUTOR_TOKEN 未配置，REAL 执行端拒绝启动")
        if service.settings.auto_login and not service.settings.fake_mode:
            service.prepare_real_session()
        yield

    app = FastAPI(
        title="Hermes REAL Executor",
        version="1.0.0-b01",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    def authorize(authorization: str | None = Header(default=None)) -> None:
        if authorization != f"Bearer {service.settings.auth_token}":
            raise HTTPException(status_code=401, detail="执行端鉴权失败")

    def validate_contract(
        x_contract_version: str | None = Header(default=None, alias="X-Contract-Version"),
    ) -> None:
        if x_contract_version != CONTRACT_VERSION:
            raise ServiceError(
                "UNSUPPORTED_VERSION",
                "VALIDATE",
                f"X-Contract-Version 必须是 {CONTRACT_VERSION}",
            )

    def error_payload(
        *,
        state: str,
        status: str,
        code: str,
        stage: str,
        message: str,
        next_action: str,
        adjudicated: bool,
    ) -> dict:
        return {
            "contract_version": CONTRACT_VERSION,
            "accepted": False,
            "execution_correlation_id": None,
            "task_id": None,
            "operation": None,
            "environment": service.settings.environment,
            "snapshot_version": None,
            "idempotency_key": None,
            "state": state,
            "status": status,
            "late": False,
            "adjudicated": adjudicated,
            "data": None,
            "error": {
                "error_code": code,
                "error_stage": stage,
                "message": message,
                "next_action": next_action,
            },
            "evidence_ref": [],
            "next_action": next_action,
        }

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        code = "AUTH_FAILED" if exc.status_code == 401 else "HTTP_ERROR"
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(
                state="REJECTED",
                status="NOT_EXECUTED",
                code=code,
                stage="AUTH" if exc.status_code == 401 else "HTTP",
                message=str(exc.detail),
                next_action="STOP",
                adjudicated=True,
            ),
        )
    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        payload = error_payload(
            state="REJECTED",
            status="NOT_EXECUTED",
            code="SCHEMA_INVALID",
            stage="VALIDATE",
            message="请求结构不符合 B01 契约",
            next_action="STOP",
            adjudicated=True,
        )
        payload["error"]["details"] = [
            {"loc": list(error.get("loc", [])), "type": error.get("type", "validation_error")}
            for error in exc.errors()
        ]
        return JSONResponse(status_code=400, content=payload)

    @app.exception_handler(DatabaseUnavailable)
    async def database_error(_request: Request, exc: DatabaseUnavailable):
        return JSONResponse(
            status_code=503,
            content=error_payload(
                state="UNKNOWN",
                status="TECH_FAIL",
                code="EXECUTOR_DATABASE_UNAVAILABLE",
                stage="INIT",
                message="执行端数据库不可用，请人工检查",
                next_action="MANUAL_CHECK",
                adjudicated=False,
            ),
        )

    @app.exception_handler(ServiceError)
    async def service_error(_request: Request, exc: ServiceError):
        if exc.code in {"EXECUTOR_BUSY", "IDEMPOTENCY_CONFLICT"}:
            http_status = 409
        elif exc.code == "EXECUTION_NOT_FOUND":
            http_status = 404
        else:
            http_status = 400
        return JSONResponse(
            status_code=http_status,
            content=error_payload(
                state="REJECTED",
                status="NOT_EXECUTED",
                code=exc.code,
                stage=exc.stage,
                message=exc.message,
                next_action=exc.next_action,
                adjudicated=True,
            ),
        )

    @app.post("/v1/exec/info")
    def get_info(
        _: Any = Depends(authorize),
        __: Any = Depends(validate_contract),
    ):
        return service.info()

    @app.post("/v1/exec/create-channel")
    def create_channel(
        request: ExecutionRequest,
        _: Any = Depends(authorize),
        __: Any = Depends(validate_contract),
    ):
        return service.create(request, "create_channel")

    @app.post("/v1/exec/create-app")
    def create_application(
        request: ExecutionRequest,
        _: Any = Depends(authorize),
        __: Any = Depends(validate_contract),
    ):
        return service.create(request, "create_app")

    @app.post("/v1/exec/query")
    def query_execution(
        request: QueryRequest,
        _: Any = Depends(authorize),
        __: Any = Depends(validate_contract),
    ):
        if not request.execution_correlation_id and not request.idempotency_key and not (
            request.task_id and request.operation and request.environment
        ):
            raise ServiceError(
                "QUERY_SELECTOR_MISSING",
                "QUERY",
                "必须提供 execution_correlation_id、idempotency_key 或完整任务定位信息",
            )
        return service.query(request)

    return app
