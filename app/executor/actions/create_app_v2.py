# -*- coding: utf-8 -*-
"""
创建应用 — 契约版（v2，JS 直操 DOM，绕过 Playwright :visible 伪类）。
"""
import base64
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qsl, quote, quote_plus, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import get_browser_page
from core.error_capture import capture_page_errors, build_error_message
from core.error_codes import err, NEXT_STOP, NEXT_QUERY, NEXT_MANUAL
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
    """用 JS 查可见对话框的 Tab 名称。"""
    try:
        return page.evaluate("""
        () => {
          const wrappers = document.querySelectorAll('.el-dialog__wrapper');
          for (const w of wrappers) {
            if (w.style.display === 'none') continue;
            const tabs = w.querySelectorAll('.el-tabs__item');
            if (tabs.length > 0) {
              return Array.from(tabs).map(t => t.innerText.trim());
            }
          }
          return [];
        }
        """)
    except Exception:
        return []


def _go_tab(page, name):
    """用 JS 点击目标 Tab。"""
    deadline = time.monotonic() + 6
    last_tabs = []
    while time.monotonic() < deadline:
        last_tabs = _available_tabs(page)
        if last_tabs:
            break
        page.wait_for_timeout(300)
    if not last_tabs:
        print(f"[create_app] WARN: 未找到Tab {name}，当前Tabs={last_tabs}")
        return False
    clicked = page.evaluate("""
    (tabName) => {
      const wrappers = document.querySelectorAll('.el-dialog__wrapper');
      for (const w of wrappers) {
        if (w.style.display === 'none') continue;
        const tabs = w.querySelectorAll('.el-tabs__item');
        for (const t of tabs) {
          if (t.innerText.trim().includes(tabName)) { t.click(); return true; }
        }
      }
      return false;
    }
    """, name)
    if clicked:
        page.wait_for_timeout(500)
        return True
    print(f"[create_app] WARN: Tab '{name}' 不在 {last_tabs} 中")
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


def _js_select(page, label, value):
    """用 JS 点下拉框并选项。"""
    opened = page.evaluate("""
    (label) => {
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
            const sel = it.querySelector('.el-select');
            if (sel) { sel.click(); return true; }
          }
        }
      }
      return false;
    }
    """, label)
    if not opened:
        return False
    page.wait_for_timeout(500)
    picked = page.evaluate("""
    (value) => {
      const dropdowns = document.querySelectorAll('.el-select-dropdown');
      for (const dd of dropdowns) {
        if (dd.style.display === 'none') continue;
        const items = dd.querySelectorAll('.el-select-dropdown__item');
        for (const it of items) {
          if (it.innerText.trim().includes(value)) { it.click(); return true; }
        }
      }
      return false;
    }
    """, value)
    if not picked:
        page.keyboard.press("Escape")
    return picked


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
    return page.evaluate("""() => {
      const wrappers = document.querySelectorAll('.el-dialog__wrapper');
      for (const w of wrappers) {
        if (w.style.display === 'none') continue;
        if (w.querySelectorAll('.el-tabs__item').length === 0) continue;
        const btns = w.querySelectorAll('button');
        for (const b of btns) { if (b.innerText.includes('保存')) { b.click(); return true; } }
      }
      return false;
    }""")


def _set_stage(execution_id, stage):
    try:
        ex.update_execution(execution_id, output_json=json.dumps({"_stage": stage}, ensure_ascii=False))
        print(f"[create_app] stage: {stage}")
    except Exception:
        pass


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


