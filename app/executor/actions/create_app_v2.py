# -*- coding: utf-8 -*-
"""
创建应用 — 契约版（v2，JS 直操 DOM，绕过 Playwright :visible 伪类）。
"""
import base64
import hashlib
import json
import os
import re
import stat
import sys
import time
from pathlib import Path
from urllib.parse import parse_qsl, quote, quote_plus, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import get_browser_page
from core.error_capture import capture_page_errors, build_error_message
from core.error_codes import (
    ERR_RESOURCE_FALLBACK_VERIFY_FAILED,
    NEXT_MANUAL,
    NEXT_QUERY,
    NEXT_STOP,
    err,
)
from core import executor as ex
from actions.ensure_login import ensure_login
from actions.link_utils import extract_cloud_app_key
from actions.pagination import wait_for_page_change, wait_for_table_update

OPERATION = "CREATE_APP"
BASE_URL_H5 = "https://uat-cloud.139.com/cloudappadmin/#/cloudAppManager"
DEFAULT_BASE = "华为底座2.0"
STEP_TIMEOUT = 5000

# 参考应用默认配置：application_type 未传 ref_cloud_app_link 时使用
DEFAULT_REF_SOURCES = {
    "云盘": {
        "app_id": "11926",
        "cloud_app_link": "https://plus.buy.139.com/mccloudgame/#/?i=KWcMvfaFlhw=",
    },
    "掌厅": {
        "app_id": "11935",
        "cloud_app_link": "https://plus.buy.139.com/mccloudgame/#/?i=zZVurLOuLsI=",
    },
}

DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUT = DIR / "output"
OUT.mkdir(exist_ok=True)


def _shot(page, name):
    try:
        page.screenshot(path=str(OUT / f"appv3_{name}.png"), timeout=8000)
    except Exception:
        pass


def _cleanup_overlays(page):
    for _ in range(3):
        try:
            page.keyboard.press("Escape"); page.wait_for_timeout(200)
        except Exception:
            pass
        try:
            mb = page.locator(".el-message-box__wrapper")
            for i in range(mb.count()):
                w = mb.nth(i).get_attribute("style") or ""
                if "none" not in w:
                    mb.nth(i).get_by_text("取消", exact=True).first.click(timeout=2000)
                    page.wait_for_timeout(200)
        except Exception:
            pass
        try:
            for i in range(page.locator(".el-dialog__headerbtn").count()):
                page.locator(".el-dialog__headerbtn").nth(i).click(timeout=2000)
                page.wait_for_timeout(200)
        except Exception:
            pass


# ====== JS 直操 DOM 工具函数（不依赖 Playwright :visible） ======

def _available_tabs(page):
    """Read tabs only from the one genuinely visible tabbed dialog."""
    try:
        return page.evaluate("""
        () => {
          const visible = el => {
            if (!el) return false;
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden' || el.getAttribute('aria-hidden') === 'true') return false;
            const rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
          };
          const dialogs = Array.from(document.querySelectorAll('.el-dialog__wrapper')).filter(
            dialog => visible(dialog) && dialog.querySelectorAll('.el-tabs__item').length > 0
          );
          if (dialogs.length !== 1) return [];
          return Array.from(dialogs[0].querySelectorAll('.el-tabs__item')).map(tab => (tab.innerText || '').trim());
        }
        """) or []
    except Exception:
        return []


def _go_tab(page, name):
    """Activate and prove the target tab in the one genuinely visible dialog."""
    discovery_deadline = time.monotonic() + 6
    last_tabs = []
    while time.monotonic() < discovery_deadline:
        last_tabs = _available_tabs(page)
        if last_tabs:
            break
        page.wait_for_timeout(300)
    if not last_tabs:
        print(f"[create_app] WARN: 未找到可见Tab {name}")
        return False
    try:
        wrappers = page.locator(".el-dialog__wrapper")
        visible_dialogs = []
        for index in range(wrappers.count()):
            dialog = wrappers.nth(index)
            if dialog.is_visible() and dialog.locator(".el-tabs__item").count() > 0:
                visible_dialogs.append(dialog)
        if len(visible_dialogs) != 1:
            print(f"[create_app] WARN: 可见Tab dialog 数量不唯一: {len(visible_dialogs)}")
            return False
        tabs = visible_dialogs[0].locator(".el-tabs__item")
        target_indexes = [
            index for index in range(tabs.count())
            if name in (tabs.nth(index).inner_text() or "").strip()
        ]
        if len(target_indexes) != 1:
            print(f"[create_app] WARN: 可见Tab '{name}' 不唯一或不存在")
            return False
        # Element-UI does not reliably react to HTMLElement.click() in this dialog;
        # use a real pointer click, then prove both tab and its mapped pane changed.
        tabs.nth(target_indexes[0]).click(timeout=STEP_TIMEOUT)
    except Exception as exc:
        print(
            f"[create_app] WARN: 可见Tab '{name}' 无法物理点击 "
            f"reason=physical_click_exception exception_type={type(exc).__name__}"
        )
        return False
    # Physical click may consume most of the discovery budget.  Activation must
    # always receive its own bounded observation window, rather than silently
    # performing zero post-click checks against the earlier deadline.
    activation_deadline = time.monotonic() + 6
    while time.monotonic() < activation_deadline:
        active = page.evaluate("""
        (tabName) => {
          const visible = el => {
            if (!el) return false;
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden' || el.getAttribute('aria-hidden') === 'true') return false;
            const rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
          };
          const dialogs = Array.from(document.querySelectorAll('.el-dialog__wrapper')).filter(
            dialog => visible(dialog) && dialog.querySelectorAll('.el-tabs__item').length > 0
          );
          if (dialogs.length !== 1) return false;
          const tab = Array.from(dialogs[0].querySelectorAll('.el-tabs__item')).find(
            item => (item.innerText || '').trim().includes(tabName)
          );
          if (!tab || !tab.classList.contains('is-active') || tab.getAttribute('aria-selected') !== 'true') return false;
          const paneId = tab.getAttribute('aria-controls');
          const pane = paneId ? document.getElementById(paneId) : null;
          if (!pane || pane.getAttribute('aria-hidden') === 'true') return false;
          const style = window.getComputedStyle(pane);
          return style.display !== 'none' && style.visibility !== 'hidden';
        }
        """, name)
        if active:
            return True
        page.wait_for_timeout(150)
    print(f"[create_app] WARN: Tab '{name}' 未激活 reason=activation_evidence_timeout")
    return False


def _js_fill(page, label, value):
    """用 JS 找 label 对应的 input 并填值。"""
    return page.evaluate("""
    ({label, value}) => {
      const wrappers = document.querySelectorAll('.el-dialog__wrapper');
      for (const w of wrappers) {
        if (w.style.display === 'none') continue;
        const items = w.querySelectorAll('.el-form-item');
        for (const it of items) {
          if (it.offsetParent === null) continue;
          const lblEl = it.querySelector('.el-form-item__label');
          if (!lblEl) continue;
          const lbl = lblEl.innerText.replace(/[ *:]/g, '').trim();
          if (lbl === label) {
            const input = it.querySelector('input.el-input__inner');
            if (input) {
              const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
              setter.call(input, value);
              input.dispatchEvent(new Event('input', {bubbles: true}));
              input.dispatchEvent(new Event('change', {bubbles: true}));
              return true;
            }
          }
        }
      }
      return false;
    }
    """, {"label": label, "value": value})


_RESOURCE_FALLBACK_INPUT_MARKER = "data-hermes-resource-fallback-input"
_POST_SAVE_FALLBACK_POLL_ATTEMPTS = 6
_POST_SAVE_FALLBACK_POLL_MS = 250
_POST_SAVE_KNOWN_ID_POLL_ATTEMPTS = 3
_POST_SAVE_KNOWN_ID_POLL_MS = 500
_POST_SAVE_KNOWN_ID_VISIBILITY_WAIT_BUDGET_MS = 15_000


def _mark_unique_visible_copy_dialog_input(page, label):
    """Mark one exact input in the only visible copy dialog for native fill()."""
    try:
        result = page.evaluate(
            """
            ({label, marker}) => {
              const visible = (el) => {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                if (el.getAttribute('aria-hidden') === 'true') return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
              };
              for (const input of document.querySelectorAll('input[' + marker + ']')) {
                input.removeAttribute(marker);
              }
              const wanted = (label || '').replace(/[ *:：]/g, '').trim();
              const dialogs = Array.from(document.querySelectorAll('.el-dialog__wrapper')).filter(
                dialog => visible(dialog) && dialog.querySelectorAll('.el-tabs__item').length > 0
              );
              if (dialogs.length !== 1) return {dialog_found: false, input_found: false};
              const matches = [];
              for (const item of dialogs[0].querySelectorAll('.el-form-item')) {
                if (item.offsetParent === null) continue;
                const labelEl = item.querySelector('.el-form-item__label');
                if (!labelEl) continue;
                const actual = (labelEl.innerText || '').replace(/[ *:：]/g, '').trim();
                if (actual !== wanted) continue;
                const input = item.querySelector('input.el-input__inner');
                if (input) matches.push(input);
              }
              if (matches.length !== 1) return {dialog_found: true, input_found: false};
              matches[0].setAttribute(marker, 'true');
              return {dialog_found: true, input_found: true};
            }
            """,
            {"label": label, "marker": _RESOURCE_FALLBACK_INPUT_MARKER},
        )
    except Exception:
        result = None
    return {
        "dialog_found": bool(isinstance(result, dict) and result.get("dialog_found")),
        "input_found": bool(isinstance(result, dict) and result.get("input_found")),
    }


def _clear_resource_fallback_input_marker(page):
    try:
        page.evaluate(
            "(marker) => document.querySelectorAll('input[' + marker + ']').forEach(input => input.removeAttribute(marker))",
            _RESOURCE_FALLBACK_INPUT_MARKER,
        )
    except Exception:
        pass


def _fill_visible_copy_dialog_input(page, label, value):
    """Use native Playwright fill and Tab on one exact input, never a JS setter."""
    located = _mark_unique_visible_copy_dialog_input(page, label)
    if not located["dialog_found"] or not located["input_found"]:
        return False
    try:
        input_locator = page.locator(f"input[{_RESOURCE_FALLBACK_INPUT_MARKER}='true']")
        if input_locator.count() != 1:
            return False
        input_locator.fill(value, timeout=STEP_TIMEOUT)
        input_locator.press("Tab", timeout=STEP_TIMEOUT)
        return True
    except Exception:
        return False
    finally:
        _clear_resource_fallback_input_marker(page)


def _read_visible_copy_dialog_input(page, label):
    """Read exact DOM and Element-UI prop values from one visible copy dialog.

    Raw values stay in memory for equality tests only. A readable component
    `value` prop is the minimum framework-binding proof required before save.
    """
    try:
        result = page.evaluate(
            """
            ({label}) => {
              const visible = (el) => {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                if (el.getAttribute('aria-hidden') === 'true') return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
              };
              const wanted = (label || '').replace(/[ *:：]/g, '').trim();
              const dialogs = Array.from(document.querySelectorAll('.el-dialog__wrapper')).filter(
                dialog => visible(dialog) && dialog.querySelectorAll('.el-tabs__item').length > 0
              );
              if (dialogs.length !== 1) {
                return {dialog_found: false, input_found: false, model_found: false, value: '', model_value: ''};
              }
              for (const item of dialogs[0].querySelectorAll('.el-form-item')) {
                if (item.offsetParent === null) continue;
                const labelEl = item.querySelector('.el-form-item__label');
                if (!labelEl) continue;
                const actual = (labelEl.innerText || '').replace(/[ *:：]/g, '').trim();
                if (actual !== wanted) continue;
                const input = item.querySelector('input.el-input__inner');
                if (!input) return {dialog_found: true, input_found: false, model_found: false, value: '', model_value: ''};
                const component = input.__vue__ || input.closest('.el-input')?.__vue__;
                const propValue = component?.$props?.value;
                const directValue = component?.value;
                const modelValue = typeof propValue === 'string' ? propValue : (
                  typeof directValue === 'string' ? directValue : null
                );
                return {
                  dialog_found: true,
                  input_found: true,
                  model_found: typeof modelValue === 'string',
                  value: input.value || '',
                  model_value: modelValue || '',
                };
              }
              return {dialog_found: true, input_found: false, model_found: false, value: '', model_value: ''};
            }
            """,
            {"label": label},
        )
    except Exception:
        result = None
    if not isinstance(result, dict):
        return {"dialog_found": False, "input_found": False, "model_found": False, "value": "", "model_value": ""}
    return {
        "dialog_found": bool(result.get("dialog_found")),
        "input_found": bool(result.get("input_found")),
        "model_found": bool(result.get("model_found")),
        "value": str(result.get("value") or ""),
        "model_value": str(result.get("model_value") or ""),
    }


def _resource_fallback_readback_error(stage, message):
    return err(ERR_RESOURCE_FALLBACK_VERIFY_FAILED, stage, message, NEXT_MANUAL)


def _verify_resource_fallback_readback(page, expected, stage, context):
    """Verify DOM and framework prop values without leaking the fallback URL."""
    readback = _read_visible_copy_dialog_input(page, "资源不足中间页链接")
    expected_text = (expected or "").strip()
    observed = readback["value"]
    model_observed = readback["model_value"]
    dom_matches = bool(readback["input_found"]) and observed == expected_text
    model_matches = bool(readback["model_found"]) and model_observed == expected_text
    print(
        "[create_app] 资源不足中间页链接核验: "
        f"context={context} dialog_found={readback['dialog_found']} "
        f"input_found={readback['input_found']} model_found={readback['model_found']} "
        f"expected_length={len(expected_text)} observed_length={len(observed)} "
        f"model_length={len(model_observed)} dom_matches={dom_matches} model_matches={model_matches}"
    )
    if not readback["dialog_found"] or not readback["input_found"]:
        return {"success": False, "error": _resource_fallback_readback_error(stage, "资源不足中间页链接控件不可读取，已停止")}
    if not readback["model_found"]:
        return {"success": False, "error": _resource_fallback_readback_error(stage, "资源不足中间页链接框架模型不可读取，已停止")}
    if not dom_matches or not model_matches:
        return {"success": False, "error": _resource_fallback_readback_error(stage, "资源不足中间页链接DOM或框架模型回读不一致，已停止")}
    return {"success": True}


def _fill_and_verify_resource_fallback(page, expected):
    """Native-fill, prove framework binding, then prove a Tab round trip before save."""
    if not _fill_visible_copy_dialog_input(page, "资源不足中间页链接", expected):
        print("[create_app] 资源不足中间页链接填写: filled=False")
        return {"success": False, "error": _resource_fallback_readback_error("FILL", "资源不足中间页链接填写失败，已停止保存")}
    initial = _verify_resource_fallback_readback(page, expected, "FILL", "before_save")
    if not initial["success"]:
        return initial
    if not _go_tab(page, "悬浮球配置") or not _go_tab(page, "基础配置"):
        return {"success": False, "error": _resource_fallback_readback_error("FILL", "资源不足中间页链接Tab往返无法确认，已停止保存")}
    return _verify_resource_fallback_readback(page, expected, "FILL", "before_save_tab_round_trip")


def _verify_persisted_main_row_identity(page, app_id, expected_app_name, expected_channel_name):
    """Prove all persisted identity anchors in the unique visible main-table row."""
    try:
        payload = page.evaluate(_MAIN_LIST_COLLECT_JS, True) or {}
    except Exception:
        payload = {}
    if payload.get("main_table_count") != 1:
        return {"success": False, "error": _resource_fallback_readback_error("VERIFY", "保存后主表不可唯一读取，已停止")}

    expected_id = str(app_id or "").strip()
    expected_name = (expected_app_name or "").strip()
    expected_channel = (expected_channel_name or "").strip()
    headers = payload.get("headers") or []
    if not all(
        _matching_main_header_count(headers, predicate) == 1
        for predicate in (_is_app_id_header, _is_app_name_header, _is_channel_name_header)
    ):
        return {"success": False, "error": _resource_fallback_readback_error("VERIFY", "保存后主表三锚列映射不可唯一，已停止")}
    candidates = []
    for row in payload.get("rows") or []:
        aligned = _align_header_cells(headers, row.get("cells") or [])
        if not aligned or _mapped_value(aligned, _is_app_id_header) != expected_id:
            continue
        candidates.append((row, aligned))
    if len(candidates) != 1:
        return {"success": False, "error": _resource_fallback_readback_error("VERIFY", "保存后主表应用ID不可唯一核验，已停止")}

    row, aligned = candidates[0]
    observed_id = _mapped_value(aligned, _is_app_id_header)
    observed_name = _mapped_value(aligned, _is_app_name_header)
    observed_channel = _mapped_value(aligned, _is_channel_name_header)
    id_matches = bool(expected_id) and observed_id == expected_id
    name_matches = bool(expected_name) and observed_name == expected_name
    channel_matches = bool(expected_channel) and observed_channel == expected_channel
    print(
        "[create_app] 保存后主表三锚核验: "
        f"id_matches={id_matches} app_name_present={bool(observed_name)} "
        f"app_name_matches={name_matches} channel_present={bool(observed_channel)} "
        f"channel_matches={channel_matches}"
    )
    if not (id_matches and name_matches and channel_matches):
        return {"success": False, "error": _resource_fallback_readback_error("VERIFY", "保存后主表应用ID、应用名或渠道身份回读不一致，已停止")}
    return {"success": True, "row_idx": row.get("row_idx")}


def _open_copy_dialog_by_app_id(page, app_id, verified_row_idx, expected_app_name, expected_channel_name):
    """Open Copy only after rechecking all three anchors on the verified row."""
    try:
        return bool(page.evaluate(
            """
            ({appId, rowIdx, appName, channelName}) => {
              const visible = (el) => Boolean(el) && el.offsetParent !== null;
              const inDialog = (el) => Boolean(el && el.closest('.el-dialog, .el-dialog__wrapper'));
              const utility = (el) => /el-table__expand-column|el-table-column--selection|\\bgutter\\b/.test(String(el.className || ''));
              const tables = Array.from(document.querySelectorAll('.el-table')).filter(table => visible(table) && !inDialog(table));
              if (tables.length !== 1 || !Number.isInteger(rowIdx)) return false;
              const table = tables[0];
              const headerNodes = table.querySelectorAll('.el-table__header-wrapper th');
              const fallbackHeaders = table.querySelectorAll('th');
              const headers = Array.from(headerNodes.length ? headerNodes : fallbackHeaders).filter(header => !utility(header));
              const textOf = header => (header.innerText || header.textContent || '').replace(/[ *:：\\s]/g, '').trim();
              const idIndexes = headers.map(textOf).map((text, index) => text === 'ID' || text.includes('应用ID') ? index : -1).filter(index => index >= 0);
              const nameIndexes = headers.map(textOf).map((text, index) => text.includes('应用名称') || text.includes('应用名') ? index : -1).filter(index => index >= 0);
              const channelIndexes = headers.map(textOf).map((text, index) => text.includes('渠道') && !/ID|编码|code/i.test(text) ? index : -1).filter(index => index >= 0);
              if (idIndexes.length !== 1 || nameIndexes.length !== 1 || channelIndexes.length !== 1) return false;
              const bodyRows = table.querySelectorAll('.el-table__body-wrapper tbody tr');
              const sourceRows = bodyRows.length ? bodyRows : table.querySelectorAll('tbody tr');
              const rows = Array.from(sourceRows).filter(row => !row.classList.contains('el-table__expanded-row'));
              const row = rows[rowIdx];
              if (!row || !visible(row)) return false;
              const cells = Array.from(row.querySelectorAll('td')).filter(cell => !utility(cell));
              if (cells.length !== headers.length) return false;
              const cellText = index => (cells[index].innerText || cells[index].textContent || '').trim();
              if (
                cellText(idIndexes[0]) !== String(appId || '').trim()
                || cellText(nameIndexes[0]) !== String(appName || '').trim()
                || cellText(channelIndexes[0]) !== String(channelName || '').trim()
              ) return false;
              const copy = Array.from(row.querySelectorAll('button, a, span')).find(button => visible(button) && (button.innerText || '').trim() === '复制');
              if (!copy) return false;
              copy.click();
              return true;
            }
            """,
            {
                "appId": app_id,
                "rowIdx": verified_row_idx,
                "appName": expected_app_name,
                "channelName": expected_channel_name,
            },
        ))
    except Exception:
        return False


def _open_copy_dialog_by_known_main_row(
    page, app_id, page_number, row_idx, logical_key, key_kind, app_name, channel_name
):
    """Atomically recheck the collector's page/index/key/main anchors before Copy."""
    try:
        return bool(page.evaluate(
            """
            ({appId, pageNumber, rowIdx, logicalKey, keyKind, appName, channelName}) => {
              const visible = el => Boolean(el) && el.offsetParent !== null;
              const inDialog = el => Boolean(el && el.closest('.el-dialog, .el-dialog__wrapper'));
              const utility = el => /el-table__expand-column|el-table-column--selection|\\bgutter\\b/.test(String(el.className || ''));
              const tables = Array.from(document.querySelectorAll('.el-table')).filter(table => visible(table) && !inDialog(table));
              if (tables.length !== 1 || !Number.isInteger(rowIdx) || !logicalKey) return false;
              const pagers = Array.from(document.querySelectorAll('.el-pagination')).filter(pager => !inDialog(pager));
              const active = pagers[0]?.querySelector('.el-pager li.active');
              const currentPage = /^\\d+$/.test((active?.innerText || '').trim()) ? Number(active.innerText.trim()) : 1;
              if (currentPage !== pageNumber) return false;
              const table = tables[0];
              const headers = Array.from((table.querySelectorAll('.el-table__header-wrapper th').length ? table.querySelectorAll('.el-table__header-wrapper th') : table.querySelectorAll('th'))).filter(header => !utility(header));
              const textOf = header => (header.innerText || header.textContent || '').replace(/[ *:：\\s]/g, '').trim();
              const indexesFor = predicate => headers.map(textOf).map((text, index) => predicate(text) ? index : -1).filter(index => index >= 0);
              const idIndexes = indexesFor(text => text === 'ID' || text.includes('应用ID'));
              const nameIndexes = indexesFor(text => text.includes('应用名称') || text.includes('应用名'));
              const channelIndexes = indexesFor(text => text.includes('渠道') && !/ID|编码|code/i.test(text));
              if (idIndexes.length !== 1 || nameIndexes.length !== 1 || channelIndexes.length !== 1) return false;
              const bodyRows = table.querySelectorAll('.el-table__body-wrapper tbody tr');
              const sourceRows = Array.from(bodyRows.length ? bodyRows : table.querySelectorAll('tbody tr'));
              const logicalRows = sourceRows.filter(row => !row.classList.contains('el-table__expanded-row'));
              const valuesOf = row => {
                const cells = Array.from(row.querySelectorAll('td')).filter(cell => !utility(cell));
                if (cells.length !== headers.length) return null;
                const text = index => (cells[index].innerText || cells[index].textContent || '').trim();
                return {id: text(idIndexes[0]), name: text(nameIndexes[0]), channel: text(channelIndexes[0])};
              };
              const nativeKeyOf = row => row.getAttribute('data-row-key') || row.getAttribute('row-key') || '';
              const keyOf = (row, index, values) => {
                const nativeKey = nativeKeyOf(row);
                return nativeKey || [`synthetic:${currentPage}`, index, values.id, values.name, values.channel].join(String.fromCharCode(31));
              };
              const matches = logicalRows.map((row, index) => ({row, index, values: valuesOf(row)})).filter(item => visible(item.row) && item.values && item.values.id === String(appId || '').trim());
              if (!matches.length) return false;
              if (keyKind === 'synthetic' && matches.length !== 1) return false;
              for (const matched of matches) {
                if (matched.values.id !== String(appId || '').trim() || matched.values.name !== String(appName || '').trim() || matched.values.channel !== String(channelName || '').trim()) return false;
                if (keyKind === 'native' && (!nativeKeyOf(matched.row) || nativeKeyOf(matched.row) !== logicalKey)) return false;
                if (keyKind === 'synthetic' && keyOf(matched.row, matched.index, matched.values) !== logicalKey) return false;
              }
              const target = logicalRows[rowIdx];
              const targetValues = target ? valuesOf(target) : null;
              if (!target || !visible(target) || !targetValues || keyOf(target, rowIdx, targetValues) !== logicalKey) return false;
              const copy = Array.from(target.querySelectorAll('button, a, span')).find(button => visible(button) && (button.innerText || '').trim() === '复制');
              if (!copy) return false;
              copy.click();
              return true;
            }
            """,
            {
                "appId": app_id,
                "pageNumber": page_number,
                "rowIdx": row_idx,
                "logicalKey": logical_key,
                "keyKind": key_kind,
                "appName": app_name,
                "channelName": channel_name,
            },
        ))
    except Exception:
        return False


def _click_unchecked_switch_by_known_main_row(
    page, app_id, page_number, row_idx, logical_key, key_kind, app_name, channel_name
):
    """Atomically re-resolve one stable main row and click its unchecked switch once."""
    try:
        return bool(page.evaluate(
            """
            ({appId, pageNumber, rowIdx, logicalKey, keyKind, appName, channelName}) => {
              const visible = el => {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                if (el.getAttribute('aria-hidden') === 'true') return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
              };
              const inDialog = el => Boolean(el && el.closest('.el-dialog, .el-dialog__wrapper'));
              const utility = el => /el-table__expand-column|el-table-column--selection|\\bgutter\\b/.test(String(el.className || ''));
              const messageBoxes = Array.from(document.querySelectorAll('.el-message-box__wrapper')).filter(visible);
              if (messageBoxes.length !== 0) return false;
              const tables = Array.from(document.querySelectorAll('.el-table')).filter(table => visible(table) && !inDialog(table));
              if (tables.length !== 1 || !Number.isInteger(rowIdx) || !logicalKey) return false;
              const pagers = Array.from(document.querySelectorAll('.el-pagination')).filter(pager => !inDialog(pager));
              const active = pagers[0]?.querySelector('.el-pager li.active');
              const currentPage = /^\\d+$/.test((active?.innerText || '').trim()) ? Number(active.innerText.trim()) : 1;
              if (currentPage !== pageNumber) return false;
              const table = tables[0];
              const headers = Array.from((table.querySelectorAll('.el-table__header-wrapper th').length ? table.querySelectorAll('.el-table__header-wrapper th') : table.querySelectorAll('th'))).filter(header => !utility(header));
              const textOf = header => (header.innerText || header.textContent || '').replace(/[ *:：\\s]/g, '').trim();
              const indexesFor = predicate => headers.map(textOf).map((text, index) => predicate(text) ? index : -1).filter(index => index >= 0);
              const idIndexes = indexesFor(text => text === 'ID' || text.includes('应用ID'));
              const nameIndexes = indexesFor(text => text.includes('应用名称') || text.includes('应用名'));
              const channelIndexes = indexesFor(text => text.includes('渠道') && !/ID|编码|code/i.test(text));
              if (idIndexes.length !== 1 || nameIndexes.length !== 1 || channelIndexes.length !== 1) return false;
              const bodyRows = table.querySelectorAll('.el-table__body-wrapper tbody tr');
              const logicalRows = Array.from(bodyRows.length ? bodyRows : table.querySelectorAll('tbody tr')).filter(row => !row.classList.contains('el-table__expanded-row'));
              const valuesOf = row => {
                const cells = Array.from(row.querySelectorAll('td')).filter(cell => !utility(cell));
                if (cells.length !== headers.length) return null;
                const text = index => (cells[index].innerText || cells[index].textContent || '').trim();
                return {id: text(idIndexes[0]), name: text(nameIndexes[0]), channel: text(channelIndexes[0])};
              };
              const nativeKeyOf = row => row.getAttribute('data-row-key') || row.getAttribute('row-key') || '';
              const keyOf = (row, index, values) => nativeKeyOf(row) || [`synthetic:${currentPage}`, index, values.id, values.name, values.channel].join(String.fromCharCode(31));
              const matches = logicalRows.map((row, index) => ({row, index, values: valuesOf(row)})).filter(item => visible(item.row) && item.values && item.values.id === String(appId || '').trim());
              if (!matches.length || (keyKind === 'synthetic' && matches.length !== 1)) return false;
              for (const matched of matches) {
                if (matched.values.id !== String(appId || '').trim() || matched.values.name !== String(appName || '').trim() || matched.values.channel !== String(channelName || '').trim()) return false;
                if (keyKind === 'native' && (!nativeKeyOf(matched.row) || nativeKeyOf(matched.row) !== logicalKey)) return false;
                if (keyKind === 'synthetic' && keyOf(matched.row, matched.index, matched.values) !== logicalKey) return false;
              }
              const target = logicalRows[rowIdx];
              const targetValues = target ? valuesOf(target) : null;
              if (!target || !visible(target) || !targetValues || keyOf(target, rowIdx, targetValues) !== logicalKey) return false;
              const switches = Array.from(target.querySelectorAll('.el-switch')).filter(visible);
              if (switches.length !== 1) return false;
              const switchControl = switches[0];
              const input = switchControl.querySelector('input');
              if (switchControl.classList.contains('is-disabled') || switchControl.getAttribute('aria-disabled') === 'true' || (input && input.disabled) || switchControl.classList.contains('is-checked')) return false;
              switchControl.click();
              return true;
            }
            """,
            {"appId": app_id, "pageNumber": page_number, "rowIdx": row_idx,
             "logicalKey": logical_key, "keyKind": key_kind, "appName": app_name,
             "channelName": channel_name},
        ))
    except Exception:
        return False


