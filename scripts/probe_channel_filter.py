# -*- coding: utf-8 -*-
"""渠道筛选只读定位自证探针（REAL）。

用途：在真实 UAT 页面上收窄"搜索已点击而行集未变"的原因，回答四个问题：
  1. 检索参数是否真正落到后台列表请求   -> list_requests_after / last_http_status
  2. 响应是否成功                       -> last_http_status（2xx 视为已成功返回）
  3. 表格读取是否选对主行               -> reader_row_count / reader_sees_target_channel
  4. 6 秒预算内是否完成稳定刷新         -> filter_stable / elapsed_ms / table_changed

安全边界（重要）：
  - 只做只读操作：导航列表页、点"重置"、选渠道、点"搜索"、翻页、展开行。
  - 绝不调用 create_channel / create_app / 上线 / 分组 / 删除 / 恢复。
  - 不读取或输出凭证；报告里渠道名与响应正文一律不落地。
  - 登录模块在内存中被替换为桩，不会启动登录流程，也不需要 config.json。

用法（先让 Chrome 以 9222 打开并登录 UAT 管理页）：
  uv run python scripts/probe_channel_filter.py --channel "<实际渠道名>"
  uv run python scripts/probe_channel_filter.py --channel "<实际渠道名>" --app-id 12042
"""
from __future__ import annotations

import argparse
import json
import sys
import types
from pathlib import Path

EXECUTOR_DIR = Path(__file__).resolve().parents[1] / "app" / "executor"
sys.path.insert(0, str(EXECUTOR_DIR))

APP_LIST_URL = "https://uat-cloud.139.com/cloudappadmin/#/cloudAppManager"


def _install_login_stub() -> None:
    """在导入自动化模块前用内存桩替换登录模块，避免读 config.json 与执行登录。"""
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


def _mask(value: str) -> str:
    raw = value or ""
    if len(raw) <= 4:
        return "*" * len(raw)
    return f"{raw[:2]}{'*' * (len(raw) - 4)}{raw[-2:]}"


RECON_JS = """
() => {
  const visible = el => el !== null && el.offsetParent !== null;
  const inDialog = el => Boolean(el.closest('.el-dialog, .el-dialog__wrapper'));

  const selects = Array.from(document.querySelectorAll('.el-select'))
    .filter(select => visible(select) && !inDialog(select));
  const selectInfo = selects.map((select, index) => {
    const item = select.closest('.el-form-item');
    const label = item && item.querySelector('.el-form-item__label');
    const input = select.querySelector('input');
    return {
      index: index,
      label: label ? (label.innerText || '').replace(/[ *:：]/g, '').trim() : '',
      placeholder: input ? (input.placeholder || '') : '',
      aria_label: input ? (input.getAttribute('aria-label') || '') : '',
      has_filterable_input: Boolean(select.querySelector('input.el-select__input')),
      current_value: input ? (input.value || '').trim() : ''
    };
  });

  const pageButtons = Array.from(document.querySelectorAll('button'))
    .filter(button => visible(button) && !inDialog(button));
  const labelOf = button => (button.innerText || '').replace(/\\s/g, '').trim();
  const searchButtons = pageButtons.filter(button => labelOf(button) === '搜索');
  const resetButtons = pageButtons.filter(button => labelOf(button) === '重置');
  const scopeOf = button => {
    const form = button.closest('.el-form');
    if (form) {
      const labels = Array.from(form.querySelectorAll('.el-form-item__label'))
        .map(node => (node.innerText || '').replace(/[ *:：]/g, '').trim())
        .filter(Boolean);
      return labels.length ? labels.slice(0, 6).join('/') : 'form(无标签)';
    }
    return '不在 el-form 内';
  };

  const tables = Array.from(document.querySelectorAll('.el-table'))
    .filter(visible)
    .map((table, index) => {
      const headers = Array.from(table.querySelectorAll('.el-table__header-wrapper th'))
        .map(th => (th.innerText || '').trim())
        .filter(Boolean);
      const bodyRows = Array.from(table.querySelectorAll('.el-table__body-wrapper tbody tr'))
        .filter(row => !row.classList.contains('el-table__expanded-row'));
      const idIndex = headers.findIndex(h => h === 'ID' || h.includes('应用ID'));
      const channelIndex = headers.findIndex(h => /渠道/.test(h));
      const ids = bodyRows.slice(0, 3).map(row => {
        const cells = row.querySelectorAll('td');
        return idIndex >= 0 && cells[idIndex] ? (cells[idIndex].textContent || '').trim() : '';
      }).filter(Boolean);
      return {
        index: index,
        headers: headers,
        body_row_count: bodyRows.length,
        looks_like_app_list: headers.some(h => h === 'ID' || h.includes('应用ID'))
          && headers.some(h => /应用名称/.test(h)),
        has_channel_column: channelIndex >= 0,
        sample_ids: ids
      };
    });

  const active = document.querySelector('.el-pagination .el-pager li.active');
  const total = document.querySelector('.el-pagination__total');
  return {
    url: location.href,
    visible_select_count: selects.length,
    selects: selectInfo,
    search_button_count: searchButtons.length,
    search_button_scopes: searchButtons.map(scopeOf),
    reset_button_count: resetButtons.length,
    table_count: tables.length,
    tables: tables,
    global_body_row_count: document.querySelectorAll('.el-table__body-wrapper tbody tr').length,
    active_page: active ? (active.innerText || '').trim() : null,
    total_text: total ? (total.innerText || '').trim() : null
  };
}
"""

