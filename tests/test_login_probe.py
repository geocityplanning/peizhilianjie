from __future__ import annotations

from app.http_executor import login_probe


class FakePage:
    def __init__(self, result):
        self.result = result
        self.script = ""
        self.payload = None

    def evaluate(self, script, payload):
        self.script = script
        self.payload = payload
        return self.result


def test_heartbeat_has_bounded_abort_timeout():
    page = FakePage({"status": 0, "json": None})

    assert login_probe._heartbeat(page, "token-for-test") is False
    assert "AbortController" in page.script
    assert "controller.abort()" in page.script
    assert "clearTimeout(timeoutHandle)" in page.script
    assert "signal: controller.signal" in page.script
    assert page.payload["timeoutMs"] == login_probe.HEARTBEAT_TIMEOUT_MS
    assert login_probe.HEARTBEAT_TIMEOUT_MS == 10_000


def test_heartbeat_accepts_only_successful_backend_status():
    page = FakePage({"status": 200, "json": {"header": {"status": "200"}}})
    assert login_probe._heartbeat(page, "token-for-test") is True

    expired_page = FakePage({"status": 200, "json": {"header": {"status": "403"}}})
    assert login_probe._heartbeat(expired_page, "token-for-test") is False