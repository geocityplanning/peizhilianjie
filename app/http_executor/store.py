from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


CONTRACT_VERSION = "http-executor.v1"


class DatabaseUnavailable(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode(value: Optional[str], default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def initialize_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS executions (
                execution_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                operation TEXT NOT NULL,
                environment TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                request_fingerprint TEXT NOT NULL,
                execution_state TEXT NOT NULL,
                business_status TEXT NOT NULL,
                request_json TEXT NOT NULL,
                data_json TEXT,
                error_json TEXT,
                evidence_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_executions_task
                ON executions(task_id, operation, environment);
            CREATE TABLE IF NOT EXISTS executor_lock (
                lock_name TEXT PRIMARY KEY,
                execution_id TEXT NOT NULL,
                acquired_at TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL
            );
            """
        )


class ExecutorStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def assert_ready(self) -> None:
        if not self.path.exists():
            raise DatabaseUnavailable(f"执行端数据库不存在: {self.path}")
        try:
            with self._connect() as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                required = {"executions", "executor_lock"}
                if not required.issubset(tables):
                    raise DatabaseUnavailable("执行端数据库缺少必要数据表")
        except sqlite3.Error as exc:
            raise DatabaseUnavailable(f"执行端数据库不可用: {exc}") from exc

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    @staticmethod
    def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        result = dict(row)
        result["request"] = _decode(result.pop("request_json"), {})
        result["data"] = _decode(result.pop("data_json"), None)
        result["error"] = _decode(result.pop("error_json"), None)
        result["evidence_ref"] = _decode(result.pop("evidence_json"), [])
        return result

    def reserve(
        self,
        *,
        task_id: str,
        operation: str,
        environment: str,
        idempotency_key: str,
        request_fingerprint: str,
        request: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], bool]:
        """Atomically resolve idempotency, create a record, and acquire the browser lock."""
        self.assert_ready()
        execution_id = f"EXEC-{uuid.uuid4().hex[:12].upper()}"
        now = utc_now()
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT * FROM executions WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if existing is not None:
                    record = self._row_to_dict(existing)
                    if record["request_fingerprint"] != request_fingerprint:
                        connection.rollback()
                        return self._rejected_record(
                            task_id=task_id,
                            operation=operation,
                            environment=environment,
                            idempotency_key=idempotency_key,
                            request=request,
                            code="IDEMPOTENCY_CONFLICT",
                            message="同一个幂等键对应了不同请求参数",
                        ), False
                    connection.commit()
                    return record, False

                active = connection.execute(
                    "SELECT execution_id FROM executor_lock WHERE lock_name = 'browser'"
                ).fetchone()
                if active is not None:
                    record = self._insert_record(
                        connection,
                        execution_id=execution_id,
                        task_id=task_id,
                        operation=operation,
                        environment=environment,
                        idempotency_key=idempotency_key,
                        request_fingerprint=request_fingerprint,
                        execution_state="REJECTED",
                        business_status="REJECTED",
                        request=request,
                        error={
                            "error_code": "EXECUTOR_BUSY",
                            "error_stage": "LOCK",
                            "message": f"执行端忙碌中，占用执行: {active['execution_id']}",
                            "next_action": "QUERY",
                        },
                        now=now,
                    )
                    connection.commit()
                    return record, False

                record = self._insert_record(
                    connection,
                    execution_id=execution_id,
                    task_id=task_id,
                    operation=operation,
                    environment=environment,
                    idempotency_key=idempotency_key,
                    request_fingerprint=request_fingerprint,
                    execution_state="ACCEPTED",
                    business_status="PENDING",
                    request=request,
                    now=now,
                )
                connection.execute(
                    "INSERT INTO executor_lock(lock_name, execution_id, acquired_at, heartbeat_at) VALUES (?, ?, ?, ?)",
                    ("browser", execution_id, now, now),
                )
                connection.commit()
                return record, True
            except Exception:
                connection.rollback()
                raise

    def _insert_record(
        self,
        connection: sqlite3.Connection,
        *,
        execution_id: str,
        task_id: str,
        operation: str,
        environment: str,
        idempotency_key: str,
        request_fingerprint: str,
        execution_state: str,
        business_status: str,
        request: Dict[str, Any],
        now: str,
        error: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        connection.execute(
            """
            INSERT INTO executions(
                execution_id, task_id, operation, environment, idempotency_key,
                request_fingerprint, execution_state, business_status,
                request_json, data_json, error_json, evidence_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, '[]', ?, ?)
            """,
            (
                execution_id,
                task_id,
                operation,
                environment,
                idempotency_key,
                request_fingerprint,
                execution_state,
                business_status,
                _json(request),
                _json(error) if error else None,
                now,
                now,
            ),
        )
        row = connection.execute(
            "SELECT * FROM executions WHERE execution_id = ?", (execution_id,)
        ).fetchone()
        return self._row_to_dict(row)  # type: ignore[return-value]

    def _rejected_record(
        self,
        *,
        task_id: str,
        operation: str,
        environment: str,
        idempotency_key: str,
        request: Dict[str, Any],
        code: str,
        message: str,
    ) -> Dict[str, Any]:
        now = utc_now()
        return {
            "execution_id": None,
            "task_id": task_id,
            "operation": operation,
            "environment": environment,
            "idempotency_key": idempotency_key,
            "request_fingerprint": "",
            "execution_state": "REJECTED",
            "business_status": "REJECTED",
            "request": request,
            "data": None,
            "error": {"error_code": code, "error_stage": "VALIDATE", "message": message},
            "evidence_ref": [],
            "created_at": now,
            "updated_at": now,
        }

    def get_by_execution_id(self, execution_id: str) -> Optional[Dict[str, Any]]:
        self.assert_ready()
        with self._connect() as connection:
            return self._row_to_dict(
                connection.execute(
                    "SELECT * FROM executions WHERE execution_id = ?", (execution_id,)
                ).fetchone()
            )

    def get_by_lookup(
        self,
        *,
        task_id: Optional[str],
        operation: Optional[str],
        environment: Optional[str],
        idempotency_key: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        self.assert_ready()
        with self._connect() as connection:
            if idempotency_key:
                row = connection.execute(
                    "SELECT * FROM executions WHERE idempotency_key = ?", (idempotency_key,)
                ).fetchone()
            elif task_id and operation and environment:
                row = connection.execute(
                    """
                    SELECT * FROM executions
                    WHERE task_id = ? AND operation = ? AND environment = ?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (task_id, operation, environment),
                ).fetchone()
            else:
                row = None
            return self._row_to_dict(row)

    def mark_running(self, execution_id: str) -> None:
        self.assert_ready()
        with self._connect() as connection:
            now = utc_now()
            connection.execute(
                "UPDATE executions SET execution_state = 'RUNNING', updated_at = ? WHERE execution_id = ? AND execution_state = 'ACCEPTED'",
                (now, execution_id),
            )
            connection.execute(
                "UPDATE executor_lock SET heartbeat_at = ? WHERE lock_name = 'browser' AND execution_id = ?",
                (now, execution_id),
            )

    def finish(
        self,
        *,
        execution_id: str,
        execution_state: str,
        business_status: str,
        data: Optional[Dict[str, Any]],
        error: Optional[Dict[str, Any]],
        evidence_ref: list[str],
    ) -> None:
        self.assert_ready()
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT execution_state FROM executions WHERE execution_id = ?",
                    (execution_id,),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    return
                if row["execution_state"] in {"SUCCEEDED", "FAILED", "UNKNOWN", "REJECTED"}:
                    connection.rollback()
                    return
                now = utc_now()
                connection.execute(
                    """
                    UPDATE executions
                    SET execution_state = ?, business_status = ?, data_json = ?,
                        error_json = ?, evidence_json = ?, updated_at = ?
                    WHERE execution_id = ?
                    """,
                    (
                        execution_state,
                        business_status,
                        _json(data) if data is not None else None,
                        _json(error) if error is not None else None,
                        _json(evidence_ref),
                        now,
                        execution_id,
                    ),
                )
                connection.execute(
                    "DELETE FROM executor_lock WHERE lock_name = 'browser' AND execution_id = ?",
                    (execution_id,),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def active_execution_id(self) -> Optional[str]:
        self.assert_ready()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT execution_id FROM executor_lock WHERE lock_name = 'browser'"
            ).fetchone()
            return row["execution_id"] if row else None

