# -*- coding: utf-8 -*-
"""S4 综合只读验收入口。

同一次运行完成：
  1. 打开既有参考应用的复制对话框，调用真实 `_select_and_verify_create_channel()`，然后关闭；
  2. 列表渠道筛选与指定应用 ID 只读定位。

不调用保存、发布、删除、分组、取链、HTTP create/query 或原单恢复。
不输出渠道值、应用名、参考链接、URL、请求/响应正文、cookie、token。
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import threading
import time
import types
from pathlib import Path

EXECUTOR_DIR = Path(__file__).resolve().parents[1] / "app" / "executor"
sys.path.insert(0, str(EXECUTOR_DIR))

PROBE_HELPERS = Path(__file__).with_name("probe_channel_filter.py")
APP_LIST_URL = "https://uat-cloud.139.com/cloudappadmin/#/cloudAppManager"
_READ_MARKERS = ("getAppInfoList", "getList", "getBasePlatform")
_WRITE_MARKERS = (
    "save",
    "createApp",
    "createChannel",
    "delete",
    "publish",
    "enable",
    "updateApp",
    "setGroup",
    "online",
)
_ALLOWED_TOP_KEYS = {
    "mode",
    "ok",
    "error",
    "wrote_any_uat_data",
    "write_request_observed",
    "save_click_count",
    "copy_dialog_opened",
    "exact_channel_unique",
    "selected_channel_matches",
    "copy_dialog_closed",
    "steps",
    "diagnosis",
    "notes",
}
DEFAULT_BUDGET_SECONDS = 20


class ProbeTimeout(Exception):
    """The combined probe exceeded its explicit total budget."""


class OwnerDeadlinePage:
    """Proxy that keeps sync Playwright calls on the owner thread and honors a deadline."""

    def __init__(self, page, owner_ident, deadline=None):
        object.__setattr__(self, "_inner", page)
        object.__setattr__(self, "_owner_ident", owner_ident)
        object.__setattr__(self, "_deadline", deadline)
        object.__setattr__(self, "thread_calls", [])

    def _check(self):
        ident = threading.get_ident()
        self.thread_calls.append(ident)
        if ident != self._owner_ident:
            raise RuntimeError("Cannot switch to a different thread")
        if self._deadline is not None and time.monotonic() >= self._deadline:
            raise ProbeTimeout()

    def clear_deadline(self):
        object.__setattr__(self, "_deadline", None)

    def wait_for_timeout(self, milliseconds):
        self._check()
        wait_ms = milliseconds
        if self._deadline is not None:
            remaining_ms = (self._deadline - time.monotonic()) * 1000
            if remaining_ms <= 0 or milliseconds > remaining_ms:
                raise ProbeTimeout()
            wait_ms = milliseconds
        self._inner.wait_for_timeout(wait_ms)
        self._check()

    def __getattr__(self, name):
        self._check()
        attr = getattr(self._inner, name)
        if callable(attr):
            def bound(*args, **kwargs):
                self._check()
                return attr(*args, **kwargs)
            return bound
        return attr


def _load_helpers():
    spec = importlib.util.spec_from_file_location("probe_channel_filter", PROBE_HELPERS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _install_login_stub() -> None:
    stub = types.ModuleType("actions.ensure_login")

    def ensure_login(page=None):  # noqa: ARG001
        return {
            "success": False,
            "message": "只读探针不执行登录",
            "already_logged_in": False,
        }

    stub.ensure_login = ensure_login
    stub.is_logged_in = lambda page: True  # noqa: ARG005
    sys.modules["actions.ensure_login"] = stub


def _is_write_request(method, url) -> bool:
    raw = url or ""
    verb = (method or "GET").upper()
    if any(marker in raw for marker in _READ_MARKERS):
        return False
    if verb in {"PUT", "DELETE", "PATCH"}:
        return True
    lowered = raw.lower()
    return verb == "POST" and any(marker.lower() in lowered for marker in _WRITE_MARKERS)


def _attach_write_guard(page, cap, guard: dict) -> None:
    original_save = cap._click_save_button

    def blocked_save(page_inner):  # noqa: ARG001
        guard["save_click_count"] = int(guard.get("save_click_count") or 0) + 1
        return False

    cap._click_save_button = blocked_save
    guard["_restore_save"] = lambda: setattr(cap, "_click_save_button", original_save)

    def _on_request(request):
        try:
            method = getattr(request, "method", "")
            url = getattr(request, "url", "")
            if callable(method):
                method = method()
            if callable(url):
                url = url()
            if _is_write_request(method, url):
                guard["write_request_observed"] = True
        except Exception:
            return

    try:
        page.on("request", _on_request)
    except Exception:
        pass


COPY_DIALOG_CLOSE_POLL_ATTEMPTS = 12
COPY_DIALOG_CLOSE_POLL_INTERVAL_MS = 100

_COPY_DIALOG_VISIBLE_JS = """() => {
  /* COPY_DIALOG_VISIBLE */
  const visible = (el) => {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    if (el.getAttribute('aria-hidden') === 'true') return false;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;
    return true;
  };
  const wrappers = document.querySelectorAll('.el-dialog__wrapper');
  for (const w of wrappers) {
    if (!visible(w)) continue;
    if (w.querySelectorAll('.el-tabs__item').length > 0) return true;
  }
  return false;
}"""

_COPY_DIALOG_CLICK_JS = """() => {
  /* COPY_DIALOG_CLICK */
  const visible = (el) => {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    if (el.getAttribute('aria-hidden') === 'true') return false;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;
    return true;
  };
  const wrappers = document.querySelectorAll('.el-dialog__wrapper');
  for (const w of wrappers) {
    if (!visible(w)) continue;
    if (w.querySelectorAll('.el-tabs__item').length === 0) continue;
    const buttons = Array.from(w.querySelectorAll('button'));
    const cancel = buttons.find(button =>
      (button.innerText || '').replace(/\\s/g, '').trim() === '取消'
    );
    if (cancel) { cancel.click(); return true; }
    const closeBtn = w.querySelector('.el-dialog__headerbtn');
    if (closeBtn) { closeBtn.click(); return true; }
  }
  return false;
}"""


def _copy_dialog_open(page) -> bool:
    """True only when a copy dialog wrapper is actually visible.

    Uses computed style, aria-hidden and real geometry instead of the inline
    `style.display` alone, so a closing animation is not reported as open.
    """
    try:
        return bool(page.evaluate(_COPY_DIALOG_VISIBLE_JS))
    except Exception:
        return False


def _close_copy_dialog(page) -> bool:
    """Close the copy dialog once, then confirm it became invisible.

    Already closed is a success without any click. A visible dialog is clicked
    at most once; the confirmation then polls a short bounded budget without
    clicking again, and stays fail-closed when it never becomes invisible.
    """
    if not _copy_dialog_open(page):
        return True
    try:
        clicked = bool(page.evaluate(_COPY_DIALOG_CLICK_JS))
    except Exception:
        return False
    if not clicked:
        return not _copy_dialog_open(page)
    for _ in range(COPY_DIALOG_CLOSE_POLL_ATTEMPTS):
        page.wait_for_timeout(COPY_DIALOG_CLOSE_POLL_INTERVAL_MS)
        if not _copy_dialog_open(page):
            return True
    return False


def _blank_dialog_facts() -> dict:
    return {
        "copy_dialog_opened": False,
        "exact_channel_unique": False,
        "selected_channel_matches": False,
        "copy_dialog_closed": False,
        "save_click_count": 0,
        "write_request_observed": False,
        "wrote_any_uat_data": False,
    }


def _lookup_facts(located: dict) -> dict:
    return {
        "found": bool(located.get("found")),
        "reason": located.get("reason"),
        "channel_source": located.get("channel_source"),
        "id_field_source": located.get("id_field_source"),
        "header_cell_alignment_used": located.get("header_cell_alignment_used"),
        "main_channel_present": located.get("main_channel_present"),
        "detail_channel_present": located.get("detail_channel_present"),
        "main_channel_matches_expected": located.get("main_channel_matches_expected"),
        "detail_channel_matches_expected": located.get("detail_channel_matches_expected"),
        "main_detail_same": located.get("main_detail_same"),
        "mismatch_source": located.get("mismatch_source"),
    }


def _budget_expired(deadline) -> bool:
    return deadline is not None and time.monotonic() >= deadline


def _raise_if_timeout(deadline, report: dict) -> None:
    if _budget_expired(deadline):
        report["ok"] = False
        report["error"] = "probe_timeout"
        raise ProbeTimeout()


def _atomic_write_report(path, report: dict) -> str:
    helpers = _load_helpers()
    text = helpers._dump_report(_public_report(report))
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)
    return text


def _empty_report(*, error=None) -> dict:
    return {
        "mode": "REAL_READ_ONLY",
        **_blank_dialog_facts(),
        "ok": False,
        "steps": {},
        "notes": [],
        "diagnosis": {},
        "error": error,
    }


def _list_filter_verified(detail) -> bool:
    if not isinstance(detail, dict):
        return False
    return bool(
        detail.get("filter_stable")
        and detail.get("response_contains_target_channel") is True
        and detail.get("reader_sees_target_channel") is True
    )


def run_acceptance(
    page,
    cap,
    *,
    channel_name: str,
    app_id: str,
    ref_app_id: str,
    deadline=None,
) -> dict:
    helpers = _load_helpers()
    guard = {
        "write_request_observed": False,
        "save_click_count": 0,
        "_restore_save": lambda: None,
    }
    recorded = {"exact_count": None, "readback_matches": False, "readback_nonempty": False}
    report = {
        "mode": "REAL_READ_ONLY",
        **_blank_dialog_facts(),
        "ok": False,
        "steps": {},
        "notes": [],
        "diagnosis": {},
        "error": None,
    }
    _attach_write_guard(page, cap, guard)

    real_popover = cap._js_channel_popover
    real_read = cap._read_dialog_channel_value
    cleanup_ran = {"done": False}

    def _cleanup_session():
        if cleanup_ran["done"]:
            return
        cleanup_ran["done"] = True
        try:
            guard["_restore_save"]()
        except Exception:
            pass
        cap._js_channel_popover = real_popover
        cap._read_dialog_channel_value = real_read

    cap._probe_cleanup = _cleanup_session
    cap._probe_page = page

    def wrapped_popover(page_inner, label, name):
        result = real_popover(page_inner, label, name)
        recorded["exact_count"] = result.get("exact_count")
        return result

    def wrapped_read(page_inner, label):
        value = real_read(page_inner, label)
        text = (value or "").strip()
        recorded["readback_nonempty"] = bool(text)
        recorded["readback_matches"] = text == (channel_name or "").strip()
        return value

    cap._js_channel_popover = wrapped_popover
    cap._read_dialog_channel_value = wrapped_read

    try:
        _raise_if_timeout(deadline, report)
        rows = cap._collect_all_app_rows(page, reset_filters=True)
        if rows is None:
            report["error"] = "reference_app_pagination_unstable"
            return report
        if str(ref_app_id) not in {str(key) for key in rows}:
            report["error"] = "reference_app_not_unique"
            return report

        _raise_if_timeout(deadline, report)
        located_ref = cap._find_target_row_by_id(page, ref_app_id, "")
        if not located_ref or not located_ref.get("found"):
            report["error"] = "reference_app_not_unique"
            return report
        copied = page.evaluate("""(rowIdx) => {
          const primaryRows = document.querySelectorAll('.el-table__body-wrapper tbody tr');
          const sourceRows = primaryRows.length ? primaryRows : document.querySelectorAll('table tbody tr');
          const tableRows = Array.from(sourceRows).filter(row => !row.classList.contains('el-table__expanded-row'));
          if (rowIdx < 0 || rowIdx >= tableRows.length) return false;
          const buttons = tableRows[rowIdx].querySelectorAll('button, a, span');
          for (const button of buttons) {
            if (button.offsetParent !== null && (button.innerText || '').trim() === '复制') {
              button.click();
              return true;
            }
          }
          return false;
        }""", located_ref.get("row_idx"))
        if not copied:
            report["error"] = "copy_dialog_unconfirmed"
            return report
        page.wait_for_timeout(500)
        report["copy_dialog_opened"] = _copy_dialog_open(page)
        if not report["copy_dialog_opened"]:
            report["error"] = "copy_dialog_unconfirmed"
            return report

        if not cap._go_tab(page, "体验配置"):
            report["error"] = "copy_dialog_unconfirmed"
            return report

        _raise_if_timeout(deadline, report)
        selected = cap._select_and_verify_create_channel(page, channel_name)
        report["exact_channel_unique"] = recorded.get("exact_count") == 1
        report["selected_channel_matches"] = bool(
            selected.get("success") and recorded.get("readback_matches")
        )
        if guard["write_request_observed"] or guard["save_click_count"]:
            report["error"] = "write_observed_or_save_clicked"
            return report
        if not selected.get("success") or not report["selected_channel_matches"]:
            report["error"] = "channel_select_not_verified"
            return report

        _raise_if_timeout(deadline, report)
        detail = cap._search_list_by_channel(page, channel_name, return_detail=True)
        report["steps"]["channel_filter"] = helpers._redact_obj(
            detail if isinstance(detail, dict) else {"result": bool(detail)}
        )
        report["diagnosis"] = helpers._build_diagnosis(detail if isinstance(detail, dict) else {})
        report["notes"].extend(helpers._diagnosis_notes(detail if isinstance(detail, dict) else {}))
        located = cap._find_target_row_by_id(page, app_id, channel_name)
        report["steps"]["exact_app_id_lookup"] = _lookup_facts(located or {})
        if guard["write_request_observed"] or guard["save_click_count"]:
            report["error"] = "write_observed_or_save_clicked"
            return report
        if not _list_filter_verified(detail if isinstance(detail, dict) else {}):
            report["error"] = "list_filter_not_verified"
            return report
        if not located or not located.get("found"):
            reason = (located or {}).get("reason")
            report["error"] = (
                reason
                if reason in {
                    "list_not_restored_after_reset",
                    "pagination_unstable",
                    "page_scan_limit_reached",
                }
                else "target_app_not_located"
            )
            return report
        report["ok"] = True
    except ProbeTimeout:
        report["ok"] = False
        report["error"] = "probe_timeout"
    except Exception as exc:
        report["ok"] = False
        report["error"] = f"{type(exc).__name__}"
    finally:
        report["save_click_count"] = int(guard.get("save_click_count") or 0)
        report["write_request_observed"] = bool(guard.get("write_request_observed"))
        report["wrote_any_uat_data"] = False
        clearer = getattr(page, "clear_deadline", None)
        if callable(clearer):
            clearer()
        try:
            report["copy_dialog_closed"] = _close_copy_dialog(page)
        except Exception:
            report["copy_dialog_closed"] = False
        if report["copy_dialog_opened"] and not report["copy_dialog_closed"]:
            report["ok"] = False
            if not report.get("error"):
                report["error"] = "copy_dialog_close_failed"
        if report["write_request_observed"] or report["save_click_count"]:
            report["ok"] = False
            if not report.get("error"):
                report["error"] = "write_observed_or_save_clicked"
        _cleanup_session()
    return report


def _public_report(report: dict) -> dict:
    helpers = _load_helpers()
    redacted = helpers._redact_obj(report)
    return {key: redacted.get(key) for key in _ALLOWED_TOP_KEYS if key in redacted}


def _emit_report(report_file, report: dict) -> str:
    text = _atomic_write_report(report_file, report)
    print(text, flush=True)
    return text


def run_acceptance_with_budget(
    page,
    cap,
    *,
    channel_name: str,
    app_id: str,
    ref_app_id: str,
    budget_seconds: float = DEFAULT_BUDGET_SECONDS,
    report_file,
) -> dict:
    owner_ident = threading.get_ident()
    deadline = time.monotonic() + max(0.01, float(budget_seconds))
    wrapped = OwnerDeadlinePage(page, owner_ident, deadline)
    report = run_acceptance(
        wrapped,
        cap,
        channel_name=channel_name,
        app_id=app_id,
        ref_app_id=ref_app_id,
        deadline=deadline,
    )
    try:
        _emit_report(report_file, report)
    except Exception:
        report["ok"] = False
        if report.get("error") != "probe_timeout":
            report["error"] = "report_write_failed"
        try:
            report["copy_dialog_closed"] = _close_copy_dialog(wrapped)
        except Exception:
            pass
        cleanup = getattr(cap, "_probe_cleanup", None)
        if callable(cleanup):
            try:
                cleanup()
            except Exception:
                pass
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="S4 综合只读验收")
    parser.add_argument("--channel", required=True, help="本机渠道名，不会写入报告")
    parser.add_argument("--app-id", required=True, help="列表核验应用 ID，不会写入报告")
    parser.add_argument("--ref-app-id", required=True, help="既有参考应用 ID，不会写入报告")
    parser.add_argument("--report-file", required=True, help="本机脱敏最终报告路径")
    parser.add_argument(
        "--budget-seconds",
        type=float,
        default=DEFAULT_BUDGET_SECONDS,
        help=f"完整入口总预算秒数，默认 {DEFAULT_BUDGET_SECONDS}",
    )
    parser.add_argument("--url", default=APP_LIST_URL, help="应用列表页地址")
    args = parser.parse_args()

    helpers = _load_helpers()
    _install_login_stub()
    from core import get_browser_page  # noqa: PLC0415
    from actions import create_app_v2 as cap  # noqa: PLC0415

    report = _empty_report(error="browser_unavailable")
    pw = None
    page = None
    try:
        pw, _browser, page = get_browser_page()
        helpers._navigate_to_list(page, args.url, {"notes": [], "url_before_navigate": None, "current_url": None})
        report = run_acceptance_with_budget(
            page,
            cap,
            channel_name=args.channel,
            app_id=args.app_id,
            ref_app_id=args.ref_app_id,
            budget_seconds=args.budget_seconds,
            report_file=args.report_file,
        )
    except Exception as exc:
        report["ok"] = False
        report["error"] = f"{type(exc).__name__}"
        if page is not None:
            try:
                report["copy_dialog_closed"] = _close_copy_dialog(page)
            except Exception:
                report["copy_dialog_closed"] = False
        cleanup = getattr(cap, "_probe_cleanup", None)
        if callable(cleanup):
            try:
                cleanup()
            except Exception:
                pass
        try:
            _emit_report(args.report_file, report)
        except Exception:
            report["error"] = "report_write_failed"
    finally:
        if pw is not None:
            try:
                pw.stop()
            except Exception:
                pass
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
