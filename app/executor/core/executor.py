# -*- coding: utf-8 -*-
"""
执行端核心：SQLite 持久化 + 幂等键 + 单实例锁 + ExecutionResult 构造。

职责：
  1. 建表 / 初始化
  2. 幂等键计算：md5(task_id + operation + canonical_json(data))
  3. 执行记录 CRUD
  4. 单实例锁：写操作期间 state=BUSY，第二个请求直接拒绝
  5. ExecutionResult 构造器
"""
import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ---- 常量 ----

CONTRACT_VERSION = "mcp-executor-v0.1-draft"
ENVIRONMENT = os.environ.get("AMOO_ENV", "TEST")
DB_PATH = os.environ.get("LINK_EXECUTOR_DB_PATH") or str(Path(__file__).resolve().parents[3] / "data" / "executor" / "executor.db")

# execution_state 枚举
STATE_ACCEPTED = "ACCEPTED"
STATE_RUNNING = "RUNNING"
STATE_FINAL = "FINAL"
STATE_RECONCILIATION = "RECONCILIATION_REQUIRED"

# business_status 枚举
BIZ_SUCCESS = "SUCCESS"
BIZ_FAILED = "FAILED"
BIZ_UNKNOWN = "UNKNOWN"

# executor state 枚举
EXEC_READY = "READY"
EXEC_BUSY = "BUSY"
EXEC_NOT_READY = "NOT_READY"


# ---- SQLite ----

_lock = threading.Lock()


def _get_db() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db():
    conn = _get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS executions (
        execution_id    TEXT PRIMARY KEY,
        task_id          TEXT NOT NULL,
        operation        TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        execution_state TEXT NOT NULL,
        business_status TEXT,
        input_json       TEXT,
        output_json      TEXT,
        error_json       TEXT,
        evidence_ref    TEXT,
        environment     TEXT NOT NULL,
        started_at      TEXT,
        finished_at     TEXT,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_idempotency ON executions(idempotency_key);
    CREATE INDEX IF NOT EXISTS idx_task ON executions(task_id);
    CREATE INDEX IF NOT EXISTS idx_state ON executions(execution_state);
    CREATE TABLE IF NOT EXISTS executor_lock (
        id          INTEGER PRIMARY KEY CHECK (id = 1),
        holder      TEXT,
        held_since  TEXT,
        UNIQUE(id)
    );
    """)
    conn.commit()
    conn.close()


# ---- 幂等键 ----

def compute_idempotency_key(task_id: str, operation: str, data: dict) -> str:
    """md5(task_id + operation + canonical_json(data))。"""
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False)
    raw = f"{task_id}.{operation}.{canonical}"
    return hashlib.md5(raw.encode()).hexdigest()


# ---- 执行记录 ----

def create_execution(task_id: str, operation: str, idempotency_key: str,
                     input_data: dict, environment: str) -> dict:
    """插入一条 ACCEPTED 执行记录，返回完整行。"""
    execution_id = f"EXEC-{uuid.uuid4().hex[:12].upper()}"
    now = datetime.now(timezone.utc).astimezone().isoformat()
    conn = _get_db()
    conn.execute(
        """INSERT INTO executions
           (execution_id, task_id, operation, idempotency_key, execution_state,
            business_status, input_json, environment, started_at)
           VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)""",
        (execution_id, task_id, operation, idempotency_key, STATE_ACCEPTED,
         json.dumps(input_data, ensure_ascii=False), environment, now)
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM executions WHERE execution_id = ?", (execution_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else {}


def find_by_idempotency(idempotency_key: str) -> Optional[dict]:
    conn = _get_db()
    row = conn.execute(
        "SELECT * FROM executions WHERE idempotency_key = ?", (idempotency_key,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def find_by_execution_id(execution_id: str) -> Optional[dict]:
    conn = _get_db()
    row = conn.execute(
        "SELECT * FROM executions WHERE execution_id = ?", (execution_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_execution(execution_id: str, **fields):
    """部分更新执行记录。"""
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [execution_id]
    conn = _get_db()
    conn.execute(f"UPDATE executions SET {sets} WHERE execution_id = ?", vals)
    conn.commit()
    conn.close()


def finalize_execution(execution_id: str, business_status: str,
                       output_data: Optional[dict] = None,
                       error: Optional[dict] = None,
                       evidence_ref: Optional[str] = None):
    now = datetime.now(timezone.utc).astimezone().isoformat()
    update_execution(
        execution_id,
        execution_state=STATE_FINAL,
        business_status=business_status,
        output_json=json.dumps(output_data, ensure_ascii=False) if output_data else None,
        error_json=json.dumps(error, ensure_ascii=False) if error else None,
        evidence_ref=evidence_ref,
        finished_at=now,
    )


# ---- 单实例锁 ----

def acquire_lock(holder: str) -> bool:
    """尝试获取单实例锁。成功返回 True，已被占用返回 False。"""
    conn = _get_db()
    now = datetime.now(timezone.utc).astimezone().isoformat()
    # INSERT OR IGNORE：如果已有行则不插入
    conn.execute(
        "INSERT OR IGNORE INTO executor_lock (id, holder, held_since) VALUES (1, ?, ?)",
        (holder, now)
    )
    row = conn.execute("SELECT holder FROM executor_lock WHERE id = 1").fetchone()
    conn.commit()
    conn.close()
    return row and row["holder"] == holder


def release_lock(holder: str):
    conn = _get_db()
    conn.execute("DELETE FROM executor_lock WHERE id = 1 AND holder = ?", (holder,))
    conn.commit()
    conn.close()


def is_locked() -> bool:
    conn = _get_db()
    row = conn.execute("SELECT holder FROM executor_lock WHERE id = 1").fetchone()
    conn.close()
    return row is not None


def get_active_execution_id() -> Optional[str]:
    """如果 BUSY，返回占用锁的 execution_id（holder 就是 execution_id）。"""
    conn = _get_db()
    row = conn.execute("SELECT holder FROM executor_lock WHERE id = 1").fetchone()
    conn.close()
    return row["holder"] if row else None


# ---- ExecutionResult 构造 ----

def build_result(
    task_id: str,
    operation: str,
    idempotency_key: str,
    execution: Optional[dict] = None,
    accepted: bool = False,
    data: Optional[dict] = None,
    error: Optional[dict] = None,
    environment: str = None,
) -> dict:
    """按契约构造 ExecutionResult。"""
    now = datetime.now(timezone.utc).astimezone().isoformat()
    return {
        "contract_version": CONTRACT_VERSION,
        "accepted": accepted,
        "execution_id": execution.get("execution_id") if execution else None,
        "task_id": task_id,
        "operation": operation,
        "environment": environment or ENVIRONMENT,
        "idempotency_key": idempotency_key,
        "execution_state": execution.get("execution_state") if execution else None,
        "business_status": execution.get("business_status") if execution else None,
        "data": data or {},
        "error": error,
        "evidence_ref": execution.get("evidence_ref") if execution else None,
        "started_at": execution.get("started_at") if execution else None,
        "finished_at": execution.get("finished_at") if execution else None,
    }


# ---- 初始化 ----

init_db()

