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
from app.services.repository import save_app, save_operation

_jobs: dict[str, dict[str, Any]] = {}
_queue: deque[str] = deque()
_jobs_lock = threading.Lock()
_queue_event = threading.Event()
_worker_started = False
_worker_lock = threading.Lock()

MAX_RETRIES = 2
STAGE_STALL_SECONDS = 300
TASK_TIMEOUT_SECONDS = 900
POLL_SECONDS = 2


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _touch(job: dict[str, Any]) -> None:
    job["updated_at"] = _now()


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


def _start_worker_once() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        threading.Thread(target=_worker_loop, daemon=True).start()
        _worker_started = True


def create_job(operation: str, payload: dict[str, Any], batch_id: str | None = None) -> dict[str, Any]:
    _start_worker_once()
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
            "max_retries": MAX_RETRIES,
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
        public = dict(job)
        public.pop("payload", None)
        public.pop("batch_id", None)
        return public


def list_jobs() -> list[dict[str, Any]]:
    with _jobs_lock:
        rows = []
        for job in _jobs.values():
            public = dict(job)
            public.pop("payload", None)
            public.pop("batch_id", None)
            rows.append(public)
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
            job.update(status="CANCELLED", stage="已取消", message="任务尚未开始，已取消。", finished_at=_now())
            _touch(job)
            _refresh_queue_positions_locked()
        elif job["status"] == "RUNNING":
            job.update(message="任务正在运行；如卡住会由超时监控自动终止并重试/失败跳过。")
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
            batch_id = job.get("batch_id")
            job.update(status="RUNNING", stage="启动执行", message=f"正在执行第 {attempt} 次尝试。", started_at=job.get("started_at") or _now())
            _touch(job)

        result = _run_subprocess_attempt(job_id, operation, payload)
        if result.get("success"):
            if operation == "CREATE_APP":
                save_operation("CREATE_APP", result, batch_id)
                if result.get("success"):
                    save_app(result)
            update_job(job_id, status="SUCCESS", stage="完成", message="任务执行成功。", result=result, finished_at=_now())
            return

        if operation == "CREATE_APP":
            save_operation("CREATE_APP", result, batch_id)

        should_retry = attempt <= MAX_RETRIES and result.get("next_action") != "STOP"
        if should_retry:
            update_job(job_id, status="RUNNING", stage="准备重试", message=f"第 {attempt} 次失败：{result.get('message')}。即将自动重试。", error=result)
            time.sleep(2)
            continue

        update_job(job_id, status="FAILED", stage=result.get("error_stage") or "失败", message=result.get("message") or "任务失败，已跳过。", result=result, error=result, finished_at=_now())
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
