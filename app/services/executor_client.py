import subprocess
import sys
from collections.abc import Callable
from typing import Any

from app.core.config import settings

BROWSER_REQUIRED_FUNCTIONS = {
    "get_status",
    "login",
    "create_channel",
    "create_app",
    "update_app",
    "locate_app",
}

HERMES_ALLOWED_FUNCTIONS = {"create_channel", "create_app"}


def _ensure_executor_path() -> None:
    executor_path = str(settings.automation_executor_path)
    if executor_path not in sys.path:
        sys.path.insert(0, executor_path)


def ensure_browser_guard() -> None:
    guard = settings.automation_executor_path / "browser_guard.ps1"
    if not guard.exists():
        raise RuntimeError(f"找不到浏览器守护脚本: {guard}")

    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(guard),
        ],
        cwd=str(settings.automation_executor_path),
        capture_output=True,
        text=True,
        timeout=20,
    )
    if completed.returncode != 0:
        output = (completed.stdout or "") + (completed.stderr or "")
        raise RuntimeError(f"启动浏览器调试端口失败: {output.strip() or completed.returncode}")


def _load_api_function(name: str) -> Callable[..., dict[str, Any]]:
    _ensure_executor_path()
    try:
        from core import executor as executor_db
        executor_db.init_db()
        from actions import api
    except Exception as exc:
        raise RuntimeError(f"无法加载自动化执行端: {settings.automation_executor_path}") from exc

    try:
        return getattr(api, name)
    except AttributeError as exc:
        raise RuntimeError(f"自动化执行端缺少函数: {name}") from exc


def call_executor(name: str, **kwargs: Any) -> dict[str, Any]:
    if name in BROWSER_REQUIRED_FUNCTIONS:
        ensure_browser_guard()
    func = _load_api_function(name)
    return func(**kwargs)


def call_hermes_executor(name: str, **kwargs: Any) -> dict[str, Any]:
    if name not in HERMES_ALLOWED_FUNCTIONS:
        raise RuntimeError(f"Hermes HTTP 执行端不允许调用: {name}")
    return call_executor(name, **kwargs)
