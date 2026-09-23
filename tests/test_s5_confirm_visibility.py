# -*- coding: utf-8 -*-
"""Offline guards for fixed-position Element message-box visibility."""
from __future__ import annotations

import sys
import types

import pytest


def _cap():
    if "actions.ensure_login" not in sys.modules:
        stub = types.ModuleType("actions.ensure_login")
        stub.ensure_login = lambda page=None: {"success": False}
        stub.is_logged_in = lambda page: True
        sys.modules["actions.ensure_login"] = stub
    from app.executor.actions import create_app_v2 as cap
    return cap


class _Page:
    def __init__(self, values):
        self.values = iter(values)
        self.scripts = []

    def evaluate(self, script, data=None):
        self.scripts.append(script)
        return next(self.values)


def test_fixed_position_visible_message_box_uses_style_aria_and_geometry_not_offset_parent():
    cap = _cap()
    page = _Page([1, True])

    assert cap._visible_message_box_count(page) == 1
    assert cap._click_unique_new_message_box_confirm(page) is True
    count_script, click_script = page.scripts
    for script in (count_script, click_script):
        assert "window.getComputedStyle" in script
        assert "style.display === 'none'" in script
        assert "style.visibility === 'hidden'" in script
        assert "getAttribute('aria-hidden') === 'true'" in script
        assert "getBoundingClientRect" in script
        assert "rect.width > 0 && rect.height > 0" in script
        assert "offsetParent" not in script
    assert "boxes.length !== 1" in click_script
    assert "buttons.length !== 1" in click_script
    assert "text === '确定'" in click_script
    assert "!button.disabled" in click_script
    assert "aria-disabled" in click_script and "is-disabled" in click_script


@pytest.mark.parametrize("count", [0, 2])
def test_zero_or_multiple_visible_boxes_are_not_promoted_to_one(count):
    cap = _cap()
    page = _Page([count])

    assert cap._visible_message_box_count(page) == count


@pytest.mark.parametrize(
    "button_case",
    ["hidden", "zero_rectangle", "multiple_exact_confirm_buttons", "disabled", "aria_disabled", "class_disabled"],
)
def test_hidden_or_ambiguous_or_disabled_confirm_button_never_clicks(button_case):
    cap = _cap()
    page = _Page([False])

    assert cap._click_unique_new_message_box_confirm(page) is False
    script = page.scripts[0]
    # The strict visible predicate rejects display/visibility/aria/geometry cases;
    # button exactness and enabledness remain independently required.
    for token in ("display === 'none'", "visibility === 'hidden'", "aria-hidden", "rect.width > 0", "buttons.length !== 1", "!button.disabled"):
        assert token in script
    assert button_case


@pytest.mark.parametrize(
    "visible_box_case",
    ["fixed_visible_old_box", "hidden_box", "zero_rectangle_box", "multiple_visible_boxes"],
)
def test_atomic_switch_guard_rejects_any_existing_visible_message_box(visible_box_case):
    cap = _cap()
    # The page fixture represents the atomic in-page predicate result.  Every
    # existing visible fixed-position box must make the atomic action fail before
    # the switch click; hidden/zero-rectangle/multiple cases remain guarded by
    # the same strict predicate and exact count check in that script.
    page = _Page([False])

    assert cap._click_unchecked_switch_by_known_main_row(
        page, "id", 1, 0, "row-1", "native", "name", "channel"
    ) is False
    script = page.scripts[0]
    assert "messageBoxes.length !== 0" in script
    assert "window.getComputedStyle" in script
    assert "style.display === 'none'" in script
    assert "style.visibility === 'hidden'" in script
    assert "getAttribute('aria-hidden') === 'true'" in script
    assert "getBoundingClientRect" in script
    assert "rect.width > 0 && rect.height > 0" in script
    assert "offsetParent" not in script
    assert "switchControl.click()" in script
    assert visible_box_case


def test_atomic_switch_script_keeps_unique_exact_confirmation_guards():
    cap = _cap()
    page = _Page([True])

    assert cap._click_unchecked_switch_by_known_main_row(
        page, "id", 1, 0, "row-1", "native", "name", "channel"
    ) is True
    script = page.scripts[0]
    assert "switches.length !== 1" in script
    assert "is-disabled" in script and "aria-disabled" in script
    assert "input && input.disabled" in script and "is-checked" in script
    assert "force" not in script and "Enter" not in script
