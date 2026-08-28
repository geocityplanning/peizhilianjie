from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .models import ExecutionRequest, QueryRequest
from .service import ExecutionService, ServiceError
from .store import DatabaseUnavailable


def create_app(service: ExecutionService) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        service.store.assert_ready()
        yield

    app = FastAPI(
        title="Hermes HTTP Executor",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    def authorize(authorization: str | None = Header(default=None)) -> None:
        if service.settings.allow_anonymous and not service.settings.auth_token:
            return
        if not service.settings.auth_token:
            raise HTTPException(status_code=503, detail="执行端未配置鉴权令牌")
        expected = f"Bearer {service.settings.auth_token}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="执行端鉴权失败")

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=400,
            content={
                "contract_version": "http-executor.v1",
                "accepted": False,
                "execution_id": None,
                "execution_state": "REJECTED",
                "business_status": "REJECTED",
                "data": None,
                "error": {
                    "error_code": "SCHEMA_INVALID",
                    "error_stage": "VALIDATE",
                    "message": "请求结构不符合接口契约",
                    "details": exc.errors(),
                    "next_action": "STOP",
                },
                "evidence_ref": [],
                "next_action": "STOP",
            },
        )

    @app.exception_handler(DatabaseUnavailable)
    async def database_error(_request: Request, exc: DatabaseUnavailable):
        return JSONResponse(
            status_code=503,
            content={
                "contract_version": "http-executor.v1",
                "accepted": False,
                "execution_id": None,
                "execution_state": "UNKNOWN",
                "business_status": "UNAVAILABLE",
                "data": None,
                "error": {
                    "error_code": "EXECUTOR_DATABASE_UNAVAILABLE",
                    "error_stage": "INIT",
                    "message": str(exc),
                    "next_action": "MANUAL_CHECK",
                },
                "evidence_ref": [],
                "next_action": "MANUAL_CHECK",
            },
        )

    @app.exception_handler(ServiceError)
    async def service_error(_request: Request, exc: ServiceError):
        status = 409 if exc.code in {"EXECUTOR_BUSY", "IDEMPOTENCY_CONFLICT"} else 400
        return JSONResponse(
            status_code=status,
            content={
                "contract_version": "http-executor.v1",
                "accepted": False,
                "execution_id": None,
                "execution_state": "REJECTED",
                "business_status": "REJECTED",
                "data": None,
                "error": {
                    "error_code": exc.code,
                    "error_stage": exc.stage,
                    "message": exc.message,
                    "next_action": exc.next_action,
                },
                "evidence_ref": [],
                "next_action": exc.next_action,
            },
        )

    @app.get("/v1/exec/info")
    def get_info(_: Any = Depends(authorize)):
        return service.info()

    @app.post("/v1/exec/create-channel")
    def create_channel(request: ExecutionRequest, _: Any = Depends(authorize)):
        return service.create(request, "exec.create_channel")

    @app.post("/v1/exec/create-app")
    def create_app(request: ExecutionRequest, _: Any = Depends(authorize)):
        return service.create(request, "exec.create_app")

    @app.post("/v1/exec/query")
    def query_execution(request: QueryRequest, _: Any = Depends(authorize)):
        if not request.execution_id and not request.idempotency_key and not (
            request.task_id and request.operation and request.environment
        ):
            raise ServiceError("QUERY_SELECTOR_MISSING", "QUERY", "必须提供 execution_id、idempotency_key 或完整任务定位信息")
        return service.query(request)

    return app

