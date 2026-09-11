from __future__ import annotations

import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen


TARGET_URL = os.getenv(
    "HERMES_BROWSER_TARGET_URL",
    "https://uat-cloud.139.com/cloudappadmin/#/cloudAppChannelManager",
)
CDP_URL = os.getenv("HERMES_CDP_URL", "http://127.0.0.1:9222").rstrip("/")
PAGE_MARKERS = ("uat-cloud.139.com", "plus.buy.139.com")


def _get_json(path: str) -> object:
    request = Request(f"{CDP_URL}{path}", headers={"Accept": "application/json"})
    with urlopen(request, timeout=5) as response:
        return response.read().decode("utf-8")


def _cdp_ready() -> bool:
    try:
        _get_json("/json/version")
        return True
    except Exception:
        return False


def _has_target_page() -> bool:
    try:
        import json

        pages = json.loads(_get_json("/json"))
        return any(any(marker in str(page.get("url") or "") for marker in PAGE_MARKERS) for page in pages)
    except Exception:
        return False


def _open_target_page() -> None:
    encoded = quote(TARGET_URL, safe="")
    request = Request(f"{CDP_URL}/json/new?{encoded}", method="PUT")
    try:
        with urlopen(request, timeout=5):
            return
    except Exception:
        # Some newer Chromium builds disable /json/new. The browser is still
        # usable when its first tab was opened with TARGET_URL at launch.
        return


def _profile_dir() -> Path:
    configured = os.getenv("HERMES_BROWSER_PROFILE_DIR", "").strip()
    profile = Path(configured) if configured else Path(__file__).resolve().parent / "browser-profile"
    profile.mkdir(parents=True, exist_ok=True)
    return profile


def _browser_candidates() -> list[tuple[str, Path]]:
    configured = os.getenv("HERMES_BROWSER_EXECUTABLE", "").strip()
    if configured:
        return [("configured browser", Path(configured).expanduser())]

    if sys.platform == "win32":
        return [
            ("Chrome", Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")),
            ("Chrome", Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe")),
            ("Edge", Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")),
            ("Edge", Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")),
        ]

    if sys.platform == "darwin":
        return [
            ("Chrome", Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")),
            ("Chrome", Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            ("Edge", Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")),
            ("Edge", Path.home() / "Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            ("Chromium", Path("/Applications/Chromium.app/Contents/MacOS/Chromium")),
        ]

    return [
        ("Chrome", Path("/usr/bin/google-chrome")),
        ("Chrome", Path("/usr/bin/google-chrome-stable")),
        ("Chromium", Path("/usr/bin/chromium")),
        ("Chromium", Path("/usr/bin/chromium-browser")),
    ]


def _launch(browser_path: Path) -> subprocess.Popen[bytes]:
    args = [
        str(browser_path),
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=9222",
        f"--user-data-dir={_profile_dir()}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-default-apps",
        "--disable-sync",
        "--disable-popup-blocking",
        TARGET_URL,
    ]
    kwargs: dict[str, object] = {
        "cwd": str(browser_path.parent),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if platform.system() == "Windows":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(args, **kwargs)


def ensure_browser() -> None:
    if _cdp_ready():
        if _has_target_page():
            return
        _open_target_page()
        for _ in range(20):
            if _has_target_page():
                return
            time.sleep(0.25)
        raise RuntimeError("浏览器调试端口已启动，但没有找到 139 测试后台页面")

    errors: list[str] = []
    for browser_name, browser_path in _browser_candidates():
        if not browser_path.is_file():
            continue
        try:
            _launch(browser_path)
        except Exception as exc:
            errors.append(f"{browser_name}: {exc}")
            continue

        for _ in range(40):
            if _cdp_ready():
                if not _has_target_page():
                    _open_target_page()
                for _ in range(20):
                    if _has_target_page():
                        return
                    time.sleep(0.25)
                errors.append(f"{browser_name}: 未找到 139 测试后台页面")
                break
            time.sleep(0.25)
        else:
            errors.append(f"{browser_name}: CDP 9222 未响应")

    platform_name = platform.system()
    hint = "请设置 HERMES_BROWSER_EXECUTABLE 指向浏览器可执行文件"
    detail = "; ".join(errors) or "未找到支持的 Chrome、Edge 或 Chromium"
    raise RuntimeError(f"跨平台浏览器守护启动失败（{platform_name}）：{detail}。{hint}")


if __name__ == "__main__":
    ensure_browser()
    print("BROWSER_GUARD: browser and 139 page are ready")