def _visible_message_box_count(page):
    """Read strict visual confirmation boxes before an irreversible switch click."""
    try:
        result = page.evaluate("""() => {
          const visible = item => {
            if (!item) return false;
            const style = window.getComputedStyle(item);
            if (style.display === 'none' || style.visibility === 'hidden') return false;
            if (item.getAttribute('aria-hidden') === 'true') return false;
            const rect = item.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
          };
          return Array.from(document.querySelectorAll('.el-message-box__wrapper')).filter(visible).length;
        }""")
        return result if isinstance(result, int) and not isinstance(result, bool) else None
    except Exception:
        return None


def _click_unique_new_message_box_confirm(page):
    """Click exactly one enabled, exact-text confirmation button; never use Enter."""
    try:
        return bool(page.evaluate("""() => {
          const visible = el => {
            if (!el) return false;
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden') return false;
            if (el.getAttribute('aria-hidden') === 'true') return false;
            const rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
          };
          const boxes = Array.from(document.querySelectorAll('.el-message-box__wrapper')).filter(visible);
          if (boxes.length !== 1) return false;
          const buttons = Array.from(boxes[0].querySelectorAll('button')).filter(button => {
            const text = (button.innerText || button.textContent || '').replace(/\\s+/g, '').trim();
            return visible(button) && text === '确定' && !button.disabled
              && button.getAttribute('aria-disabled') !== 'true' && !button.classList.contains('is-disabled');
          });
          if (buttons.length !== 1) return false;
          buttons[0].click();
          return true;
        }"""))
    except Exception:
        return False


def _copy_dialog_visible(page):
    """Strict visible-copy-dialog check used only for bounded verification cleanup."""
    return bool(page.evaluate("""() => {
      const visible = (el) => {
        if (!el) return false;
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden') return false;
        if (el.getAttribute('aria-hidden') === 'true') return false;
        const rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
      };
      return Array.from(document.querySelectorAll('.el-dialog__wrapper')).some(
        dialog => visible(dialog) && dialog.querySelectorAll('.el-tabs__item').length > 0
      );
    }"""))


def _close_copy_dialog_after_verify(page):
    """Close the read-only verification dialog once and confirm it closed."""
    try:
        if not _copy_dialog_visible(page):
            return True
        clicked = bool(page.evaluate("""() => {
          const visible = (el) => {
            if (!el) return false;
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden') return false;
            const rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
          };
          const dialog = Array.from(document.querySelectorAll('.el-dialog__wrapper')).find(
            item => visible(item) && item.querySelectorAll('.el-tabs__item').length > 0
          );
          if (!dialog) return false;
          const cancel = Array.from(dialog.querySelectorAll('button')).find(
            button => visible(button) && (button.innerText || '').replace(/\\s/g, '').trim() === '取消'
          );
          if (cancel) { cancel.click(); return true; }
          const close = dialog.querySelector('.el-dialog__headerbtn');
          if (close) { close.click(); return true; }
          return false;
        }"""))
        if not clicked:
            return not _copy_dialog_visible(page)
        for _ in range(12):
            page.wait_for_timeout(100)
            if not _copy_dialog_visible(page):
                return True
    except Exception:
        return False
    return False


def _verify_resource_fallback_by_known_main_row(
    page, app_id, expected, expected_app_name, expected_channel_name, main_identity
):
    """Double-read fallback through one already-stable current-view row handle."""
    if not _open_copy_dialog_by_known_main_row(
        page,
        app_id,
        main_identity.get("page"),
        main_identity.get("row_idx"),
        main_identity.get("row_key"),
        main_identity.get("key_kind"),
        expected_app_name,
        expected_channel_name,
    ):
        return {"success": False, "error": _resource_fallback_readback_error("VERIFY", "保存后无法从已核验主表应用ID行打开资源不足中间页链接核验，已停止")}
    verified = None
    try:
        try:
            page.wait_for_selector(
                ".el-dialog__wrapper:not([style*='display: none']) .el-tabs__item",
                timeout=STEP_TIMEOUT,
            )
        except Exception:
            verified = {"success": False, "error": _resource_fallback_readback_error("VERIFY", "保存后资源不足中间页链接核验对话框未就绪，已停止")}
        if verified is None and not _go_tab(page, "基础配置"):
            verified = {"success": False, "error": _resource_fallback_readback_error("VERIFY", "保存后无法切换资源不足中间页链接核验页签，已停止")}
        if verified is None:
            stable_reads = 0
            last_failure = None
            for attempt in range(_POST_SAVE_FALLBACK_POLL_ATTEMPTS):
                current = _verify_resource_fallback_readback(page, expected, "VERIFY", "after_save")
                if current["success"]:
                    stable_reads += 1
                    if stable_reads >= 2:
                        verified = current
                        break
                else:
                    stable_reads = 0
                    last_failure = current
                if attempt < _POST_SAVE_FALLBACK_POLL_ATTEMPTS - 1:
                    page.wait_for_timeout(_POST_SAVE_FALLBACK_POLL_MS)
            if verified is None:
                verified = last_failure or {
                    "success": False,
                    "error": _resource_fallback_readback_error("VERIFY", "保存后资源不足中间页链接未能稳定核验，已停止"),
                }
    finally:
        closed = _close_copy_dialog_after_verify(page)
    if not closed:
        return {"success": False, "error": _resource_fallback_readback_error("VERIFY", "保存后资源不足中间页链接核验对话框未能确认关闭，已停止")}
    return verified


def _verify_persisted_resource_fallback(page, app_id, expected, expected_app_name, expected_channel_name, narrow_context_profiles=None):
    """Compatibility wrapper: locate through the fresh unfiltered gate, then read."""
    main_identity = _locate_known_main_row_for_resource_fallback(
        page, app_id, expected_app_name, expected_channel_name, narrow_context_profiles=narrow_context_profiles
    )
    if not main_identity["success"]:
        diagnostics = _known_main_redacted_facts(main_identity, main_identity.get("stable_row_key", False))
        return {
            "success": False,
            "error": _resource_fallback_readback_error(
                "VERIFY",
                "保存后已知应用ID主行身份未能稳定核验，已停止 "
                f"candidate_category={diagnostics['candidate_category']} page={diagnostics['page']} "
                f"stable_row_key={diagnostics['stable_row_key']} id_field_source={diagnostics['id_field_source']}",
            ),
        }
    return _verify_resource_fallback_by_known_main_row(
        page, app_id, expected, expected_app_name, expected_channel_name, main_identity
    )


def _js_select(page, label, value):
    """Native-select an exact value and prove the Element-UI control settled."""
    target = (value or "").strip()
    if not target:
        print(f"[create_app] WARN: 下拉选择失败 label={label} reason=empty_target")
        return False
    try:
        wrappers = page.locator(".el-dialog__wrapper")
        dialogs = []
        for index in range(wrappers.count()):
            dialog = wrappers.nth(index)
            if dialog.is_visible() and dialog.locator(".el-tabs__item").count() > 0:
                dialogs.append(dialog)
        if len(dialogs) != 1:
            print(f"[create_app] WARN: 下拉选择失败 label={label} reason=visible_dialog_not_unique")
            return False
        form_items = dialogs[0].locator(".el-form-item")
        matched_items = []
        for index in range(form_items.count()):
            item = form_items.nth(index)
            if not item.is_visible():
                continue
            label_node = item.locator(".el-form-item__label")
            if label_node.count() != 1:
                continue
            observed_label = (label_node.inner_text() or "").replace(" ", "").replace("*", "").replace(":", "").replace("：", "").strip()
            if observed_label == label and item.locator(".el-select").count() == 1:
                matched_items.append(item)
        if len(matched_items) != 1:
            print(f"[create_app] WARN: 下拉选择失败 label={label} reason=control_not_unique")
            return False
        select = matched_items[0].locator(".el-select")
        select.click(timeout=STEP_TIMEOUT)
    except Exception as exc:
        print(f"[create_app] WARN: 下拉选择失败 label={label} reason=physical_open_exception exception_type={type(exc).__name__}")
        return False

    dropdown = None
    deadline = time.monotonic() + (STEP_TIMEOUT / 1000)
    while time.monotonic() < deadline:
        try:
            candidates = page.locator(".el-select-dropdown")
            visible_dropdowns = [
                candidates.nth(index) for index in range(candidates.count())
                if candidates.nth(index).is_visible()
            ]
            if len(visible_dropdowns) == 1:
                dropdown = visible_dropdowns[0]
                break
            if len(visible_dropdowns) > 1:
                print(f"[create_app] WARN: 下拉选择失败 label={label} reason=visible_dropdown_not_unique")
                return False
        except Exception as exc:
            print(f"[create_app] WARN: 下拉选择失败 label={label} reason=dropdown_observe_exception exception_type={type(exc).__name__}")
            return False
        page.wait_for_timeout(100)
    if dropdown is None:
        print(f"[create_app] WARN: 下拉选择失败 label={label} reason=dropdown_not_visible")
        return False

    try:
        options = dropdown.locator(".el-select-dropdown__item")
        exact_options = [
            options.nth(index) for index in range(options.count())
            if options.nth(index).is_visible() and (options.nth(index).inner_text() or "").strip() == target
        ]
        if len(exact_options) != 1:
            print(f"[create_app] WARN: 下拉选择失败 label={label} reason=option_not_unique")
            return False
        option_model = exact_options[0].evaluate("""
        option => {
          const component = option.__vue__ || null;
          const value = component?.$props?.value;
          return { found: value !== undefined, value };
        }
        """)
        if not option_model.get("found"):
            print(f"[create_app] WARN: 下拉选择失败 label={label} reason=option_model_unreadable")
            return False
        exact_options[0].click(timeout=STEP_TIMEOUT)
    except Exception as exc:
        print(f"[create_app] WARN: 下拉选择失败 label={label} reason=physical_option_exception exception_type={type(exc).__name__}")
        return False

    deadline = time.monotonic() + (STEP_TIMEOUT / 1000)
    while time.monotonic() < deadline:
        try:
            candidates = page.locator(".el-select-dropdown")
            if all(not candidates.nth(index).is_visible() for index in range(candidates.count())):
                break
        except Exception as exc:
            print(f"[create_app] WARN: 下拉选择失败 label={label} reason=dropdown_close_observe_exception exception_type={type(exc).__name__}")
            return False
        page.wait_for_timeout(100)
    else:
        print(f"[create_app] WARN: 下拉选择失败 label={label} reason=dropdown_not_closed")
        return False

    try:
        binding = select.evaluate("""
        (node, expected) => {
          const input = node.querySelector('input');
          const component = node.__vue__ || input?.__vue__ || null;
          const model = component?.$vnode?.data?.model?.expression || null;
          const propValue = component?.$props?.value;
          const domValue = input?.value;
          return {
            input_found: Boolean(input),
            model_found: Boolean(model) && propValue !== undefined,
            dom_matches: typeof domValue === 'string' && domValue.trim() === expected.displayValue,
            model_matches: propValue !== undefined && String(propValue) === String(expected.optionModelValue),
          };
        }
        """, {"displayValue": target, "optionModelValue": option_model.get("value")})
    except Exception as exc:
        print(f"[create_app] WARN: 下拉选择失败 label={label} reason=model_observe_exception exception_type={type(exc).__name__}")
        return False
    if not all(binding.get(key) for key in ("input_found", "model_found", "dom_matches", "model_matches")):
        print(f"[create_app] WARN: 下拉选择失败 label={label} reason=dom_model_mismatch")
        return False
    return True


_CHANNEL_LAYER_COMMON_JS = """
  const HERMES_CHANNEL_MARKERS =
    '.channel-popover, .el-popover, [id^="el-popover-"], .el-select-dropdown';
  const HERMES_OPTION_ATTR = 'data-hermes-channel-option';
  const hermesShown = (el) => {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    if (el.getAttribute('aria-hidden') === 'true') return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const hermesCopyDialog = () => {
    const wrappers = document.querySelectorAll('.el-dialog__wrapper');
    for (const wrapper of wrappers) {
      if (wrapper.style.display === 'none') continue;
      if (wrapper.querySelectorAll('.el-tabs__item').length === 0) continue;
      return wrapper;
    }
    return null;
  };
  const hermesChannelInput = (dialog, label) => {
    if (!dialog) return null;
    const wanted = (label || '').replace(/[ *:：]/g, '');
    for (const item of dialog.querySelectorAll('.el-form-item')) {
      if (item.offsetParent === null) continue;
      const labelEl = item.querySelector('.el-form-item__label');
      if (!labelEl) continue;
      const text = (labelEl.innerText || '').replace(/[ *:：]/g, '').trim();
      if (wanted && !text.includes(wanted)) continue;
      const input = item.querySelector('.channel-input');
      if (input) return input;
    }
    return null;
  };
  const hermesDialogSelectOpen = (dialog) => {
    if (!dialog) return false;
    return Array.from(dialog.querySelectorAll('.el-select')).some((sel) => {
      return sel.classList.contains('is-focus') || Boolean(sel.querySelector('.el-input.is-focus'));
    });
  };
  const hermesAnchored = (node, input) => {
    if (!input) return false;
    const r = node.getBoundingClientRect();
    const i = input.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0 || i.width <= 0) return false;
    const overlap = Math.min(r.right, i.right) - Math.max(r.left, i.left);
    if (overlap < Math.max(8, i.width * 0.3)) return false;
    const gap = r.top >= i.bottom ? r.top - i.bottom : (i.top >= r.bottom ? i.top - r.bottom : 0);
    return gap < 240;
  };
  const hermesLayerNodes = () => Array.from(document.querySelectorAll(HERMES_CHANNEL_MARKERS));
  const hermesLayerState = (node, dialog, input) => ({
    shown: hermesShown(node),
    aria_hidden: node.getAttribute('aria-hidden') === 'true',
    is_select_dropdown: node.classList.contains('el-select-dropdown'),
    dialog_select_open: hermesDialogSelectOpen(dialog),
    in_copy_dialog: Boolean(dialog && dialog.contains(node)),
    anchored_to_channel_input: hermesAnchored(node, input),
    is_channel_popover: node.classList.contains('channel-popover'),
  });
  const hermesClearMarkers = () => {
    for (const node of document.querySelectorAll('[' + HERMES_OPTION_ATTR + ']')) {
      node.removeAttribute(HERMES_OPTION_ATTR);
    }
  };
"""


def _channel_layer_is_usable(layer):
    """Keep only the current visible copy-dialog channel layer.

    A layer must be positively bound to the current copy dialog or its channel
    input: it is `.channel-popover`, or it lives inside the visible copy dialog,
    or it is anchored to that dialog's channel input. Hidden, aria-hidden, other
    form poppers and leftover list `el-select-dropdown` layers are excluded.
    """
    if not isinstance(layer, dict):
        return False
    if not layer.get("shown") or layer.get("aria_hidden"):
        return False
    if layer.get("is_select_dropdown") and not layer.get("dialog_select_open"):
        return False
    return bool(
        layer.get("is_channel_popover")
        or layer.get("in_copy_dialog")
        or layer.get("anchored_to_channel_input")
    )


def _match_channel_option(snapshot, channel_name):
    """Count exact option texts on usable layers only. No prefix/contains."""
    target = (channel_name or "").strip()
    if not target or not isinstance(snapshot, dict) or not snapshot.get("has_dialog"):
        return {"exact_count": 0, "option_index": None}
    hits = []
    for layer in snapshot.get("layers") or []:
        if not _channel_layer_is_usable(layer):
            continue
        for option in layer.get("options") or []:
            if not isinstance(option, dict):
                continue
            if option.get("text") == target:
                hits.append(option.get("index"))
    if len(hits) == 1:
        return {"exact_count": 1, "option_index": hits[0]}
    return {"exact_count": len(hits), "option_index": None}


_CHANNEL_LAYER_OPEN_JS = (
    """
(label) => {
  /* CHANNEL_LAYER_OPEN */
"""
    + _CHANNEL_LAYER_COMMON_JS
    + """
  const dialog = hermesCopyDialog();
  const input = hermesChannelInput(dialog, label);
  if (!input) return false;
  input.click();
  return true;
}
"""
)


_CHANNEL_LAYER_SNAPSHOT_JS = (
    """
(label) => {
  /* CHANNEL_LAYER_SNAPSHOT */
"""
    + _CHANNEL_LAYER_COMMON_JS
    + """
  const dialog = hermesCopyDialog();
  const input = hermesChannelInput(dialog, label);
  const layers = [];
  let ordinal = 0;
  for (const node of hermesLayerNodes()) {
    const state = hermesLayerState(node, dialog, input);
    const candidates = node.querySelectorAll('li, td, span, div, a, p, [role="option"]');
    const els = [];
    for (const el of candidates) {
      if (el.offsetParent === null) continue;
      const text = el.innerText ? el.innerText.trim() : '';
      if (!text) continue;
      els.push(el);
    }
    const innermost = els.filter((el) => !els.some((other) => other !== el && el.contains(other)));
    const options = [];
    for (const el of innermost) {
      const text = (el.innerText || '').trim();
      if (!text) continue;
      el.setAttribute(HERMES_OPTION_ATTR, String(ordinal));
      options.push({index: ordinal, text});
      ordinal += 1;
    }
    layers.push(Object.assign(state, {options}));
  }
  return {has_dialog: Boolean(dialog), layers};
}
"""
)

_CHANNEL_OPTION_CLICK_JS = """
(index) => {
  /* CHANNEL_OPTION_CLICK */
  if (index === null || index === undefined) return false;
  const node = document.querySelector('[data-hermes-channel-option="' + String(index) + '"]');
  if (!node) return false;
  node.click();
  return true;
}
"""

_CHANNEL_LAYER_CLEANUP_JS = (
    """
() => {
  /* CHANNEL_LAYER_CLEANUP */
"""
    + _CHANNEL_LAYER_COMMON_JS
    + """
  const dialog = hermesCopyDialog();
  const input = hermesChannelInput(dialog, null);
  hermesClearMarkers();
  for (const node of hermesLayerNodes()) {
    if (node.classList.contains('el-select-dropdown')) continue;
    const state = hermesLayerState(node, dialog, input);
    if (!state.shown || state.aria_hidden) continue;
    const bound = state.is_channel_popover || state.in_copy_dialog || state.anchored_to_channel_input;
    if (bound) node.style.display = 'none';
  }
  return true;
}
"""
)


def _js_channel_popover(page, label, channel_name):
    """Select a unique exact channel option from the current copy-dialog layer."""
    opened = page.evaluate(_CHANNEL_LAYER_OPEN_JS, label)
    if not opened:
        return {"opened": False, "exact_count": 0, "clicked": False}
    last = {"exact_count": 0, "clicked": False}
    for _ in range(15):
        snapshot = page.evaluate(_CHANNEL_LAYER_SNAPSHOT_JS, label) or {}
        matched = _match_channel_option(snapshot, channel_name)
        last["exact_count"] = int(matched.get("exact_count") or 0)
        if last["exact_count"] > 1:
            break
        if last["exact_count"] == 1:
            last["clicked"] = bool(
                page.evaluate(_CHANNEL_OPTION_CLICK_JS, matched.get("option_index"))
            )
            break
        page.wait_for_timeout(100)
    if last.get("clicked"):
        page.wait_for_timeout(500)
    page.evaluate(_CHANNEL_LAYER_CLEANUP_JS)
    if last.get("clicked"):
        page.wait_for_timeout(300)
    return {
        "opened": True,
        "exact_count": int(last.get("exact_count") or 0),
        "clicked": bool(last.get("clicked")),
    }


def _read_dialog_channel_value(page, label):
    """Read the selected channel display value from the create dialog control."""
    return page.evaluate("""
    (label) => {
      const wrappers = document.querySelectorAll('.el-dialog__wrapper');
      for (const w of wrappers) {
        if (w.style.display === 'none') continue;
        const items = w.querySelectorAll('.el-form-item');
        for (const it of items) {
          if (it.offsetParent === null) continue;
          const lblEl = it.querySelector('.el-form-item__label');
          if (!lblEl) continue;
          const lbl = (lblEl.innerText || '').replace(/[ *:：]/g, '').trim();
          if (!lbl.includes(label.replace(/[ *:：]/g, '').trim())) continue;
          const ci = it.querySelector('.channel-input');
          if (ci) {
            const input = ci.querySelector('input');
            const fromInput = input ? (input.value || '').trim() : '';
            if (fromInput) return fromInput;
            return (ci.innerText || '').replace(/\\s+/g, ' ').trim();
          }
          const input = it.querySelector('input.el-input__inner');
          if (input) return (input.value || '').trim();
        }
      }
      return '';
    }
    """, label)


def _select_and_verify_create_channel(page, channel_name):
    """Exact-select the create-dialog channel and read it back before save."""
    target = (channel_name or "").strip()
    if not target:
        return {
            "success": False,
            "error": err("CHANNEL_SELECT_FAILED", "FILL", "目标渠道为空，已停止保存", NEXT_MANUAL),
        }
    opened_any = False
    for label in ("所属渠道", "渠道"):
        result = _js_channel_popover(page, label, target)
        if not result.get("opened"):
            continue
        opened_any = True
        if result.get("exact_count") != 1 or not result.get("clicked"):
            return {
                "success": False,
                "error": err(
                    "CHANNEL_SELECT_FAILED",
                    "FILL",
                    "创建对话框中渠道不是唯一精确匹配，已停止保存",
                    NEXT_MANUAL,
                ),
            }
        selected = (_read_dialog_channel_value(page, label) or "").strip()
        if selected != target:
            return {
                "success": False,
                "error": err(
                    "CHANNEL_SELECT_FAILED",
                    "FILL",
                    "所属渠道回读值与目标不一致，已停止保存",
                    NEXT_MANUAL,
                ),
            }
        return {"success": True}
    message = (
        "未找到可精确选择的所属渠道控件，已停止保存"
        if not opened_any
        else "创建对话框中渠道不是唯一精确匹配，已停止保存"
    )
    return {
        "success": False,
        "error": err("CHANNEL_SELECT_FAILED", "FILL", message, NEXT_MANUAL),
    }


def _click_save_button(page):
    """Click only the unique semantic save control of the visible copy dialog.

    Element-UI keeps this control in a layout-hidden parent, so geometry cannot
    be a safety predicate.  The semantic control itself must instead be exact,
    enabled, handler-bound, and uniquely owned by the one visible tabbed dialog.
    """
    return page.evaluate("""() => {
      const visible = el => {
        if (!el) return false;
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || el.getAttribute('aria-hidden') === 'true') return false;
        const rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
      };
      const dialogs = Array.from(document.querySelectorAll('.el-dialog__wrapper')).filter(
        dialog => visible(dialog) && dialog.querySelectorAll('.el-tabs__item').length > 0
      );
      if (dialogs.length !== 1) return false;
      const hasHandler = button => {
        for (let node = button, depth = 0; node && depth < 4; node = node.parentElement, depth += 1) {
          const component = node.__vue__;
          const listener = component?.$listeners || component?.$vnode?.data?.on;
          if (listener && (listener.click || listener.submit)) return true;
          if (component?.$options?.methods && Object.keys(component.$options.methods).some(key => /click|submit/i.test(key))) return true;
        }
        return false;
      };
      const candidates = Array.from(dialogs[0].querySelectorAll('button')).filter(button => {
        const text = (button.innerText || button.textContent || '').replace(/\\s+/g, '').trim();
        return text === '保存'
          && !button.disabled
          && button.getAttribute('aria-disabled') !== 'true'
          && hasHandler(button);
      });
      if (candidates.length !== 1) return false;
      candidates[0].click();
      return true;
    }""")


def _set_stage(execution_id, stage):
    try:
        ex.update_execution(execution_id, output_json=json.dumps({"_stage": stage}, ensure_ascii=False))
        print(f"[create_app] stage: {stage}")
    except Exception:
        pass


def _post_save_unconfirmed_failure(error):
    """Once Save clicked, preserve uncertainty and prohibit a fresh create."""
    return {
        "success": False,
        "error": {**(error or {}), "next_action": NEXT_QUERY},
        "save_may_have_occurred": True,
    }


def _create_failure_business_status(create_result):
    return ex.BIZ_UNKNOWN if create_result.get("save_may_have_occurred") else ex.BIZ_FAILED


def _open_group_dialog(page, row_idx):
    return page.evaluate("""
    (rowIdx) => {
      const allRows = document.querySelectorAll('.el-table__body-wrapper tbody tr');
      const sourceRows = allRows.length ? allRows : document.querySelectorAll('table tbody tr');
      const rows = Array.from(sourceRows).filter(row => !row.classList.contains('el-table__expanded-row'));
      if (rowIdx < 0 || rowIdx >= rows.length) return false;
      const row = rows[rowIdx];
      // 只匹配 BUTTON 元素（排除 SPAN），行里有两个"设置"按钮：
      // 第一个是"白名单设置"，第二个是"云机链接设置"（含按分组）
      const buttons = Array.from(row.querySelectorAll('button')).filter(
        el => el.offsetParent !== null && el.innerText && el.innerText.trim() === '设置'
      );
      if (!buttons.length) return false;
      // 点最后一个（第二个"设置" = 云机链接设置）
      buttons[buttons.length - 1].click();
      return true;
    }
    """, row_idx)


