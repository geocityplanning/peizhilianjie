import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import settings


def _db_path() -> Path:
    return settings.automation_executor_path / "data" / "executor.db"


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _read_stage(output_json: str | None, evidence_ref: str | None) -> str:
    if output_json:
        try:
            data = json.loads(output_json)
            if isinstance(data, dict) and data.get("_stage"):
                return str(data["_stage"])
        except Exception:
            pass
    return evidence_ref or ""


def get_executor_lock_status() -> dict[str, Any]:
    db = _db_path()
    if not db.exists():
        return {"locked": False, "message": "执行端数据库不存在。"}

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    lock = conn.execute("select * from executor_lock where id = 1").fetchone()
    if not lock:
        conn.close()
        return {"locked": False, "message": "执行端空闲。"}

    holder = lock["holder"]
    execution = conn.execute(
        """
        select execution_id, operation, execution_state, business_status, output_json,
               error_json, evidence_ref, started_at, finished_at, created_at
        from executions
        where execution_id = ?
        """,
        (holder,),
    ).fetchone()
    conn.close()

    held_since = lock["held_since"]
    held_dt = _parse_dt(held_since)
    age_seconds = int((datetime.now(held_dt.tzinfo) - held_dt).total_seconds()) if held_dt else None
    result = {
        "locked": True,
        "holder": holder,
        "held_since": held_since,
        "age_seconds": age_seconds,
        "stale_candidate": bool(age_seconds is not None and age_seconds >= 600),
        "message": f"执行端忙碌中，占用执行: {holder}",
    }
    if execution:
        row = dict(execution)
        result.update({
            "operation": row.get("operation"),
            "execution_state": row.get("execution_state"),
            "business_status": row.get("business_status"),
            "stage": _read_stage(row.get("output_json"), row.get("evidence_ref")),
            "started_at": row.get("started_at"),
            "finished_at": row.get("finished_at"),
            "error_json": row.get("error_json"),
        })
    return result


def release_executor_lock(reason: str = "管理员确认任务卡住，释放执行端锁。") -> dict[str, Any]:
    status = get_executor_lock_status()
    if not status.get("locked"):
        return status

    holder = status["holder"]
    now = datetime.now().astimezone().isoformat()
    error = {
        "code": "STALE_LOCK_RELEASED",
        "stage": "LOCK",
        "message": reason,
        "next_action": "QUERY",
    }
    db = _db_path()
    conn = sqlite3.connect(db)
    conn.execute("delete from executor_lock where holder = ?", (holder,))
    conn.execute(
        """
        update executions
        set execution_state = 'FINAL', business_status = 'UNKNOWN', finished_at = ?, error_json = ?
        where execution_id = ? and execution_state = 'RUNNING'
        """,
        (now, json.dumps(error, ensure_ascii=False), holder),
    )
    conn.commit()
    changes = conn.total_changes
    conn.close()
    return {"released": True, "holder": holder, "changes": changes, "message": reason}
