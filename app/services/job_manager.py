import json
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.executor_lock import get_executor_lock_status, release_executor_lock
from app.services.job_store import load_jobs as load_job_records, save_job as save_job_record
from app.services.repository import save_app, save_operation

_jobs: dict[str, dict[str, Any]] = {}
_queue: deque[str] = deque()
_jobs_lock = threading.RLock()
_queue_event = threading.Event()
_worker_started = False
_worker_lock = threading.Lock()
_initialized = False
_initialize_lock = threading.Lock()

DEFAULT_MAX_RETRIES = 2
OPERATION_MAX_RETRIES = {
    "CREATE_APP": 0,
}
RETRYABLE_OPERATIONS: set[str] = set()
STAGE_STALL_SECONDS = 300
TASK_TIMEOUT_SECONDS = 900
POLL_SECONDS = 2


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _max_retries_for(operation: str) -> int:
    return OPERATION_MAX_RETRIES.get(operation, DEFAULT_MAX_RETRIES)


def _should_retry(operation: str, attempt: int, max_retries: int, result: dict[str, Any]) -> bool:
    return (
        operation in RETRYABLE_OPERATIONS
        and attempt <= max_retries
        and result.get("next_action") == "QUERY"
    )


def _touch(job: dict[str, Any]) -> None:
    job["updated_at"] = _now()
    save_job_record(job)


def _executor_db_path() -> Path:
    return settings.automation_executor_path / "data" / "executor.db"


def _read_stage_from_output(output_json: str | None, evidence_ref: str | None) -> str:
    if output_json:
        try:
            data = json.loads(output_json)
            if isinstance(data, dict) and data.get("_stage"):
                return str(data["_stage"])
        except Exception:
            pass
    return evidence_ref or ""


def read_running_executor_stage() -> dict[str, Any]:
    db_path = _executor_db_path()
    if not db_path.exists():
        return {}
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT execution_id, operation, execution_state, business_status, output_json,
                   error_json, evidence_ref, started_at, finished_at, created_at
            FROM executions
            WHERE execution_state = 'RUNNING'
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
        conn.close()
        if not row:
            return {}
        data = dict(row)
        data["stage"] = _read_stage_from_output(data.get("output_json"), data.get("evidence_ref"))
        return data
    except Exception:
        return {}


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    public = dict(job)
    public.pop("payload", None)
    public.pop("batch_id", None)
    return public


def initialize_job_manager() -> None:
    """Load persisted jobs and resume only work that had not started."""
    global _initialized
    with _initialize_lock:
        if _initialized:
            return

        persisted_jobs = load_job_records()
        with _jobs_lock:
            _jobs.clear()
            _queue.clear()
            for job in persisted_jobs:
                _jobs[job["job_id"]] = job
                status = job.get("status")
                if status in {"QUEUED", "WAITING", "RUNNING"}:
                    job["max_retries"] = min(
                        int(job.get("max_retries") or 0),
                        _max_retries_for(str(job.get("operation") or "")),
                    )
                if status in {"QUEUED", "WAITING"}:
                    job.update(
                        status="QUEUED",
                        stage="后端重启后恢复排队",
                        message="后端已重启，未开始的任务已恢复到队列。",
                        queue_position=None,
                    )
                    _queue.append(job["job_id"])
                    _touch(job)
                elif status == "RUNNING":
                    recovery_error = {
                        "success": False,
                        "message": "后端重启时任务仍在执行，无法确认平台侧是否已完成；已停止自动重跑并放行后续任务。",
                        "error_code": "BACKEND_RESTARTED",
                        "error_stage": "RECOVERY",
                        "next_action": "MANUAL_CHECK",
                    }
                    job.update(
                        status="FAILED",
                        stage="后端重启导致任务中断",
                        message=recovery_error["message"],
                        queue_position=None,
                        result=recovery_error,
                        error=recovery_error,
                        finished_at=_now(),
                    )
                    _touch(job)

            _refresh_queue_positions_locked()
            _initialized = True

    _start_worker_once()
    if _queue:
        _queue_event.set()


def _start_worker_once() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        threading.Thread(target=_worker_loop, daemon=True).start()
        _worker_started = True