def _read_group_dialog(page, expected_group):
    return page.evaluate("""
    (expectedGroup) => {
      const wrappers = document.querySelectorAll('.el-dialog__wrapper');
      for (const wrapper of wrappers) {
        // 不用 offsetParent（fixed 元素返回 null），只用 display 判断可见性
        if (wrapper.style.display === 'none') continue;
        if (!wrapper.innerText.includes('按分组')) continue;

        const values = Array.from(wrapper.querySelectorAll('input.el-input__inner'))
          .filter(input => input.offsetParent !== null)
          .map(input => (input.value || '').trim())
          .filter(Boolean);
        const groupLabels = Array.from(wrapper.querySelectorAll('.el-radio, .el-radio-button'))
          .filter(el => el.innerText && el.innerText.includes('按分组'));
        const modeSelected = groupLabels.some(el =>
          el.className.includes('is-checked') ||
          (el.querySelector('input') && el.querySelector('input').checked)
        );
        return {
          found: true,
          mode_selected: modeSelected,
          values: values,
          expected_present: values.includes(expectedGroup)
        };
      }
      return {found: false, mode_selected: false, values: [], expected_present: false};
    }
    """, expected_group)


def _close_group_dialog(page):
    page.evaluate("""
    () => {
      const wrappers = document.querySelectorAll('.el-dialog__wrapper');
      for (const wrapper of wrappers) {
        if (wrapper.style.display === 'none') continue;
        if (!wrapper.innerText.includes('按分组')) continue;
        const close = wrapper.querySelector('.el-dialog__headerbtn');
        if (close) { close.click(); return true; }
        const cancel = Array.from(wrapper.querySelectorAll('button')).find(
          button => button.offsetParent !== null && button.innerText.trim() === '取消'
        );
        if (cancel) { cancel.click(); return true; }
      }
      return false;
    }
    """)


def _search_by_link(page, ref_cloud_app_link):
    """主路径：用长链接搜索参考应用并点击复制。

    Returns:
        {clicked: bool, uncertain: bool, error: str_or_None}
        - clicked=True: 已点击复制按钮
        - clicked=False, uncertain=False, error=None: 安全，可进入兜底
        - clicked=False, uncertain=False, error="SEARCH_FAILED": 搜索组件缺失
        - clicked=True, uncertain=True: 不确定是否点了复制（不能进兜底）
    """
    link_key = extract_cloud_app_key(ref_cloud_app_link)

    # 1. 精确查找 placeholder 包含"长链接"的可见输入框
    input_ok = page.evaluate("""
    (link) => {
      const inputs = document.querySelectorAll('input');
      for (const inp of inputs) {
        if (inp.offsetParent === null) continue;
        if ((inp.placeholder || '').includes('长链接')) {
          const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
          setter.call(inp, link);
          inp.dispatchEvent(new Event('input', {bubbles: true}));
          return {filled: true, actual: inp.value};
        }
      }
      return {filled: false};
    }
    """, ref_cloud_app_link)
    if not input_ok or not input_ok.get("filled"):
        print("[create_app] 主路径失败：未找到长链接输入框")
        return {"clicked": False, "uncertain": False, "error": "SEARCH_FAILED"}
    if input_ok.get("actual") != ref_cloud_app_link:
        print(f"[create_app] 主路径失败：长链接输入框值不匹配 expected={ref_cloud_app_link[:40]} actual={input_ok.get('actual', '')[:40]}")
        return {"clicked": False, "uncertain": False, "error": "SEARCH_FAILED"}

    # 2. JS 遍历 button，只点击 innerText.trim() === "搜索"
    search_clicked = page.evaluate("""
    () => {
      const btns = document.querySelectorAll('button');
      for (const b of btns) {
        if (b.offsetParent === null) continue;
        if (b.innerText.trim() === '搜索') { b.click(); return true; }
      }
      return false;
    }
    """)
    if not search_clicked:
        print("[create_app] 主路径失败：未找到搜索按钮")
        return {"clicked": False, "uncertain": False, "error": "SEARCH_FAILED"}

    # 等搜索结果
    page.wait_for_timeout(2000)
    try:
        page.wait_for_selector("table tbody tr", timeout=5000)
    except Exception:
        pass
    page.wait_for_timeout(1000)

    # 3. 从 ref_cloud_app_link 提取 i 参数，用 textContent 匹配行
    copied = page.evaluate("""
    (linkKey) => {
      const rows = document.querySelectorAll('table tbody tr');
      for (let i = 0; i < rows.length; i++) {
        const row = rows[i];
        if (row.offsetParent === null) continue;
        // 用 textContent 匹配（包含隐藏的完整URL），用 i= 参数部分匹配
        if (!(row.textContent || '').includes(linkKey)) continue;
        const btns = row.querySelectorAll('button, a, span');
        for (const b of btns) {
          if (b.offsetParent !== null && b.innerText.trim() === '复制') {
            b.click();
            return {clicked: true, row_idx: i};
          }
        }
      }
      return {clicked: false};
    }
    """, link_key)

    if copied and copied.get("clicked"):
        print(f"[create_app] 主路径：已点击复制 (row_idx={copied.get('row_idx')})")
        return {"clicked": True, "uncertain": False, "error": None}
    else:
        print("[create_app] 主路径：长链接搜索失败，未找到复制按钮")
        return {"clicked": False, "uncertain": False, "error": None}


def _locate_by_app_id(page, ref_app_id):
    """兜底路径：按 app_id 逐页精确定位并点击复制。

    Returns:
        {clicked: bool, error: str_or_None}
        - clicked=True: 已点击复制按钮
        - clicked=False, error="COPY_FAILED": 所有页都没有找到
    """
    print(f"[create_app] 兜底路径：按 app_id={ref_app_id} 逐页精确定位")

    # 先重置搜索条件
    page.evaluate("""
    () => {
      const inputs = document.querySelectorAll('input');
      for (const inp of inputs) {
        if (inp.offsetParent === null) continue;
        const ph = inp.placeholder || '';
        if (ph.includes('长链接') || ph.includes('应用名称') || ph.includes('应用')) {
          const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
          setter.call(inp, '');
          inp.dispatchEvent(new Event('input', {bubbles: true}));
        }
      }
    }
    """)
    page.wait_for_timeout(300)
    page.evaluate("""
    () => {
      const btns = document.querySelectorAll('button');
      for (const b of btns) {
        if (b.offsetParent === null) continue;
        if (b.innerText.trim() === '搜索') { b.click(); return; }
      }
    }
    """)
    page.wait_for_timeout(2000)

    # 逐页查找
    for _page_num in range(50):  # 最多翻50页
        # 展开所有折叠行
        page.evaluate("""
        () => {
          const rows = document.querySelectorAll('table tbody tr');
          for (const row of rows) {
            if (row.offsetParent === null) continue;
            const ei = row.querySelector('.el-table__expand-icon, [class*="expand-icon"]');
            if (ei && !ei.className.includes('expanded')) ei.click();
          }
        }
        """)
        page.wait_for_timeout(1000)

        # 在当前页用完整单元格精确匹配 app_id
        found = page.evaluate("""
        (refId) => {
          const rows = document.querySelectorAll('table tbody tr');
          for (let i = 0; i < rows.length; i++) {
            const row = rows[i];
            if (row.offsetParent === null) continue;
            // 逐个单元格精确匹配
            const cells = row.querySelectorAll('td');
            for (const cell of cells) {
              if ((cell.textContent || '').trim() === refId) {
                // 找到，点复制
                const btns = row.querySelectorAll('button, a, span');
                for (const b of btns) {
                  if (b.offsetParent !== null && b.innerText.trim() === '复制') {
                    b.click();
                    return {clicked: true, row_idx: i};
                  }
                }
                return {clicked: false, reason: 'found_row_no_copy_button', row_idx: i};
              }
            }
          }
          return {clicked: false, reason: 'not_on_this_page'};
        }
        """, ref_app_id)

        if found and found.get("clicked"):
            print(f"[create_app] 兜底结果：成功 (page={_page_num+1}, row_idx={found.get('row_idx')})")
            return {"clicked": True, "error": None}

        # 翻页前记录当前内容，确认下一页真实渲染后再继续匹配。
        if not _click_next_page_and_wait(page):
            break

    print(f"[create_app] 兜底结果：失败 (app_id={ref_app_id} 在所有页都未找到)")
    return {"clicked": False, "error": "COPY_FAILED"}


def _trigger_unfiltered_list_refresh(page):
    """At most one exact Search in the uniquely associated main-list filter form."""
    try:
        return bool(page.evaluate("""() => {
          const visible = element => Boolean(element) && element.offsetParent !== null;
          const outsideDialog = element => visible(element) && !element.closest('.el-dialog, .el-dialog__wrapper');
          const tables = Array.from(document.querySelectorAll('.el-table')).filter(outsideDialog);
          const pagers = Array.from(document.querySelectorAll('.el-pagination')).filter(outsideDialog);
          if (tables.length !== 1 || pagers.length !== 1) return false;
          const scopeOf = element => element.closest('.el-card, .el-main, .el-container');
          const scope = scopeOf(tables[0]);
          if (!scope || !scope.contains(pagers[0])) return false;
          const forms = Array.from(scope.querySelectorAll('.el-form')).filter(form =>
            outsideDialog(form) && form.querySelector('.el-select, input')
          );
          if (forms.length !== 1) return false;
          const buttons = Array.from(forms[0].querySelectorAll('button')).filter(button =>
            visible(button) && (button.innerText || '').replace(/\\s/g, '').trim() === '搜索'
          );
          if (buttons.length !== 1) return false;
          buttons[0].click();
          return true;
        }"""))
    except Exception:
        return False


def _reset_list_filters(page):
    """Clear list filters without depending on fuzzy text locators."""
    clicked = page.evaluate("""() => {
      const buttons = document.querySelectorAll('button');
      for (const button of buttons) {
        if (button.offsetParent !== null && button.innerText.trim() === '重置') {
          button.click();
          return true;
        }
      }
      return false;
    }""")
    if not clicked:
        page.evaluate("""() => {
          const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
          for (const input of document.querySelectorAll('input')) {
            if (input.offsetParent === null) continue;
            const placeholder = input.placeholder || '';
            if (!placeholder.includes('搜索') && !placeholder.includes('应用') && !placeholder.includes('长链接')) continue;
            setter.call(input, '');
            input.dispatchEvent(new Event('input', {bubbles: true}));
          }
        }""")
    page.wait_for_timeout(1200)
    return bool(clicked)


_MAIN_LIST_COLLECT_JS = """
(includeDetails) => {
  const visible = (el) => Boolean(el) && el.offsetParent !== null;
  const inDialog = (el) => Boolean(el && el.closest('.el-dialog, .el-dialog__wrapper'));
  const tables = Array.from(document.querySelectorAll('.el-table')).filter(
    (t) => visible(t) && !inDialog(t)
  );
  const pagers = Array.from(document.querySelectorAll('.el-pagination')).filter((p) => !inDialog(p));
  const pager = pagers.length ? pagers[0] : null;
  const active = pager ? pager.querySelector('.el-pager li.active') : null;
  const pageText = active ? (active.innerText || '').trim() : '';
  const payload = {
    main_table_count: tables.length,
    page_number: /^\\d+$/.test(pageText) ? Number(pageText) : 1,
    headers: [],
    rows: []
  };
  if (tables.length !== 1) return payload;
  const table = tables[0];
  const serialize = (el) => ({
    text: (el.innerText || el.textContent || '').trim(),
    classes: String(el.className || '')
  });
  const headerNodes = table.querySelectorAll('.el-table__header-wrapper th');
  const fallbackHeaders = table.querySelectorAll('th');
  payload.headers = Array.from(headerNodes.length ? headerNodes : fallbackHeaders).map(serialize);
  const bodyRows = table.querySelectorAll('.el-table__body-wrapper tbody tr');
  const sourceRows = bodyRows.length ? bodyRows : table.querySelectorAll('tbody tr');
  const rows = Array.from(sourceRows).filter((row) => !row.classList.contains('el-table__expanded-row'));
  const expandedRowFor = (row) => {
    const next = row.nextElementSibling;
    return next && next.classList.contains('el-table__expanded-row') ? next : null;
  };
  const labeledValue = (root, labelPattern) => {
    if (!includeDetails || !root) return '';
    const labels = root.querySelectorAll('.el-form-item__label, .el-descriptions-item__label, th, dt');
    for (const label of labels) {
      const name = (label.innerText || label.textContent || '').replace(/[ *:：\\s]/g, '').trim();
      if (!labelPattern.test(name)) continue;
      const item = label.closest('.el-form-item, .el-descriptions-item, tr, li, dt');
      if (!item) continue;
      const value = item.querySelector('.el-form-item__content, .el-descriptions-item__content, td, dd');
      if (value) return (value.innerText || value.textContent || '').trim();
    }
    return '';
  };
  for (let rowIdx = 0; rowIdx < rows.length; rowIdx++) {
    const row = rows[rowIdx];
    if (row.offsetParent === null) continue;
    const detail = includeDetails ? expandedRowFor(row) : null;
    let detailId = labeledValue(detail, /^(应用)?ID$/i);
    if (!detailId && detail) {
      const match = (detail.textContent || '').match(/(?:应用)?ID\\s*[:：]\\s*([A-Za-z0-9_-]+)/i);
      detailId = match ? match[1] : '';
    }
    payload.rows.push({
      cells: Array.from(row.querySelectorAll('td')).map(serialize),
      detail_id: detailId,
      detail_app_name: labeledValue(detail, /^应用(名称|名)?$/),
      detail_channel: labeledValue(detail, /^(所属)?渠道(名称)?$/),
      // Element's row-key is the only safe way to collapse fixed-column DOM mirrors.
      row_key: row.getAttribute('data-row-key') || row.getAttribute('row-key') || '',
      row_idx: rowIdx,
      row_text: (row.textContent || '').trim()
    });
  }
  return payload;
}
"""

_PAGINATION_SCOPE_JS = """const hermesInDialog = (el) => Boolean(el && el.closest('.el-dialog, .el-dialog__wrapper'));
      const hermesPager = () => {
        const pagers = Array.from(document.querySelectorAll('.el-pagination')).filter((p) => !hermesInDialog(p));
        return pagers.length ? pagers[0] : null;
      };
      """


def _go_to_first_page(page):
    for _ in range(50):
        previous_state = _read_pagination_state(page)
        if previous_state.get("page_number") == 1:
            return True
        moved = page.evaluate("""() => {
          """ + _PAGINATION_SCOPE_JS + """const pager = hermesPager();
          const prev = pager ? pager.querySelector('.btn-prev') : null;
          if (!prev || prev.disabled || prev.className.includes('disabled')) return false;
          prev.click();
          return true;
        }""")
        if not moved:
            return False
        if not wait_for_page_change(page, previous_state, -1, _read_pagination_state):
            print("[create_app] 返回第一页时页面未稳定，停止继续翻页")
            return False
    return _read_pagination_state(page).get("page_number") == 1


def _expand_visible_rows(page):
    expanded = page.evaluate("""() => {
      const visible = (el) => Boolean(el) && el.offsetParent !== null;
      const inDialog = (el) => Boolean(el && el.closest('.el-dialog, .el-dialog__wrapper'));
      const tables = Array.from(document.querySelectorAll('.el-table')).filter(
        (t) => visible(t) && !inDialog(t)
      );
      if (tables.length !== 1) return 0;
      const table = tables[0];
      const bodyRows = table.querySelectorAll('.el-table__body-wrapper tbody tr');
      const sourceRows = bodyRows.length ? bodyRows : table.querySelectorAll('tbody tr');
      let count = 0;
      for (const row of sourceRows) {
        if (row.offsetParent === null) continue;
        if (row.classList.contains('el-table__expanded-row')) continue;
        const icon = row.querySelector('.el-table__expand-icon, [class*="expand-icon"]');
        if (icon && !icon.className.includes('expanded')) {
          icon.click();
          count += 1;
        }
      }
      return count;
    }""")
    if expanded:
        page.wait_for_timeout(600)


def _read_pagination_state(page):
    """Read active page and a signature of visible primary rows separately.

    Headers and rows are read from the same unique visible main list table so a
    copy dialog cannot contribute extra header cells. When no unique main table
    exists the caller sees row_count 0 and fails safe.
    """
    payload = page.evaluate(_MAIN_LIST_COLLECT_JS, False) or {}
    count = payload.get("main_table_count")
    page_number = payload.get("page_number") or 1
    if count is not None and count != 1:
        return {"page_number": page_number, "table_signature": "", "row_count": 0}
    headers = payload.get("headers") or []
    header_texts = [
        (item.get("text") if isinstance(item, dict) else str(item or "")).strip()
        for item in headers
    ]
    id_index = next(
        (index for index, text in enumerate(header_texts) if text == "ID" or "应用ID" in text),
        -1,
    )
    signature = []
    for row in payload.get("rows") or []:
        cells = row.get("cells") or []
        cell_id = ""
        if 0 <= id_index < len(cells):
            cell = cells[id_index]
            cell_id = (cell.get("text") if isinstance(cell, dict) else str(cell or "")).strip()
        signature.append(f"{cell_id}|{row.get('row_text') or ''}")
    return {
        "page_number": page_number,
        "table_signature": json.dumps(signature, ensure_ascii=False, separators=(",", ":")),
        "row_count": len(payload.get("rows") or []),
    }


def _click_next_page_and_wait(page):
    """Move one page only when the next page is enabled and actually rendered."""
    previous_state = _read_pagination_state(page)
    moved = page.evaluate("""() => {
      """ + _PAGINATION_SCOPE_JS + """const pager = hermesPager();
      const next = pager ? pager.querySelector('.btn-next') : null;
      if (!next || next.disabled || next.className.includes('disabled')) return false;
      next.click();
      return true;
    }""")
    if not moved:
        return False
    if not wait_for_page_change(page, previous_state, 1, _read_pagination_state):
        print("[create_app] 分页切换后页面未稳定，停止继续翻页")
        return None
    return True


_ACTION_DIAGNOSTIC_COUNT_LIMIT = 3
_ACTION_DIAGNOSTIC_PATH_LIMIT = 3
_ACTION_DIAGNOSTIC_METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"}


class _ActionWriteDiagnostics:
    """Bounded value-free request classification for one enable action window."""

    def __init__(self):
        self.counts = {
            "accepted_write": 0,
            "method_rejected": 0,
            "list_rejected": 0,
            "origin_rejected": 0,
            "missing_request_id": 0,
        }
        self.origin_counts = {"same_origin": 0, "cross_origin": 0}
        self.methods = set()
        self.path_hashes = set()
        self.observer_event_unseen = 0

    @staticmethod
    def _bounded_add(mapping, key):
        mapping[key] = min(int(mapping.get(key, 0) or 0) + 1, _ACTION_DIAGNOSTIC_COUNT_LIMIT)

    def classify(self, url, method, origin, request_id_required=False, request_id=None):
        """Classify a request without retaining its URL or other request values."""
        fixed_method = str(method or "").upper()
        if fixed_method not in _ACTION_DIAGNOSTIC_METHODS:
            fixed_method = "OTHER"
        self.methods.add(fixed_method)
        same_origin = urlparse(_as_text(url)).netloc == origin
        self._bounded_add(self.origin_counts, "same_origin" if same_origin else "cross_origin")
        path_hash = _save_path_hash(url)
        if path_hash and len(self.path_hashes) < _ACTION_DIAGNOSTIC_PATH_LIMIT:
            self.path_hashes.add(path_hash)
        if fixed_method not in {"POST", "PUT", "PATCH", "DELETE"}:
            self._bounded_add(self.counts, "method_rejected")
            return False
        if _is_list_request_url(url):
            self._bounded_add(self.counts, "list_rejected")
            return False
        if not same_origin:
            self._bounded_add(self.counts, "origin_rejected")
            return False
        if request_id_required and not request_id:
            self._bounded_add(self.counts, "missing_request_id")
            return False
        self._bounded_add(self.counts, "accepted_write")
        return True

    def projection(self):
        return {
            **{key: min(max(int(self.counts.get(key, 0) or 0), 0), _ACTION_DIAGNOSTIC_COUNT_LIMIT)
               for key in ("accepted_write", "method_rejected", "list_rejected", "origin_rejected", "missing_request_id")},
            "observer_event_unseen": min(max(int(self.observer_event_unseen or 0), 0), 1),
            "origin": {key: min(max(int(self.origin_counts.get(key, 0) or 0), 0), _ACTION_DIAGNOSTIC_COUNT_LIMIT)
                       for key in ("same_origin", "cross_origin")},
            "methods": sorted(method for method in self.methods if method in _ACTION_DIAGNOSTIC_METHODS | {"OTHER"})[:_ACTION_DIAGNOSTIC_PATH_LIMIT],
            "path_hashes": sorted(self.path_hashes)[:_ACTION_DIAGNOSTIC_PATH_LIMIT],
        }


class _SaveClickObserver:
    """Bounded, redacted observer for exactly one write request after Save click."""

    def __init__(self):
        self.keepalive = None
        self.candidates = {}
        self.candidate_overflow = False
        self.page_write_request_count = 0
        self.page_responses = []
        self.page_response_overflow = False
        self.page_event_handlers = []
        self.detached = False
        self.cdp_diagnostics = _ActionWriteDiagnostics()
        self.page_diagnostics = _ActionWriteDiagnostics()
        self.confirmation_trace = "none_seen"
        self.confirmation_clicked = False
        self.confirmation_closed = False
        self.post_action_switch_state = "not_inferable"


_SAVE_PAGE_RESPONSE_LIMIT = 2
_SAVE_CANDIDATE_LIMIT = 2


_LIST_STRUCTURE_BUCKETS = (
    "response_body_missing", "response_body_read_error", "base64_decode_error", "json_unparsable",
    "json_non_object", "required_structure_missing", "complete",
)

_LIST_DIAGNOSTIC_KEYS = (
    "source_cdp", "source_page_event", "payload_inline", "payload_fetched", "payload_unavailable",
    "payload_read_error", "payload_not_applicable", "shape_empty", "shape_json_object",
    "shape_form_pairs", "shape_non_object", "shape_unparsable", "unknown_unsupported_key",
    "unknown_unsupported_value_shape", "unknown_unreadable_payload", "unknown_unsupported_method",
    "unknown_unparsable", "query_known", "query_unknown", "body_known", "body_unknown",
)


_PRIVATE_OUTLINE_SWITCH = "HERMES_LIST_RESPONSE_SCHEMA_OUTLINE_ONCE"
_PRIVATE_OUTLINE_PATH = "HERMES_LIST_RESPONSE_SCHEMA_OUTLINE_PATH"
_PRIVATE_OUTLINE_MAX_RECORDS = 3
_PRIVATE_OUTLINE_MAX_BODY_BYTES = 1 << 20
_PRIVATE_OUTLINE_MAX_UNSAFE_KEYS = 128
_PRIVATE_OUTLINE_MAX_UNSAFE_SCAN_KEYS = _PRIVATE_OUTLINE_MAX_UNSAFE_KEYS + 1
_PRIVATE_OUTLINE_TMP_ROOT = Path("/private/tmp")
_PRIVATE_OUTLINE_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def _safe_private_outline_target():
    """Create this-run's private parent and return its new target, or None."""
    if ex.ENVIRONMENT != "TEST":
        return None
    if os.getenv(_PRIVATE_OUTLINE_SWITCH, "").strip().lower() not in {"1", "true", "yes", "on"}:
        return None
    raw_path = os.getenv(_PRIVATE_OUTLINE_PATH, "").strip()
    if not raw_path:
        return None
    target = Path(raw_path)
    root = _PRIVATE_OUTLINE_TMP_ROOT
    if (
        not target.is_absolute() or target.parent.parent != root or os.path.lexists(target)
        or target.is_relative_to(PROJECT_ROOT.resolve())
    ):
        return None
    try:
        for ancestor in (root.parent, root):
            info = os.lstat(ancestor)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                return None
        os.mkdir(target.parent, 0o700)
        parent_info = os.lstat(target.parent)
        if (
            stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode)
            or parent_info.st_uid != os.getuid() or stat.S_IMODE(parent_info.st_mode) != 0o700
        ):
            return None
        if os.path.lexists(target):
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    return target


def _private_outline_target():
    """Compatibility alias for tests; this has no effect outside explicit TEST mode."""
    return _safe_private_outline_target()


