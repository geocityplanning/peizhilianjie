# -*- coding: utf-8 -*-
"""渠道筛选只读定位自证探针（REAL）。

用途：在真实 UAT 页面上收窄"搜索已点击而行集未变"的原因，回答：
  1. 点击搜索后是否观察到列表请求     -> request_reached_backend
  2. 列表请求是否携带目标渠道筛选     -> request_carried_target_filter（仅布尔）
  3. 观察到的列表请求是否 HTTP 2xx   -> http_status_2xx_observed（仅网络层，不等于业务成功）
  4. 行集是否变化、读取是否见目标渠道 -> table_changed / reader_sees_target_channel
  5. 6 秒预算内是否完成稳定刷新       -> filter_stable / elapsed_ms

安全边界（重要）：
  - 只做只读操作：导航列表页、点"重置"、选渠道、点"搜索"、翻页、展开行。
  - 绝不调用 create_channel / create_app / 上线 / 分组 / 删除 / 恢复。
  - 不读取或输出凭证；默认 JSON 只输出脱敏后的渠道、页面和样本标识。
  - 原始渠道选项只允许本机 TTY 查看，不写入交接文档、日志或可转发 JSON。
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
    raw = value if isinstance(value, str) else str(value or "")
    if len(raw) <= 4:
        return "*" * len(raw)
    return f"{raw[:2]}{'*' * (len(raw) - 4)}{raw[-2:]}"


def _mask_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return raw
    fragment = ""
    if "#" in raw:
        fragment = raw.split("#", 1)[1].split("?", 1)[0]
        fragment = f"#{fragment}" if fragment else ""
    return f"[redacted-url]{fragment}"


HTTP_2XX_NOT_BUSINESS_SUCCESS_NOTE = (
    "http_status_2xx_observed 只表示观察到列表请求的 HTTP 2xx，"
    "不等于业务查询成功、应用定位成功或 S4 通过"
)


def _http_status_2xx_observed(status):
    if status is None:
        return "NO_RESPONSE_OBSERVED"
    try:
        code = int(status)
    except (TypeError, ValueError):
        return "NO_RESPONSE_OBSERVED"
    return 200 <= code < 300


def _filter_path_case(detail):
    if not isinstance(detail, dict):
        return "unknown"
    if detail.get("list_requests_before") is None or detail.get("list_requests_after") is None:
        return "unknown_no_observer"
    if not detail["list_requests_after"] > detail["list_requests_before"]:
        return "no_list_request"
    if detail.get("request_carried_target_filter") is not True:
        return "request_missing_target_filter"
    if detail.get("table_changed") and detail.get("reader_sees_target_channel"):
        return "dom_refreshed_with_target_channel"
    if detail.get("response_contains_target_channel") is True:
        return "response_has_target_dom_not_refreshed"
    if detail.get("response_contains_target_channel") is False:
        return "response_missing_target_channel"
    return "unknown_no_response_body"


def _build_diagnosis(detail):
    diagnosis = {
        "request_reached_backend": None,
        "request_carried_target_filter": None,
        "target_filter_field": None,
        "target_filter_field_is_channel": None,
        "response_contains_target_channel": None,
        "http_status_2xx_observed": None,
        "table_changed": None,
        "reader_sees_target_channel": None,
        "table_reader_looks_correct": None,
        "refresh_stable_within_budget": bool(
            isinstance(detail, dict) and detail.get("filter_stable")
        ),
        "filter_path": _filter_path_case(detail),
        "http_2xx_is_not_business_success": True,
    }
    if not isinstance(detail, dict):
        return diagnosis
    before = detail.get("list_requests_before")
    after = detail.get("list_requests_after")
    if before is None or after is None:
        diagnosis["request_reached_backend"] = "UNKNOWN_NO_OBSERVER"
    else:
        diagnosis["request_reached_backend"] = after > before
    diagnosis["request_carried_target_filter"] = detail.get("request_carried_target_filter")
    diagnosis["target_filter_field"] = detail.get("target_filter_field")
    diagnosis["target_filter_field_is_channel"] = detail.get("target_filter_field_is_channel")
    diagnosis["response_contains_target_channel"] = detail.get("response_contains_target_channel")
    diagnosis["http_status_2xx_observed"] = _http_status_2xx_observed(
        detail.get("last_http_status")
    )
    diagnosis["table_changed"] = detail.get("table_changed")
    diagnosis["reader_sees_target_channel"] = detail.get("reader_sees_target_channel")
    if detail.get("reader_row_count") is not None:
        diagnosis["table_reader_looks_correct"] = bool(
            detail.get("reader_distinct_channels") in (0, 1)
            and detail.get("reader_sees_target_channel")
        ) or bool(detail.get("reader_row_count"))
    return diagnosis


def _diagnosis_notes(detail):
    notes = [HTTP_2XX_NOT_BUSINESS_SUCCESS_NOTE]
    path = _filter_path_case(detail)
    if path == "no_list_request":
        notes.append("点击搜索后没有观察到任何列表请求：优先怀疑点到了非渠道组的搜索按钮")
    elif path == "request_missing_target_filter":
        notes.append(
            "列表请求已发出，但未携带目标渠道筛选；不要靠加长等待判断筛选是否生效"
        )
    elif path == "response_missing_target_channel":
        notes.append(
            "列表请求已携带目标筛选，但响应数据未含目标渠道；不要靠加长等待判断 DOM"
        )
    elif path == "response_has_target_dom_not_refreshed":
        notes.append(
            "响应数据已含目标渠道，但行集未刷新或读取未见目标渠道"
        )
    elif path == "unknown_no_response_body":
        notes.append(
            "列表请求已携带目标筛选，但未读到响应正文，无法判断响应是否含目标渠道"
        )
    elif path == "dom_refreshed_with_target_channel":
        notes.append("行集已变化且读取见到目标渠道；仍不等于 S4 通过")
    field = detail.get("target_filter_field") if isinstance(detail, dict) else None
    is_channel = detail.get("target_filter_field_is_channel") if isinstance(detail, dict) else None
    if field == "raw_text_only" or is_channel is False:
        notes.append("目标文本未落在渠道筛选字段，优先核对页面控件绑定")
    elif is_channel is True and path == "response_missing_target_channel":
        notes.append("目标文本已落在渠道字段，但响应未含目标；转人工核对 UAT 后端筛选行为")
    if (
        isinstance(detail, dict)
        and detail.get("last_http_status")
        and int(detail["last_http_status"]) >= 400
    ):
        notes.append("列表请求返回了非 2xx：优先怀疑会话/权限或参数被拒")
    return notes


def _redact_obj(value):
    drop_keys = {
        "postData",
        "post_data",
        "request_body",
        "response_body",
        "body",
        "response_text",
        "query",
        "token",
        "authorization",
        "cookie",
    }
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in drop_keys:
                continue
            if key == "options" and isinstance(item, list):
                out["options_redacted"] = True
                if "option_count" not in value:
                    out["option_count"] = len(item)
                continue
            if key in {"url", "current_url", "url_before_navigate"}:
                out[key] = _mask_url(item if isinstance(item, str) else str(item or ""))
            elif key in {"current_value", "channel"} and item not in (None, ""):
                text = str(item)
                out[key] = text if "*" in text else _mask(text)
            elif key == "sample_ids" and isinstance(item, list):
                out[key] = [_mask(str(x)) for x in item]
                out["sample_ids_redacted"] = True
            elif key == "app_id" and item not in (None, ""):
                out[key] = _mask(str(item))
            else:
                out[key] = _redact_obj(item)
        return out
    if isinstance(value, list):
        return [_redact_obj(item) for item in value]
    return value


def _emit_local_channel_options(options, stream=None) -> bool:
    stream = sys.stderr if stream is None else stream
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty) or not isatty():
        return False
    stream.write("LOCAL-ONLY channel options; do not copy into handoff JSON or logs:\n")
    for index, name in enumerate(options or []):
        stream.write(f"  {index}\t{name}\n")
    stream.flush()
    return True


def _dump_report(report) -> str:
    return json.dumps(_redact_obj(report), ensure_ascii=False, indent=2)


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
    report["url_before_navigate"] = _mask_url(page.url)
    page.goto(url, wait_until="domcontentloaded")
    try:
        page.wait_for_selector("table tbody tr", timeout=15000)
    except Exception:
        page.wait_for_timeout(3000)
    report["current_url"] = _mask_url(page.url)
    report["notes"].append("已只读重新加载应用列表页，确保无残留筛选/下拉状态")


def _recon(page, report: dict) -> None:
    """只读侦察：页面结构决定后续归因，不需要渠道名。"""
    from actions import create_app_v2 as cap  # noqa: PLC0415

    structure = page.evaluate(RECON_JS)
    report["steps"]["recon"] = _redact_obj(structure)

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
    raw_options = options.get("options") or []
    shown_locally = _emit_local_channel_options(raw_options)
    report["steps"]["channel_options"] = {
        "opened": True,
        "visible_dropdown_count": options.get("visible_dropdown_count"),
        "option_count": options.get("option_count") or len(raw_options),
        "options_redacted": True,
        "options_shown_on_local_tty": shown_locally,
    }
    report["notes"].append(
        "原始渠道选项只允许本机 TTY 查看，不写入 JSON、日志或交接文档"
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
        print(_dump_report(report))
        return 2

    try:
        _navigate_to_list(page, args.url, report)

        if args.recon:
            _recon(page, report)
            report["ok"] = True
            print(_dump_report(report))
            return 0

        from actions import create_app_v2 as cap  # noqa: PLC0415

        detail = cap._search_list_by_channel(page, args.channel, return_detail=True)
        report["steps"]["channel_filter"] = detail if isinstance(detail, dict) else {"result": detail}
        report["diagnosis"] = _build_diagnosis(detail)
        report["notes"].extend(_diagnosis_notes(detail))

        if args.app_id:
            located = cap._find_target_row_by_id(page, args.app_id, args.channel)
            report["steps"]["exact_app_id_lookup"] = {
                "found": bool(located.get("found")),
                "reason": located.get("reason"),
                "app_id": _mask(args.app_id),
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

    print(_dump_report(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
