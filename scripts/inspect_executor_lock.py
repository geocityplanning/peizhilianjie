import sqlite3
from pathlib import Path

DB = Path(r"F:\卓望\配链接项目\执行端代码\执行端打包文件夹\data\executor.db")
TARGET = "EXEC-E421BAAD0433"
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
print("LOCKS")
for row in conn.execute("select * from executor_lock").fetchall():
    print(dict(row))
print("TARGET")
for row in conn.execute("""
    select execution_id, task_id, operation, execution_state, business_status,
           input_json, output_json, error_json, evidence_ref, started_at, finished_at, created_at
    from executions
    where execution_id = ?
""", (TARGET,)).fetchall():
    print(dict(row))
print("RUNNING")
for row in conn.execute("""
    select execution_id, operation, execution_state, business_status,
           output_json, error_json, evidence_ref, started_at, finished_at, created_at
    from executions
    where execution_state = 'RUNNING'
    order by created_at desc
""").fetchall():
    print(dict(row))
conn.close()
