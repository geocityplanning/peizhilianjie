# -*- coding: utf-8 -*-
"""Core utilities: CDP browser connection, auth token, request signing, API calls."""
import hashlib
import json
import random
import string
import sys
from datetime import datetime

from playwright.sync_api import sync_playwright

BASE_URL = "https://plus.buy.139.com"
ADMIN_URL = f"{BASE_URL}/cloudappadmin/#/cloudAppChannelManager"
CDP_URL = "http://127.0.0.1:9222"


def get_browser_page(cdp_url=CDP_URL):
    """Attach to the running browser via CDP and return (browser, page).
    If no 139 page is open, create one automatically."""
    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(cdp_url)
    page = None
    for ctx in browser.contexts:
        for pg in ctx.pages:
            if "plus.buy.139.com" in pg.url:
                page = pg
                break
        if page:
            break
    if page is None:
        # 没有139页面，自动开一个
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        page.goto(ADMIN_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
    return pw, browser, page


def get_token(page):
    token = page.evaluate("() => window.sessionStorage.getItem('token')")
    if not token:
        raise RuntimeError("NO_TOKEN: please log in first")
    return token


def make_headers(token: str) -> dict:
    now = datetime.now()
    rid = now.strftime("%Y%m%d%H%M%S") + str(int(now.timestamp() * 1000)) + "".join(
        random.choices(string.ascii_letters + string.digits, k=8)
    )
    sign = hashlib.md5((rid + token + "backend").encode()).hexdigest()
    return {
        "requestId": rid,
        "PMS-TOKEN": token,
        "sign": sign,
        "Content-Type": "application/json;charset=UTF-8",
        "Accept": "application/json, text/plain, */*",
    }


def api_post(page, path: str, body: dict):
    """POST to an internal API from inside the page context (proxy/VPN handled by browser)."""
    token = get_token(page)
    headers = make_headers(token)
    js = """
    async ({url, headers, body}) => {
      const resp = await fetch(url, {
        method: 'POST',
        headers: headers,
        body: JSON.stringify(body),
        credentials: 'include'
      });
      return { status: resp.status, json: await resp.json() };
    }
    """
    return page.evaluate(js, {"url": path, "headers": headers, "body": body})


def get_all_channels(page):
    """Fetch the full channel list."""
    first = api_post(page, "/backend/cloudTrial/channel/getList", {
        "channelIdList": [], "basePlatform": "", "creator": "",
        "pageNum": 1, "pageSize": 100, "startTime": None, "endTime": None,
    })
    if first["status"] != 200 or (first["json"].get("header") or {}).get("status") != "200":
        raise RuntimeError(f"getList failed: {first}")
    data = first["json"].get("data") or {}
    total = int(data.get("totalCount", 0))
    items = data.get("list") or []
    page_count = int(data.get("pageCount", 1))
    for pn in range(2, page_count + 1):
        r = api_post(page, "/backend/cloudTrial/channel/getList", {
            "channelIdList": [], "basePlatform": "", "creator": "",
            "pageNum": pn, "pageSize": 100, "startTime": None, "endTime": None,
        })
        items.extend(((r.get("json") or {}).get("data") or {}).get("list") or [])
    seen, channels = set(), []
    for it in items:
        if it.get("id") not in seen:
            seen.add(it.get("id"))
            channels.append(it)
    return channels


def get_base_platforms(page):
    """Return list of {'base': id, 'name': name}."""
    r = api_post(page, "/backend/cloudTrial/appInfo/getBasePlatformList", {})
    if r["status"] != 200:
        raise RuntimeError(f"getBasePlatformList failed: {r}")
    return (r["json"].get("data") or {}).get("platformList") or []


def make_unique_channel_name(existing_names, desired):
    """Return a unique channel name by appending 1,2,3... if needed."""
    if desired not in existing_names:
        return desired
    i = 1
    while f"{desired}{i}" in existing_names:
        i += 1
    return f"{desired}{i}"