def _json_outline_type(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _response_schema_outline_v1(payload, observer_source):
    """Pure, value-free schema outline for a parsed response JSON object."""
    if observer_source not in {"cdp", "page_event"} or not isinstance(payload, dict):
        return None
    unsafe_key_count = 0
    unsafe_key_count_capped = False

    def note_key(key):
        nonlocal unsafe_key_count, unsafe_key_count_capped
        if isinstance(key, str) and _PRIVATE_OUTLINE_KEY_RE.fullmatch(key):
            return True
        if unsafe_key_count < _PRIVATE_OUTLINE_MAX_UNSAFE_KEYS:
            unsafe_key_count += 1
        else:
            unsafe_key_count_capped = True
        return False

    root_items = []
    object_children = []
    root_exceeded = False
    for key, value in payload.items():
        if len(root_items) >= 32:
            root_exceeded = True
            note_key(key)
            break
        note_key(key)
        root_items.append((key, value))
        if isinstance(value, dict):
            if len(object_children) >= 8:
                root_exceeded = True
                break
            object_children.append((key, value))
    if root_exceeded:
        return {
            "schema_version": "response_schema_outline_v1", "observer_source": observer_source,
            "unsafe_key_count": unsafe_key_count, "unsafe_key_count_capped": unsafe_key_count_capped,
            "outline_status": "truncated", "limit_exceeded": True,
        }
    entry_count = len(root_items)
    for _, child in object_children:
        if len(child) > 64 or entry_count + len(child) > 128:
            for scanned_keys, key in enumerate(child, start=1):
                note_key(key)
                if unsafe_key_count_capped or scanned_keys >= _PRIVATE_OUTLINE_MAX_UNSAFE_SCAN_KEYS:
                    unsafe_key_count_capped = True
                    break
            return {
                "schema_version": "response_schema_outline_v1", "observer_source": observer_source,
                "unsafe_key_count": unsafe_key_count, "unsafe_key_count_capped": unsafe_key_count_capped,
                "outline_status": "truncated", "limit_exceeded": True,
            }
        entry_count += len(child)
        for key in child:
            note_key(key)
    base = {
        "schema_version": "response_schema_outline_v1",
        "observer_source": observer_source,
        "unsafe_key_count": unsafe_key_count,
        "unsafe_key_count_capped": unsafe_key_count_capped,
    }
    root = [
        {"key": key, "type": _json_outline_type(value)}
        for key, value in root_items
        if isinstance(key, str) and _PRIVATE_OUTLINE_KEY_RE.fullmatch(key)
    ]
    children = []
    for key, child in object_children:
        if not isinstance(key, str) or not _PRIVATE_OUTLINE_KEY_RE.fullmatch(key):
            continue
        children.append({
            "path": [key],
            "keys": sorted([
                {"key": child_key, "type": _json_outline_type(child_value)}
                for child_key, child_value in child.items()
                if isinstance(child_key, str) and _PRIVATE_OUTLINE_KEY_RE.fullmatch(child_key)
            ], key=lambda item: item["key"]),
        })
    return {
        **base,
        "outline_status": "complete",
        "limit_exceeded": False,
        "root": sorted(root, key=lambda item: item["key"]),
        "object_children": sorted(children, key=lambda item: item["path"]),
    }


class _PrivateOutlineBatch:
    def __init__(self, target):
        self.target = target
        self.records = []
        self.overflow_count = 0
        self.write_attempted = False

    def add(self, record):
        if not isinstance(record, dict):
            return
        if len(self.records) >= _PRIVATE_OUTLINE_MAX_RECORDS:
            self.overflow_count = min(self.overflow_count + 1, _PRIVATE_OUTLINE_MAX_RECORDS)
            return
        self.records.append(record)

    def write_once(self):
        if self.write_attempted or self.target is None or not self.records:
            return False
        self.write_attempted = True
        temporary = None
        temporary_created = False
        parent_fd = None
        fd = None
        installed_identity = None
        result = False
        try:
            parent_info = os.lstat(self.target.parent)
            if (
                stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode)
                or parent_info.st_uid != os.getuid() or stat.S_IMODE(parent_info.st_mode) != 0o700
                or os.path.lexists(self.target)
            ):
                return False
            payload = json.dumps({
                "schema_version": "response_schema_outline_batch_v1",
                "outlines": self.records,
                "outline_overflow_count": self.overflow_count,
            }, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
            parent_fd = os.open(self.target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            temporary = f".{self.target.name}.tmp"
            fd = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd
            )
            temporary_created = True
            os.fchmod(fd, 0o600)
            installed_identity = os.fstat(fd)
            with os.fdopen(fd, "wb") as handle:
                fd = None
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary, self.target.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd, follow_symlinks=False)
            target_info = os.lstat(self.target)
            valid_target = (
                stat.S_ISREG(target_info.st_mode) and stat.S_IMODE(target_info.st_mode) == 0o600
                and (target_info.st_dev, target_info.st_ino) == (installed_identity.st_dev, installed_identity.st_ino)
            )
            if not valid_target:
                # A path check and unlink cannot be atomic against a same-UID
                # replacement.  Fail closed and preserve the unknown path.
                return False
            result = True
        except Exception:
            result = False
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if temporary_created and temporary is not None and parent_fd is not None:
                try:
                    os.unlink(temporary, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
            if parent_fd is not None:
                try:
                    os.fsync(parent_fd)
                except OSError:
                    pass
                try:
                    os.close(parent_fd)
                except OSError:
                    pass
        return result


class _ListRequestObserver(list):
    """记录列表请求的 HTTP 状态，并持有 CDP 会话引用避免被回收。

    只保留状态码和布尔结果，不保存 URL、请求体、响应正文或筛选原文。
    """

    def __init__(self, *args, private_outline_batch=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.keepalive = None
        self.carried_target_filter = False
        self.response_contains_target_channel = None
        self.target_filter_field = None
        self.target_filter_field_is_channel = None
        self.exact_required_filter_request_count = 0
        self.exact_required_filter_2xx_count = 0
        self.exact_required_filter_success_records = []
        self.exact_required_filter_structure_invalid_count = 0
        self.success_records = []
        self.request_filter_states = {}
        self.request_context_profiles = {}
        self.target_filter_context_profiles = []
        self.complete_unfiltered_context_profiles = []
        self.target_absent_2xx_count = 0
        self.target_absent_structure_invalid_count = 0
        self.list_request_count = 0
        self.target_absent_request_count = 0
        self.target_filtered_request_count = 0
        self.unknown_request_count = 0
        self.page_event_handlers = []
        self.diagnostic_counts = {key: 0 for key in _LIST_DIAGNOSTIC_KEYS}
        self.structure_diagnostic_counts = {key: 0 for key in _LIST_STRUCTURE_BUCKETS}
        self.private_outline_batch = private_outline_batch


# 后台列表接口的实际路径是 /backend/cloudTrial/appInfo/getAppInfoList，
# 不含 getList；只匹配 getList 会把真实请求全部漏掉。
_LIST_URL_MARKERS = ("getAppInfoList", "getList")


def _is_list_request_url(url):
    raw = url or ""
    if any(marker in raw for marker in _LIST_URL_MARKERS):
        return True
    return "/backend/" in raw and raw.split("?")[0].endswith("List")


def _as_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "ignore")
    return str(value)


def _text_contains_target(text, target):
    if not target or not text:
        return False
    if target in text:
        return True
    encoded = quote(target, safe="")
    if encoded and encoded in text:
        return True
    plus_encoded = quote_plus(target)
    return bool(plus_encoded and plus_encoded in text)


def _field_path_is_channel(path):
    if not path or path == "raw_text_only":
        return False
    return "channel" in path.lower() or "渠道" in path


def _json_paths_containing_target(obj, target, prefix=""):
    hits = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            hits.extend(_json_paths_containing_target(value, target, path))
    elif isinstance(obj, list):
        list_path = f"{prefix}[]" if prefix else "[]"
        for value in obj:
            hits.extend(_json_paths_containing_target(value, target, list_path))
    elif isinstance(obj, str) and prefix and _text_contains_target(obj, target):
        hits.append(prefix)
    return hits


def _form_keys_containing_target(raw, target):
    hits = []
    try:
        pairs = parse_qsl(_as_text(raw), keep_blank_values=True)
    except Exception:
        return hits
    for key, value in pairs:
        if _text_contains_target(value, target):
            hits.append(str(key))
    return hits


def _pick_filter_location(json_paths, query_keys, raw_hit):
    channel_json = [path for path in json_paths if _field_path_is_channel(path)]
    if channel_json:
        return {"field": channel_json[0], "is_channel": True}
    channel_query = [key for key in query_keys if _field_path_is_channel(key)]
    if channel_query:
        return {"field": f"query.{channel_query[0]}", "is_channel": True}
    if json_paths:
        return {"field": json_paths[0], "is_channel": False}
    if query_keys:
        return {"field": f"query.{query_keys[0]}", "is_channel": False}
    if raw_hit:
        return {"field": "raw_text_only", "is_channel": False}
    return None


def _locate_target_filter_field(url, post_data, target_filter):
    """Return field path / query key / raw_text_only, never the target value."""
    target = (target_filter or "").strip() if isinstance(target_filter, str) else ""
    if not target:
        return None
    url_text = _as_text(url)
    body_text = _as_text(post_data)
    query_keys = []
    try:
        query_keys = _form_keys_containing_target(urlparse(url_text).query, target)
    except Exception:
        query_keys = []
    json_paths = []
    if body_text:
        try:
            loaded = json.loads(body_text)
        except Exception:
            loaded = None
        if loaded is not None:
            json_paths = _json_paths_containing_target(loaded, target)
        else:
            json_paths = _form_keys_containing_target(body_text, target)
    raw_hit = _text_contains_target(url_text + body_text, target)
    return _pick_filter_location(json_paths, query_keys, raw_hit)


_LIST_PAGING_KEYS = {"pagenum", "pagenumber", "page", "pagesize", "size", "current", "limit", "sort", "order", "orderby"}
_LIST_FILTER_KEYS = {
    "channelidlist", "channelname", "channelnames", "channelid", "appname", "applicationname", "appid", "appids",
    "appstatus", "appshorturl", "applongurl", "placelist", "categorylist", "balancetype", "id",
    "baseplatform", "creator", "starttime", "endtime", "status", "type",
}
_LIST_PLATFORM_CONTEXT_KEY = "platformtype"


def _is_valid_list_platform_context(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value in {0, 1, 2}
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip()):
        try:
            return int(value.strip()) in {0, 1, 2}
        except ValueError:
            return False
    return False


def _list_request_context_profile(url, post_data, payload_known=True):
    """Return only an in-memory normalized platform category, else None.

    No request values, keys, or raw payloads escape this function.  Any
    unregistered dimension is deliberately unreadable rather than fingerprinted.
    """
    if not payload_known:
        return None

    def normalized(key):
        return re.sub(r"[^a-z0-9]", "", str(key or "").lower())

    fields = []
    try:
        fields.extend((normalized(key), value) for key, value in parse_qsl(urlparse(_as_text(url)).query, keep_blank_values=True))
    except Exception:
        return None
    raw = _as_text(post_data)
    if raw:
        try:
            body = json.loads(raw)
        except Exception:
            try:
                form = parse_qsl(raw, keep_blank_values=True)
            except Exception:
                return None
            if not form:
                return None
            fields.extend((normalized(key), value) for key, value in form)
        else:
            if not isinstance(body, dict):
                return None
            fields.extend((normalized(key), value) for key, value in body.items())
    platforms = []
    for key, value in fields:
        if key == _LIST_PLATFORM_CONTEXT_KEY:
            platforms.append(value)
        elif key not in _LIST_PAGING_KEYS and key not in _LIST_FILTER_KEYS:
            return None
    if len(platforms) != 1 or not _is_valid_list_platform_context(platforms[0]):
        return None
    value = platforms[0]
    return int(value.strip()) if isinstance(value, str) else value


def _list_context_relation(narrow_profiles, unfiltered_profiles):
    """Compare in-memory profiles and return the sole public fixed enum."""
    narrow = list(narrow_profiles or [])
    unfiltered = list(unfiltered_profiles or [])
    if len(narrow) != 1 or not unfiltered or any(profile not in {0, 1, 2} for profile in narrow + unfiltered):
        return "unreadable"
    if any(profile != narrow[0] for profile in unfiltered):
        return "different"
    return "equal"


def _list_request_filter_state(url, post_data):
    """Classify one list request without retaining any request values.

    target_absent is deliberately conservative: all parsed fields must be known
    pagination/filter fields and every filter field must be empty.  Anything else
    is unknown and cannot unlock the post-reset restore gate.
    """
    def normalized(key):
        return re.sub(r"[^a-z0-9]", "", str(key or "").lower())

    def empty(value):
        return (
            value is None or value == "" or value == [] or value == {}
            or (isinstance(value, str) and value in {"[]", "null"})
        )

    fields = []
    try:
        query = parse_qsl(urlparse(_as_text(url)).query, keep_blank_values=True)
    except Exception:
        return "unknown"
    fields.extend((normalized(key), value) for key, value in query)
    raw = _as_text(post_data)
    if raw:
        try:
            body = json.loads(raw)
        except Exception:
            try:
                form = parse_qsl(raw, keep_blank_values=True)
            except Exception:
                return "unknown"
            if not form:
                return "unknown"
            fields.extend((normalized(key), value) for key, value in form)
        else:
            if not isinstance(body, dict):
                return "unknown"
            fields.extend((normalized(key), value) for key, value in body.items())
    if not fields:
        return "target_absent"
    for key, value in fields:
        if key == _LIST_PLATFORM_CONTEXT_KEY:
            if not _is_valid_list_platform_context(value):
                return "unknown"
            continue
        if key in _LIST_FILTER_KEYS and not empty(value):
            return "target_filtered"
        if key not in _LIST_PAGING_KEYS and key not in _LIST_FILTER_KEYS:
            return "unknown"
        if key in _LIST_FILTER_KEYS and not empty(value):
            return "target_filtered"
    return "target_absent"


def _list_payload_shape(post_data):
    raw = _as_text(post_data)
    if not raw:
        return "empty"
    try:
        return "json-object" if isinstance(json.loads(raw), dict) else "non-object"
    except Exception:
        if "=" not in raw:
            return "unparsable"
        try:
            return "form-pairs" if parse_qsl(raw, keep_blank_values=True) else "unparsable"
        except Exception:
            return "unparsable"


def _list_unknown_reason(url, post_data, payload_known, method):
    if not payload_known:
        return "unreadable-payload"
    if _list_request_filter_state(url, post_data) != "unknown":
        return None
    if method not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"}:
        return "unsupported-method"
    shape = _list_payload_shape(post_data)
    if shape == "unparsable":
        return "unparsable"
    if shape == "non-object":
        return "unsupported-value-shape"
    return "unsupported-key"


def _note_list_request_diagnostic(observations, url, post_data, *, source, payload_state, payload_known, method):
    """Aggregate fixed diagnostic buckets only; discard all request text immediately."""
    counts = observations.diagnostic_counts
    counts["source_" + source] += 1
    counts["payload_" + payload_state] += 1
    counts["shape_" + _list_payload_shape(post_data).replace("-", "_")] += 1
    try:
        parse_qsl(urlparse(_as_text(url)).query, keep_blank_values=True)
        counts["query_known"] += 1
    except Exception:
        counts["query_unknown"] += 1
    counts["body_known" if payload_known else "body_unknown"] += 1
    reason = _list_unknown_reason(url, post_data, payload_known, method)
    if reason is not None:
        counts["unknown_" + reason.replace("-", "_")] += 1


def _request_carries_target_filter(url, post_data, target_filter):
    """Return whether a list request payload contains the target filter value.

    The function only returns a boolean. It does not keep or return the URL,
    query string, request body, or channel text.
    """
    return _locate_target_filter_field(url, post_data, target_filter) is not None


_EXACT_TERMINAL_FILTER_KEYS = {
    "channel": {"channelname", "channelnames"},
    "app": {"appname", "applicationname"},
}


def _exact_terminal_filter_fields(url, post_data):
    """Parse only registered top-level list fields; discard values after the check."""
    def normalized(key):
        return re.sub(r"[^a-z0-9]", "", str(key or "").lower())

    fields = []
    try:
        fields.extend((normalized(key), value) for key, value in parse_qsl(
            urlparse(_as_text(url)).query, keep_blank_values=True
        ))
    except Exception:
        return None
    raw = _as_text(post_data)
    if not raw:
        return fields
    try:
        body = json.loads(raw)
    except Exception:
        try:
            parsed = parse_qsl(raw, keep_blank_values=True)
        except Exception:
            return None
        if not parsed:
            return None
        fields.extend((normalized(key), value) for key, value in parsed)
        return fields
    if not isinstance(body, dict):
        return None
    fields.extend((normalized(key), value) for key, value in body.items())
    return fields


def _exact_terminal_filter_value(value, expected):
    if isinstance(value, str):
        return value == expected
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], str):
        return value[0] == expected
    return False


def _request_carries_exact_terminal_filters(url, post_data, channel_name, app_name):
    """Prove both exact filters using only registered fields and values in memory."""
    if not isinstance(channel_name, str) or not channel_name or not isinstance(app_name, str) or not app_name:
        return False
    fields = _exact_terminal_filter_fields(url, post_data)
    if fields is None or _list_request_context_profile(url, post_data, payload_known=True) not in {0, 1, 2}:
        return False
    seen = {"channel": 0, "app": 0}
    allowed_exact = _EXACT_TERMINAL_FILTER_KEYS["channel"] | _EXACT_TERMINAL_FILTER_KEYS["app"]
    for key, value in fields:
        if key in _EXACT_TERMINAL_FILTER_KEYS["channel"]:
            if not _exact_terminal_filter_value(value, channel_name):
                return False
            seen["channel"] += 1
        elif key in _EXACT_TERMINAL_FILTER_KEYS["app"]:
            if not _exact_terminal_filter_value(value, app_name):
                return False
            seen["app"] += 1
        elif key == _LIST_PLATFORM_CONTEXT_KEY or key in _LIST_PAGING_KEYS:
            continue
        elif key in _LIST_FILTER_KEYS:
            # A hidden nonempty list filter weakens the exact-query proof.
            if value not in (None, "", [], {}, "[]", "null"):
                return False
        elif key not in allowed_exact:
            return False
    return seen == {"channel": 1, "app": 1}


def _filter_field_rank(field, is_channel):
    if not field:
        return 0
    if is_channel:
        return 3
    if field != "raw_text_only":
        return 2
    return 1


def _detach_list_response_observer(observations):
    """Release observers and atomically flush any enabled private value-free outline."""
    for page, event, handler in getattr(observations, "page_event_handlers", []) or []:
        off = getattr(page, "off", None)
        if callable(off):
            try:
                off(event, handler)
            except Exception:
                pass
    if observations is not None:
        observations.page_event_handlers = []
    sidecar = getattr(observations, "keepalive", None)
    detach = getattr(sidecar, "detach", None)
    if callable(detach):
        try:
            detach()
        except Exception:
            pass
    if observations is not None:
        try:
            observations.keepalive = None
        except Exception:
            pass


def _redacted_text_summary(value):
    text = _as_text(value)
    return {
        "present": bool(text),
        "length": len(text),
        "sha256_16": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] if text else None,
    }


_SAVE_BODY_FETCH_STATES = {"available", "missing", "read_error", "base64_decoded", "base64_decode_error"}
_SAVE_BODY_PARSE_STATES = {"not_applicable", "json_unparsable", "json_non_object", "envelope_missing", "envelope_classified"}


def _save_response_body(result):
    """Return response text and fixed fetch state; never retain the CDP result."""
    if not isinstance(result, dict) or "body" not in result:
        return "", "missing"
    body = result.get("body")
    if result.get("base64Encoded"):
        try:
            raw = base64.b64decode(_as_text(body), validate=True)
            return raw.decode("utf-8"), "base64_decoded"
        except Exception:
            return "", "base64_decode_error"
    return _as_text(body), "available"


def _save_business_summary_detail(body):
    """Classify an evidenced generic envelope without retaining its text."""
    try:
        payload = json.loads(_as_text(body))
    except Exception:
        return {"outcome": "unreadable", "code": _redacted_text_summary(""), "message": _redacted_text_summary("")}, "json_unparsable"
    if not isinstance(payload, dict):
        return {"outcome": "unreadable", "code": _redacted_text_summary(""), "message": _redacted_text_summary("")}, "json_non_object"
    header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
    status = header.get("status", payload.get("status"))
    code = header.get("code", payload.get("code"))
    message = header.get("message", header.get("msg", payload.get("message", payload.get("msg", ""))))
    success = payload.get("success")
    if str(status) == "200" or success is True:
        outcome = "success"
    elif status is not None or success is False or code is not None:
        outcome = "rejected"
    else:
        outcome = "unreadable"
    parse_state = "envelope_classified" if outcome != "unreadable" else "envelope_missing"
    return {"outcome": outcome, "code": _redacted_text_summary(code), "message": _redacted_text_summary(message)}, parse_state


def _save_business_summary(body):
    return _save_business_summary_detail(body)[0]


def _save_path_hash(url):
    path = urlparse(_as_text(url)).path or ""
    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:16] if path else None


def _redacted_summary_projection(summary):
    summary = summary if isinstance(summary, dict) else {}
    length = summary.get("length")
    digest = summary.get("sha256_16")
    return {
        "present": summary.get("present") is True,
        "length": length if isinstance(length, int) and length >= 0 else 0,
        "sha256_16": digest if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{16}", digest) else None,
    }


def _save_candidate_evidence(candidate):
    """Project a candidate to the sole fields permitted in logs and decisions."""
    candidate = candidate if isinstance(candidate, dict) else {}
    business = candidate.get("business") if isinstance(candidate.get("business"), dict) else {}
    business_outcome = business.get("outcome")
    if business_outcome not in {"success", "rejected", "unreadable"}:
        business_outcome = "unreadable"
    method = candidate.get("method")
    path_hash = candidate.get("path_hash")
    status = candidate.get("http_status")
    body = candidate.get("response_body") if isinstance(candidate.get("response_body"), dict) else {}
    fetch_state = body.get("fetch_state")
    parse_state = body.get("parse_state")
    return {
        "method": method if method in {"POST", "PUT", "PATCH", "DELETE"} else None,
        "path_hash": path_hash if isinstance(path_hash, str) and re.fullmatch(r"[0-9a-f]{16}", path_hash) else None,
        "http_status": status if isinstance(status, int) else None,
        "response_body": {
            "fetch_state": fetch_state if fetch_state in _SAVE_BODY_FETCH_STATES else "missing",
            "parse_state": parse_state if parse_state in _SAVE_BODY_PARSE_STATES else "not_applicable",
        },
        "business": {
            "outcome": business_outcome,
            "code": _redacted_summary_projection(business.get("code")),
            "message": _redacted_summary_projection(business.get("message")),
        },
    }


def _save_click_diagnostic(decision):
    """One stable, machine-readable and redacted log record for every outcome."""
    decision = decision if isinstance(decision, dict) else {}
    outcome = decision.get("outcome") if isinstance(decision.get("outcome"), str) else "unreadable"
    if outcome in {"no_candidate", "ambiguous_candidate"}:
        count = decision.get("candidate_count")
        return {"outcome": outcome, "candidate_count": count if isinstance(count, int) and count >= 0 else 0}
    if outcome == "observer_unavailable":
        return {"outcome": outcome}
    evidence = _save_candidate_evidence(decision)
    return {"outcome": outcome, **evidence}


def _log_save_click_observation(decision):
    print("[create_app] save_observation=" + json.dumps(
        _save_click_diagnostic(decision), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ))


def _apply_save_page_response_fallback(observer, candidate):
    """Read one strictly associated page response only after CDP body unavailability."""
    if observer is None or not isinstance(candidate, dict) or candidate.get("page_fallback_attempted"):
        return
    body_state = candidate.get("response_body") if isinstance(candidate.get("response_body"), dict) else {}
    if body_state.get("fetch_state") not in {"missing", "read_error"}:
        return
    matches = [
        item for item in getattr(observer, "page_responses", [])
        if item.get("method") == candidate.get("method")
        and item.get("path_hash") == candidate.get("path_hash")
        and item.get("http_status") == candidate.get("http_status")
    ]
    if getattr(observer, "page_response_overflow", False) or len(matches) != 1:
        if matches or getattr(observer, "page_response_overflow", False):
            candidate["page_fallback_attempted"] = True
        return
    candidate["page_fallback_attempted"] = True
    response = matches[0].get("response")
    try:
        text = response.text() if response is not None and hasattr(response, "text") else None
    except Exception:
        return
    if text is None:
        return
    business, parse_state = _save_business_summary_detail(text)
    candidate["response_body"] = {"fetch_state": "available", "parse_state": parse_state}
    candidate["business"] = business
    matches[0]["response"] = None


def _save_click_observation(observer):
    """Return a redacted atomic decision; never promote 2xx alone to success."""
    if observer is None:
        return {"outcome": "observer_unavailable"}
    candidates = list(getattr(observer, "candidates", {}).values())
    if len(candidates) != 1:
        return {"outcome": "no_candidate" if not candidates else "ambiguous_candidate", "candidate_count": len(candidates)}
    candidate = candidates[0]
    _apply_save_page_response_fallback(observer, candidate)
    evidence = _save_candidate_evidence(candidate)
    if not candidate.get("completed"):
        return {"outcome": "response_timeout", **evidence}
    status = evidence["http_status"]
    if not isinstance(status, int) or not 200 <= status < 300:
        return {"outcome": "non_2xx", **evidence}
    if evidence["business"]["outcome"] != "success":
        return {"outcome": "business_" + evidence["business"]["outcome"], **evidence}
    return {"outcome": "success", **evidence}


def _save_page_fallback_pending(observer):
    if observer is None or getattr(observer, "page_response_overflow", False):
        return False
    candidates = list(getattr(observer, "candidates", {}).values())
    if len(candidates) != 1:
        return False
    candidate = candidates[0]
    body = candidate.get("response_body") if isinstance(candidate.get("response_body"), dict) else {}
    if not candidate.get("completed") or body.get("fetch_state") not in {"missing", "read_error"}:
        return False
    if candidate.get("page_fallback_attempted"):
        return False
    return not any(
        item.get("method") == candidate.get("method")
        and item.get("path_hash") == candidate.get("path_hash")
        and item.get("http_status") == candidate.get("http_status")
        for item in getattr(observer, "page_responses", [])
    )


def _wait_for_save_click_observation(page, observer, timeout_ms=6000, poll_interval_ms=150):
    deadline = time.monotonic() + (timeout_ms / 1000)
    while time.monotonic() < deadline:
        decision = _save_click_observation(observer)
        if decision.get("outcome") not in {"no_candidate", "response_timeout"} and not (
            decision.get("outcome") == "business_unreadable" and _save_page_fallback_pending(observer)
        ):
            return decision
        page.wait_for_timeout(poll_interval_ms)
    return _save_click_observation(observer)


def _wait_for_switch_action_observation(page, observer, timeout_ms=6000, poll_interval_ms=150):
    """Keep one bounded action window; accept one new confirm box or direct dispatch.

    A response is intentionally not accepted early: the direct path must prove no
    confirmation box appeared anywhere in the bounded window.  A box after a
    request, multiple boxes, an invalid confirm button, or a second box is an
    attribution ambiguity and therefore fail-closed.
    """
    deadline = time.monotonic() + (timeout_ms / 1000)
    saw_box = False
    confirm_clicked = False
    confirm_closed = False
    while time.monotonic() < deadline:
        box_count = _visible_message_box_count(page)
        if box_count is None:
            observer.confirmation_trace = "unreadable"
            return {"outcome": "message_box_unreadable"}
        candidate_count = len(getattr(observer, "candidates", {}) or {})
        if candidate_count > 1:
            return {"outcome": "ambiguous_candidate", "candidate_count": candidate_count}
        if box_count:
            # The original box may remain visible through its close animation;
            # only a box before it or after it closed is ambiguous.
            if box_count != 1:
                observer.confirmation_trace = "multiple_visible"
                return {"outcome": "message_box_ambiguous"}
            if confirm_closed:
                observer.confirmation_trace = "reappeared"
                return {"outcome": "message_box_ambiguous"}
            if not saw_box:
                if candidate_count:
                    observer.confirmation_trace = "request_before_confirmation"
                    return {"outcome": "message_box_ambiguous"}
                saw_box = True
                observer.confirmation_trace = "unique_seen"
                if not _click_unique_new_message_box_confirm(page):
                    observer.confirmation_trace = "confirm_button_rejected"
                    return {"outcome": "message_box_confirm_rejected"}
                confirm_clicked = True
                observer.confirmation_clicked = True
        elif confirm_clicked:
            confirm_closed = True
            observer.confirmation_closed = True
            observer.confirmation_trace = "unique_clicked_closed"
        page.wait_for_timeout(poll_interval_ms)
    if saw_box and (not confirm_clicked or not confirm_closed):
        observer.confirmation_trace = "unclosed"
        return {"outcome": "message_box_ambiguous"}
    if not saw_box:
        observer.confirmation_trace = "none_seen"
    return _save_click_observation(observer)


def _enable_action_observation_diagnostic(observer, decision):
    """Value-free enable-only observer facts; never upgrade an observation outcome."""
    cdp = getattr(observer, "cdp_diagnostics", None)
    page = getattr(observer, "page_diagnostics", None)
    cdp_projection = cdp.projection() if isinstance(cdp, _ActionWriteDiagnostics) else _ActionWriteDiagnostics().projection()
    page_projection = page.projection() if isinstance(page, _ActionWriteDiagnostics) else _ActionWriteDiagnostics().projection()
    # Record directional visibility after the window has closed. A double miss is
    # represented by both accepted counts being zero, without inventing an event.
    cdp_projection["observer_event_unseen"] = int(cdp_projection["accepted_write"] == 0 and page_projection["accepted_write"] > 0)
    page_projection["observer_event_unseen"] = int(page_projection["accepted_write"] == 0 and cdp_projection["accepted_write"] > 0)
    trace = getattr(observer, "confirmation_trace", "not_inferable")
    if trace not in {"none_seen", "unique_seen", "unique_clicked_closed", "multiple_visible", "reappeared", "request_before_confirmation", "confirm_button_rejected", "unclosed", "unreadable"}:
        trace = "not_inferable"
    switch_state = getattr(observer, "post_action_switch_state", "not_inferable")
    if switch_state not in _NARROW_ROW_SWITCH_STATES:
        switch_state = "not_inferable"
    return {
        "observation": _save_click_diagnostic(decision),
        "request_observer": {"cdp": cdp_projection, "page_event": page_projection},
        "confirmation": {
            "trace": trace,
            "unique_confirm_clicked": bool(getattr(observer, "confirmation_clicked", False)),
            "confirmation_closed": bool(getattr(observer, "confirmation_closed", False)),
        },
        "post_action_switch_state": switch_state,
    }


def _log_enable_click_observation(observer, decision):
    print("[create_app] enable_observation=" + json.dumps(
        _enable_action_observation_diagnostic(observer, decision), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ))