def create_job(operation: str, payload: dict[str, Any], batch_id: str | None = None) -> dict[str, Any]:
    initialize_job_manager()
    job_id = f"JOB-{uuid.uuid4().hex[:10].upper()}"
    with _jobs_lock:
        position = len(_queue) + 1
        job = {
            "job_id": job_id,
            "operation": operation,
            "status": "QUEUED",
            "message": f"任务已进入队列，前面还有 {position - 1} 个任务。",
            "stage": "排队等待",
            "queue_position": position,
            "attempt": 0,
            "max_retries": _max_retries_for(operation),
            "result": None,
            "error": None,
            "payload": payload,
            "batch_id": batch_id,
            "created_at": _now(),
            "updated_at": _now(),
            "started_at": None,
            "finished_at": None,
        }
        _jobs[job_id] = job
        _queue.append(job_id)
        _refresh_queue_positions_locked()
    _queue_event.set()
    return get_job(job_id) or job


def get_job(job_id: str) -> dict[str, Any] | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return None
        return _public_job(job)



def list_jobs() -> list[dict[str, Any]]:
    with _jobs_lock:
        rows = [_public_job(job) for job in _jobs.values()]
        return sorted(rows, key=lambda item: item.get("created_at") or "", reverse=True)


def cancel_job(job_id: str) -> dict[str, Any] | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return None
        if job["status"] in {"QUEUED", "WAITING"}:
            try:
                _queue.remove(job_id)
            except ValueError:
                pass
            job.update(status="CANCELLED", stage="已取消", message="任务尚未开始，已取消。", queue_position=None, finished_at=_now())
            _touch(job)
            _refresh_queue_positions_locked()
        elif job["status"] == "RUNNING":
            job.update(message="任务正在运行，无法中途取消；如卡住会由超时监控终止，本任务不会自动重跑。")
            _touch(job)
        return get_job(job_id)


def update_job(job_id: str, **fields: Any) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job.update(fields)
        _touch(job)


def _refresh_queue_positions_locked() -> None:
    for idx, queued_id in enumerate(_queue, start=1):
        job = _jobs.get(queued_id)
        if job and job.get("status") in {"QUEUED", "WAITING"}:
            job["queue_position"] = idx
            job["message"] = f"任务排队中，前面还有 {idx - 1} 个任务。"
            _touch(job)


def _worker_loop() -> None:
    while True:
        _queue_event.wait()
        while True:
            with _jobs_lock:
                if not _queue:
                    _queue_event.clear()
                    break
                job_id = _queue[0]
                job = _jobs.get(job_id)
                if not job or job.get("status") == "CANCELLED":
                    _queue.popleft()
                    _refresh_queue_positions_locked()
                    continue

            lock = get_executor_lock_status()
            if lock.get("locked"):
                update_job(job_id, status="WAITING", stage="等待执行端空闲", message=f"执行端正在处理 {lock.get('holder')}，当前任务排队等待。")
                if lock.get("stale_candidate"):
                    update_job(job_id, message=f"执行端锁占用超过阈值，正在自动释放卡住任务：{lock.get('holder')}")
                    release_executor_lock("执行端锁超过阈值，批量调度器自动释放。")
                else:
                    time.sleep(POLL_SECONDS)
                    continue

            with _jobs_lock:
                try:
                    _queue.popleft()
                except IndexError:
                    continue
                _refresh_queue_positions_locked()

            _run_job_with_retries(job_id)


def _run_job_with_retries(job_id: str) -> None:
    while True:
        with _jobs_lock:
            job = _jobs.get(job_id)
            if not job or job.get("status") == "CANCELLED":
                return
            attempt = int(job.get("attempt") or 0) + 1
            job["attempt"] = attempt
            payload = dict(job.get("payload") or {})
            operation = job["operation"]
            max_retries = min(
                int(job.get("max_retries") or 0),
                _max_retries_for(operation),
            )
            job["max_retries"] = max_retries
            batch_id = job.get("batch_id")
            total_attempts = max_retries + 1
            job.update(
                status="RUNNING",
                stage="启动执行",
                message=f"正在执行第 {attempt} 次尝试，共允许 {total_attempts} 次。",
                queue_position=None,
                started_at=job.get("started_at") or _now(),
            )
            _touch(job)

        result = _run_subprocess_attempt(job_id, operation, payload)
        if result.get("success"):
            if operation == "CREATE_APP":
                save_operation("CREATE_APP", result, batch_id)
                if result.get("success"):
                    save_app(result)
            update_job(job_id, status="SUCCESS", stage="完成", message="任务执行成功。", queue_position=None, result=result, finished_at=_now())
            return

        if operation == "CREATE_APP":
            save_operation("CREATE_APP", result, batch_id)

        should_retry = _should_retry(operation, attempt, max_retries, result)
        if should_retry:
            update_job(job_id, status="RUNNING", stage="准备重试", message=f"第 {attempt} 次失败：{result.get('message')}。即将自动重试。", error=result)
            time.sleep(2)
            continue

        failure = dict(result)
        failure_message = failure.get("message") or "任务执行失败。"
        if operation == "CREATE_APP":
            failure["automatic_retry"] = False
            completed = failure.get("completed_stages") or []
            completed_text = f"已完成阶段：{', '.join(completed)}。" if completed else ""
            failure["operator_message"] = (
                f"{completed_text}创建应用未自动重试。"
                "请先按应用ID或实际渠道名检查平台，再决定是手动处理还是重新提交。"
            )
            failure_message = f"{failure_message} 创建应用未自动重试，请先检查平台是否已保存。"
        update_job(
            job_id,
            status="FAILED",
            stage=failure.get("failed_stage") or failure.get("error_stage") or "失败",
            message=failure_message,
            queue_position=None,
            result=failure,
            error=failure,
            finished_at=_now(),
        )
        return