def _go_to_first_page(page):
    for _ in range(50):
        previous_state = _read_pagination_state(page)
        if previous_state.get("page_number") == 1:
            return True
        moved = page.evaluate("""() => {
          const prev = document.querySelector('.el-pagination .btn-prev');
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
      let count = 0;
      for (const row of document.querySelectorAll('table tbody tr')) {
        if (row.offsetParent === null) continue;
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
    """Read active page and a signature of visible primary rows separately."""
    return page.evaluate("""() => {
      const active = document.querySelector('.el-pagination .el-pager li.active');
      const primaryRows = document.querySelectorAll('.el-table__body-wrapper tbody tr');
      const allRows = primaryRows.length ? primaryRows : document.querySelectorAll('table tbody tr');
      const headerNodes = document.querySelectorAll('.el-table__header-wrapper th');
      const headers = Array.from(headerNodes).map(th => (th.innerText || '').trim());
      const rows = Array.from(allRows).filter(row =>
        row.offsetParent !== null && !row.classList.contains('el-table__expanded-row')
      );
      const rowSignature = rows.map(row => {
        const cells = Array.from(row.querySelectorAll('td'));
        const idIndex = headers.findIndex(header => header === 'ID' || header.includes('应用ID'));
        const id = idIndex >= 0 && cells[idIndex] ? (cells[idIndex].textContent || '').trim() : '';
        return `${id}|${(row.textContent || '').trim()}`;
      });
      const pageText = active ? active.innerText.trim() : '';
      const pageNumber = /^\\d+$/.test(pageText) ? Number(pageText) : 1;
      return {
        page_number: pageNumber,
        table_signature: JSON.stringify(rowSignature),
        row_count: rows.length
      };
    }""")


def _click_next_page_and_wait(page):
    """Move one page only when the next page is enabled and actually rendered."""
    previous_state = _read_pagination_state(page)
    moved = page.evaluate("""() => {
      const next = document.querySelector('.el-pagination .btn-next');
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


class _ListRequestObserver(list):
    """记录列表请求的 HTTP 状态，并持有 CDP 会话引用避免被回收。

    只保留状态码和布尔结果，不保存 URL、请求体、响应正文或筛选原文。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.keepalive = None
        self.carried_target_filter = False
        self.response_contains_target_channel = None
        self.target_filter_field = None
        self.target_filter_field_is_channel = None
        self.success_records = []


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


def _request_carries_target_filter(url, post_data, target_filter):
    """Return whether a list request payload contains the target filter value.

    The function only returns a boolean. It does not keep or return the URL,
    query string, request body, or channel text.
    """
    return _locate_target_filter_field(url, post_data, target_filter) is not None


def _filter_field_rank(field, is_channel):
    if not field:
        return 0
    if is_channel:
        return 3
    if field != "raw_text_only":
        return 2
    return 1


def _detach_list_response_observer(observations):
    """用完即释放 CDP 会话，避免长时间占用调试通道。"""
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


def _attach_list_response_observer(page, target_filter=None):
    """只读网络观察器：列表请求、HTTP 状态、是否携带筛选、响应是否含目标。

    只记录状态码和布尔结果。响应正文只在内存中为已携带目标筛选的请求做
    包含判断，随后丢弃；不写磁盘、日志、JSON 或观察器属性。

    通过 CDP 接入的既有页面上，显式开一个 CDP 会话并启用 Network 域最可靠；
    失败再退回 Playwright 页面事件监听。两者都不可用时返回 None，调用方据此
    给出"未观察到请求"而不是失败。
    """
    observations = _ListRequestObserver()
    list_request_ids = set()
    carried_request_ids = set()
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

    def _mark_list_request(url, post_data, request_id=None):
        if not _is_list_request_url(url):
            return False
        if request_id is not None:
            list_request_ids.add(request_id)
        location = _locate_target_filter_field(url, post_data, target_filter)
        if location is not None:
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
                and status is not None
                and 200 <= int(status) < 300
            )
            if not need_target and not need_structure:
                return
            try:
                result = session.send("Network.getResponseBody", {"requestId": request_id})
            except Exception:
                return
            if not isinstance(result, dict) or "body" not in result:
                return
            body = result.get("body") or ""
            try:
                if result.get("base64Encoded") and isinstance(body, str):
                    body = base64.b64decode(body).decode("utf-8", "ignore")
                if need_target:
                    _note_response_contains_target(_body_contains_target(body))
                if need_structure:
                    meta = _extract_list_structure(body)
                    if meta:
                        meta = dict(meta)
                        meta["status"] = int(status)
                        observations.success_records.append(meta)
            finally:
                body = None

        def _on_request(params):
            try:
                data = params or {}
                request = data.get("request") or {}
                request_id = data.get("requestId")
                url = request.get("url") or ""
                post_data = request.get("postData") or ""
                if not post_data and request.get("hasPostData") and request_id:
                    try:
                        extra = session.send("Network.getRequestPostData", {"requestId": request_id}) or {}
                        post_data = extra.get("postData") or ""
                    except Exception:
                        post_data = ""
                _mark_list_request(url, post_data, request_id)
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
            post_data = getattr(request, "post_data", None) or ""
            _mark_list_request(getattr(request, "url", ""), post_data)

        def _on_response(response):
            url = getattr(response, "url", "") or ""
            status = getattr(response, "status", 0)
            _record_status(url, status)
            request = getattr(response, "request", None)
            req_url = getattr(request, "url", url) if request is not None else url
            post_data = getattr(request, "post_data", None) if request is not None else ""
            if not _is_list_request_url(req_url or url):
                return
            need_target = _request_carries_target_filter(req_url or url, post_data or "", target_filter)
            need_structure = False
            try:
                need_structure = 200 <= int(status or 0) < 300
            except (TypeError, ValueError):
                need_structure = False
            if not need_target and not need_structure:
                return
            body = None
            try:
                if hasattr(response, "text"):
                    body = response.text()
                elif hasattr(response, "body"):
                    raw = response.body()
                    body = raw.decode("utf-8", "ignore") if isinstance(raw, (bytes, bytearray)) else raw
                if body is None:
                    return
                if need_target:
                    _note_response_contains_target(_body_contains_target(body))
                if need_structure:
                    meta = _extract_list_structure(body)
                    if meta:
                        meta = dict(meta)
                        meta["status"] = int(status or 0)
                        observations.success_records.append(meta)
            except Exception:
                return
            finally:
                body = None

        def _on_request_failed(request):
            _record_status(getattr(request, "url", ""), 0)

        page.on("request", _on_request)
        page.on("response", _on_response)
        page.on("requestfailed", _on_request_failed)
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


def _search_list_by_channel(page, channel_name, return_detail=False):
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


def _main_channel_from_row(headers, cells):
    aligned = _align_header_cells(headers, cells)
    if not aligned:
        return ""
    return _mapped_value(aligned, _is_channel_name_header)


def _is_app_id_header(header):
    text = (header or "").strip()
    return text == "ID" or "应用ID" in text


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
    payload = page.evaluate("""() => {
      const serialize = el => ({
        text: (el.innerText || el.textContent || '').trim(),
        classes: String(el.className || '')
      });
      const primaryRows = document.querySelectorAll('.el-table__body-wrapper tbody tr');
      const sourceRows = primaryRows.length ? primaryRows : document.querySelectorAll('table tbody tr');
      const rows = Array.from(sourceRows).filter(row => !row.classList.contains('el-table__expanded-row'));
      const primaryHeaders = document.querySelectorAll('.el-table__header-wrapper th');
      const headerNodes = primaryHeaders.length ? primaryHeaders : document.querySelectorAll('th');
      const headers = Array.from(headerNodes).map(serialize);
      const expandedRowFor = row => {
        const next = row.nextElementSibling;
        return next && next.classList.contains('el-table__expanded-row') ? next : null;
      };
      const labeledValue = (root, labelPattern) => {
        if (!root) return '';
        const labels = root.querySelectorAll(
          '.el-form-item__label, .el-descriptions-item__label, th, dt'
        );
        for (const label of labels) {
          const name = (label.innerText || label.textContent || '')
            .replace(/[ *:：\\s]/g, '').trim();
          if (!labelPattern.test(name)) continue;
          const item = label.closest('.el-form-item, .el-descriptions-item, tr, li, dt');
          if (!item) continue;
          const value = item.querySelector(
            '.el-form-item__content, .el-descriptions-item__content, td, dd'
          );
          if (value) return (value.innerText || value.textContent || '').trim();
        }
        return '';
      };
      const result = [];
      for (let rowIdx = 0; rowIdx < rows.length; rowIdx++) {
        const row = rows[rowIdx];
        if (row.offsetParent === null) continue;
        const detail = expandedRowFor(row);
        let detailId = labeledValue(detail, /^(应用)?ID$/i);
        if (!detailId && detail) {
          const match = (detail.textContent || '').match(/(?:应用)?ID\\s*[:：]\\s*([A-Za-z0-9_-]+)/i);
          detailId = match ? match[1] : '';
        }
        result.push({
          cells: Array.from(row.querySelectorAll('td')).map(serialize),
          detail_id: detailId,
          detail_app_name: labeledValue(detail, /^应用(名称|名)?$/),
          detail_channel: labeledValue(detail, /^(所属)?渠道(名称)?$/),
          row_idx: rowIdx,
          row_text: (row.textContent || '').trim()
        });
      }
      return {headers: headers, rows: result};
    }""")
    headers = (payload or {}).get("headers") or []
    rows = []
    for raw in (payload or {}).get("rows") or []:
        aligned = _align_header_cells(headers, raw.get("cells") or [])
        app_id = _mapped_value(aligned, _is_app_id_header) or (raw.get("detail_id") or "")
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


def _identify_new_app(page, before_ids, actual_channel_name, app_name):
    """Find the row created by this save using an ID set difference."""
    last_candidates = []

    # Narrow the post-save lookup by channel and app name first. Exact ID
    # difference and channel verification remain the identity/safety checks.
    # New channels may be briefly unfilterable; retry a bounded number of
    # times, then fall back to a full scan. This path never creates or saves.
    channel_filter = None
    for filter_attempt in range(_POST_SAVE_FILTER_ATTEMPTS):
        channel_filter = _search_list_by_channel(page, actual_channel_name, return_detail=True)
        if channel_filter is True or (
            isinstance(channel_filter, dict) and channel_filter.get("filter_stable")
        ):
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
            return {"success": True, "app_id": fast_candidates[0]["app_id"]}
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
        print(f"[create_app] 新增ID识别 attempt={attempt + 1}: {[row['app_id'] for row in candidates]}")

        if len(candidates) == 1:
            return {"success": True, "app_id": candidates[0]["app_id"]}
        if len(candidates) > 1:
            return {
                "success": False,
                "error": err(
                    "NEW_APP_ID_AMBIGUOUS",
                    "VERIFY",
                    f"保存后出现多个新增应用ID，无法安全确定目标: "
                    f"{[row['app_id'] for row in candidates]}",
                    NEXT_MANUAL,
                ),
            }
        page.wait_for_timeout(1500)

    if last_candidates:
        candidate_ids = [row["app_id"] for row in last_candidates]
        message = f"保存后新增应用ID无法唯一确定: {candidate_ids}"
        code = "NEW_APP_ID_AMBIGUOUS"
    else:
        message = "保存后未检测到新增应用ID"
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
      const next = document.querySelector('.el-pagination .btn-next');
      const total = document.querySelector('.el-pagination__total');
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


def _wait_for_unfiltered_list_restore(
    page,
    previous_state,
    read_state=None,
    observations=None,
    records_before=0,
    requests_before=None,
    timeout_ms=6000,
    poll_interval_ms=250,
):
    """Wait until a complete 2xx+structure record for this reset has been rendered."""
    baseline = records_before if requests_before is None else requests_before
    reader = read_state or _read_list_restore_state
    previous_table = (previous_state or {}).get("table_signature")
    stable_candidate = None
    stable_reads = 0
    polls = max(1, (int(timeout_ms) + int(poll_interval_ms) - 1) // int(poll_interval_ms))
    for _ in range(polls):
        page.wait_for_timeout(poll_interval_ms)
        current = reader(page) or {}
        records = _complete_reset_records(observations, baseline)
        meta = records[-1] if records else None
        if (
            not records
            or current.get("table_signature") == previous_table
            or current.get("row_count", 0) <= 0
            or not _pagination_synced_with_unfiltered_list(current)
            or not _dom_matches_success_structure(current, meta)
        ):
            stable_candidate = None
            stable_reads = 0
            continue
        candidate = _restore_fingerprint(current)
        if candidate == stable_candidate:
            stable_reads += 1
        else:
            stable_candidate = candidate
            stable_reads = 1
        if stable_reads >= 2:
            return True
    return False


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
        payload = page.evaluate("""
        () => {
          const serialize = el => ({
            text: (el.innerText || el.textContent || '').trim(),
            classes: String(el.className || '')
          });
          const primaryRows = document.querySelectorAll('.el-table__body-wrapper tbody tr');
          const sourceRows = primaryRows.length ? primaryRows : document.querySelectorAll('table tbody tr');
          const rows = Array.from(sourceRows).filter(row => !row.classList.contains('el-table__expanded-row'));
          const headerNodes = document.querySelectorAll('.el-table__header-wrapper th');
          const headers = Array.from(headerNodes).map(serialize);

          const expandedRowFor = (row) => {
            const next = row.nextElementSibling;
            return next && next.classList.contains('el-table__expanded-row') ? next : null;
          };

          const labeledValue = (root, labelPattern) => {
            if (!root) return '';
            const labels = root.querySelectorAll(
              '.el-form-item__label, .el-descriptions-item__label, th, dt'
            );
            for (const label of labels) {
              const name = (label.innerText || label.textContent || '')
                .replace(/[ *:：\\s]/g, '').trim();
              if (!labelPattern.test(name)) continue;
              const item = label.closest('.el-form-item, .el-descriptions-item, tr, li, dt');
              if (!item) continue;
              const value = item.querySelector(
                '.el-form-item__content, .el-descriptions-item__content, td, dd'
              );
              if (value) return (value.innerText || value.textContent || '').trim();
            }
            return '';
          };

          const result = [];
          for (let rowIdx = 0; rowIdx < rows.length; rowIdx++) {
            const row = rows[rowIdx];
            if (row.offsetParent === null) continue;
            const detail = expandedRowFor(row);
            result.push({
              cells: Array.from(row.querySelectorAll('td')).map(serialize),
              detail_channel: labeledValue(detail, /^(所属)?渠道(名称)?$/),
              detail_id: labeledValue(detail, /^(应用)?ID$/i),
              row_idx: rowIdx
            });
          }
          return {headers: headers, rows: result};
        }
        """)
        headers = (payload or {}).get("headers") or []
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
    if resource_fallback_page:
        _js_fill(page, "资源不足中间页链接", resource_fallback_page)
    if settlement_type:
        _js_select(page, "结算类型", settlement_type)
    _shot(page, "05_basic_config")

    # 保存
    _set_stage(execution_id, "正在保存应用")
    if not _go_tab(page, "悬浮球配置"):
        return {"success": False, "error": err("TAB_SWITCH_FAILED", "SAVE", f"无法切换到悬浮球配置Tab", NEXT_MANUAL)}
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
    saved = _click_save_button(page)
    if not saved:
        return {"success": False, "error": err("SAVE_FAILED", "SAVE", "未找到保存按钮", NEXT_MANUAL)}
    try:
        page.wait_for_selector(".el-dialog__wrapper:not([style*='display: none'])", state="detached", timeout=8000)
    except Exception:
        page.wait_for_timeout(2000)
    _shot(page, "06_after_save")
    save_err = capture_page_errors(page, screenshot_name=f"app_save_{app_name}")
    if save_err["dialog_open"]:
        return {"success": False, "error": err("SAVE_FAILED", "SAVE", build_error_message(save_err, "保存失败(对话框未关闭)"), NEXT_MANUAL)}

    # 保存后不按应用名称定位；通过前后快照得到的新 ID 继续定位。
    try:
        page.wait_for_selector("table tbody tr", timeout=STEP_TIMEOUT)
    except Exception:
        pass

    _set_stage(execution_id, "正在识别新增应用ID")
    identified = _identify_new_app(page, before_ids, data["actual_channel_name"], app_name)
    if not identified["success"]:
        return identified
    target_app_id = identified["app_id"]
    located = _find_target_row_by_id(page, target_app_id, data["actual_channel_name"])
    if not located.get("found"):
        location_reason = located.get("reason")
        if location_reason == "channel_mismatch":
            location_code = "CHANNEL_MISMATCH"
            location_message = f"应用ID={target_app_id}存在，但渠道归属不匹配"
        elif location_reason == "channel_unverified":
            location_code = "CHANNEL_UNVERIFIED"
            location_message = f"应用ID={target_app_id}已找到，但无法从主行或展开详情核验渠道"
        else:
            location_code = "NEW_APP_ID_NOT_FOUND"
            location_message = f"已识别新增应用ID={target_app_id}，但无法按ID重新定位"
        return {
            "success": False,
            "error": err(location_code, "VERIFY", location_message, NEXT_MANUAL),
        }
    _shot(page, "07_created")
    return {"success": True, "target_app_id": target_app_id}


def _stage_enable(page, execution_id, target_app_id, app_name, actual_channel_name):
    """Stage 2: 检查状态 → 如果已上线则跳过 → 如果没上线则点击开关 → 确认 → 验证。
    Returns: {success: bool, error: err_or_None}
    """
    _set_stage(execution_id, "正在检查应用状态")
    located = _find_target_row_by_id(page, target_app_id, actual_channel_name)
    if not located.get("found"):
        return {
            "success": False,
            "error": err("TARGET_APP_NOT_FOUND", "PUBLISH", f"上线前未找到应用ID={target_app_id}", NEXT_MANUAL),
        }
    target_idx = located["row_idx"]

    # 先检查当前是否已上线
    status_check = page.evaluate("""
    (rowIdx) => {
      const primaryRows = document.querySelectorAll('.el-table__body-wrapper tbody tr');
      const sourceRows = primaryRows.length ? primaryRows : document.querySelectorAll('table tbody tr');
      const rows = Array.from(sourceRows).filter(row => !row.classList.contains('el-table__expanded-row'));
      if (rowIdx < 0 || rowIdx >= rows.length) return {error: 'row not found'};
      const sw = rows[rowIdx].querySelector('.el-switch');
      if (!sw) return {error: 'no switch'};
      const isOn = sw.className.includes('is-checked');
      return {isOn: isOn};
    }
    """, target_idx)
    print(f"[create_app] 状态检查: {status_check}")

    if status_check.get("isOn"):
        print("[create_app] 应用已上线，跳过上线步骤")
        _shot(page, "08_already_published")
        return {"success": True, "error": None}

    # 没上线，点击开关
    _set_stage(execution_id, "正在发布上线")
    publish_result = page.evaluate("""
    (rowIdx) => {
      const primaryRows = document.querySelectorAll('.el-table__body-wrapper tbody tr');
      const sourceRows = primaryRows.length ? primaryRows : document.querySelectorAll('table tbody tr');
      const rows = Array.from(sourceRows).filter(row => !row.classList.contains('el-table__expanded-row'));
      if (rowIdx < 0 || rowIdx >= rows.length) return {error: 'row not found'};
      const sw = rows[rowIdx].querySelector('.el-switch');
      if (!sw) return {error: 'no switch'};
      if (sw.className.includes('is-checked')) return {clicked: false, wasOn: true};
      sw.click();
      return {clicked: true, wasOn: false};
    }
    """, target_idx)
    print(f"[create_app] publish: {publish_result}")

    if publish_result.get("clicked"):
        page.wait_for_timeout(1500)
        # 用 JS 精确点击"确定"按钮
        confirmed = page.evaluate("""() => {
          const mbs = document.querySelectorAll('.el-message-box__wrapper');
          for (const mb of mbs) {
            if (mb.style.display === 'none') continue;
            const btns = mb.querySelectorAll('button');
            for (const b of btns) {
              if (b.offsetParent !== null && b.innerText.trim() === '确定') { b.click(); return true; }
            }
          }
          return false;
        }""")
        print(f"[create_app] 确认上线: {confirmed}")
        if not confirmed:
            try:
                page.keyboard.press("Enter")
            except Exception:
                pass
        page.wait_for_timeout(2000)

    # 验证开关是否变 ON
    is_on = page.evaluate("""
    (rowIdx) => {
      const primaryRows = document.querySelectorAll('.el-table__body-wrapper tbody tr');
      const sourceRows = primaryRows.length ? primaryRows : document.querySelectorAll('table tbody tr');
      const rows = Array.from(sourceRows).filter(row => !row.classList.contains('el-table__expanded-row'));
      if (rowIdx < 0 || rowIdx >= rows.length) return false;
      const sw = rows[rowIdx].querySelector('.el-switch');
      return sw ? sw.className.includes('is-checked') : false;
    }
    """, target_idx)
    print(f"[create_app] 上线验证: is_on={is_on}")
    if not is_on:
        pub_err = capture_page_errors(page, screenshot_name=f"app_publish_fail_{app_name}")
        return {"success": False, "error": err("PUBLISH_FAILED", "PUBLISH", build_error_message(pub_err, "发布失败(状态未开启)"), NEXT_MANUAL)}
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
            return finish_failure(create_result["error"], current_stage)
        target_app_id = create_result["target_app_id"]
        completed_stages.append(current_stage)

        current_stage = "ENABLE"
        enable_result = _stage_enable(
            page,
            execution_id,
            target_app_id,
            app_name,
            data["actual_channel_name"],
        )
        if not enable_result["success"]:
            return finish_failure(enable_result["error"], current_stage)
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
