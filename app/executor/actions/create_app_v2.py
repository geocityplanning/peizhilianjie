# -*- coding: utf-8 -*-
"""
创建应用 — 契约版（v2，JS 直操 DOM，绕过 Playwright :visible 伪类）。
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import get_browser_page
from core.error_capture import capture_page_errors, build_error_message
from core.error_codes import err, NEXT_STOP, NEXT_QUERY, NEXT_MANUAL
from core import executor as ex
from actions.ensure_login import ensure_login

OPERATION = "CREATE_APP"
BASE_URL_H5 = "https://plus.buy.139.com/cloudappadmin/#/cloudAppManager"
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


def _js_channel_popover(page, label, channel_name):
    """用 JS 处理渠道 popover 选择器（带 A-Z 字母索引的自定义组件）。"""
    # 1. 点 channel-input 打开 popover
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
          if (lblEl.innerText.includes(label)) {
            const ci = it.querySelector('.channel-input');
            if (ci) { ci.click(); return true; }
          }
        }
      }
      return false;
    }
    """, label)
    if not opened:
        return False
    page.wait_for_timeout(1500)

    # 2. 在 popover 里找渠道名并点击
    picked = page.evaluate("""
    (name) => {
      // 找所有可能的 popover 元素
      const pops = document.querySelectorAll('[id^="el-popover-"], .el-popover, .el-popper, .channel-popover');
      for (const p of pops) {
        if (p.offsetParent === null && !p.style.display) continue;
        if (p.style.display === 'none') continue;
        // 查找所有可能的渠道选项元素
        const candidates = p.querySelectorAll('li, td, span, div, a, p');
        for (const el of candidates) {
          if (el.offsetParent === null) continue;
          const t = el.innerText ? el.innerText.trim() : '';
          // 精确匹配渠道名（排除包含匹配，避免误点）
          if (t === name) {
            el.click();
            return true;
          }
        }
      }
      // 如果精确匹配没找到，试前缀匹配
      for (const p of pops) {
        if (p.style.display === 'none') continue;
        const candidates = p.querySelectorAll('li, td, span, div, a, p');
        for (const el of candidates) {
          if (el.offsetParent === null) continue;
          const t = el.innerText ? el.innerText.trim() : '';
          if (t.startsWith(name) || (name.startsWith(t) && t.length > 3)) {
            el.click();
            return true;
          }
        }
      }
      return false;
    }
    """, channel_name)
    if picked:
        page.wait_for_timeout(500)
        # 用 JS 关闭残留 popover，不用 Escape（会误关主弹窗）
        page.evaluate("""
        () => {
          const pops = document.querySelectorAll('.el-popover, .el-popper, [id^="el-popover-"]');
          for (const p of pops) {
            if (p.style.display !== 'none') p.style.display = 'none';
          }
        }
        """)
        page.wait_for_timeout(300)
    return picked


def _set_stage(execution_id, stage):
    try:
        ex.update_execution(execution_id, output_json=json.dumps({"_stage": stage}, ensure_ascii=False))
        print(f"[create_app] stage: {stage}")
    except Exception:
        pass