CHANNEL_OPTIONS_JS = """
(selectIndex) => {
  const visible = el => el !== null && el.offsetParent !== null;
  const selects = Array.from(document.querySelectorAll('.el-select'))
    .filter(select => visible(select) && !select.closest('.el-dialog, .el-dialog__wrapper'));
  const target = selects[selectIndex];
  if (!target) return {opened: false};
  const input = target.querySelector('input.el-select__input') ||
    target.querySelector('input.el-input__inner');
  if (!input || input.disabled) return {opened: false};
  input.click();
  return {opened: true};
}
"""

CHANNEL_OPTIONS_READ_JS = """
() => {
  const dropdowns = Array.from(document.querySelectorAll('.el-select-dropdown'))
    .filter(dd => dd.offsetParent !== null && dd.style.display !== 'none');
  const options = dropdowns.flatMap(dd => Array.from(dd.querySelectorAll('.el-select-dropdown__item'))
    .filter(item => item.offsetParent !== null)
    .map(item => (item.innerText || '').trim()));
  return {visible_dropdown_count: dropdowns.length, option_count: options.length, options: options};
}
"""


def _navigate_to_list(page, url: str, report: dict) -> None:
    """每次都以全新加载进入列表页。

    不复用上一次残留的 DOM/Vue 状态：直接改 style 隐藏下拉会让 Element UI 的
    内部 visible 与 DOM 失步，下一次点击反而把下拉关掉，从而产出假的
    "控件打不开"结论。重新加载是只读 GET，代价可接受且结果可复现。
    """
    report["url_before_navigate"] = page.url
    page.goto(url, wait_until="domcontentloaded")
    try:
        page.wait_for_selector("table tbody tr", timeout=15000)
    except Exception:
        page.wait_for_timeout(3000)
    report["current_url"] = page.url
    report["notes"].append("已只读重新加载应用列表页，确保无残留筛选/下拉状态")


