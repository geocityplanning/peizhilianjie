from pathlib import Path

p = Path(r"F:\卓望\配链接项目\codex\app\api\routes.py")
s = p.read_text(encoding="utf-8")
s = s.replace(
    "from app.services.executor_client import call_executor",
    "from app.services.executor_client import call_executor\nfrom app.services.executor_lock import get_executor_lock_status, release_executor_lock",
)
insert = '''

@router.get("/executor/lock")
def executor_lock() -> dict:
    return get_executor_lock_status()


@router.post("/executor/lock/release")
def release_lock() -> dict:
    return release_executor_lock()
'''
if '/executor/lock' not in s:
    s = s.replace('@router.get("/health")', insert + '\n\n@router.get("/health")')
p.write_text(s, encoding="utf-8")