def _open_group_dialog(page, row_idx):
    return page.evaluate("""
    (rowIdx) => {
      const rows = document.querySelectorAll('table tbody tr');
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
    link_key = ref_cloud_app_link.split("i=")[-1] if "i=" in ref_cloud_app_link else ref_cloud_app_link

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

        # 检查是否有下一页
        has_next = page.evaluate("""
        () => {
          const next = document.querySelector('.el-pagination .btn-next');
          if (!next) return false;
          return !next.className.includes('disabled');
        }
        """)
        if not has_next:
            break

        # 翻到下一页
        page.evaluate("""() => {
          const next = document.querySelector('.el-pagination .btn-next');
          if (next) next.click();
        }""")
        page.wait_for_timeout(2000)

    print(f"[create_app] 兜底结果：失败 (app_id={ref_app_id} 在所有页都未找到)")
    return {"clicked": False, "error": "COPY_FAILED"}


def _stage_create_save(page, execution_id, data, ref_cloud_app_link, ref_app_id, app_name,
                       jump_address, resource_fallback_page, settlement_type):
    """Stage 1: 用长链接搜索参考应用 → 点复制 → 填字段 → 保存 → 定位新行。
    Returns: {success: bool, target_idx: int, error: err_or_None}
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
    if not _js_fill(page, "应用名称", app_name):
        print("[create_app] WARN: 应用名称 填写失败")
    _bp = data.get("base_platform", "")
    if _bp:
        _js_select(page, "底座", _bp)
    if not _js_channel_popover(page, "所属渠道", data["actual_channel_name"]):
        print(f"[create_app] WARN: 所属渠道 选择失败")
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
    saved = page.evaluate("""() => {
      const wrappers = document.querySelectorAll('.el-dialog__wrapper');
      for (const w of wrappers) {
        if (w.style.display === 'none') continue;
        if (w.querySelectorAll('.el-tabs__item').length === 0) continue;
        const btns = w.querySelectorAll('button');
        for (const b of btns) { if (b.innerText.includes('保存')) { b.click(); return true; } }
      }
      return false;
    }""")
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

    # 清空搜索框，用新应用名搜索
    try:
        page.wait_for_selector("table tbody tr", timeout=STEP_TIMEOUT)
    except Exception:
        pass

    # 清空长链接搜索框，用应用名称搜索新创建的应用
    page.evaluate("""() => {
      const inputs = document.querySelectorAll('input');
      for (const inp of inputs) {
        if (inp.offsetParent === null) continue;
        const ph = inp.placeholder || '';
        if (ph.includes('长链接')) {
          const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
          setter.call(inp, '');
          inp.dispatchEvent(new Event('input', {bubbles: true}));
          break;
        }
      }
    }""")
    page.wait_for_timeout(300)
    # 在应用名称搜索框输入新应用名
    page.evaluate("""
    (appName) => {
      const inputs = document.querySelectorAll('input');
      for (const inp of inputs) {
        if (inp.offsetParent === null) continue;
        const ph = inp.placeholder || '';
        if (ph.includes('应用名称') || ph.includes('应用')) {
          const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
          setter.call(inp, appName);
          inp.dispatchEvent(new Event('input', {bubbles: true}));
          break;
        }
      }
    }
    """, app_name)
    page.wait_for_timeout(500)
    # 用 JS 精确点击"搜索"按钮
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
    try:
        page.wait_for_selector("table tbody tr", timeout=5000)
    except Exception:
        pass
    page.wait_for_timeout(1000)

    found_info = page.evaluate("""
    (appName) => {
      const rows = document.querySelectorAll('table tbody tr');
      for (let i = 0; i < rows.length; i++) {
        const row = rows[i];
        if (row.offsetParent === null) continue;
        if (!row.innerText.includes(appName)) continue;
        const ei = row.querySelector('.el-table__expand-icon, [class*="expand-icon"]');
        if (ei) {
          if (!ei.className.includes('expanded')) ei.click();
          return {found: true, expanded: !ei.className.includes('expanded'), index: i};
        }
        return {found: true, expanded: false, index: i};
      }
      return {found: false, index: -1};
    }
    """, app_name)
    print(f"[create_app] found_info: {found_info}")
    if found_info.get("expanded"):
        page.wait_for_timeout(1000)

    target_idx = page.evaluate("""
    (appName) => {
      const rows = document.querySelectorAll('table tbody tr');
      let lastMatch = -1;
      for (let i = 0; i < rows.length; i++) {
        const row = rows[i];
        if (row.offsetParent === null) continue;
        if (!row.innerText.includes(appName)) continue;
        if (row.querySelector('.el-switch') || row.innerText.includes('设置')) lastMatch = i;
      }
      return lastMatch;
    }
    """, app_name)
    print(f"[create_app] target row index: {target_idx}")

    if target_idx < 0:
        if found_info.get("found"):
            target_idx = found_info["index"]
        else:
            return {"success": False, "error": err("SAVE_FAILED", "VERIFY", f"保存后未在列表中找到应用: {app_name}", NEXT_MANUAL)}
    _shot(page, "07_created")
    return {"success": True, "target_idx": target_idx}


def _stage_enable(page, execution_id, target_idx, app_name):
    """Stage 2: 检查状态 → 如果已上线则跳过 → 如果没上线则点击开关 → 确认 → 验证。
    Returns: {success: bool, error: err_or_None}
    """
    _set_stage(execution_id, "正在检查应用状态")
    
    # 先检查当前是否已上线
    status_check = page.evaluate("""
    (rowIdx) => {
      const rows = document.querySelectorAll('table tbody tr');
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
      const rows = document.querySelectorAll('table tbody tr');
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
      const rows = document.querySelectorAll('table tbody tr');
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


def _stage_set_group(page, execution_id, target_idx, group_name, app_name):
    """Stage 3: 打开云机链接设置 → 选按分组 → 填值 → 确定 → 读回验证。
    Returns: {success: bool, error: err_or_None}
    """
    _set_stage(execution_id, "正在设置分组")

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


def _stage_collect_result(page, execution_id, target_idx, app_name):
    """Read the final row and verify that the app is online with a generated link."""
    _set_stage(execution_id, "正在校验终态")
    try:
        row_data = page.evaluate("""
        (rowIdx) => {
          const rows = document.querySelectorAll('table tbody tr');
          if (rowIdx < 0 || rowIdx >= rows.length) return {};
          const row = rows[rowIdx];
          const headers = Array.from(document.querySelectorAll('th')).map(
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
    activity_name = (data.get("activity_name") or "").strip()
    app_name = f"{business_object}-{activity_name}" if activity_name else business_object

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
        target_idx = create_result["target_idx"]
        completed_stages.append(current_stage)

        current_stage = "ENABLE"
        enable_result = _stage_enable(page, execution_id, target_idx, app_name)
        if not enable_result["success"]:
            return finish_failure(enable_result["error"], current_stage)
        completed_stages.append(current_stage)

        if group_name:
            current_stage = "SET_GROUP"
            group_result = _stage_set_group(
                page,
                execution_id,
                target_idx,
                group_name,
                app_name,
            )
            if not group_result["success"]:
                return finish_failure(group_result["error"], current_stage)
            completed_stages.append(current_stage)

        current_stage = "VERIFY"
        final_result = _stage_collect_result(page, execution_id, target_idx, app_name)
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
