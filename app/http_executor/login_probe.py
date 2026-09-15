from __future__ import annotations

import asyncio
import hashlib
import random
import string
import time
from datetime import datetime
from typing import Any

import requests
CDP_URL = "http://127.0.0.1:9222"
LOGIN_HOST = "uat-cloud.139.com"
HEARTBEAT_PATH = "/backend/cloudTrial/channel/getList"
PROBE_TIMEOUT_SECONDS = 10.0
HEARTBEAT_TIMEOUT_MS = 8_000
CDP_CONNECT_TIMEOUT_MS = 8_000
CLEANUP_TIMEOUT_SECONDS = 0.25


def _async_playwright_factory() -> Any:
    from playwright.async_api import async_playwright

    return async_playwright()


def _request_headers(token: str) -> dict[str, str]:
    now = datetime.now()
    request_id = now.strftime("%Y%m%d%H%M%S") + str(int(now.timestamp() * 1000))
    request_id += "".join(random.choices(string.ascii_letters + string.digits, k=8))
    sign = hashlib.md5((request_id + token + "backend").encode()).hexdigest()
    return {
        "requestId": request_id,
        "PMS-TOKEN": token,
        "sign": sign,
        "Content-Type": "application/json;charset=UTF-8",
        "Accept": "application/json, text/plain, */*",
    }


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("login probe deadline exceeded")
    return remaining


async def _within_deadline(awaitable: Any, deadline: float) -> Any:
    try:
        timeout = _remaining(deadline)
    except TimeoutError:
        if hasattr(awaitable, "close"):
            awaitable.close()
        raise
    return await asyncio.wait_for(awaitable, timeout=timeout)


async def _heartbeat(page: Any, token: str, deadline: float) -> bool:
    timeout_ms = max(1, min(HEARTBEAT_TIMEOUT_MS, int(_remaining(deadline) * 1000)))
    result = await _within_deadline(
        page.evaluate(
            """
            async ({path, headers, body, timeoutMs}) => {
              const controller = new AbortController();
              const timeoutHandle = setTimeout(() => controller.abort(), timeoutMs);
              try {
                const response = await fetch(path, {
                  method: 'POST',
                  headers,
                  body: JSON.stringify(body),
                  credentials: 'include',
                  signal: controller.signal
                });
                let json = null;
                try {
                  json = await response.json();
                } catch (_) {
                  json = null;
                }
                return {status: response.status, json};
              } catch (_) {
                return {status: 0, json: null};
              } finally {
                clearTimeout(timeoutHandle);
              }
            }
            """,
            {
                "path": HEARTBEAT_PATH,
                "headers": _request_headers(token),
                "body": {
                    "channelIdList": [],
                    "basePlatform": "",
                    "creator": "",
                    "pageNum": 1,
                    "pageSize": 1,
                    "startTime": None,
                    "endTime": None,
                },
                "timeoutMs": timeout_ms,
            },
        ),
        deadline,
    )
    header = (result.get("json") or {}).get("header") or {}
    return result.get("status") == 200 and str(header.get("status")) == "200"


async def _close_quietly(awaitable: Any) -> None:
    try:
        await asyncio.wait_for(awaitable, timeout=CLEANUP_TIMEOUT_SECONDS)
    except BaseException:
        pass


async def _probe_real_login_async(cdp_url: str, deadline: float) -> bool:
    playwright = browser = None
    try:
        playwright = await _within_deadline(_async_playwright_factory().start(), deadline)
        browser = await _within_deadline(
            playwright.chromium.connect_over_cdp(
                cdp_url,
                timeout=max(1, min(CDP_CONNECT_TIMEOUT_MS, int(_remaining(deadline) * 1000))),
            ),
            deadline,
        )
        for context in browser.contexts:
            for page in context.pages:
                if LOGIN_HOST not in (page.url or "") or "/login" in (page.url or ""):
                    continue
                token = await _within_deadline(
                    page.evaluate("() => window.sessionStorage.getItem('token')"),
                    deadline,
                )
                if token and await _heartbeat(page, token, deadline):
                    return True
        return False
    except Exception:
        return False
    finally:
        if browser is not None:
            await _close_quietly(browser.close())
        if playwright is not None:
            await _close_quietly(playwright.stop())


def probe_real_login(cdp_url: str = CDP_URL) -> bool:
    """Check an existing 139 session within a strict end-to-end time budget."""
    deadline = time.monotonic() + PROBE_TIMEOUT_SECONDS
    operation_deadline = deadline - (2 * CLEANUP_TIMEOUT_SECONDS)
    try:
        response = requests.get(
            f"{cdp_url}/json/list",
            timeout=max(0.05, min(1.0, _remaining(operation_deadline))),
        )
        if response.status_code != 200:
            return False
        pages = response.json()
        if not any(LOGIN_HOST in str(page.get("url") or "") for page in pages):
            return False
        return asyncio.run(_probe_real_login_async(cdp_url, operation_deadline))
    except Exception:
        return False
