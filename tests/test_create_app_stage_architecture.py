import ast
from pathlib import Path


SOURCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "executor"
    / "actions"
    / "create_app_v2.py"
)


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    assert len(matches) == 1, f"{name} should have exactly one definition"
    return matches[0]


def test_create_app_main_orchestrates_the_three_stages():
    source = SOURCE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    execute = _function(tree, "execute_create_app")
    execute_source = ast.get_source_segment(source, execute) or ""

    calls = {
        node.func.id: node.lineno
        for node in ast.walk(execute)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    stage_calls = [
        "_stage_create_save",
        "_stage_enable",
        "_stage_set_group",
        "_stage_collect_result",
    ]
    for call in stage_calls:
        assert call in calls
    assert [calls[call] for call in stage_calls] == sorted(
        calls[call] for call in stage_calls
    )
    assert ".evaluate(" not in execute_source


def test_create_app_stage_contract_and_required_fields_are_present():
    source = SOURCE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    execute = _function(tree, "execute_create_app")
    execute_source = ast.get_source_segment(source, execute) or ""

    assert "completed_stages" in execute_source
    assert "failed_stage" in execute_source
    assert "resource_fallback_page" in execute_source
    assert source.count("def execute_create_app(") == 1


def test_application_name_and_target_identity_are_separate():
    source = SOURCE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    execute = _function(tree, "execute_create_app")
    execute_source = ast.get_source_segment(source, execute) or ""
    create_stage = _function(tree, "_stage_create_save")
    create_source = ast.get_source_segment(source, create_stage) or ""

    assert "app_name = business_object" in execute_source
    assert "business_object}-{activity_name" not in source
    assert "before_ids" in create_source
    assert "_identify_new_app" in create_source
    assert "target_app_id" in create_source
    assert "用应用名称搜索新创建的应用" not in create_source


def test_later_stages_relocate_by_exact_app_id():
    source = SOURCE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for name in ("_stage_enable", "_stage_set_group", "_stage_collect_result"):
        function = _function(tree, name)
        function_source = ast.get_source_segment(source, function) or ""
        assert "target_app_id" in function_source
        assert "_find_target_row_by_id" in function_source
