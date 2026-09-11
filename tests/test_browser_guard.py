from __future__ import annotations

import sys


def test_browser_candidates_include_macos_locations(monkeypatch):
    from app.executor import browser_guard

    monkeypatch.setattr(browser_guard.sys, "platform", "darwin")
    candidates = browser_guard._browser_candidates()

    paths = {path.as_posix() for _, path in candidates}
    assert "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" in paths
    assert "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge" in paths


def test_browser_executable_override(monkeypatch):
    from app.executor import browser_guard

    monkeypatch.setenv("HERMES_BROWSER_EXECUTABLE", "/custom/browser")
    assert browser_guard._browser_candidates() == [("configured browser", browser_guard.Path("/custom/browser"))]


def test_executor_client_uses_python_guard(tmp_path, monkeypatch):
    from app.services import executor_client

    guard = tmp_path / "browser_guard.py"
    guard.write_text("print('ready')\n", encoding="utf-8")
    monkeypatch.setattr(executor_client.settings, "automation_executor_path", tmp_path)

    calls = []
    original_run = executor_client.subprocess.run

    def capture_run(args, **kwargs):
        calls.append((args, kwargs))
        return original_run(args, **kwargs)

    monkeypatch.setattr(executor_client.subprocess, "run", capture_run)
    executor_client.ensure_browser_guard()

    assert calls[0][0][:2] == [sys.executable, str(guard)]
    assert "powershell" not in " ".join(calls[0][0]).lower()