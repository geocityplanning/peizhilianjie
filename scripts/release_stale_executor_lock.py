import sqlite3
from datetime import datetime
from pathlib import Path

DB = Path(r"F:\卓望\配链接项目\执行端代码\执行端打包文件夹\data\executor.db")
EXECUTION_ID = "EXEC-3235E9E2D471"
now = datetime.now().astimezone().isoformat()
error = '{"code":"STALE_LOCK_RELEASED","stage":"LOCK","message":"旧创建应用任务长时间无进度，已由编排层人工释放僵尸锁。","next_action":"QUERY"}'
conn = sqlite3.connect(DB)
conn.execute("delete from executor_lock where holder = ?", (EXECUTION_ID,))
conn.execute(
    """
    update executions
    set execution_state = 'FINAL', business_status = 'UNKNOWN', finished_at = ?, error_json = ?
    where execution_id = ? and execution_state = 'RUNNING'
    """,
    (now, error, EXECUTION_ID),
)
conn.commit()
print("released", conn.total_changes)
print(conn.execute("select * from executor_lock").fetchall())
conn.close()