def _attach_save_click_observer(page):
    """Observe one post-click same-origin non-list write request by request ID only."""
    observer = _SaveClickObserver()
    try:
        session = page.context.new_cdp_session(page)
        session.send("Network.enable")
        origin = urlparse(BASE_URL_H5).netloc

        def _is_save_write(url, method):
            return (
                method in {"POST", "PUT", "PATCH", "DELETE"}
                and not _is_list_request_url(url)
                and urlparse(url).netloc == origin
            )

        def _on_request(params):
            if observer.detached:
                return
            data = params or {}
            request = data.get("request") or {}
            url = request.get("url") or ""
            method = str(request.get("method") or "").upper()
            request_id = data.get("requestId")
            if not observer.cdp_diagnostics.classify(
                url, method, origin, request_id_required=True, request_id=request_id
            ):
                return
            if request_id not in observer.candidates and len(observer.candidates) >= _SAVE_CANDIDATE_LIMIT:
                observer.candidate_overflow = True
                return
            observer.candidates[request_id] = {
                "method": method,
                "path_hash": _save_path_hash(url),
                "completed": False,
            }

        def _on_response(params):
            if observer.detached:
                return
            data = params or {}
            request_id = data.get("requestId")
            candidate = observer.candidates.get(request_id)
            if candidate is None:
                return
            response = data.get("response") or {}
            try:
                candidate["http_status"] = int(response.get("status") or 0)
            except (TypeError, ValueError):
                candidate["http_status"] = 0

        def _on_finished(params):
            if observer.detached:
                return
            request_id = (params or {}).get("requestId")
            candidate = observer.candidates.get(request_id)
            if candidate is None:
                return
            candidate["completed"] = True
            try:
                result = session.send("Network.getResponseBody", {"requestId": request_id})
                body, fetch_state = _save_response_body(result)
                business, parse_state = _save_business_summary_detail(body)
                candidate["response_body"] = {"fetch_state": fetch_state, "parse_state": parse_state}
                candidate["business"] = business
            except Exception:
                candidate["response_body"] = {"fetch_state": "read_error", "parse_state": "not_applicable"}
                candidate["business"] = {"outcome": "unreadable"}

        def _on_failed(params):
            if observer.detached:
                return
            candidate = observer.candidates.get((params or {}).get("requestId"))
            if candidate is not None:
                candidate["completed"] = True
                candidate["http_status"] = 0
                candidate["business"] = {"outcome": "unreadable"}

        session.on("Network.requestWillBeSent", _on_request)
        session.on("Network.responseReceived", _on_response)
        session.on("Network.loadingFinished", _on_finished)
        session.on("Network.loadingFailed", _on_failed)

        def _on_page_request(request):
            try:
                if observer.detached:
                    return
                url = getattr(request, "url", "") or ""
                method = str(getattr(request, "method", "") or "").upper()
                if not observer.page_diagnostics.classify(url, method, origin):
                    return
                if observer.page_write_request_count >= _SAVE_PAGE_RESPONSE_LIMIT:
                    observer.page_response_overflow = True
                    return
                observer.page_write_request_count += 1
            except Exception:
                return

        def _on_page_response(response):
            try:
                if observer.detached:
                    return
                # Playwright may wrap the same protocol request in distinct Python
                # objects across callbacks. Re-project response.request immediately;
                # neither its URL nor an object identity is retained.
                request = getattr(response, "request", None)
                if request is None or observer.page_write_request_count <= 0:
                    return
                url = getattr(request, "url", "") or ""
                method = str(getattr(request, "method", "") or "").upper()
                if not _is_save_write(url, method):
                    return
                if len(observer.page_responses) >= _SAVE_PAGE_RESPONSE_LIMIT:
                    observer.page_response_overflow = True
                    return
                try:
                    status = int(getattr(response, "status", 0) or 0)
                except (TypeError, ValueError):
                    status = 0
                observer.page_responses.append({
                    "method": method,
                    "path_hash": _save_path_hash(url),
                    "http_status": status,
                    "response": response,
                })
            except Exception:
                return

        try:
            page.on("request", _on_page_request)
            page.on("response", _on_page_response)
            observer.page_event_handlers = [(page, "request", _on_page_request), (page, "response", _on_page_response)]
        except Exception:
            observer.page_event_handlers = []
        observer.keepalive = session
        return observer
    except Exception:
        return None


def _detach_save_click_observer(observer):
    if observer is not None:
        try:
            observer.detached = True
        except Exception:
            pass
    _detach_list_response_observer(observer)


