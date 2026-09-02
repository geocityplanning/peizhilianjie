from __future__ import annotations

import json
from typing import Any

from app.db.database import get_connection


_JOB_COLUMNS = (
    "job_id",
    "operation",
    "status",
    "message",
    "stage",
    "queue_position",
    "attempt",
    "max_retries",
    "payload_json",
    "result_json",
    "error_json",
    "batch_id",
    "execution_id",
    "stage_elapsed_seconds",
    "elapsed_seconds",
    "created_at",
    "updated_at",
    "started_at",
    "finished_at",
)


def _dump(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def save_job(job: dict[str, Any]) -> None:
    values = (
        job["job_id"],
        job["operation"],
        job["status"],
        job.get("message") or "",
        job.get("stage") or "",
        job.get("queue_position"),
        int(job.get("attempt") or 0),
        int(job.get("max_retries") or 0),
        _dump(job.get("payload") or {}),
        _dump(job.get("result")),
        _dump(job.get("error")),
        job.get("batch_id"),
        job.get("execution_id"),
        job.get("stage_elapsed_seconds"),
        job.get("elapsed_seconds"),
        job["created_at"],
        job["updated_at"],
        job.get("started_at"),
        job.get("finished_at"),
    )
    assignments = ", ".join(
        f"{column} = excluded.{column}" for column in _JOB_COLUMNS if column != "job_id"
    )
    placeholders = ", ".join("?" for _ in _JOB_COLUMNS)
    with get_connection() as conn:
        conn.execute(
            f"""
            INSERT INTO jobs ({", ".join(_JOB_COLUMNS)})
            VALUES ({placeholders})
            ON CONFLICT(job_id) DO UPDATE SET {assignments}
            """,
            values,
        )


def load_jobs() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT job_id, operation, status, message, stage, queue_position,
                   attempt, max_retries, payload_json, result_json, error_json,
                   batch_id, execution_id, stage_elapsed_seconds, elapsed_seconds,
                   created_at, updated_at, started_at, finished_at
            FROM jobs
            ORDER BY created_at ASC
            """
        ).fetchall()

    jobs: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["payload"] = _load(item.pop("payload_json"), {})
        item["result"] = _load(item.pop("result_json"), None)
        item["error"] = _load(item.pop("error_json"), None)
        jobs.append(item)
    return jobs