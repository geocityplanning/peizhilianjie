from __future__ import annotations

import asyncio
import time

from app.http_executor import login_probe


class FakePage:
    def __init__(self, result):
        self.result = result
        self.script = ""
        self.payload = None

    async def evaluate(self, script, payload):
        self.script = script
        self.payload = payload
        return self.result


def test_heartbeat_has_bounded_abort_timeout():
    page = FakePage({"status": 0, "json": None})

    assert asyncio.run(login_probe._heartbeat(page, "token-for-test", time.monotonic() + 30)) is False
    assert "AbortController" in page.script
    assert "controller.abort()" in page.script
    assert "clearTimeout(timeoutHandle)" in page.script
    assert "signal: controller.signal" in page.script
    assert page.payload["timeoutMs"] == login_probe.HEARTBEAT_TIMEOUT_MS
    assert login_probe.HEARTBEAT_TIMEOUT_MS == 8_000


def test_heartbeat_accepts_only_successful_backend_status():
    page = FakePage({"status": 200, "json": {"header": {"status": "200"}}})
    assert asyncio.run(login_probe._heartbeat(page, "token-for-test", time.monotonic() + 2)) is True

    expired_page = FakePage({"status": 200, "json": {"header": {"status": "403"}}})
    assert asyncio.run(login_probe._heartbeat(expired_page, "token-for-test", time.monotonic() + 2)) is False


def test_probe_returns_within_budget_when_cdp_connection_hangs(monkeypatch):
    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return [{"url": "https://uat-cloud.139.com/cloudappadmin/#/welcome"}]

    async def hanging_connect(*args, **kwargs):
        await asyncio.sleep(30)

    class FakeChromium:
        connect_over_cdp = hanging_connect

    class FakePlaywright:
        chromium = FakeChromium()

        async def stop(self):
            return None

    class FakePlaywrightContext:
        async def start(self):
            return FakePlaywright()

    monkeypatch.setattr(login_probe.requests, "get", lambda *args, **kwargs: FakeResponse())
    monkeypatch.setattr(login_probe, "_async_playwright_factory", lambda: FakePlaywrightContext())
    monkeypatch.setattr(login_probe, "PROBE_TIMEOUT_SECONDS", 0.15)

    started = time.monotonic()
    assert login_probe.probe_real_login() is False
    assert time.monotonic() - started < 1.0


def test_probe_returns_within_budget_when_token_evaluate_hangs(monkeypatch):
    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return [{"url": "https://uat-cloud.139.com/cloudappadmin/#/welcome"}]

    class FakePage:
        url = "https://uat-cloud.139.com/cloudappadmin/#/welcome"

        async def evaluate(self, *args, **kwargs):
            await asyncio.sleep(30)

    class FakeContext:
        pages = [FakePage()]

    class FakeBrowser:
        contexts = [FakeContext()]

        async def close(self):
            return None

    class FakeChromium:
        async def connect_over_cdp(self, *args, **kwargs):
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

        async def stop(self):
            return None

    class FakePlaywrightContext:
        async def start(self):
            return FakePlaywright()

    monkeypatch.setattr(login_probe.requests, "get", lambda *args, **kwargs: FakeResponse())
    monkeypatch.setattr(login_probe, "_async_playwright_factory", lambda: FakePlaywrightContext())
    monkeypatch.setattr(login_probe, "PROBE_TIMEOUT_SECONDS", 0.15)

    started = time.monotonic()
    assert login_probe.probe_real_login() is False
    assert time.monotonic() - started < 1.0


def test_probe_returns_true_for_valid_session(monkeypatch):
    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return [{"url": "https://uat-cloud.139.com/cloudappadmin/#/welcome"}]

    class FakePage:
        url = "https://uat-cloud.139.com/cloudappadmin/#/welcome"

        async def evaluate(self, script, payload=None):
            if payload is None:
                return "valid-token"
            return {"status": 200, "json": {"header": {"status": "200"}}}

    class FakeContext:
        pages = [FakePage()]

    class FakeBrowser:
        contexts = [FakeContext()]

        async def close(self):
            return None

    class FakeChromium:
        async def connect_over_cdp(self, *args, **kwargs):
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

        async def stop(self):
            return None

    class FakePlaywrightContext:
        async def start(self):
            return FakePlaywright()

    monkeypatch.setattr(login_probe.requests, "get", lambda *args, **kwargs: FakeResponse())
    monkeypatch.setattr(login_probe, "_async_playwright_factory", lambda: FakePlaywrightContext())

    assert login_probe.probe_real_login() is True