def _run_subprocess_attempt(job_id: str, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    function_map = {"CREATE_APP": "create_app"}
    function_name = function_map.get(operation)
    if not function_name:
        return {"success": False, "message": f"不支持的任务类型: {operation}", "error_stage": "UNSUPPORTED", "next_action": "STOP"}

    work_dir = Path("data") / "job_runs" / job_id / f"attempt_{int(_jobs[job_id]['attempt'])}"
    work_dir.mkdir(parents=True, exist_ok=True)
    input_path = work_dir / "input.json"
    output_path = work_dir / "output.json"
    input_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    cmd = [
        sys.executable,
        str(Path("scripts") / "run_executor_call.py"),
        "--function",
        function_name,
        "--input",
        str(input_path),
        "--output",
        str(output_path),
    ]
    proc = subprocess.Popen(cmd, cwd=str(Path.cwd()), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    started = time.monotonic()
    last_stage = ""
    last_stage_change = time.monotonic()

    while True:
        if proc.poll() is not None:
            break

        running = read_running_executor_stage()
        stage = running.get("stage") or running.get("evidence_ref") or "执行端运行中"
        execution_id = running.get("execution_id")
        if stage != last_stage:
            last_stage = stage
            last_stage_change = time.monotonic()
        elapsed = int(time.monotonic() - started)
        stage_elapsed = int(time.monotonic() - last_stage_change)
        update_job(
            job_id,
            stage=stage,
            execution_id=execution_id,
            message=f"{stage}；本次尝试已运行 {elapsed} 秒，本阶段 {stage_elapsed} 秒。",
            stage_elapsed_seconds=stage_elapsed,
            elapsed_seconds=elapsed,
        )

        if elapsed >= TASK_TIMEOUT_SECONDS:
            return _kill_and_fail(proc, job_id, "任务总时长超过限制，已终止本次尝试。", "TASK_TIMEOUT")
        if stage_elapsed >= STAGE_STALL_SECONDS:
            return _kill_and_fail(proc, job_id, f"阶段“{stage}”超过 {STAGE_STALL_SECONDS} 秒无变化，已终止本次尝试。", "STAGE_STALLED")
        time.sleep(POLL_SECONDS)

    stdout, stderr = proc.communicate(timeout=5)
    if output_path.exists():
        try:
            payload_out = json.loads(output_path.read_text(encoding="utf-8"))
            if payload_out.get("ok"):
                result = payload_out.get("result") or {}
                return result if isinstance(result, dict) else {"success": False, "message": "执行端返回格式异常", "error_stage": "RESULT"}
            err = payload_out.get("error") or {}
            return {"success": False, "message": err.get("message") or "执行端子进程异常", "error_stage": "SUBPROCESS", "next_action": "QUERY", "detail": err}
        except Exception as exc:
            return {"success": False, "message": f"读取子进程结果失败: {exc}", "error_stage": "RESULT", "next_action": "QUERY"}
    return {"success": False, "message": f"执行端子进程未生成结果。stdout={stdout[-500:]} stderr={stderr[-500:]}", "error_stage": "SUBPROCESS", "next_action": "QUERY"}


def _kill_and_fail(proc: subprocess.Popen, job_id: str, message: str, stage: str) -> dict[str, Any]:
    try:
        proc.kill()
        proc.communicate(timeout=5)
    except Exception:
        pass
    release_executor_lock(message)
    update_job(job_id, stage=stage, message=message)
    return {"success": False, "message": message, "error_code": stage, "error_stage": stage, "next_action": "QUERY"}