def _recon(page, report: dict) -> None:
    """只读侦察：页面结构决定后续归因，不需要渠道名。"""
    from actions import create_app_v2 as cap  # noqa: PLC0415

    structure = page.evaluate(RECON_JS)
    report["steps"]["recon"] = structure

    channel_index = None
    for item in structure.get("selects") or []:
        haystack = f"{item.get('label', '')} {item.get('placeholder', '')} {item.get('aria_label', '')}"
        if "渠道" in haystack:
            channel_index = item.get("index")
            break
    if channel_index is None:
        report["notes"].append("可见的非弹窗 el-select 中找不到带『渠道』标签的筛选框")
        return

    opened = page.evaluate(CHANNEL_OPTIONS_JS, channel_index)
    if not opened or not opened.get("opened"):
        report["steps"]["channel_options"] = {"opened": False}
        report["notes"].append("渠道筛选下拉无法打开")
        return
    page.wait_for_timeout(600)
    options = page.evaluate(CHANNEL_OPTIONS_READ_JS)
    # 用"点击外部"这个 Element UI 官方关闭路径收起下拉，不要直接改 style，
    # 否则 Vue 的 visible 与 DOM 会失步，下一次打开会被自己关掉。
    page.evaluate("""() => {
      const event = new MouseEvent('click', {bubbles: true});
      document.dispatchEvent(event);
      document.body.dispatchEvent(new MouseEvent('click', {bubbles: true}));
      return true;
    }""")
    page.wait_for_timeout(400)
    leftover = page.evaluate("""() => Array.from(document.querySelectorAll('.el-select-dropdown'))
      .filter(dd => dd.offsetParent !== null && dd.style.display !== 'none').length""")
    if leftover:
        report["notes"].append(f"下拉未能自动收起（仍有 {leftover} 个可见），本次结果按现场原样记录")
    report["steps"]["channel_options"] = options
    report["notes"].append(
        "channel_options.options 是 UAT 当前渠道下拉的原始选项，仅供本机挑选 --channel，"
        "不要原样写进给编排端的报告"
    )
    report["diagnosis_hints"] = {
        "multiple_search_buttons": structure.get("search_button_count", 0) > 1,
        "multiple_app_tables": len([t for t in structure.get("tables", []) if t.get("looks_like_app_list")]) > 1,
        "top_level_el_table_body_rows": structure.get("global_body_row_count"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="渠道筛选只读定位自证")
    parser.add_argument("--channel", default="", help="要筛选的实际渠道名")
    parser.add_argument("--recon", action="store_true", help="只做页面结构侦察，不需要渠道名")
    parser.add_argument("--app-id", default="", help="可选：要只读确认是否存在的应用 ID")
    parser.add_argument("--url", default=APP_LIST_URL, help="应用列表页地址")
    args = parser.parse_args()

    if not args.recon and not args.channel:
        print("必须提供 --channel，或使用 --recon 只做结构侦察")
        return 2

    _install_login_stub()

    from core import get_browser_page  # noqa: PLC0415

    report: dict = {
        "mode": "REAL_READ_ONLY",
        "channel": _mask(args.channel) if args.channel else None,
        "recon_only": bool(args.recon),
        "wrote_any_uat_data": False,
        "steps": {},
        "notes": [],
    }

    try:
        pw, _browser, page = get_browser_page()
    except Exception as exc:
        report["ok"] = False
        report["error"] = f"无法连接 CDP 浏览器: {type(exc).__name__}: {exc}"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    try:
        _navigate_to_list(page, args.url, report)

        if args.recon:
            _recon(page, report)
            report["ok"] = True
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0

        from actions import create_app_v2 as cap  # noqa: PLC0415

        detail = cap._search_list_by_channel(page, args.channel, return_detail=True)
        report["steps"]["channel_filter"] = detail if isinstance(detail, dict) else {"result": detail}

        report["diagnosis"] = {
            "request_reached_backend": None,
            "response_ok": None,
            "table_reader_looks_correct": None,
            "refresh_stable_within_budget": bool(
                isinstance(detail, dict) and detail.get("filter_stable")
            ),
        }
        if isinstance(detail, dict):
            before = detail.get("list_requests_before")
            after = detail.get("list_requests_after")
            if before is None or after is None:
                report["diagnosis"]["request_reached_backend"] = "UNKNOWN_NO_OBSERVER"
            else:
                report["diagnosis"]["request_reached_backend"] = after > before
            status = detail.get("last_http_status")
            if status is None:
                report["diagnosis"]["response_ok"] = "NO_RESPONSE_OBSERVED"
            else:
                report["diagnosis"]["response_ok"] = 200 <= int(status) < 300
            if detail.get("reader_row_count") is not None:
                report["diagnosis"]["table_reader_looks_correct"] = bool(
                    detail.get("reader_distinct_channels") in (0, 1)
                    and detail.get("reader_sees_target_channel")
                ) or bool(detail.get("reader_row_count"))
            if not detail.get("filter_stable"):
                if detail.get("list_requests_before") is not None and detail.get(
                    "list_requests_after"
                ) is not None and detail["list_requests_after"] <= detail["list_requests_before"]:
                    report["notes"].append(
                        "点击搜索后没有观察到任何列表请求：优先怀疑点到了非渠道组的搜索按钮"
                    )
                elif detail.get("last_http_status") and int(detail["last_http_status"]) >= 400:
                    report["notes"].append("列表请求返回了非 2xx：优先怀疑会话/权限或参数被拒")
                elif detail.get("elapsed_ms") is not None and detail["elapsed_ms"] >= 6000:
                    report["notes"].append("6 秒预算内未完成稳定刷新：需要放宽预算或改判刷新条件")
                else:
                    report["notes"].append(
                        "请求已发出且返回正常，但表格行集未变化：优先怀疑筛选参数未随请求下发，"
                        "或表格读取选中的不是应用列表主表"
                    )

        if args.app_id:
            located = cap._find_target_row_by_id(page, args.app_id, args.channel)
            report["steps"]["exact_app_id_lookup"] = {
                "found": bool(located.get("found")),
                "reason": located.get("reason"),
                "app_id": args.app_id,
            }
            report["notes"].append("按 ID 未找到不等于不存在：筛选未生效时只能判定未验证")

        report["ok"] = True
    except Exception as exc:
        report["ok"] = False
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            pw.stop()
        except Exception:
            pass

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
