from __future__ import annotations

import hashlib
import random
import string
from datetime import datetime
from typing import Any

import requests


CDP_URL = "http://127.0.0.1:9222"
LOGIN_HOST = "uat-cloud.139.com"
HEARTBEAT_PATH = "/backend/cloudTrial/channel/getList"


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


HEARTBEAT_TIMEOUT_MS = 10_000


def _heartbeat(page: Any, token: str) -> bool:
    result = page.evaluate(
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
            "timeoutMs": HEARTBEAT_TIMEOUT_MS,
        },
    )
    header = (result.get("json") or {}).get("header") or {}
    return result.get("status") == 200 and str(header.get("status")) == "200"

def probe_real_login(cdp_url: str = CDP_URL) -> bool:
    """Check an existing 139 browser session without launching or creating a page."""
    try:
        response = requests.get(f"{cdp_url}/json/list", timeout=1)
        if response.status_code != 200:
            return False
        pages = response.json()
        if not any(LOGIN_HOST in str(page.get("url") or "") for page in pages):
            return False
    except Exception:
        return False

    playwright = browser = None
    try:
        from playwright.sync_api import sync_playwright

        playwright = sync_playwright().start()
        browser = playwright.chromium.connect_over_cdp(cdp_url)
        for context in browser.contexts:
            for page in context.pages:
                if LOGIN_HOST not in (page.url or "") or "/login" in (page.url or ""):
                    continue
                token = page.evaluate("() => window.sessionStorage.getItem('token')")
                if token and _heartbeat(page, token):
                    return True
        return False
    except Exception:
        return False
    finally:
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass