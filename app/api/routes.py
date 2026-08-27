import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from app.core.config import settings
from app.schemas import (
    AppCreateRequest,
    AppLocateRequest,
    AppUpdateRequest,
    ChannelCreateRequest,
    EmailParseRequest,
    LedgerRowRequest,
)
from app.services.email_parser import build_ledger_row, parse_customer_email
from app.services.executor_client import call_executor
from app.services.executor_lock import get_executor_lock_status, release_executor_lock
from app.services.job_manager import cancel_job, create_job, get_job, list_jobs, read_running_executor_stage
from app.services.repository import save_app, save_channel, save_operation

router = APIRouter()


def executor_error(message: str) -> dict:
    return {
        "success": False,
        "message": message,
        "error_code": "ORCHESTRATOR_EXECUTOR_ERROR",
        "error_stage": "CALL_EXECUTOR",
        "next_action": "QUERY",
    }




@router.get("/executor/lock")
def executor_lock() -> dict:
    return get_executor_lock_status()


@router.post("/executor/lock/release")
def release_lock() -> dict:
    return release_executor_lock()


@router.get("/health")
def health() -> dict:
    return {
        "success": True,
        "app": settings.app_name,
        "env": settings.app_env,
        "database_path": str(settings.database_path),
        "executor_path": str(settings.automation_executor_path),
    }


@router.get("/status")
async def status() -> dict:
    try:
        return await run_in_threadpool(call_executor, "get_status")
    except Exception as exc:
        return executor_error(f"检查状态调用异常: {exc}")


@router.post("/login")
async def login() -> dict:
    try:
        return await run_in_threadpool(call_executor, "login")
    except Exception as exc:
        return executor_error(f"登录调用异常: {exc}")


@router.post("/channels")
async def create_channel(payload: ChannelCreateRequest) -> dict:
    lock = get_executor_lock_status()
    if lock.get("locked") and lock.get("stale_candidate"):
        release_executor_lock("创建渠道前发现执行端旧锁超过阈值，自动释放。")
    elif lock.get("locked"):
        return {
            "success": False,
            "message": f"执行端正在处理 {lock.get('holder')}，请等待当前任务结束后再创建渠道。当前阶段：{lock.get('stage') or '未知'}",
            "error_code": "EXECUTOR_BUSY",
            "error_stage": "LOCK",
            "next_action": "QUERY",
            "lock": lock,
        }

    try:
        result = await run_in_threadpool(
            call_executor,
            "create_channel",
            channel_base_name=payload.channel_base_name,
            base_platform=payload.base_platform,
        )
    except Exception as exc:
        return executor_error(f"创建渠道调用异常: {exc}")

    save_operation("CREATE_CHANNEL", result, payload.batch_id)
    if result.get("success"):
        save_channel(result)
    return result


@router.post("/apps")
async def create_app(payload: AppCreateRequest) -> dict:
    data = payload.model_dump(exclude={"batch_id", "extra"})
    data.update(payload.extra)
    try:
        result = await run_in_threadpool(call_executor, "create_app", **data)
    except Exception as exc:
        return executor_error(f"创建应用调用异常: {exc}")

    save_operation("CREATE_APP", result, payload.batch_id)
    if result.get("success"):
        save_app(result)
    return result


@router.post("/apps/async")
def create_app_async(payload: AppCreateRequest) -> dict:
    data = payload.model_dump(exclude={"batch_id", "extra"})
    data.update(payload.extra)
    job = create_job("CREATE_APP", data, payload.batch_id)
    return {"job_id": job["job_id"], "status": job["status"], "message": job["message"]}


@router.get("/jobs")
def jobs() -> list[dict]:
    return list_jobs()


@router.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    running = read_running_executor_stage()
    if job.get("status") == "RUNNING" and running:
        stage = running.get("stage") or running.get("evidence_ref") or job.get("stage")
        job["stage"] = stage
        job["execution_id"] = running.get("execution_id")
        if stage:
            job["message"] = stage
    return job


@router.post("/jobs/{job_id}/cancel")
def cancel_job_api(job_id: str) -> dict:
    job = cancel_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


@router.patch("/apps")
async def update_app(payload: AppUpdateRequest) -> dict:
    try:
        result = await run_in_threadpool(
            call_executor,
            "update_app",
            app_name=payload.app_name,
            fields_to_update=payload.fields_to_update,
            app_id=payload.app_id,
        )
    except Exception as exc:
        return executor_error(f"修改应用调用异常: {exc}")

    save_operation("UPDATE_APP", result, payload.batch_id)
    if result.get("success"):
        save_app(result)
    return result


@router.post("/apps/locate")
async def locate_app(payload: AppLocateRequest) -> dict:
    if not payload.channel_name and not payload.app_name:
        raise HTTPException(status_code=422, detail="channel_name 和 app_name 至少提供一个")

    try:
        result = await run_in_threadpool(
            call_executor,
            "locate_app",
            channel_name=payload.channel_name,
            app_name=payload.app_name,
        )
    except Exception as exc:
        return executor_error(f"定位应用调用异常: {exc}")

    save_operation("LOCATE_APP", result, payload.batch_id)
    if result.get("success"):
        save_app(result)
    return result


@router.get("/fields")
async def field_list() -> dict:
    try:
        return await run_in_threadpool(call_executor, "get_field_list")
    except Exception as exc:
        return executor_error(f"字段列表调用异常: {exc}")


@router.post("/parse-email")
def parse_email(payload: EmailParseRequest) -> dict:
    return parse_customer_email(payload.raw_text)


@router.post("/ledger-row")
def ledger_row(payload: LedgerRowRequest) -> dict:
    return build_ledger_row(payload.parsed, payload.result)

