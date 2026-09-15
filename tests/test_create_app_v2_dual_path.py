# -*- coding: utf-8 -*-
"""
双路径方案架构测试：
  1. 不再存在 get_by_text("搜", exact=False)
  2. 不再存在 row.innerText.includes(link) 形式的完整长链接匹配
  3. 包含 SEARCH_FAILED 或 COPY_RESULT_UNKNOWN
  4. 包含 link_key 和 textContent 匹配
  5. ID 使用完整单元格等值匹配
  6. ID 兜底具有分页循环
  7. execute_create_app() 仍只有一个定义
  8. 三个阶段函数仍由主函数依次调用
"""
import ast
import re
from pathlib import Path

SOURCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "executor"
    / "actions"
    / "create_app_v2.py"
)


def _source() -> str:
    return SOURCE_PATH.read_text(encoding="utf-8")


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    assert len(matches) == 1, f"{name} should have exactly one definition"
    return matches[0]


# ====== 1. 不再存在 get_by_text("搜", exact=False) ======

def test_no_get_by_text_search():
    src = _source()
    # 不得使用 get_by_text 模糊匹配"搜"作为搜索按钮
    assert 'get_by_text("搜"' not in src, "不得使用 get_by_text 模糊匹配'搜'"
    assert "get_by_text(\"搜\"" not in src


# ====== 2. 不再存在 row.innerText.includes(link) 完整长链接匹配 ======

def test_no_inner_text_includes_full_link():
    src = _source()
    # 主路径匹配应使用 textContent + link_key，不使用 innerText + 完整长链接
    assert "row.innerText.includes(link)" not in src
    assert "row.innerText.includes(ref_cloud_app_link)" not in src


# ====== 3. 包含 SEARCH_FAILED 或 COPY_RESULT_UNKNOWN ======

def test_error_codes_present():
    src = _source()
    assert "SEARCH_FAILED" in src, "必须包含 SEARCH_FAILED 错误码"
    assert "COPY_RESULT_UNKNOWN" in src, "必须包含 COPY_RESULT_UNKNOWN 错误码"
    assert "COPY_FAILED" in src, "必须包含 COPY_FAILED 错误码"


# ====== 4. 包含 link_key 和 textContent 匹配 ======

def test_link_key_and_textcontent_matching():
    src = _source()
    assert "link_key" in src, "必须从长链接提取 link_key"
    assert 'textContent' in src, "必须使用 textContent 匹配行"
    # 主路径的匹配逻辑应该用 link_key
    assert 'includes(linkKey)' in src or 'includes(link_key)' in src


# ====== 5. ID 使用完整单元格等值匹配 ======

def test_id_exact_cell_match():
    src = _source()
    # 兜底路径必须用 cell.textContent 精确等值匹配
    assert "cell.textContent" in src, "必须用 cell.textContent 读取单元格"
    assert "=== refId" in src or "=== ref_app_id" in src, "必须用等值匹配而非 includes"


# ====== 6. ID 兜底具有分页循环 ======

def test_fallback_has_pagination_loop():
    src = _source()
    tree = ast.parse(src)
    # _locate_by_app_id 函数应存在
    func = _function(tree, "_locate_by_app_id")
    func_src = ast.get_source_segment(src, func) or ""
    # 分页按钮由共享辅助函数检查，兜底路径负责循环调用它。
    assert "_click_next_page_and_wait" in func_src, "兜底路径必须调用稳定翻页辅助函数"
    assert "for " in func_src, "兜底路径必须有循环"
    helper = _function(tree, "_click_next_page_and_wait")
    helper_src = ast.get_source_segment(src, helper) or ""
    assert "btn-next" in helper_src, "稳定翻页辅助函数必须检查分页 btn-next"
    assert "click" in helper_src, "稳定翻页辅助函数必须点击翻页"


# ====== 7. execute_create_app() 仍只有一个定义 ======

def test_single_execute_create_app():
    src = _source()
    assert src.count("def execute_create_app(") == 1


# ====== 8. 三个阶段函数仍由主函数依次调用 ======

def test_three_stages_called_in_order():
    src = _source()
    tree = ast.parse(src)
    execute = _function(tree, "execute_create_app")
    execute_src = ast.get_source_segment(src, execute) or ""

    stage_calls = [
        "_stage_create_save",
        "_stage_enable",
        "_stage_set_group",
    ]
    calls = {
        node.func.id: node.lineno
        for node in ast.walk(execute)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    for call in stage_calls:
        assert call in calls, f"{call} 必须被 execute_create_app 调用"
    assert [calls[call] for call in stage_calls] == sorted(
        calls[call] for call in stage_calls
    ), "三个阶段必须按顺序调用"


# ====== 额外：DEFAULT_REF_SOURCES 结构正确 ======

def test_default_ref_sources_structure():
    src = _source()
    assert "DEFAULT_REF_SOURCES" in src
    assert '"app_id"' in src
    assert '"cloud_app_link"' in src
    assert "DEFAULT_REF_LINKS" not in src, "旧的 DEFAULT_REF_LINKS 应被替换"


# ====== 额外：主路径和兜底路径函数都存在 ======

def test_dual_path_functions_exist():
    src = _source()
    tree = ast.parse(src)
    _function(tree, "_search_by_link")
    _function(tree, "_locate_by_app_id")


# ====== 额外：主路径已点复制则不进兜底 ======

def test_primary_blocks_fallback_when_clicked():
    src = _source()
    tree = ast.parse(src)
    stage = _function(tree, "_stage_create_save")
    stage_src = ast.get_source_segment(src, stage) or ""
    # 主路径 clicked=True 后不应执行兜底
    assert "if not copy_clicked:" in stage_src, "必须有条件判断控制兜底入口"
    assert "_locate_by_app_id" in stage_src, "兜底函数必须被调用"


def test_post_save_relocation_waits_for_stable_pagination():
    src = _source()
    tree = ast.parse(src)
    collect = _function(tree, "_collect_all_app_rows")
    collect_src = ast.get_source_segment(src, collect) or ""
    assert "_read_pagination_state" in src
    assert "wait_for_page_change" in src
    assert "_click_next_page_and_wait" in collect_src
    assert "seen_pages" in collect_src


def test_multiple_new_ids_stop_without_guessing_by_channel_text():
    src = _source()
    tree = ast.parse(src)
    identify = _function(tree, "_identify_new_app")
    identify_src = ast.get_source_segment(src, identify) or ""
    assert "NEW_APP_ID_AMBIGUOUS" in identify_src
    assert "channel_matches" not in identify_src


def test_target_relocation_checks_expanded_detail_separately():
    src = _source()
    tree = ast.parse(src)
    locate = _function(tree, "_find_target_row_by_id")
    locate_src = ast.get_source_segment(src, locate) or ""
    assert "el-table__expanded-row" in locate_src
    assert "el-table__expanded-row" in locate_src
    assert "channel_unverified" in locate_src
    assert "channel_mismatch" in locate_src
    assert "channelIndex" in locate_src
    assert "detailChannel" in locate_src
    assert "channelValue === expectedChannel" in locate_src
