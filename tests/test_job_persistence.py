from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.db.database import init_db
from app.services.job_store import load_jobs, save_job


def make_job(job_id: str, status: str) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "operation": "CREATE_APP",
        "status": status,
        "message": "测试任务",
        "stage": "测试阶段",
        "queue_position": 1,
        "attempt": 1 if status == "RUNNING" else 0,
        "max_retries": 2,
        "payload": {"activity_name": "测试活动", "actual_channel_name": "测试渠道"},
        "result": None,
        "error": None,
        "batch_id": "BATCH-TEST",
        "execution_id": "EXEC-TEST" if status == "RUNNING" else None,
        "stage_elapsed_seconds": 12,
        "elapsed_seconds": 20,
        "created_at": "2026-08-28 10:00:00",
        "updated_at": "2026-08-28 10:00:01",
        "started_at": "2026-08-28 10:00:01" if status == "RUNNING" else None,
        "finished_at": None,
    }


def use_temp_database(monkeypatch, tmp_path: Path) -> Path:
    database_path = tmp_path / "orchestrator.sqlite3"
    monkeypatch.setattr(settings, "database_path", database_path)
    init_db()
    return database_path


def test_job_store_round_trip(monkeypatch, tmp_path: Path) -> None:
    use_temp_database(monkeypatch, tmp_path)
    original = make_job("JOB-ROUNDTRIP", "RUNNING")

    save_job(original)
    loaded = load_jobs()

    assert len(loaded) == 1
    assert loaded[0]["job_id"] == original["job_id"]
    assert loaded[0]["payload"] == original["payload"]
    assert loaded[0]["batch_id"] == "BATCH-TEST"
    assert loaded[0]["stage_elapsed_seconds"] == 12


def test_create_app_retry_policy_is_single_attempt() -> None:
    import app.services.job_manager as job_manager

    assert job_manager._max_retries_for("CREATE_APP") == 0
    assert not job_manager._should_retry(
        "CREATE_APP",
        attempt=1,
        max_retries=0,
        result={"next_action": "QUERY"},
    )
    assert not job_manager._should_retry(
        "CREATE_APP",
        attempt=1,
        max_retries=2,
        result={"next_action": "MANUAL_CHECK"},
    )


def test_create_app_query_failure_runs_only_once(monkeypatch) -> None:
    import app.services.job_manager as job_manager

    job = make_job("JOB-NO-RETRY", "QUEUED")
    job.update(attempt=0, max_retries=0)
    calls = []

    monkeypatch.setattr(job_manager, "_jobs", {job["job_id"]: job})
    monkeypatch.setattr(job_manager, "_touch", lambda current: None)
    monkeypatch.setattr(job_manager, "save_operation", lambda *args, **kwargs: None)

    def fail_once(job_id, operation, payload):
        calls.append((job_id, operation))
        return {
            "success": False,
            "message": "响应未知",
            "error_stage": "QUERY",
            "next_action": "QUERY",
        }

    monkeypatch.setattr(job_manager, "_run_subprocess_attempt", fail_once)
    job_manager._run_job_with_retries(job["job_id"])

    assert calls == [("JOB-NO-RETRY", "CREATE_APP")]
    assert job["attempt"] == 1
    assert job["status"] == "FAILED"
    assert job["result"]["automatic_retry"] is False
    assert "先检查平台" in job["message"]


def test_restart_recovers_waiting_and_stops_running(monkeypatch, tmp_path: Path) -> None:
    use_temp_database(monkeypatch, tmp_path)
    save_job(make_job("JOB-WAITING", "WAITING"))
    save_job(make_job("JOB-RUNNING", "RUNNING"))

    import app.services.job_manager as job_manager

    job_manager = importlib.reload(job_manager)
    monkeypatch.setattr(job_manager, "_start_worker_once", lambda: None)
    job_manager.initialize_job_manager()

    waiting = job_manager.get_job("JOB-WAITING")
    interrupted = job_manager.get_job("JOB-RUNNING")

    assert waiting is not None
    assert waiting["status"] == "QUEUED"
    assert waiting["queue_position"] == 1
    assert waiting["max_retries"] == 0
    assert "恢复" in waiting["stage"]

    assert interrupted is not None
    assert interrupted["status"] == "FAILED"
    assert interrupted["error"]["error_code"] == "BACKEND_RESTARTED"
    assert interrupted["error"]["next_action"] == "MANUAL_CHECK"

    persisted = {job["job_id"]: job for job in load_jobs()}
    assert persisted["JOB-WAITING"]["status"] == "QUEUED"
    assert persisted["JOB-RUNNING"]["status"] == "FAILED"