def _body_exceeds_private_outline_limit(body, *, base64_encoded=False):
    text = _as_text(body)
    if base64_encoded:
        if len(text) > ((_PRIVATE_OUTLINE_MAX_BODY_BYTES + 2) // 3) * 4:
            return True
        try:
            return len(base64.b64decode(text, validate=True)) > _PRIVATE_OUTLINE_MAX_BODY_BYTES
        except Exception:
            return False
    return len(text) > _PRIVATE_OUTLINE_MAX_BODY_BYTES or len(text.encode("utf-8")) > _PRIVATE_OUTLINE_MAX_BODY_BYTES


def _classify_list_structure_body(body, *, base64_encoded=False):
    """Return one fixed structure bucket and in-memory meta; never retain body text."""
    if body is None:
        return "response_body_missing", None
    text = _as_text(body)
    if base64_encoded:
        try:
            text = base64.b64decode(text, validate=True).decode("utf-8")
        except Exception:
            return "base64_decode_error", None
    if not text:
        return "response_body_missing", None
    try:
        payload = json.loads(text)
    except Exception:
        return "json_unparsable", None
    if not isinstance(payload, dict):
        return "json_non_object", None
    meta = _extract_list_structure(text)
    if not meta:
        return "required_structure_missing", None
    return "complete", meta


def _capture_private_structure_outline(observations, body, *, base64_encoded, observer_source):
    """Capture only a bounded value-free outline; failures remain invisible publicly."""
    batch = getattr(observations, "private_outline_batch", None)
    if batch is None or batch.target is None or body is None:
        return
    try:
        if _body_exceeds_private_outline_limit(body, base64_encoded=base64_encoded):
            batch.add({
                "schema_version": "response_schema_outline_v1", "observer_source": observer_source,
                "outline_status": "too_large_not_outlined", "body_size_capped": True,
            })
            return
        text = _as_text(body)
        if base64_encoded:
            text = base64.b64decode(text, validate=True).decode("utf-8")
        payload = json.loads(text)
        batch.add(_response_schema_outline_v1(payload, observer_source))
    except Exception:
        pass


def _record_target_absent_structure(observations, body, *, base64_encoded=False, read_error=False, observer_source="cdp"):
    """Record exactly one fixed bucket for one completed target-absent 2xx response."""
    observations.target_absent_2xx_count += 1
    if read_error:
        bucket, meta = "response_body_read_error", None
    else:
        bucket, meta = _classify_list_structure_body(body, base64_encoded=base64_encoded)
    observations.structure_diagnostic_counts[bucket] += 1
    if meta:
        meta = dict(meta)
        observations.success_records.append(meta)
    else:
        observations.target_absent_structure_invalid_count += 1
        if bucket == "required_structure_missing":
            _capture_private_structure_outline(
                observations, body, base64_encoded=base64_encoded, observer_source=observer_source
            )
    return meta


def _record_exact_terminal_filter_structure(observations, body, *, base64_encoded=False, read_error=False):
    """Record a completed exact dual-filter response without retaining its body."""
    observations.exact_required_filter_2xx_count += 1
    if read_error:
        bucket, meta = "response_body_read_error", None
    else:
        bucket, meta = _classify_list_structure_body(body, base64_encoded=base64_encoded)
    observations.structure_diagnostic_counts[bucket] += 1
    if meta:
        observations.exact_required_filter_success_records.append(dict(meta))
    else:
        observations.exact_required_filter_structure_invalid_count += 1
    return meta


def _attach_list_response_observer(page, target_filter=None, private_outline_batch=None, exact_terminal_filters=None):
    """只读网络观察器：列表请求、HTTP 状态、是否携带筛选、响应是否含目标。

    只记录状态码和布尔结果。响应正文只在内存中为已携带目标筛选的请求做
    包含判断，随后丢弃；不写磁盘、日志、JSON 或观察器属性。

    通过 CDP 接入的既有页面上，显式开一个 CDP 会话并启用 Network 域最可靠；
    失败再退回 Playwright 页面事件监听。两者都不可用时返回 None，调用方据此
    给出"未观察到请求"而不是失败。
    """
    observations = _ListRequestObserver(private_outline_batch=private_outline_batch)
    if exact_terminal_filters is not None:
        if not isinstance(exact_terminal_filters, tuple) or len(exact_terminal_filters) != 2:
            return None
        exact_channel_name, exact_app_name = exact_terminal_filters
        if not all(isinstance(value, str) and value for value in exact_terminal_filters):
            return None
    else:
        exact_channel_name = exact_app_name = None
    list_request_ids = set()
    carried_request_ids = set()
    exact_required_request_ids = set()
    list_status_by_id = {}

    def _note_response_contains_target(contained):
        if contained:
            observations.response_contains_target_channel = True
        elif observations.response_contains_target_channel is not True:
            observations.response_contains_target_channel = False

    def _note_filter_field(location):
        field = location.get("field")
        is_channel = bool(location.get("is_channel"))
        if _filter_field_rank(field, is_channel) <= _filter_field_rank(
            observations.target_filter_field,
            observations.target_filter_field_is_channel,
        ):
            return
        observations.target_filter_field = field
        observations.target_filter_field_is_channel = is_channel

    def _body_contains_target(body):
        return _request_carries_target_filter("", body, target_filter)

    def _mark_list_request(
        url, post_data, request_id=None, payload_known=True, source="cdp", payload_state="inline", method="POST"
    ):
        if not _is_list_request_url(url):
            return False
        if request_id is not None:
            list_request_ids.add(request_id)
            state = _list_request_filter_state(url, post_data) if payload_known else "unknown"
            observations.request_context_profiles[request_id] = _list_request_context_profile(url, post_data, payload_known)
            _note_list_request_diagnostic(
                observations, url, post_data, source=source, payload_state=payload_state,
                payload_known=payload_known, method=method,
            )
            observations.request_filter_states[request_id] = state
            observations.list_request_count += 1
            if state == "target_absent":
                observations.target_absent_request_count += 1
            elif state == "target_filtered":
                observations.target_filtered_request_count += 1
            else:
                observations.unknown_request_count += 1
        if (
            request_id is not None
            and exact_terminal_filters is not None
            and payload_known
            and _request_carries_exact_terminal_filters(url, post_data, exact_channel_name, exact_app_name)
        ):
            exact_required_request_ids.add(request_id)
            observations.exact_required_filter_request_count += 1
        location = _locate_target_filter_field(url, post_data, target_filter)
        if location is not None:
            if request_id is not None:
                observations.target_filter_context_profiles.append(
                    observations.request_context_profiles.get(request_id)
                )
            observations.carried_target_filter = True
            _note_filter_field(location)
            if request_id is not None:
                carried_request_ids.add(request_id)
        return True

    def _record_status(url, status, request_id=None):
        try:
            if request_id is not None:
                if request_id not in list_request_ids and not _is_list_request_url(url):
                    return
            elif not _is_list_request_url(url):
                return
            code = int(status or 0)
            observations.append(code)
            if request_id is not None:
                list_status_by_id[request_id] = code
        except Exception:
            pass

    try:
        session = page.context.new_cdp_session(page)
        session.send("Network.enable")

        def _inspect_cdp_body(request_id):
            status = list_status_by_id.get(request_id)
            need_target = request_id in carried_request_ids
            need_structure = (
                request_id in list_request_ids
                and observations.request_filter_states.get(request_id) == "target_absent"
                and status is not None
                and 200 <= int(status) < 300
            )
            need_exact_structure = (
                request_id in exact_required_request_ids
                and status is not None
                and 200 <= int(status) < 300
            )
            if not need_target and not need_structure and not need_exact_structure:
                return
            try:
                result = session.send("Network.getResponseBody", {"requestId": request_id})
            except Exception:
                if need_structure:
                    _record_target_absent_structure(observations, None, read_error=True)
                if need_exact_structure:
                    _record_exact_terminal_filter_structure(observations, None, read_error=True)
                return
            if not isinstance(result, dict) or "body" not in result:
                if need_structure:
                    _record_target_absent_structure(observations, None)
                if need_exact_structure:
                    _record_exact_terminal_filter_structure(observations, None)
                return
            body = result.get("body")
            try:
                if need_target:
                    target_body, target_bucket = _save_response_body(result)
                    if target_bucket in {"available", "base64_decoded"}:
                        _note_response_contains_target(_body_contains_target(target_body))
                if need_structure:
                    meta = _record_target_absent_structure(
                        observations, body, base64_encoded=bool(result.get("base64Encoded")), observer_source="cdp"
                    )
                    if meta:
                        meta["status"] = int(status)
                        observations.complete_unfiltered_context_profiles.append(
                            observations.request_context_profiles.get(request_id)
                        )
                if need_exact_structure:
                    _record_exact_terminal_filter_structure(
                        observations, body, base64_encoded=bool(result.get("base64Encoded"))
                    )
            finally:
                body = None

        def _on_request(params):
            try:
                data = params or {}
                request = data.get("request") or {}
                request_id = data.get("requestId")
                url = request.get("url") or ""
                post_data = request.get("postData") or ""
                method = str(request.get("method") or "").upper()
                payload_known = True
                payload_state = "not_applicable" if method in {"GET", "HEAD"} else "inline"
                if not post_data and request.get("hasPostData") and request_id:
                    try:
                        extra = session.send("Network.getRequestPostData", {"requestId": request_id}) or {}
                        if not isinstance(extra, dict) or "postData" not in extra:
                            payload_known = False
                            payload_state = "unavailable"
                        else:
                            post_data = extra.get("postData") or ""
                            payload_state = "fetched"
                    except Exception:
                        payload_known = False
                        payload_state = "read_error"
                _mark_list_request(
                    url, post_data, request_id, payload_known, "cdp", payload_state, method
                )
            except Exception:
                pass

        def _on_response(params):
            try:
                data = params or {}
                response = data.get("response") or {}
                _record_status(response.get("url"), response.get("status"), data.get("requestId"))
            except Exception:
                pass

        def _on_finished(params):
            try:
                _inspect_cdp_body((params or {}).get("requestId"))
            except Exception:
                pass

        def _on_failed(params):
            try:
                data = params or {}
                _record_status("", 0, data.get("requestId"))
            except Exception:
                pass

        session.on("Network.requestWillBeSent", _on_request)
        session.on("Network.responseReceived", _on_response)
        session.on("Network.loadingFinished", _on_finished)
        session.on("Network.loadingFailed", _on_failed)
        observations.keepalive = session
        return observations
    except Exception:
        pass

    try:
        def _on_request(request):
            try:
                method = str(getattr(request, "method", "") or "").upper()
            except Exception:
                method = ""
            payload_known = method in {"GET", "HEAD"}
            payload_state = "not_applicable" if payload_known else "unavailable"
            post_data = ""
            if method in {"POST", "PUT", "PATCH", "DELETE"}:
                try:
                    post_data = request.post_data
                    payload_known = post_data is not None
                    payload_state = "inline" if payload_known else "unavailable"
                    post_data = post_data or ""
                except Exception:
                    payload_known = False
                    payload_state = "read_error"
            _mark_list_request(
                getattr(request, "url", ""), post_data, id(request), payload_known,
                "page_event", payload_state, method,
            )

        def _on_response(response):
            url = getattr(response, "url", "") or ""
            status = getattr(response, "status", 0)
            request = getattr(response, "request", None)
            request_id = id(request) if request is not None else None
            _record_status(url, status, request_id)
            req_url = getattr(request, "url", url) if request is not None else url
            request_state = observations.request_filter_states.get(request_id, "unknown")
            if not _is_list_request_url(req_url or url) or request_state == "unknown":
                return
            try:
                post_data = request.post_data if request is not None else ""
            except Exception:
                return
            need_target = _request_carries_target_filter(req_url or url, post_data or "", target_filter)
            try:
                is_2xx = 200 <= int(status or 0) < 300
            except (TypeError, ValueError):
                is_2xx = False
            need_structure = is_2xx and observations.request_filter_states.get(request_id) == "target_absent"
            need_exact_structure = is_2xx and request_id in exact_required_request_ids
            if not need_target and not need_structure and not need_exact_structure:
                return
            body = None
            try:
                if hasattr(response, "text"):
                    body = response.text()
                elif hasattr(response, "body"):
                    raw = response.body()
                    body = raw.decode("utf-8", "ignore") if isinstance(raw, (bytes, bytearray)) else raw
            except Exception:
                if need_structure:
                    _record_target_absent_structure(observations, None, read_error=True)
                if need_exact_structure:
                    _record_exact_terminal_filter_structure(observations, None, read_error=True)
                return
            try:
                if body is None:
                    if need_structure:
                        _record_target_absent_structure(observations, None)
                    if need_exact_structure:
                        _record_exact_terminal_filter_structure(observations, None)
                    return
                if need_target:
                    _note_response_contains_target(_body_contains_target(body))
                if need_structure:
                    meta = _record_target_absent_structure(observations, body, observer_source="page_event")
                    if meta:
                        meta["status"] = int(status or 0)
                        observations.complete_unfiltered_context_profiles.append(
                            observations.request_context_profiles.get(request_id)
                        )
                if need_exact_structure:
                    _record_exact_terminal_filter_structure(observations, body)
            finally:
                body = None

        def _on_request_failed(request):
            _record_status(getattr(request, "url", ""), 0, id(request))

        page.on("request", _on_request)
        page.on("response", _on_response)
        page.on("requestfailed", _on_request_failed)
        observations.page_event_handlers = [
            (page, "request", _on_request),
            (page, "response", _on_response),
            (page, "requestfailed", _on_request_failed),
        ]
    except Exception:
        return None
    return observations


def _dismiss_stray_dropdowns(page):
    """用"点击外部"这个官方关闭路径收起残留下拉。

    不要直接改 style：那样会让 Element UI 内部的 visible 与 DOM 失步，
    下一次点 select 反而把下拉关掉，表现为"选了但列表没反应"。
    """
    try:
        page.evaluate("""() => {
          const event = new MouseEvent('click', {bubbles: true});
          document.dispatchEvent(event);
          document.body.dispatchEvent(new MouseEvent('click', {bubbles: true}));
          return true;
        }""")
        page.wait_for_timeout(300)
    except Exception:
        pass


def _search_list_by_channel(page, channel_name, return_detail=False, context_sink=None):
    """Select an exact channel in the list filter and verify the table refresh."""
    _reset_list_filters(page)
    previous_state = _read_pagination_state(page)
    detail = {
        "reset": True,
        "opened": False,
        "selected": False,
        "verify": False,
        "search_clicked": False,
        "search_scope": None,
        "filter_stable": False,
        "list_requests_before": None,
        "list_requests_after": None,
        "last_http_status": None,
        "elapsed_ms": None,
        "previous_row_count": previous_state.get("row_count"),
        "final_row_count": None,
        "table_changed": False,
        "reader_sees_target_channel": None,
        "reader_distinct_channels": None,
        "request_carried_target_filter": None,
        "response_contains_target_channel": None,
        "target_filter_field": None,
        "target_filter_field_is_channel": None,
    }
    opened = None
    selected = None
    # 有界重试：只重试"展开下拉 / 选中选项"这两步。上一次调用可能残留一个已
    # 展开的下拉，本次点击会被 Element UI 当成"再次点击"而收起，表现为
    # "选中值看着对、点搜索却没反应"。重试不改变任何匹配与稳定判定门槛。
    for attempt in range(2):
        opened = page.evaluate("""
    (channelName) => {
      const selects = Array.from(document.querySelectorAll('.el-select'))
        .filter(select => select.offsetParent !== null &&
          !select.closest('.el-dialog, .el-dialog__wrapper'));
      const labeled = selects.filter(select => {
        const item = select.closest('.el-form-item');
        const label = item && item.querySelector('.el-form-item__label');
        const input = select.querySelector('input');
        const metadata = input
          ? `${input.placeholder || ''} ${input.getAttribute('aria-label') || ''}`
          : '';
        return /渠道/.test(metadata) || Boolean(label && /渠道/.test(label.innerText || ''));
      });
      const editableSelects = selects.filter(select => select.querySelector('input.el-select__input'));
      const candidates = labeled.length === 1 ? labeled : (labeled.length === 0 ? editableSelects : []);
      if (candidates.length !== 1) {
        return {opened: false, labeled_count: labeled.length, candidate_count: candidates.length};
      }

      const target = candidates[0];
      const targetIndex = selects.indexOf(target);
      const visibleInput = target.querySelector('input.el-select__input') ||
        target.querySelector('input.el-input__inner');
      if (!visibleInput || visibleInput.disabled) return {opened: false};
      visibleInput.click();

      const searchInput = target.querySelector('input.el-select__input');
      if (searchInput && !searchInput.readOnly) {
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        setter.call(searchInput, channelName);
        searchInput.dispatchEvent(new Event('input', {bubbles: true}));
        searchInput.dispatchEvent(new Event('change', {bubbles: true}));
      }
      return {opened: true, select_index: targetIndex};
    }
    """, channel_name)
        if not opened or not opened.get("opened"):
            _dismiss_stray_dropdowns(page)
            continue
        detail["opened"] = True

        page.wait_for_timeout(300)
        selected = page.evaluate("""
    (channelName) => {
      const dropdowns = Array.from(document.querySelectorAll('.el-select-dropdown'))
        .filter(dropdown => dropdown.offsetParent !== null && dropdown.style.display !== 'none');
      if (dropdowns.length !== 1) return {selected: false, visible_dropdown_count: dropdowns.length};
      const options = dropdowns.flatMap(dropdown =>
        Array.from(dropdown.querySelectorAll('.el-select-dropdown__item'))
          .filter(option => option.offsetParent !== null && !option.className.includes('is-disabled'))
          .filter(option => (option.innerText || '').trim() === channelName)
      );
      if (options.length !== 1) return {selected: false, exact_option_count: options.length};
      options[0].click();
      return {selected: true};
    }
    """, channel_name)
        if not selected or not selected.get("selected"):
            detail.update(selected or {})
            _dismiss_stray_dropdowns(page)
            continue
        break

    if not opened or not opened.get("opened"):
        detail.update(opened or {})
        return detail if return_detail else False
    if not selected or not selected.get("selected"):
        return detail if return_detail else False
    detail["selected"] = True

    page.wait_for_timeout(300)
    verified = page.evaluate("""
    ({channelName, selectIndex}) => {
      const selects = Array.from(document.querySelectorAll('.el-select'))
        .filter(select => select.offsetParent !== null &&
          !select.closest('.el-dialog, .el-dialog__wrapper'));
      const target = selects[selectIndex];
      if (!target) return false;
      const input = target.querySelector('input.el-input__inner');
      const tags = Array.from(target.querySelectorAll('.el-tag__content, .el-select__tags-text'))
        .map(tag => (tag.innerText || '').trim());
      return Boolean((input && (input.value || '').trim() === channelName) || tags.includes(channelName));
    }
    """, {"channelName": channel_name, "selectIndex": opened.get("select_index")})
    if not verified:
        return detail if return_detail else False
    detail["verify"] = True

    # 观察器只在"点搜索"这一步打开，用完即释放，少占用一条 CDP 通道。
    observations = _attach_list_response_observer(page, target_filter=channel_name)
    if observations is not None:
        detail["list_requests_before"] = len(observations)
    click_result = page.evaluate("""
    () => {
      // 页面上可能有多个"搜索"（列表筛选、其它工具栏、卡片内）。点错按钮会
      // 出现"渠道已选中但列表没有刷新"的假象，因此优先点与渠道筛选同一表单
      // 容器内的那个；容器内没有时再退回全页第一个可见"搜索"，保持原行为。
      const selects = Array.from(document.querySelectorAll('.el-select'))
        .filter(select => select.offsetParent !== null &&
          !select.closest('.el-dialog, .el-dialog__wrapper'));
      const channelSelect = selects.find(select => {
        const item = select.closest('.el-form-item');
        const label = item && item.querySelector('.el-form-item__label');
        const input = select.querySelector('input');
        const metadata = input
          ? `${input.placeholder || ''} ${input.getAttribute('aria-label') || ''}`
          : '';
        return /渠道/.test(metadata) || Boolean(label && /渠道/.test(label.innerText || ''));
      });

      const containers = [];
      if (channelSelect) {
        const form = channelSelect.closest('.el-form');
        const item = channelSelect.closest('.el-form-item');
        containers.push(form);
        containers.push(item && item.parentElement);
        containers.push(channelSelect.parentElement && channelSelect.parentElement.parentElement);
      }
      for (const container of containers) {
        if (!container) continue;
        for (const button of container.querySelectorAll('button')) {
          if (button.offsetParent === null) continue;
          const label = (button.innerText || '').replace(/\\s/g, '').trim();
          if (label === '搜索') { button.click(); return {clicked: true, scope: 'channel-form'}; }
        }
      }

      const buttons = document.querySelectorAll('button');
      for (const button of buttons) {
        if (button.offsetParent === null) continue;
        const label = (button.innerText || '').replace(/\\s/g, '').trim();
        if (label === '搜索') { button.click(); return {clicked: true, scope: 'global'}; }
      }
      return {clicked: false, scope: 'none'};
    }
    """)
    clicked_ok = (
        click_result.get("clicked")
        if isinstance(click_result, dict)
        else bool(click_result)
    )
    if not clicked_ok:
        _detach_list_response_observer(observations)
        return detail if return_detail else False
    detail["search_clicked"] = True
    detail["search_scope"] = click_result.get("scope") if isinstance(click_result, dict) else None

    started = time.monotonic()
    stable = wait_for_table_update(page, previous_state, _read_pagination_state)
    detail["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    detail["filter_stable"] = bool(stable)

    final_state = _read_pagination_state(page)
    detail["final_row_count"] = final_state.get("row_count")
    detail["table_changed"] = (
        final_state.get("table_signature") != previous_state.get("table_signature")
    )
    try:
        visible_rows = _read_current_page_app_rows(page)
        detail["reader_row_count"] = len(visible_rows)
        detail["reader_sees_target_channel"] = any(
            row.get("channel_name") == channel_name for row in visible_rows
        )
        detail["reader_distinct_channels"] = len(
            {row.get("channel_name") for row in visible_rows}
        )
    except Exception:
        pass
    if observations is not None:
        detail["list_requests_after"] = len(observations)
        if len(observations) > detail.get("list_requests_before", 0):
            detail["last_http_status"] = observations[-1]
            detail["request_carried_target_filter"] = bool(
                getattr(observations, "carried_target_filter", False)
            )
            if detail["request_carried_target_filter"]:
                detail["response_contains_target_channel"] = getattr(
                    observations, "response_contains_target_channel", None
                )
                detail["target_filter_field"] = getattr(
                    observations, "target_filter_field", None
                )
                detail["target_filter_field_is_channel"] = getattr(
                    observations, "target_filter_field_is_channel", None
                )
            else:
                detail["response_contains_target_channel"] = None
                detail["target_filter_field"] = None
                detail["target_filter_field_is_channel"] = None
        else:
            detail["request_carried_target_filter"] = None
            detail["response_contains_target_channel"] = None
            detail["target_filter_field"] = None
            detail["target_filter_field_is_channel"] = None
    if context_sink is not None and observations is not None:
        context_sink.extend(getattr(observations, "target_filter_context_profiles", []) or [])
    _detach_list_response_observer(observations)
    if not stable:
        print(f"[create_app] 渠道筛选未稳定: {detail}")
    return detail if return_detail else bool(stable)


_UTILITY_COLUMN_CLASS_RE = re.compile(
    r"el-table__expand-column|el-table-column--selection|el-table-column--index|\bgutter\b"
)
_POST_SAVE_FILTER_ATTEMPTS = 3
_POST_SAVE_FILTER_RETRY_MS = 1500


def _is_utility_column(item):
    classes = ""
    if isinstance(item, dict):
        classes = str(item.get("classes") or "")
    else:
        classes = str(item or "")
    return bool(_UTILITY_COLUMN_CLASS_RE.search(classes))


def _align_header_cells(headers, cells):
    """Pair data headers with data cells, skipping expand/select/gutter columns."""
    kept_headers = [item for item in (headers or []) if not _is_utility_column(item)]
    kept_cells = [item for item in (cells or []) if not _is_utility_column(item)]
    if not kept_headers or not kept_cells:
        return []
    if len(kept_headers) != len(kept_cells):
        return []
    aligned = []
    for header, cell in zip(kept_headers, kept_cells):
        aligned.append({
            "header": (header.get("text") if isinstance(header, dict) else str(header or "")).strip(),
            "value": (cell.get("text") if isinstance(cell, dict) else str(cell or "")).strip(),
        })
    return aligned


def _is_channel_name_header(header):
    text = re.sub(r"[ *:：\s]", "", header or "")
    if not text or re.search(r"ID|编码|code", text, re.I):
        return False
    return "渠道" in (header or "")


def _mapped_value(aligned, predicate):
    for item in aligned:
        if predicate(item.get("header") or ""):
            return (item.get("value") or "").strip()
    return ""


def _matching_main_header_count(headers, predicate):
    return sum(
        1
        for header in (headers or [])
        if not _is_utility_column(header)
        and predicate(header.get("text") if isinstance(header, dict) else str(header or ""))
    )


def _main_channel_from_row(headers, cells):
    aligned = _align_header_cells(headers, cells)
    if not aligned:
        return ""
    return _mapped_value(aligned, _is_channel_name_header)


def _is_app_id_header(header):
    text = (header or "").strip()
    return text == "ID" or "应用ID" in text


def _is_app_name_header(header):
    return bool(re.search(r"应用名称|应用名", header or ""))


def _channel_verify_facts(
    expected_channel_name,
    main_channel,
    detail_channel,
    *,
    aligned_used=False,
    id_field_source=None,
):
    expected = (expected_channel_name or "").strip()
    main = (main_channel or "").strip()
    detail = (detail_channel or "").strip()
    main_present = bool(main)
    detail_present = bool(detail)
    main_matches = bool(expected) and main == expected
    detail_matches = bool(expected) and detail == expected
    facts = {
        "id_field_source": id_field_source,
        "header_cell_alignment_used": bool(aligned_used),
        "main_channel_present": main_present,
        "detail_channel_present": detail_present,
        "main_channel_matches_expected": main_matches,
        "detail_channel_matches_expected": detail_matches,
        "main_detail_same": main_present and detail_present and main == detail,
        "mismatch_source": None,
        "verified": True,
        "reason": None,
        "source": None,
    }
    if not expected:
        return facts
    if detail_matches or main_matches:
        facts["source"] = "detail" if detail_matches else "main"
        return facts
    if not main_present and not detail_present:
        facts["verified"] = False
        facts["reason"] = "channel_unverified"
        facts["mismatch_source"] = "none"
        return facts
    if main_present and not main_matches and detail_present and not detail_matches:
        mismatch_source = "both"
    elif detail_present and not detail_matches:
        mismatch_source = "detail"
    else:
        mismatch_source = "main"
    facts["verified"] = False
    facts["reason"] = "channel_mismatch"
    facts["mismatch_source"] = mismatch_source
    facts["source"] = mismatch_source
    return facts


def _channel_verify_decision(expected_channel_name, main_channel, detail_channel):
    """Verify channel using main column and expanded detail independently."""
    facts = _channel_verify_facts(expected_channel_name, main_channel, detail_channel)
    return {
        "verified": facts["verified"],
        "reason": facts["reason"],
        "source": facts["source"],
    }


def _id_matched_in_row(app_id, headers, cells, detail_id=""):
    expected = str(app_id or "").strip()
    if not expected:
        return False, None
    aligned = _align_header_cells(headers, cells)
    main_id = _mapped_value(aligned, _is_app_id_header)
    if main_id == expected:
        return True, "main"
    if str(detail_id or "").strip() == expected:
        return True, "detail"
    return False, None


def _redacted_locate_facts(facts):
    if not facts:
        return {
            "id_field_source": None,
            "header_cell_alignment_used": False,
            "main_channel_present": False,
            "detail_channel_present": False,
            "main_channel_matches_expected": False,
            "detail_channel_matches_expected": False,
            "main_detail_same": False,
            "mismatch_source": None,
        }
    return {
        "id_field_source": facts.get("id_field_source"),
        "header_cell_alignment_used": bool(facts.get("header_cell_alignment_used")),
        "main_channel_present": bool(facts.get("main_channel_present")),
        "detail_channel_present": bool(facts.get("detail_channel_present")),
        "main_channel_matches_expected": bool(facts.get("main_channel_matches_expected")),
        "detail_channel_matches_expected": bool(facts.get("detail_channel_matches_expected")),
        "main_detail_same": bool(facts.get("main_detail_same")),
        "mismatch_source": facts.get("mismatch_source"),
    }


def _read_current_page_app_rows(page):
    payload = page.evaluate(_MAIN_LIST_COLLECT_JS, True) or {}
    count = payload.get("main_table_count")
    if count is not None and count != 1:
        return []
    headers = payload.get("headers") or []
    rows = []
    for raw in payload.get("rows") or []:
        aligned = _align_header_cells(headers, raw.get("cells") or [])
        # A detail ID can describe the expanded content, never the list row identity.
        app_id = _mapped_value(aligned, _is_app_id_header)
        app_name = _mapped_value(
            aligned,
            lambda header: bool(re.search(r"应用名称|应用名", header or "")),
        ) or (raw.get("detail_app_name") or "")
        channel_name = _main_channel_from_row(headers, raw.get("cells") or []) or (
            raw.get("detail_channel") or ""
        )
        if not app_id:
            continue
        rows.append({
            "app_id": app_id,
            "row_idx": raw.get("row_idx"),
            "row_text": raw.get("row_text") or "",
            "app_name": app_name,
            "channel_name": channel_name,
        })
    return rows


_NARROW_ROW_SWITCH_STATE_JS = """
(expected) => {
  const visible = (el) => Boolean(el) && el.offsetParent !== null;
  const inDialog = (el) => Boolean(el && el.closest('.el-dialog, .el-dialog__wrapper'));
  const utility = (classes) => /el-table__expand-column|el-table-column--selection|el-table-column--index|\\bgutter\\b/.test(String(classes || ''));
  const tables = Array.from(document.querySelectorAll('.el-table')).filter((table) => visible(table) && !inDialog(table));
  if (tables.length !== 1) return null;
  const table = tables[0];
  const rawHeaders = Array.from(table.querySelectorAll('.el-table__header-wrapper th').length
    ? table.querySelectorAll('.el-table__header-wrapper th') : table.querySelectorAll('th'));
  const headers = rawHeaders.filter((header) => !utility(header.className)).map((header) => (header.innerText || header.textContent || '').trim());
  const rawRows = Array.from(table.querySelectorAll('.el-table__body-wrapper tbody tr').length
    ? table.querySelectorAll('.el-table__body-wrapper tbody tr') : table.querySelectorAll('tbody tr'));
  const rows = rawRows.filter((row) => visible(row) && !row.classList.contains('el-table__expanded-row'));
  const idHeaders = headers.map((header, index) => ({header, index})).filter(({header}) => header === 'ID' || header.includes('应用ID'));
  const nameHeaders = headers.map((header, index) => ({header, index})).filter(({header}) => /应用名称|应用名/.test(header));
  const channelHeaders = headers.map((header, index) => ({header, index})).filter(({header}) => {
    const normalized = header.replace(/[ *:：\\s]/g, '');
    return normalized && !/ID|编码|code/i.test(normalized) && header.includes('渠道');
  });
  if (idHeaders.length !== 1 || nameHeaders.length !== 1 || channelHeaders.length !== 1) return null;
  const matches = [];
  for (let rowIdx = 0; rowIdx < rows.length; rowIdx++) {
    const cells = Array.from(rows[rowIdx].querySelectorAll('td')).filter((cell) => !utility(cell.className));
    if (cells.length !== headers.length) continue;
    const valueAt = (index) => (cells[index].innerText || cells[index].textContent || '').trim();
    if (valueAt(idHeaders[0].index) === expected.appId
      && valueAt(nameHeaders[0].index) === expected.appName
      && valueAt(channelHeaders[0].index) === expected.channelName) {
      matches.push({row: rows[rowIdx], rowIdx});
    }
  }
  if (matches.length !== 1) return null;
  const switches = Array.from(matches[0].row.querySelectorAll('.el-switch')).filter(visible);
  if (switches.length !== 1) return null;
  const checked = switches[0].classList.contains('is-checked');
  return {state: checked ? 'switch_checked' : 'switch_unchecked', row_idx: matches[0].rowIdx};
}
"""
_NARROW_ROW_SWITCH_STATES = {"switch_checked", "switch_unchecked", "unreadable"}


def _post_save_narrow_row_switch_state(page, app_id, app_name, channel_name):
    """Read a uniquely anchored filtered-row switch twice without interacting."""
    expected = {
        "appId": str(app_id or "").strip(),
        "appName": str(app_name or "").strip(),
        "channelName": str(channel_name or "").strip(),
    }
    if not all(expected.values()):
        return "unreadable"
    snapshots = []
    for _ in range(2):
        try:
            snapshot = page.evaluate(_NARROW_ROW_SWITCH_STATE_JS, expected)
        except Exception:
            return "unreadable"
        if not isinstance(snapshot, dict):
            return "unreadable"
        state = snapshot.get("state")
        row_idx = snapshot.get("row_idx")
        if state not in _NARROW_ROW_SWITCH_STATES - {"unreadable"} or isinstance(row_idx, bool) or not isinstance(row_idx, int) or row_idx < 0:
            return "unreadable"
        snapshots.append((state, row_idx))
    return snapshots[0][0] if snapshots[0] == snapshots[1] else "unreadable"


def _log_post_save_narrow_row_switch_state(state):
    fixed_state = state if state in _NARROW_ROW_SWITCH_STATES else "unreadable"
    print(f"[create_app] post_save_narrow_row_switch_state={fixed_state}")


def _collect_all_app_rows(page, reset_filters=False):
    """Collect app rows across pagination, keyed by the exact application ID."""
    if reset_filters:
        _reset_list_filters(page)
    if not _go_to_first_page(page):
        return None
    records = {}
    seen_pages = set()

    for _ in range(50):
        _expand_visible_rows(page)
        page_state = _read_pagination_state(page)
        active_page = page_state.get("page_number")
        if active_page is None or active_page in seen_pages or page_state.get("row_count", 0) <= 0:
            return None
        seen_pages.add(active_page)

        for row in _read_current_page_app_rows(page):
            records[row["app_id"]] = row

        moved = _click_next_page_and_wait(page)
        if moved is None:
            return None
        if not moved:
            break
    else:
        return None

    return records


def _known_main_candidate_category(candidates):
    return "0" if not candidates else ("1" if len(candidates) == 1 else ">1")


def _known_main_redacted_facts(snapshot=None, stable_row_key=False):
    snapshot = snapshot or {}
    return {
        "candidate_category": snapshot.get("candidate_category") or _known_main_candidate_category(snapshot.get("candidates") or []),
        "page": snapshot.get("page") if snapshot.get("page") is not None else "unknown",
        "stable_row_key": bool(stable_row_key),
        "id_field_source": "main",
    }


def _known_main_failure(reason, snapshot=None, stable_row_key=False):
    return {
        "success": False,
        "reason": reason,
        **_known_main_redacted_facts(snapshot, stable_row_key),
    }


def _snapshot_known_main_id_rows(page, app_id):
    """Read one main-table page using main cells only; detail fields are ignored."""
    try:
        payload = page.evaluate(_MAIN_LIST_COLLECT_JS, False) or {}
    except Exception:
        payload = {}
    page_number = payload.get("page_number") or 1
    if payload.get("main_table_count") != 1:
        return {"success": False, "reason": "main_list_table_unavailable", "page": page_number, "candidates": []}
    headers = payload.get("headers") or []
    if not all(
        _matching_main_header_count(headers, predicate) == 1
        for predicate in (_is_app_id_header, _is_app_name_header, _is_channel_name_header)
    ):
        return {"success": False, "reason": "main_anchor_columns_ambiguous", "page": page_number, "candidates": []}
    expected_id = str(app_id or "").strip()
    candidates = []
    for raw in payload.get("rows") or []:
        aligned = _align_header_cells(headers, raw.get("cells") or [])
        if not aligned or _mapped_value(aligned, _is_app_id_header) != expected_id:
            continue
        candidates.append({
            "row_idx": raw.get("row_idx"),
            "row_key": str(raw.get("row_key") or "").strip(),
            "app_id": _mapped_value(aligned, _is_app_id_header),
            "app_name": _mapped_value(aligned, _is_app_name_header),
            "channel_name": _mapped_value(aligned, _is_channel_name_header),
        })
    return {
        "success": True,
        "page": page_number,
        "candidates": candidates,
    }


def _known_main_logical_key(page_number, row, *, native_only=False):
    """Keep native keys verbatim; synthesize only a single candidate in memory."""
    native_key = str((row or {}).get("row_key") or "").strip()
    if native_key:
        return native_key, "native"
    if native_only:
        return "", "none"
    anchors = (
        str((row or {}).get("app_id") or ""),
        str((row or {}).get("app_name") or ""),
        str((row or {}).get("channel_name") or ""),
    )
    return "synthetic:" + "\x1f".join((str(page_number), str((row or {}).get("row_idx")), *anchors)), "synthetic"


def _known_main_identity_from_snapshot(snapshot, app_id, app_name, channel_name):
    """Accept one main row, or only a native-key-proven fixed-column mirror."""
    candidates = snapshot.get("candidates") or []
    if not candidates:
        return {"success": False, "reason": "zero_candidates"}
    expected = (str(app_id or "").strip(), (app_name or "").strip(), (channel_name or "").strip())
    anchors = [
        (row.get("app_id") or "", row.get("app_name") or "", row.get("channel_name") or "")
        for row in candidates
    ]
    native_keys = [str(row.get("row_key") or "").strip() for row in candidates]
    # Multiple DOM rows may fold only with one nonempty native key and identical anchors.
    if len(candidates) > 1 and (
        not all(native_keys)
        or len(set(native_keys)) != 1
        or len(set(anchors)) != 1
    ):
        return {"success": False, "reason": "ambiguous_main_rows"}
    candidate = dict(candidates[0])
    if anchors[0] != expected:
        return {"success": False, "reason": "main_anchor_mismatch"}
    logical_key, key_kind = _known_main_logical_key(snapshot.get("page"), candidate)
    candidate["logical_key"] = logical_key
    candidate["key_kind"] = key_kind
    return {"success": True, "row": candidate}


def _log_known_main_snapshot(snapshot, decision, stable_row_key=False):
    facts = _known_main_redacted_facts(snapshot, stable_row_key)
    print(
        "[create_app] 保存后已知ID主行读取: "
        f"candidate_category={facts['candidate_category']} page={facts['page']} "
        f"stable_row_key={facts['stable_row_key']} id_field_source={facts['id_field_source']}"
    )


def _return_to_known_main_page(page, page_number):
    """Return to a previously scanned page using only the stable pager helper."""
    if not isinstance(page_number, int) or page_number < 1 or not _go_to_first_page(page):
        return False
    for _ in range(page_number - 1):
        if _click_next_page_and_wait(page) is not True:
            return False
    return True


def _scan_known_main_id_pages(page, app_id, app_name, channel_name):
    """Scan every stable page; a same-ID candidate on another page is a conflict."""
    seen_pages = set()
    candidate = None
    for _ in range(50):
        snapshot = _snapshot_known_main_id_rows(page, app_id)
        if not snapshot.get("success"):
            return {"status": "failure", "snapshot": snapshot, "reason": snapshot.get("reason")}
        page_number = snapshot.get("page")
        if page_number is None or page_number in seen_pages:
            return {"status": "failure", "snapshot": snapshot, "reason": "pagination_unstable"}
        seen_pages.add(page_number)
        decision = _known_main_identity_from_snapshot(snapshot, app_id, app_name, channel_name)
        _log_known_main_snapshot(snapshot, decision)
        if decision.get("success"):
            if candidate is not None:
                return {"status": "failure", "snapshot": snapshot, "reason": "ambiguous_main_rows"}
            candidate = {"snapshot": snapshot, "decision": decision}
        elif decision.get("reason") != "zero_candidates":
            return {"status": "failure", "snapshot": snapshot, "reason": decision.get("reason")}
        moved = _click_next_page_and_wait(page)
        if moved is None:
            return {"status": "failure", "snapshot": snapshot, "reason": "pagination_unstable"}
        if not moved:
            if candidate is None:
                return {"status": "zero", "snapshot": snapshot}
            if candidate["snapshot"].get("page") != page_number and not _return_to_known_main_page(
                page, candidate["snapshot"].get("page")
            ):
                return {"status": "failure", "snapshot": candidate["snapshot"], "reason": "pagination_unstable"}
            return {"status": "candidate", **candidate}
    return {"status": "failure", "snapshot": {}, "reason": "page_scan_limit_reached"}


def _locate_known_main_row_in_current_view(page, app_id, app_name, channel_name):
    """Return a stable known-main handle without resetting the narrow view or clicking.

    This is deliberately separate from the fresh-unfiltered restore gate: the
    post-save fallback proof must use the filtered view which identified the new
    row, while enable's terminal proof installs its own unfiltered gate later.
    """
    if not _go_to_first_page(page):
        return _known_main_failure("pagination_unstable")
    scanned = _scan_known_main_id_pages(page, app_id, app_name, channel_name)
    if scanned.get("status") != "candidate":
        return _known_main_failure(scanned.get("reason") or "zero_candidates", scanned.get("snapshot"))
    first_snapshot = scanned["snapshot"]
    first_row = scanned["decision"]["row"]
    page.wait_for_timeout(_POST_SAVE_KNOWN_ID_POLL_MS)
    second_snapshot = _snapshot_known_main_id_rows(page, app_id)
    if not second_snapshot.get("success") or second_snapshot.get("page") != first_snapshot.get("page"):
        _log_known_main_snapshot(second_snapshot, {}, False)
        return _known_main_failure("main_identity_unstable", second_snapshot)
    second_decision = _known_main_identity_from_snapshot(second_snapshot, app_id, app_name, channel_name)
    stable_key = bool(
        second_decision.get("success")
        and second_decision["row"].get("logical_key") == first_row.get("logical_key")
        and second_decision["row"].get("row_idx") == first_row.get("row_idx")
    )
    _log_known_main_snapshot(second_snapshot, second_decision, stable_key)
    if not stable_key:
        return _known_main_failure("main_identity_unstable", second_snapshot)
    return {
        "success": True,
        "page": second_snapshot.get("page"),
        "row_idx": second_decision["row"].get("row_idx"),
        "row_key": second_decision["row"].get("logical_key"),
        "key_kind": second_decision["row"].get("key_kind"),
    }


def _same_known_main_handle(left, right):
    """Compare only the in-memory stable handle fields; never log their values."""
    fields = ("page", "row_idx", "row_key", "key_kind")
    return all(left.get(field) == right.get(field) for field in fields)


def _log_unfiltered_list_gate(reason, attempts, gate_passed, counts, refresh_attempted, refresh_clicked, diagnostics, structure_diagnostics, context_relation="unreadable"):
    bounded = lambda value: min(max(int(value or 0), 0), _POST_SAVE_KNOWN_ID_POLL_ATTEMPTS * 2)
    print("[create_app] unfiltered_list_gate=" + json.dumps({
        "reason": reason,
        "attempts": min(max(int(attempts or 0), 0), _POST_SAVE_KNOWN_ID_POLL_ATTEMPTS),
        "gate_passed": bool(gate_passed),
        "refresh_attempted": bool(refresh_attempted),
        "refresh_clicked": bool(refresh_clicked),
        "target_absent_requests": bounded(counts.get("target_absent_requests")),
        "target_filtered_requests": bounded(counts.get("target_filtered_requests")),
        "unknown_requests": bounded(counts.get("unknown_requests")),
        "target_absent_2xx": bounded(counts.get("target_absent_2xx")),
        "observer_diagnostics": {key: bounded(diagnostics.get(key)) for key in _LIST_DIAGNOSTIC_KEYS},
        "response_structure": {key: bounded(structure_diagnostics.get(key)) for key in _LIST_STRUCTURE_BUCKETS},
        "context_relation": context_relation if context_relation in {"equal", "different", "unreadable"} else "unreadable",
    }, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


def _deepest_list_gate_reason(reasons):
    ranks = {
        _LIST_GATE_NO_COMPLETE_RESPONSE: 1,
        _LIST_GATE_STRUCTURE_UNPARSEABLE: 2,
        _LIST_GATE_RESPONSE_DOM_MISMATCH: 3,
        _LIST_GATE_DOM_UNSTABLE: 4,
    }
    return max(reasons, key=lambda reason: ranks.get(reason, 0), default=_LIST_GATE_NO_COMPLETE_RESPONSE)


def _locate_known_main_row_for_resource_fallback_impl(page, app_id, app_name, channel_name, private_outline_batch, narrow_context_profiles=None):
    """Read-only post-save locator: require a fresh unfiltered response before scanning.

    `_identify_new_app` may have found an ID in a channel-filtered view.  Resetting
    filters must not immediately reuse stale unfiltered DOM: each reset is bound to
    a newly observed complete 2xx list structure and its stable rendered DOM before
    the known ID may be scanned.
    """
    scanned = {"snapshot": {}}
    gate_reasons = []
    gate_passed = False
    refresh_attempted = False
    refresh_clicked = False
    gate_counts = {"target_absent_requests": 0, "target_filtered_requests": 0, "unknown_requests": 0, "target_absent_2xx": 0}
    gate_diagnostics = {key: 0 for key in _LIST_DIAGNOSTIC_KEYS}
    gate_structure_diagnostics = {key: 0 for key in _LIST_STRUCTURE_BUCKETS}
    unfiltered_context_profiles = []
    visibility_wait_remaining_ms = _POST_SAVE_KNOWN_ID_VISIBILITY_WAIT_BUDGET_MS
    attempts_run = 0
    for attempt in range(_POST_SAVE_KNOWN_ID_POLL_ATTEMPTS):
        if attempt:
            remaining_waits = _POST_SAVE_KNOWN_ID_POLL_ATTEMPTS - attempt
            wait_ms = visibility_wait_remaining_ms // remaining_waits
            page.wait_for_timeout(wait_ms)
            visibility_wait_remaining_ms -= wait_ms
        attempts_run += 1
        previous_state = _read_list_restore_state(page) or {}
        observations = (
            _attach_list_response_observer(page, private_outline_batch=private_outline_batch)
            if private_outline_batch.target is not None else _attach_list_response_observer(page)
        )
        records_before = len(getattr(observations, "success_records", []) or []) if observations is not None else 0
        target_absent_before = int(getattr(observations, "target_absent_2xx_count", 0) or 0) if observations is not None else 0
        try:
            _reset_list_filters(page)
            if (
                observations is not None
                and not refresh_attempted
                and not getattr(observations, "list_request_count", 0)
            ):
                refresh_attempted = True
                try:
                    refresh_clicked = bool(_trigger_unfiltered_list_refresh(page))
                except Exception:
                    refresh_clicked = False
            restored = _wait_for_unfiltered_list_restore(
                page,
                previous_state,
                observations=observations,
                records_before=records_before,
                target_absent_before=target_absent_before,
                return_detail=True,
            )
        finally:
            if observations is not None:
                gate_counts["target_absent_requests"] += int(getattr(observations, "target_absent_request_count", 0) or 0)
                gate_counts["target_filtered_requests"] += int(getattr(observations, "target_filtered_request_count", 0) or 0)
                gate_counts["unknown_requests"] += int(getattr(observations, "unknown_request_count", 0) or 0)
                gate_counts["target_absent_2xx"] += int(getattr(observations, "target_absent_2xx_count", 0) or 0)
                for key in _LIST_DIAGNOSTIC_KEYS:
                    gate_diagnostics[key] += int(getattr(observations, "diagnostic_counts", {}).get(key, 0) or 0)
                for key in _LIST_STRUCTURE_BUCKETS:
                    gate_structure_diagnostics[key] += int(getattr(observations, "structure_diagnostic_counts", {}).get(key, 0) or 0)
                unfiltered_context_profiles.extend(
                    getattr(observations, "complete_unfiltered_context_profiles", []) or []
                )
            _detach_list_response_observer(observations)
        if not isinstance(restored, dict):
            restored = {"restored": bool(restored), "reason": _LIST_GATE_NO_COMPLETE_RESPONSE}
        if not restored.get("restored"):
            gate_reasons.append(restored.get("reason"))
            continue
        gate_passed = True
        if not _go_to_first_page(page):
            return _known_main_failure("pagination_unstable")
        scanned = _scan_known_main_id_pages(page, app_id, app_name, channel_name)
        if scanned.get("status") == "zero":
            continue
        if scanned.get("status") != "candidate":
            return _known_main_failure(scanned.get("reason"), scanned.get("snapshot"))
        first_snapshot = scanned["snapshot"]
        first_row = scanned["decision"]["row"]
        page.wait_for_timeout(_POST_SAVE_KNOWN_ID_POLL_MS)
        second_snapshot = _snapshot_known_main_id_rows(page, app_id)
        if not second_snapshot.get("success") or second_snapshot.get("page") != first_snapshot.get("page"):
            _log_known_main_snapshot(second_snapshot, {}, False)
            return _known_main_failure("main_identity_unstable", second_snapshot)
        second_decision = _known_main_identity_from_snapshot(second_snapshot, app_id, app_name, channel_name)
        stable_key = bool(
            second_decision.get("success")
            and second_decision["row"].get("logical_key") == first_row.get("logical_key")
            and second_decision["row"].get("row_idx") == first_row.get("row_idx")
        )
        _log_known_main_snapshot(second_snapshot, second_decision, stable_key)
        if not stable_key:
            return _known_main_failure("main_identity_unstable", second_snapshot)
        return {
            "success": True,
            "page": second_snapshot.get("page"),
            "row_idx": second_decision["row"].get("row_idx"),
            "row_key": second_decision["row"].get("logical_key"),
            "key_kind": second_decision["row"].get("key_kind"),
        }
    reason = _LIST_GATE_PASSED_ID_NOT_FOUND if gate_passed else _deepest_list_gate_reason(gate_reasons)
    _log_unfiltered_list_gate(
        reason, attempts_run, gate_passed, gate_counts, refresh_attempted, refresh_clicked,
        gate_diagnostics, gate_structure_diagnostics,
        _list_context_relation(narrow_context_profiles, unfiltered_context_profiles),
    )
    return _known_main_failure("zero_candidates_after_poll", scanned.get("snapshot"))


def _locate_known_main_row_for_resource_fallback(page, app_id, app_name, channel_name, narrow_context_profiles=None):
    """Run the entire post-save locator with one optional private outline batch."""
    batch = _PrivateOutlineBatch(_safe_private_outline_target())
    try:
        return _locate_known_main_row_for_resource_fallback_impl(
            page, app_id, app_name, channel_name, batch, narrow_context_profiles
        )
    finally:
        batch.write_once()


def _identify_new_app(page, before_ids, actual_channel_name, app_name, context_sink=None):
    """Find the row created by this save using an ID set difference."""
    last_candidates = []

    # Narrow the post-save lookup by channel and app name first. Exact ID
    # difference and channel verification remain the identity/safety checks.
    # New channels may be briefly unfilterable; retry a bounded number of
    # times, then fall back to a full scan. This path never creates or saves.
    channel_filter = None
    for filter_attempt in range(_POST_SAVE_FILTER_ATTEMPTS):
        attempt_context_profiles = []
        channel_filter = (
            _search_list_by_channel(page, actual_channel_name, return_detail=True, context_sink=attempt_context_profiles)
            if context_sink is not None else _search_list_by_channel(page, actual_channel_name, return_detail=True)
        )
        if channel_filter is True or (
            isinstance(channel_filter, dict) and channel_filter.get("filter_stable")
        ):
            if context_sink is not None:
                context_sink.extend(attempt_context_profiles)
            break
        carried = isinstance(channel_filter, dict) and channel_filter.get(
            "request_carried_target_filter"
        )
        if not carried or filter_attempt >= _POST_SAVE_FILTER_ATTEMPTS - 1:
            break
        page.wait_for_timeout(_POST_SAVE_FILTER_RETRY_MS)
    if isinstance(channel_filter, dict) and not channel_filter.get("filter_stable"):
        print(f"[create_app] 渠道筛选未生效，回退全量扫描: {channel_filter}")
    if channel_filter is True or (isinstance(channel_filter, dict) and channel_filter.get("filter_stable")):
        filtered_rows = _collect_all_app_rows(page, reset_filters=False)
        if filtered_rows is None:
            return {
                "success": False,
                "error": err(
                    "APP_ID_SNAPSHOT_FAILED",
                    "VERIFY",
                    "渠道筛选后的应用列表分页未稳定，不能安全识别新增应用ID",
                    NEXT_MANUAL,
                ),
            }
        fast_candidates = [
            row for app_id, row in filtered_rows.items()
            if app_id not in before_ids
            and row.get("app_name") == app_name
            and row.get("channel_name") == actual_channel_name
        ]
        if len(fast_candidates) == 1:
            candidate = fast_candidates[0]
            _log_post_save_narrow_row_switch_state(
                _post_save_narrow_row_switch_state(
                    page, candidate.get("app_id"), candidate.get("app_name"), candidate.get("channel_name")
                )
            )
            return {"success": True, "app_id": candidate["app_id"]}
        if len(fast_candidates) > 1:
            return {
                "success": False,
                "error": err(
                    "NEW_APP_ID_AMBIGUOUS",
                    "VERIFY",
                    "按渠道和应用名筛选后仍有多个新增应用ID，停止自动选择",
                    NEXT_MANUAL,
                ),
            }

    for attempt in range(5):
        after_rows = _collect_all_app_rows(page, reset_filters=True)
        if after_rows is None:
            return {
                "success": False,
                "error": err(
                    "APP_ID_SNAPSHOT_FAILED",
                    "VERIFY",
                    "保存后应用列表分页未稳定，不能安全识别新增应用ID",
                    NEXT_MANUAL,
                ),
            }
        candidates = [row for app_id, row in after_rows.items() if app_id not in before_ids]
        last_candidates = candidates
        category = "0" if not candidates else ("1" if len(candidates) == 1 else ">1")
        try:
            page_number = (_read_pagination_state(page) or {}).get("page_number", "unknown")
        except Exception:
            page_number = "unknown"
        print(
            "[create_app] 新增ID识别: "
            f"attempt={attempt + 1} candidate_category={category} "
            f"page={page_number} stable_row_key=False id_field_source=main"
        )

        if len(candidates) == 1:
            return {"success": True, "app_id": candidates[0]["app_id"]}
        if len(candidates) > 1:
            return {
                "success": False,
                "error": err(
                    "NEW_APP_ID_AMBIGUOUS",
                    "VERIFY",
                    "保存后新增应用ID候选数大于一，无法安全确定目标",
                    NEXT_MANUAL,
                ),
            }
        page.wait_for_timeout(1500)

    if last_candidates:
        message = "保存后新增应用ID候选数大于一，无法安全确定目标"
        code = "NEW_APP_ID_AMBIGUOUS"
    else:
        message = "保存后主表应用ID不可读取或未检测到新增应用ID"
        code = "NEW_APP_ID_NOT_FOUND"
    return {"success": False, "error": err(code, "VERIFY", message, NEXT_MANUAL)}


def _pagination_synced_with_unfiltered_list(state):
    """True when a loaded list can paginate, or a completed single-page list is confirmed."""
    if not state or (state.get("row_count") or 0) <= 0:
        return False
    if state.get("next_enabled"):
        return True
    total_count = state.get("total_count")
    row_count = state.get("row_count") or 0
    return isinstance(total_count, int) and total_count == row_count and total_count > 0


def _read_list_restore_state(page):
    state = _read_pagination_state(page) or {}
    extra = page.evaluate("""() => {
      """ + _PAGINATION_SCOPE_JS + """const pager = hermesPager();
      const next = pager ? pager.querySelector('.btn-next') : null;
      const total = pager ? pager.querySelector('.el-pagination__total') : null;
      const nextDisabled = !next || next.disabled || (next.className || '').includes('disabled');
      const totalText = total ? (total.innerText || '') : '';
      const match = totalText.replace(/,/g, '').match(/(\\d+)/);
      return {
        next_enabled: Boolean(next) && !nextDisabled,
        total_count: match ? Number(match[1]) : null
      };
    }""") or {}
    state.update(extra)
    return state


def _restore_fingerprint(state):
    return (
        (state or {}).get("table_signature"),
        (state or {}).get("row_count"),
        bool((state or {}).get("next_enabled")),
        (state or {}).get("total_count"),
    )


def _extract_list_structure(body):
    """Return in-memory counts only; never keep body, URL, names, or ids."""
    raw = body
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "ignore")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        data = payload if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None
    meta = {}
    for key in ("totalCount", "total", "total_count"):
        if data.get(key) is None:
            continue
        try:
            meta["total_count"] = int(data[key])
        except (TypeError, ValueError):
            continue
        break
    items = data.get("list")
    if items is None:
        items = data.get("records")
    if items is None:
        items = data.get("rows")
    if items is None:
        items = data.get("appInfoList")
    if isinstance(items, list):
        meta["item_count"] = len(items)
    for key in ("pageCount", "pages", "page_count"):
        if data.get(key) is None:
            continue
        try:
            meta["page_count"] = int(data[key])
        except (TypeError, ValueError):
            continue
        break
    if not {"total_count", "item_count", "page_count"}.issubset(meta):
        return None
    return {
        "total_count": int(meta["total_count"]),
        "item_count": int(meta["item_count"]),
        "page_count": int(meta["page_count"]),
    }


def _latest_success_structure(observations):
    records = _complete_reset_records(observations, 0)
    if not records:
        return None
    return records[-1]


def _as_int_or_none(value):
    if value is None or value is False:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dom_matches_success_structure(state, meta):
    if not meta:
        return False
    if meta.get("total_count") is not None:
        dom_total = _as_int_or_none((state or {}).get("total_count"))
        if dom_total is None or dom_total != int(meta["total_count"]):
            return False
    if meta.get("item_count") is not None:
        if int(state.get("row_count") or 0) != int(meta["item_count"]):
            return False
    page_count = meta.get("page_count")
    if page_count == 1 and state.get("next_enabled"):
        return False
    if isinstance(page_count, int) and page_count > 1 and not state.get("next_enabled"):
        return False
    return True


def _complete_reset_records(observations, records_before=0):
    records = getattr(observations, "success_records", None) or []
    return list(records)[int(records_before or 0):]


_LIST_GATE_NO_COMPLETE_RESPONSE = "no_complete_unfiltered_response"
_LIST_GATE_STRUCTURE_UNPARSEABLE = "unfiltered_response_structure_unparseable"
_LIST_GATE_RESPONSE_DOM_MISMATCH = "unfiltered_response_dom_mismatch"
_LIST_GATE_DOM_UNSTABLE = "unfiltered_dom_unstable"
_LIST_GATE_PASSED_ID_NOT_FOUND = "unfiltered_gate_passed_id_not_found"


def _unfiltered_restore_result(success, reason, return_detail):
    result = {"restored": bool(success), "reason": reason}
    return result if return_detail else result["restored"]


def _wait_for_unfiltered_list_restore(
    page,
    previous_state,
    read_state=None,
    observations=None,
    records_before=0,
    requests_before=None,
    target_absent_before=None,
    timeout_ms=6000,
    poll_interval_ms=250,
    return_detail=False,
):
    """Require this reset's complete 2xx target-absent response and stable DOM."""
    baseline = records_before if requests_before is None else requests_before
    if target_absent_before is None:
        target_absent_before = int(getattr(observations, "target_absent_2xx_count", 0) or 0)
    reader = read_state or _read_list_restore_state
    previous_table = (previous_state or {}).get("table_signature")
    stable_candidate = None
    stable_reads = 0
    saw_dom_match = False
    saw_stability_candidate = False
    polls = max(1, (int(timeout_ms) + int(poll_interval_ms) - 1) // int(poll_interval_ms))
    for _ in range(polls):
        page.wait_for_timeout(poll_interval_ms)
        current = reader(page) or {}
        records = _complete_reset_records(observations, baseline)
        meta = records[-1] if records else None
        if not records or not _dom_matches_success_structure(current, meta):
            stable_candidate = None
            stable_reads = 0
            continue
        saw_dom_match = True
        if (
            current.get("table_signature") == previous_table
            or current.get("row_count", 0) <= 0
            or not _pagination_synced_with_unfiltered_list(current)
        ):
            stable_candidate = None
            stable_reads = 0
            continue
        saw_stability_candidate = True
        candidate = _restore_fingerprint(current)
        if candidate == stable_candidate:
            stable_reads += 1
        else:
            stable_candidate = candidate
            stable_reads = 1
        if stable_reads >= 2:
            return _unfiltered_restore_result(True, None, return_detail)
    fresh_2xx = int(getattr(observations, "target_absent_2xx_count", 0) or 0) > target_absent_before
    structured = bool(_complete_reset_records(observations, baseline))
    if not fresh_2xx:
        reason = _LIST_GATE_NO_COMPLETE_RESPONSE
    elif not structured:
        reason = _LIST_GATE_STRUCTURE_UNPARSEABLE
    elif not saw_dom_match:
        reason = _LIST_GATE_RESPONSE_DOM_MISMATCH
    else:
        reason = _LIST_GATE_DOM_UNSTABLE
    return _unfiltered_restore_result(False, reason, return_detail)


def _find_target_row_by_id(page, app_id, expected_channel_name=""):
    """Locate by exact ID, then verify the channel from the row or expanded detail."""
    previous_state = _read_pagination_state(page) or {}
    restore_observations = None
    records_before = 0
    empty_before_reset = previous_state.get("row_count", 0) <= 0
    if empty_before_reset:
        restore_observations = _attach_list_response_observer(page)
        records_before = len(getattr(restore_observations, "success_records", []) or []) if restore_observations is not None else 0
    _reset_list_filters(page)
    if empty_before_reset:
        try:
            restored = _wait_for_unfiltered_list_restore(
                page,
                previous_state,
                observations=restore_observations,
                records_before=records_before,
            )
        finally:
            _detach_list_response_observer(restore_observations)
        if not restored:
            return {
                "found": False,
                "reason": "list_not_restored_after_reset",
                **_redacted_locate_facts(None),
            }
    if not _go_to_first_page(page):
        return {"found": False, "reason": "pagination_unstable", **_redacted_locate_facts(None)}
    channel_error = None
    channel_facts = None

    for _ in range(50):
        _expand_visible_rows(page)
        payload = page.evaluate(_MAIN_LIST_COLLECT_JS, True) or {}
        count = payload.get("main_table_count")
        if count is not None and count != 1:
            return {
                "found": False,
                "reason": "main_list_table_unavailable",
                **_redacted_locate_facts(None),
            }
        headers = payload.get("headers") or []
        matched = None
        for row in (payload or {}).get("rows") or []:
            ok, id_source = _id_matched_in_row(
                app_id, headers, row.get("cells") or [], row.get("detail_id") or ""
            )
            if ok:
                matched = (row, id_source)
                break
        if matched:
            row, id_source = matched
            aligned = _align_header_cells(headers, row.get("cells") or [])
            main_channel = _main_channel_from_row(headers, row.get("cells") or [])
            facts = _channel_verify_facts(
                expected_channel_name,
                main_channel,
                row.get("detail_channel") or "",
                aligned_used=bool(aligned),
                id_field_source=id_source,
            )
            redacted = _redacted_locate_facts(facts)
            if facts["verified"]:
                return {
                    "found": True,
                    "row_idx": row.get("row_idx"),
                    "channel_source": facts["source"],
                    "reason": None,
                    **redacted,
                }
            channel_error = facts["reason"]
            channel_facts = redacted
            break

        moved = _click_next_page_and_wait(page)
        if moved is None:
            return {"found": False, "reason": "pagination_unstable", **_redacted_locate_facts(channel_facts)}
        if not moved:
            break
    else:
        return {
            "found": False,
            "reason": "page_scan_limit_reached",
            **_redacted_locate_facts(channel_facts),
        }

    if channel_error:
        return {"found": False, "reason": channel_error, **_redacted_locate_facts(channel_facts)}
    return {"found": False, "reason": "not_found", **_redacted_locate_facts(channel_facts)}


def _stage_create_save(page, execution_id, data, ref_cloud_app_link, ref_app_id, app_name,
                       jump_address, resource_fallback_page, settlement_type):
    """Stage 1: 用长链接搜索参考应用 → 点复制 → 填字段 → 保存 → 定位新行。
    Returns: {success: bool, target_app_id: str, error: err_or_None}
    """
    _set_stage(execution_id, "正在加载应用列表")
    _cleanup_overlays(page)
    page.goto("about:blank")
    page.goto(BASE_URL_H5, wait_until="domcontentloaded")
    try:
        page.wait_for_selector("table tbody tr", timeout=10000)
    except Exception:
        page.wait_for_timeout(3000)
    try:
        page.get_by_text("确定", exact=True).first.click(timeout=2000)
    except Exception:
        pass

    # 重置筛选
    try:
        page.get_by_text("重置", exact=True).first.click(timeout=3000)
        page.wait_for_timeout(1500)
    except Exception:
        pass

    _set_stage(execution_id, "正在记录创建前应用ID")
    before_rows = _collect_all_app_rows(page, reset_filters=True)
    if not before_rows:
        return {
            "success": False,
            "error": err("APP_ID_SNAPSHOT_FAILED", "VERIFY", "创建前未读取到任何应用ID", NEXT_MANUAL),
        }
    before_ids = set(before_rows)
    print(f"[create_app] 创建前应用ID数量: {len(before_ids)}")

    _shot(page, "01_list")

    # ====== 主路径：用长链接搜索参考应用并点复制 ======
    _set_stage(execution_id, f"正在搜索参考应用(长链接)")
    primary = _search_by_link(page, ref_cloud_app_link)

    # 安全边界
    if primary["error"] == "SEARCH_FAILED":
        # 主路径组件缺失，返回错误，不进兜底
        return {"success": False, "error": err("SEARCH_FAILED", "SEARCH",
                f"主路径失败：未找到长链接输入框/搜索按钮", NEXT_STOP)}
    if primary.get("uncertain"):
        # 不确定是否点了复制，不能进兜底
        return {"success": False, "error": err("COPY_RESULT_UNKNOWN", "COPY",
                "无法确认是否已经点击复制", NEXT_STOP)}

    copy_clicked = primary["clicked"]

    # ====== 兜底路径：主路径明确未点击复制时，按 app_id 逐页定位 ======
    if not copy_clicked:
        _set_stage(execution_id, f"主路径：长链接搜索失败\n兜底路径：按 app_id={ref_app_id} 逐页精确定位")
        fallback = _locate_by_app_id(page, ref_app_id)
        if fallback.get("error") == "COPY_FAILED":
            return {"success": False, "error": err("COPY_FAILED", "COPY",
                    f"两条路径都没有找到复制源或复制按钮 (ref_app_id={ref_app_id})", NEXT_STOP)}
        if not fallback.get("clicked"):
            return {"success": False, "error": err("COPY_RESULT_UNKNOWN", "COPY",
                    "兜底路径无法确认复制结果", NEXT_STOP)}
        copy_clicked = True

    try:
        page.wait_for_selector(".el-dialog__wrapper:not([style*='display: none']) .el-tabs__item", timeout=STEP_TIMEOUT)
    except Exception:
        page.wait_for_timeout(1000)
    _shot(page, "02_copy_dialog")

    # 体验配置
    _set_stage(execution_id, "正在填写体验配置")
    if not _go_tab(page, "体验配置"):
        return {"success": False, "error": err("TAB_SWITCH_FAILED", "FILL", f"无法切换到体验配置Tab", NEXT_MANUAL)}
    app_name_filled = _js_fill(page, "应用名称", app_name)
    if not app_name_filled:
        print("[create_app] WARN: 应用名称 填写失败")
    else:
        page.keyboard.press("Tab")
        page.wait_for_timeout(300)
    _bp = data.get("base_platform", "")
    if _bp:
        _js_select(page, "底座", _bp)
    channel_selected = _select_and_verify_create_channel(page, data["actual_channel_name"])
    if not channel_selected.get("success"):
        return {"success": False, "error": channel_selected["error"]}
    page.keyboard.press("Tab")
    page.wait_for_timeout(300)
    page.wait_for_timeout(500)
    page.evaluate("""() => {
      const pops = document.querySelectorAll('.el-popover, .el-popper, [id^="el-popover-"]');
      for (const p of pops) { if (p.style.display !== 'none') p.style.display = 'none'; }
    }""")
    _shot(page, "03_exp_config")

    # 登录页配置
    _set_stage(execution_id, "正在填写登录页配置")
    if not _go_tab(page, "登录页配置"):
        return {"success": False, "error": err("TAB_SWITCH_FAILED", "FILL", f"无法切换到登录页配置Tab", NEXT_MANUAL)}
    if jump_address:
        page.evaluate("""() => {
          const wrappers = document.querySelectorAll('.el-dialog__wrapper');
          for (const w of wrappers) {
            if (w.style.display === 'none') continue;
            const items = w.querySelectorAll('.el-form-item');
            for (const it of items) {
              if (it.offsetParent === null) continue;
              const lblEl = it.querySelector('.el-form-item__label');
              if (!lblEl) continue;
              if (lblEl.innerText.replace(/[ *:：]/g, '').trim() !== '指定访问页面') continue;
              const sw = it.querySelector('.el-switch');
              if (sw && !sw.className.includes('is-checked')) sw.click();
            }
          }
        }""")
        page.wait_for_timeout(800)
        filled = page.evaluate("""
        (jumpAddr) => {
          const wrappers = document.querySelectorAll('.el-dialog__wrapper');
          for (const w of wrappers) {
            if (w.style.display === 'none') continue;
            const items = w.querySelectorAll('.el-form-item');
            for (const it of items) {
              if (it.offsetParent === null) continue;
              const lblEl = it.querySelector('.el-form-item__label');
              if (!lblEl || !lblEl.innerText.includes('配置调起路径')) continue;
              const input = it.querySelector('input.el-input__inner');
              if (input) {
                const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                setter.call(input, jumpAddr);
                input.dispatchEvent(new Event('input', {bubbles: true}));
                input.dispatchEvent(new Event('change', {bubbles: true}));
                return true;
              }
            }
          }
          return false;
        }
        """, jump_address)
        if not filled:
            print("[create_app] WARN: 配置调起路径 填写失败")
    _shot(page, "04_login_config")

    # 基础配置
    _set_stage(execution_id, "正在填写基础配置")
    if not _go_tab(page, "基础配置"):
        return {"success": False, "error": err("TAB_SWITCH_FAILED", "FILL", f"无法切换到基础配置Tab", NEXT_MANUAL)}
    fallback_filled = _fill_and_verify_resource_fallback(page, resource_fallback_page)
    if not fallback_filled["success"]:
        return {"success": False, "error": fallback_filled["error"], "save_may_have_occurred": False}
    if settlement_type and not _js_select(page, "结算类型", settlement_type):
        return {"success": False, "error": err(
            "SETTLEMENT_SELECT_FAILED", "FILL", "结算类型未能完成稳定原生选择，已停止保存", NEXT_MANUAL
        ), "save_may_have_occurred": False}
    _shot(page, "05_basic_config")

    # 保存：资源兜底字段已在选择结算类型前完成物理 Tab 往返和 DOM/model
    # 回读；结算类型也已完成原生选择、下拉关闭及 DOM/model 闭环。该组件
    # 在选择后拒绝额外 Tab 切换，不能以无关切换阻断已经证明稳定的保存。
    _set_stage(execution_id, "正在保存应用")
    page.evaluate("""() => {
      const wrappers = document.querySelectorAll('.el-dialog__wrapper');
      for (const w of wrappers) {
        if (w.style.display === 'none') continue;
        if (w.querySelectorAll('.el-tabs__item').length === 0) {
          const c = w.querySelector('.el-dialog__headerbtn'); if (c) c.click(); return;
        }
      }
    }""")
    page.wait_for_timeout(300)
    save_observer = _attach_save_click_observer(page)
    saved = _click_save_button(page)
    if not saved:
        _detach_save_click_observer(save_observer)
        return {"success": False, "error": err("SAVE_FAILED", "SAVE", "未找到保存按钮", NEXT_MANUAL)}
    try:
        save_observation = _wait_for_save_click_observation(page, save_observer)
    finally:
        _detach_save_click_observer(save_observer)
    _log_save_click_observation(save_observation)
    if save_observation.get("outcome") != "success":
        return _post_save_unconfirmed_failure(
            err("SAVE_FAILED", "SAVE", "保存响应未能唯一确认业务成功，已停止", NEXT_MANUAL)
        )
    try:
        page.wait_for_selector(".el-dialog__wrapper:not([style*='display: none'])", state="detached", timeout=8000)
    except Exception:
        page.wait_for_timeout(2000)
    _shot(page, "06_after_save")
    save_err = capture_page_errors(page, screenshot_name=f"app_save_{app_name}")
    if save_err["dialog_open"]:
        return _post_save_unconfirmed_failure(
            err("SAVE_FAILED", "SAVE", build_error_message(save_err, "保存失败(对话框未关闭)"), NEXT_MANUAL)
        )

    # 保存后不按应用名称定位；通过前后快照得到的新 ID 继续定位。
    try:
        page.wait_for_selector("table tbody tr", timeout=STEP_TIMEOUT)
    except Exception:
        pass

    _set_stage(execution_id, "正在识别新增应用ID")
    narrow_context_profiles = []
    identified = _identify_new_app(
        page, before_ids, data["actual_channel_name"], app_name, context_sink=narrow_context_profiles
    )
    if not identified["success"]:
        return _post_save_unconfirmed_failure(identified["error"])
    target_app_id = identified["app_id"]
    # Keep this filtered/narrow view intact: fallback double-read, close, then
    # re-resolve the exact same row before any switch action is even considered.
    try:
        narrow_handle = _locate_known_main_row_in_current_view(
            page, target_app_id, app_name, data["actual_channel_name"]
        )
    except Exception:
        narrow_handle = {"success": False}
    if not narrow_handle["success"]:
        return _post_save_unconfirmed_failure(_resource_fallback_readback_error(
            "VERIFY", "保存后窄路径主行身份未能稳定核验，已停止"
        ))
    fallback_persisted = _verify_resource_fallback_by_known_main_row(
        page, target_app_id, resource_fallback_page, app_name,
        data["actual_channel_name"], narrow_handle,
    )
    if not fallback_persisted["success"]:
        return _post_save_unconfirmed_failure(fallback_persisted["error"])
    try:
        rechecked_handle = _locate_known_main_row_in_current_view(
            page, target_app_id, app_name, data["actual_channel_name"]
        )
    except Exception:
        rechecked_handle = {"success": False}
    if not rechecked_handle.get("success") or not _same_known_main_handle(narrow_handle, rechecked_handle):
        return _post_save_unconfirmed_failure(_resource_fallback_readback_error(
            "VERIFY", "保存后资源兜底核验关窗后主行身份发生变化，已停止"
        ))
    _shot(page, "07_created")
    # This handle is internal-only and exists only until _stage_enable completes.
    return {"success": True, "target_app_id": target_app_id, "known_main_handle": rechecked_handle}


def _enable_unknown_failure(message):
    """A saved object may exist (and may now be enabled); freeze its original order."""
    return {
        "success": False,
        "error": err("PUBLISH_FAILED", "PUBLISH", message, NEXT_QUERY),
        "enable_may_have_occurred": True,
    }


def _prepare_exact_terminal_filters(page, channel_name, app_name):
    """Fill the sole visible list form without searching or retaining values."""
    opened = page.evaluate("""
    (channelName) => {
      const visible = node => {
        if (!node || node.closest('.el-dialog, .el-dialog__wrapper')) return false;
        const style = window.getComputedStyle(node);
        const rect = node.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden' &&
          style.opacity !== '0' && node.getAttribute('aria-hidden') !== 'true' &&
          rect.width > 0 && rect.height > 0;
      };
      const forms = Array.from(document.querySelectorAll('.el-form')).filter(visible);
      const matches = forms.map(form => {
        const channels = Array.from(form.querySelectorAll('.el-select')).filter(select => {
          if (!visible(select)) return false;
          const item = select.closest('.el-form-item');
          const label = item && item.querySelector('.el-form-item__label');
          const input = select.querySelector('input');
          const metadata = input ? `${input.placeholder || ''} ${input.getAttribute('aria-label') || ''}` : '';
          return /渠道/.test(metadata) || Boolean(label && /渠道/.test(label.innerText || ''));
        });
        const apps = Array.from(form.querySelectorAll('.el-form-item')).filter(item => {
          if (!visible(item)) return false;
          const label = item.querySelector('.el-form-item__label');
          const input = item.querySelector('input.el-input__inner');
          return Boolean(label && /应用.*(名称|名)/.test(label.innerText || '') && input && visible(input) &&
            !input.disabled && !input.readOnly);
        });
        return channels.length === 1 && apps.length === 1 ? {form, channel: channels[0]} : null;
      }).filter(Boolean);
      if (matches.length !== 1) return {opened: false};
      const input = matches[0].channel.querySelector('input.el-select__input') ||
        matches[0].channel.querySelector('input.el-input__inner');
      if (!input || input.disabled) return {opened: false};
      input.click();
      const searchInput = matches[0].channel.querySelector('input.el-select__input');
      if (searchInput && !searchInput.readOnly) {
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        setter.call(searchInput, channelName);
        searchInput.dispatchEvent(new Event('input', {bubbles: true}));
        searchInput.dispatchEvent(new Event('change', {bubbles: true}));
      }
      return {opened: true};
    }
    """, channel_name) or {}
    if not opened.get("opened"):
        return False
    page.wait_for_timeout(300)
    selected = page.evaluate("""
    (channelName) => {
      const visible = node => {
        const style = window.getComputedStyle(node); const rect = node.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0' &&
          node.getAttribute('aria-hidden') !== 'true' && rect.width > 0 && rect.height > 0;
      };
      const dropdowns = Array.from(document.querySelectorAll('.el-select-dropdown')).filter(visible);
      if (dropdowns.length !== 1) return false;
      const options = Array.from(dropdowns[0].querySelectorAll('.el-select-dropdown__item')).filter(option =>
        visible(option) && !option.className.includes('is-disabled') && (option.innerText || '').trim() === channelName
      );
      if (options.length !== 1) return false;
      options[0].click(); return true;
    }
    """, channel_name)
    if selected is not True:
        return False
    page.wait_for_timeout(300)
    configured = page.evaluate("""
    ({channelName, appName}) => {
      const visible = node => {
        if (!node || node.closest('.el-dialog, .el-dialog__wrapper')) return false;
        const style = window.getComputedStyle(node); const rect = node.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0' &&
          node.getAttribute('aria-hidden') !== 'true' && rect.width > 0 && rect.height > 0;
      };
      const forms = Array.from(document.querySelectorAll('.el-form')).filter(visible);
      const matches = forms.map(form => {
        const channels = Array.from(form.querySelectorAll('.el-select')).filter(select => {
          const item = select.closest('.el-form-item'); const label = item && item.querySelector('.el-form-item__label');
          const input = select.querySelector('input'); const metadata = input ? `${input.placeholder || ''} ${input.getAttribute('aria-label') || ''}` : '';
          return visible(select) && (/渠道/.test(metadata) || Boolean(label && /渠道/.test(label.innerText || '')));
        });
        const apps = Array.from(form.querySelectorAll('.el-form-item')).filter(item => {
          const label = item.querySelector('.el-form-item__label'); const input = item.querySelector('input.el-input__inner');
          return visible(item) && Boolean(label && /应用.*(名称|名)/.test(label.innerText || '') && input && visible(input) && !input.disabled && !input.readOnly);
        });
        return channels.length === 1 && apps.length === 1 ? {channel: channels[0], app: apps[0].querySelector('input.el-input__inner')} : null;
      }).filter(Boolean);
      if (matches.length !== 1) return false;
      const channelInput = matches[0].channel.querySelector('input.el-input__inner');
      const channelTags = Array.from(matches[0].channel.querySelectorAll('.el-tag__content, .el-select__tags-text')).map(tag => (tag.innerText || '').trim());
      if (!((channelInput && (channelInput.value || '').trim() === channelName) || channelTags.includes(channelName))) return false;
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
      setter.call(matches[0].app, appName);
      matches[0].app.dispatchEvent(new Event('input', {bubbles: true}));
      matches[0].app.dispatchEvent(new Event('change', {bubbles: true}));
      return (matches[0].app.value || '').trim() === appName;
    }
    """, {"channelName": channel_name, "appName": app_name})
    return configured is True


def _click_exact_terminal_search_once(page, channel_name, app_name):
    """Atomically recheck both exact filters and consume the one-search budget."""
    result = page.evaluate("""
    ({channelName, appName}) => {
      const visible = node => {
        if (!node || node.closest('.el-dialog, .el-dialog__wrapper')) return false;
        const style = window.getComputedStyle(node); const rect = node.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0' &&
          node.getAttribute('aria-hidden') !== 'true' && rect.width > 0 && rect.height > 0;
      };
      const forms = Array.from(document.querySelectorAll('.el-form')).filter(visible);
      const matches = forms.map(form => {
        const channels = Array.from(form.querySelectorAll('.el-select')).filter(select => {
          const item = select.closest('.el-form-item'); const label = item && item.querySelector('.el-form-item__label');
          const input = select.querySelector('input'); const metadata = input ? `${input.placeholder || ''} ${input.getAttribute('aria-label') || ''}` : '';
          return visible(select) && (/渠道/.test(metadata) || Boolean(label && /渠道/.test(label.innerText || '')));
        });
        const apps = Array.from(form.querySelectorAll('.el-form-item')).filter(item => {
          const label = item.querySelector('.el-form-item__label'); const input = item.querySelector('input.el-input__inner');
          return visible(item) && Boolean(label && /应用.*(名称|名)/.test(label.innerText || '') && input && visible(input));
        });
        return channels.length === 1 && apps.length === 1 ? {form, channel: channels[0], app: apps[0].querySelector('input.el-input__inner')} : null;
      }).filter(Boolean);
      if (matches.length !== 1) return false;
      const channelInput = matches[0].channel.querySelector('input.el-input__inner');
      const channelTags = Array.from(matches[0].channel.querySelectorAll('.el-tag__content, .el-select__tags-text')).map(tag => (tag.innerText || '').trim());
      if (!((channelInput && (channelInput.value || '').trim() === channelName) || channelTags.includes(channelName)) ||
          !matches[0].app || (matches[0].app.value || '').trim() !== appName) return false;
      const buttons = Array.from(matches[0].form.querySelectorAll('button')).filter(button =>
        visible(button) && !button.disabled && button.getAttribute('aria-disabled') !== 'true' &&
        (button.innerText || '').replace(/\\s/g, '').trim() === '搜索'
      );
      if (buttons.length !== 1) return false;
      buttons[0].click(); return true;
    }
    """, {"channelName": channel_name, "appName": app_name})
    return result is True


def _wait_for_fresh_exact_terminal_proof(page, observations, records_before, requests_before, app_id, app_name, channel_name):
    """Accept one new exact response only when its one-row DOM is stable twice."""
    stable_fingerprint = None
    stable_reads = 0
    for _ in range(24):
        page.wait_for_timeout(250)
        records = list(getattr(observations, "exact_required_filter_success_records", []) or [])[records_before:]
        request_count = int(getattr(observations, "exact_required_filter_request_count", 0) or 0) - int(requests_before or 0)
        if len(records) != 1 or request_count != 1:
            stable_fingerprint = None
            stable_reads = 0
            continue
        state = _read_list_restore_state(page) or {}
        meta = records[0]
        if (
            not _dom_matches_success_structure(state, meta)
            or meta.get("item_count") != 1
            or meta.get("total_count") != 1
        ):
            stable_fingerprint = None
            stable_reads = 0
            continue
        snapshot = _snapshot_known_main_id_rows(page, app_id)
        decision = _known_main_identity_from_snapshot(snapshot, app_id, app_name, channel_name)
        if not decision.get("success"):
            _log_known_main_snapshot(snapshot, decision, False)
            stable_fingerprint = None
            stable_reads = 0
            continue
        row = decision["row"]
        fingerprint = (
            snapshot.get("page"), row.get("logical_key"), row.get("row_idx"),
            _restore_fingerprint(state),
        )
        if fingerprint == stable_fingerprint:
            stable_reads += 1
        else:
            stable_fingerprint = fingerprint
            stable_reads = 1
        _log_known_main_snapshot(snapshot, decision, stable_reads >= 2)
        if stable_reads >= 2:
            return True
    return False


def _verify_enable_with_fresh_exact_terminal_query(page, app_id, app_name, channel_name):
    """D-044 B: a new observer plus one exact dual-filter terminal query only."""
    observations = _attach_list_response_observer(
        page, exact_terminal_filters=(channel_name, app_name)
    )
    if observations is None:
        return False
    try:
        if not _prepare_exact_terminal_filters(page, channel_name, app_name):
            return False
        records_before = len(getattr(observations, "exact_required_filter_success_records", []) or [])
        requests_before = int(getattr(observations, "exact_required_filter_request_count", 0) or 0)
        if not _click_exact_terminal_search_once(page, channel_name, app_name):
            return False
        return _wait_for_fresh_exact_terminal_proof(
            page, observations, records_before, requests_before, app_id, app_name, channel_name
        )
    finally:
        _detach_list_response_observer(observations)


def _stage_enable(page, execution_id, target_app_id, app_name, actual_channel_name, known_main_handle):
    """Stage 2: one anchored enable click plus new unfiltered terminal proof."""
    _set_stage(execution_id, "正在检查应用状态")
    if not isinstance(known_main_handle, dict) or not known_main_handle.get("success"):
        return _enable_unknown_failure("缺少保存后已核验主行身份，已停止")
    try:
        pre_click_handle = _locate_known_main_row_in_current_view(
            page, target_app_id, app_name, actual_channel_name
        )
    except Exception:
        pre_click_handle = {"success": False}
    if not pre_click_handle.get("success") or not _same_known_main_handle(known_main_handle, pre_click_handle):
        return _enable_unknown_failure("上线前同行身份未能连续稳定复核，已停止")
    if _post_save_narrow_row_switch_state(page, target_app_id, app_name, actual_channel_name) != "switch_unchecked":
        return _enable_unknown_failure("上线前开关不是唯一可读未启用状态，已停止")
    if _visible_message_box_count(page) != 0:
        return _enable_unknown_failure("上线前存在未归属确认框，已停止")

    _set_stage(execution_id, "正在发布上线")
    observer = _attach_save_click_observer(page)
    if observer is None:
        return _enable_unknown_failure("上线写请求观察器不可用，已停止")
    clicked = False
    observation = {"outcome": "observer_unavailable"}
    try:
        clicked = _click_unchecked_switch_by_known_main_row(
            page, target_app_id, pre_click_handle.get("page"), pre_click_handle.get("row_idx"),
            pre_click_handle.get("row_key"), pre_click_handle.get("key_kind"), app_name,
            actual_channel_name,
        )
        if clicked:
            try:
                observation = _wait_for_switch_action_observation(page, observer)
            except Exception:
                observation = {"outcome": "observer_unavailable"}
    finally:
        _detach_save_click_observer(observer)
    if not clicked:
        return _enable_unknown_failure("上线同行开关未通过原子复核或不可点击，已停止")
    try:
        observer.post_action_switch_state = _post_save_narrow_row_switch_state(
            page, target_app_id, app_name, actual_channel_name
        )
    except Exception:
        observer.post_action_switch_state = "unreadable"
    _log_enable_click_observation(observer, observation)
    if observation.get("outcome") != "success":
        return _enable_unknown_failure("上线响应未能唯一确认业务成功，已停止")

    # Checked state is diagnostic only and never substitutes for the write proof.
    if observer.post_action_switch_state != "switch_checked":
        return _enable_unknown_failure("上线后开关状态未能连续稳定回读，已停止")

    # Throw away every narrow locator, response and DOM observation. The final
    # proof owns a new observer and performs one exact channel+application query.
    try:
        terminal_verified = _verify_enable_with_fresh_exact_terminal_query(
            page, target_app_id, app_name, actual_channel_name
        )
    except Exception:
        terminal_verified = False
    if not terminal_verified:
        return _enable_unknown_failure("上线后精确窄查询终验未能唯一稳定核验目标主行，已停止")
    _shot(page, "08_published")
    return {"success": True, "error": None}


def _stage_set_group(page, execution_id, target_app_id, group_name, app_name, actual_channel_name):
    """Stage 3: 打开云机链接设置 → 选按分组 → 填值 → 确定 → 读回验证。
    Returns: {success: bool, error: err_or_None}
    """
    _set_stage(execution_id, "正在设置分组")
    located = _find_target_row_by_id(page, target_app_id, actual_channel_name)
    if not located.get("found"):
        return {
            "success": False,
            "error": err("TARGET_APP_NOT_FOUND", "GROUP", f"设置分组前未找到应用ID={target_app_id}", NEXT_MANUAL),
        }
    target_idx = located["row_idx"]

    if not _open_group_dialog(page, target_idx):
        capture_page_errors(page, screenshot_name=f"app_group_fail_{app_name}")
        return {"success": False, "error": err("GROUP_SET_FAILED", "GROUP", "未找到云机链接设置按钮", NEXT_MANUAL)}
    page.wait_for_timeout(1500)

    initial_state = _read_group_dialog(page, group_name)
    if not initial_state.get("found"):
        capture_page_errors(page, screenshot_name=f"app_group_fail_{app_name}")
        return {"success": False, "error": err("GROUP_SET_FAILED", "GROUP", "未打开包含'按分组'的设置弹窗", NEXT_MANUAL)}

    # Step 1: 先点"按分组"radio，单独一个evaluate，让Vue有时间响应
    radio_clicked = page.evaluate("""
    () => {
      const wrappers = document.querySelectorAll('.el-dialog__wrapper');
      for (const wrapper of wrappers) {
        if (wrapper.style.display === 'none') continue;
        if (!wrapper.innerText.includes('按分组')) continue;
        const groupMode = Array.from(wrapper.querySelectorAll('.el-radio, .el-radio-button')).find(
          el => el.offsetParent !== null && el.innerText && el.innerText.trim().includes('按分组')
        );
        if (!groupMode) return {clicked: false, reason: '未找到按分组选项'};
        // 优先点 label，再兜底点 input
        const label = groupMode.querySelector('label') || groupMode;
        const radioInput = groupMode.querySelector('input[type="radio"]');
        if (label && label !== groupMode) { label.click(); }
        else if (radioInput) { radioInput.click(); }
        else { groupMode.click(); }
        return {clicked: true};
      }
      return {clicked: false, reason: '弹窗不可见'};
    }
    """)
    print(f"[create_app] 按分组radio: {radio_clicked}")
    if not radio_clicked.get("clicked"):
        capture_page_errors(page, screenshot_name=f"app_group_fail_{app_name}")
        return {"success": False, "error": err("GROUP_SET_FAILED", "GROUP", f"点击按分组失败: {radio_clicked.get('reason')}", NEXT_MANUAL)}

    # 等 Vue 渲染分组输入框
    page.wait_for_timeout(1000)

    # Step 2: 填分组名（带验证和重试）
    filled_ok = False
    for _attempt in range(3):
        filled_ok = page.evaluate("""
        (groupName) => {
          const wrappers = document.querySelectorAll('.el-dialog__wrapper');
          for (const wrapper of wrappers) {
            if (wrapper.style.display === 'none') continue;
            if (!wrapper.innerText.includes('按分组')) continue;
            // 精确找"指定分组"标签下的input
            const formItems = wrapper.querySelectorAll('.el-form-item');
            let targetInput = null;
            for (const fi of formItems) {
              if (fi.offsetParent === null) continue;
              const lbl = fi.querySelector('.el-form-item__label');
              if (!lbl) continue;
              if (!lbl.innerText.includes('指定分组') && !lbl.innerText.includes('分组')) continue;
              const inp = fi.querySelector('input.el-input__inner, input[type="text"]');
              if (inp) { targetInput = inp; break; }
            }
            // 兜底：找最后一个可见的文本输入框
            if (!targetInput) {
              const allInputs = Array.from(wrapper.querySelectorAll('input.el-input__inner'))
                .filter(inp => inp.offsetParent !== null && inp.type === 'text');
              if (allInputs.length) targetInput = allInputs[allInputs.length - 1];
            }
            if (!targetInput) return {filled: false, reason: '未找到分组输入框'};
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            setter.call(targetInput, groupName);
            targetInput.dispatchEvent(new Event('input', {bubbles: true}));
            targetInput.dispatchEvent(new Event('change', {bubbles: true}));
            // 验证值是否填入
            return {filled: true, actual_value: targetInput.value};
          }
          return {filled: false, reason: '弹窗不可见'};
        }
        """, group_name)
        print(f"[create_app] 填分组名 (attempt {_attempt+1}): {filled_ok}")
        if filled_ok and filled_ok.get("filled") and filled_ok.get("actual_value") == group_name:
            break
        page.wait_for_timeout(500)

    if not filled_ok or not filled_ok.get("filled") or filled_ok.get("actual_value") != group_name:
        capture_page_errors(page, screenshot_name=f"app_group_fail_{app_name}")
        return {"success": False, "error": err("GROUP_SET_FAILED", "GROUP",
            f"分组名填写失败: 期望={group_name}, 实际={filled_ok.get('actual_value', '(空)') if filled_ok else '(未执行)'}", NEXT_MANUAL)}

    page.wait_for_timeout(500)

    # Step 3: 点确定
    confirmed = page.evaluate("""
    () => {
      const wrappers = document.querySelectorAll('.el-dialog__wrapper');
      for (const wrapper of wrappers) {
        if (wrapper.style.display === 'none') continue;
        if (!wrapper.innerText.includes('按分组')) continue;
        const confirm = Array.from(wrapper.querySelectorAll('button')).find(
          b => b.offsetParent !== null && b.innerText && b.innerText.trim() === '确定'
        );
        if (confirm) { confirm.click(); return true; }
      }
      return false;
    }
    """)
    if not confirmed:
        capture_page_errors(page, screenshot_name=f"app_group_fail_{app_name}")
        return {"success": False, "error": err("GROUP_SET_FAILED", "GROUP", "未找到确定按钮", NEXT_MANUAL)}

    page.wait_for_timeout(3000)
    if _read_group_dialog(page, group_name).get("found"):
        capture_page_errors(page, screenshot_name=f"app_group_fail_{app_name}")
        return {"success": False, "error": err("GROUP_SET_FAILED", "GROUP", "分组提交后弹窗未关闭，可能存在页面校验错误", NEXT_MANUAL)}

    # 读回验证
    if not _open_group_dialog(page, target_idx):
        return {"success": False, "error": err("GROUP_SET_FAILED", "GROUP", "分组提交后无法重新打开设置弹窗进行读回", NEXT_MANUAL)}
    page.wait_for_timeout(1500)
    verified_state = _read_group_dialog(page, group_name)
    _shot(page, "09_group_verify")
    _close_group_dialog(page)
    page.wait_for_timeout(500)

    if not verified_state.get("found"):
        return {"success": False, "error": err("GROUP_SET_FAILED", "GROUP", "重新打开后未找到分组设置弹窗", NEXT_MANUAL)}
    if not verified_state.get("mode_selected"):
        return {"success": False, "error": err("GROUP_SET_FAILED", "GROUP", "分组读回失败: '按分组'未选中", NEXT_MANUAL)}
    if not verified_state.get("expected_present"):
        vals = verified_state.get("values") or []
        return {"success": False, "error": err("GROUP_SET_FAILED", "GROUP", f"分组读回不一致: 期望={group_name}, 实际={vals}", NEXT_MANUAL)}
    _shot(page, "09_group_set")
    return {"success": True, "error": None}


def _stage_collect_result(page, execution_id, target_app_id, app_name, actual_channel_name):
    """Read the final row and verify that the app is online with a generated link."""
    _set_stage(execution_id, "正在校验终态")
    located = _find_target_row_by_id(page, target_app_id, actual_channel_name)
    if not located.get("found"):
        return {
            "success": False,
            "error": err("TARGET_APP_NOT_FOUND", "VERIFY", f"终态校验前未找到应用ID={target_app_id}", NEXT_MANUAL),
        }
    target_idx = located["row_idx"]
    try:
        row_data = page.evaluate("""
        (rowIdx) => {
          const primaryRows = document.querySelectorAll('.el-table__body-wrapper tbody tr');
          const sourceRows = primaryRows.length ? primaryRows : document.querySelectorAll('table tbody tr');
          const rows = Array.from(sourceRows).filter(row => !row.classList.contains('el-table__expanded-row'));
          if (rowIdx < 0 || rowIdx >= rows.length) return {};
          const row = rows[rowIdx];
          const primaryHeaders = document.querySelectorAll('.el-table__header-wrapper th');
          const headerNodes = primaryHeaders.length ? primaryHeaders : document.querySelectorAll('th');
          const headers = Array.from(headerNodes).map(
            th => (th.innerText || '').trim()
          );
          const cells = row.querySelectorAll('td');
          const result = {};
          for (let i = 0; i < cells.length && i < headers.length; i++) {
            result[headers[i]] = (cells[i].innerText || '').trim();
          }
          const sw = row.querySelector('.el-switch');
          if (sw) result._status = sw.className.includes('is-checked') ? 'ON' : 'OFF';
          return result;
        }
        """, target_idx)
    except Exception as exc:
        return {
            "success": False,
            "error": err("FINAL_VERIFY_FAILED", "VERIFY", f"终态读取失败: {exc}", NEXT_MANUAL),
        }

    base_val = row_data.get("底座", "")
    status_val = row_data.get("_status", "")
    cloud_link = row_data.get("长连接", "")
    short_link = row_data.get("应用链接", "")
    valid_bases = {"华为底座2.0", "华为底座", "蜂助手底座", "中兴底座", "红手指底座"}

    reasons = []
    if base_val not in valid_bases:
        reasons.append(f"底座={base_val}")
    if status_val != "ON":
        reasons.append("状态未开启")
    if not cloud_link:
        reasons.append("未获取到链接")
    if reasons:
        return {
            "success": False,
            "error": err(
                "FINAL_VERIFY_FAILED",
                "VERIFY",
                f"终态校验失败: {'; '.join(reasons)}",
                NEXT_MANUAL,
            ),
        }

    _shot(page, "10_final")
    return {
        "success": True,
        "data": {
            "app_id": str(target_app_id),
            "app_name": app_name,
            "cloud_app_link": cloud_link,
            "cloud_app_short_link": short_link,
            "row_data": row_data,
        },
    }


def execute_create_app(request: dict) -> dict:
    task_id = request["task_id"]
    data = request["data"]
    environment = request.get("environment", "TEST")
    idempotency_key = request["idempotency_key"]

    existing = ex.find_by_idempotency(idempotency_key)
    if existing:
        return ex.build_result(
            task_id,
            OPERATION,
            idempotency_key,
            execution=existing,
            accepted=True,
            data=json.loads(existing.get("output_json") or "{}"),
            error=(
                json.loads(existing.get("error_json") or "null")
                if existing.get("error_json")
                else None
            ),
            environment=existing.get("environment"),
        )

    def rejected(error, failed_stage):
        stage_data = {"completed_stages": [], "failed_stage": failed_stage}
        enriched_error = {**error, **stage_data}
        return ex.build_result(
            task_id,
            OPERATION,
            idempotency_key,
            accepted=False,
            data=stage_data,
            error=enriched_error,
            environment=environment,
        )

    if ex.is_locked():
        active = ex.get_active_execution_id()
        return rejected(
            err("EXECUTOR_BUSY", "LOCK", f"执行端忙碌中，占用执行: {active}", NEXT_QUERY),
            "LOCK",
        )

    required = [
        "application_type",
        "business_object",
        "actual_channel_name",
        "jump_address",
        "resource_fallback_page",
        "settlement_type",
    ]
    missing = [field for field in required if not (data.get(field) or "").strip()]
    if missing:
        return rejected(
            err(
                "FIELD_VALIDATION",
                "VALIDATE",
                f"必填字段为空: {', '.join(missing)}",
                NEXT_STOP,
            ),
            "VALIDATE",
        )

    group_name = (data.get("group_name") or "").strip()
    jump_address = (data.get("jump_address") or "").strip()
    resource_fallback_page = (data.get("resource_fallback_page") or "").strip()
    settlement_type = (data.get("settlement_type") or "").strip()
    business_object = data["business_object"].strip()
    app_name = business_object

    ref_cloud_app_link = (data.get("ref_cloud_app_link") or "").strip()
    app_type = data["application_type"].strip()
    ref_source = DEFAULT_REF_SOURCES.get(app_type, {})
    if not ref_cloud_app_link:
        ref_cloud_app_link = ref_source.get("cloud_app_link", "")
    ref_app_id = ref_source.get("app_id", "")
    if not ref_cloud_app_link:
        return rejected(
            err(
                "FIELD_VALIDATION",
                "VALIDATE",
                f"无法确定参考应用长链接（ref_cloud_app_link未传且application_type={app_type}无默认值）",
                NEXT_STOP,
            ),
            "VALIDATE",
        )

    execution = ex.create_execution(task_id, OPERATION, idempotency_key, data, environment)
    execution_id = execution["execution_id"]
    if not ex.acquire_lock(execution_id):
        lock_error = err("EXECUTOR_BUSY", "LOCK", "获取执行锁失败", NEXT_QUERY)
        stage_data = {"completed_stages": [], "failed_stage": "LOCK"}
        enriched_error = {**lock_error, **stage_data}
        ex.finalize_execution(
            execution_id,
            ex.BIZ_UNKNOWN,
            output_data=stage_data,
            error=enriched_error,
        )
        return ex.build_result(
            task_id,
            OPERATION,
            idempotency_key,
            execution=ex.find_by_execution_id(execution_id),
            accepted=False,
            data=stage_data,
            error=enriched_error,
            environment=environment,
        )

    ex.update_execution(execution_id, execution_state=ex.STATE_RUNNING)
    completed_stages = []
    current_stage = "LOGIN"
    pw = None
    page = None

    def finish_failure(error, failed_stage, business_status=ex.BIZ_FAILED):
        stage_data = {
            "completed_stages": list(completed_stages),
            "failed_stage": failed_stage,
        }
        enriched_error = {**error, **stage_data}
        ex.finalize_execution(
            execution_id,
            business_status,
            output_data=stage_data,
            error=enriched_error,
        )
        return ex.build_result(
            task_id,
            OPERATION,
            idempotency_key,
            execution=ex.find_by_execution_id(execution_id),
            accepted=True,
            data=stage_data,
            error=enriched_error,
            environment=environment,
        )

    try:
        pw, _browser, page = get_browser_page()
        page.bring_to_front()
        _set_stage(execution_id, "正在登录")
        auth = ensure_login(page)
        if not auth["success"]:
            return finish_failure(
                err("NOT_LOGGED_IN", "LOGIN", f"登录失败: {auth['message']}", NEXT_QUERY),
                "LOGIN",
            )

        current_stage = "CREATE_SAVE"
        create_result = _stage_create_save(
            page,
            execution_id,
            data,
            ref_cloud_app_link,
            ref_app_id,
            app_name,
            jump_address,
            resource_fallback_page,
            settlement_type,
        )
        if not create_result["success"]:
            return finish_failure(
                create_result["error"],
                current_stage,
                _create_failure_business_status(create_result),
            )
        target_app_id = create_result["target_app_id"]
        completed_stages.append(current_stage)

        current_stage = "ENABLE"
        enable_result = _stage_enable(
            page,
            execution_id,
            target_app_id,
            app_name,
            data["actual_channel_name"],
            create_result.get("known_main_handle"),
        )
        if not enable_result["success"]:
            return finish_failure(enable_result["error"], current_stage, ex.BIZ_UNKNOWN)
        completed_stages.append(current_stage)

        if group_name:
            current_stage = "SET_GROUP"
            group_result = _stage_set_group(
                page,
                execution_id,
                target_app_id,
                group_name,
                app_name,
                data["actual_channel_name"],
            )
            if not group_result["success"]:
                return finish_failure(group_result["error"], current_stage)
            completed_stages.append(current_stage)

        current_stage = "VERIFY"
        final_result = _stage_collect_result(
            page,
            execution_id,
            target_app_id,
            app_name,
            data["actual_channel_name"],
        )
        if not final_result["success"]:
            return finish_failure(final_result["error"], current_stage, ex.BIZ_UNKNOWN)

        completed_stages.append("COMPLETED")
        output = {
            **final_result["data"],
            "completed_stages": list(completed_stages),
        }
        ex.finalize_execution(
            execution_id,
            ex.BIZ_SUCCESS,
            output_data=output,
            evidence_ref=f"{execution_id}/app-final.json",
        )
        return ex.build_result(
            task_id,
            OPERATION,
            idempotency_key,
            execution=ex.find_by_execution_id(execution_id),
            accepted=True,
            data=output,
            environment=environment,
        )
    except Exception as exc:
        try:
            error_info = (
                capture_page_errors(page, screenshot_name=f"app_exc_{app_name}")
                if page
                else {}
            )
            message = str(exc) + " | " + build_error_message(error_info)
        except Exception:
            message = str(exc)
        return finish_failure(
            err("APP_CREATE_FAILED", "EXECUTE", message, NEXT_QUERY),
            current_stage,
            ex.BIZ_UNKNOWN,
        )
    finally:
        ex.release_lock(execution_id)
        if pw:
            try:
                pw.stop()
            except Exception:
                pass
