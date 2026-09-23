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

    def evaluate(self, script):
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


@pytest.mark.parametrize("click_result", [False])
def test_hidden_or_ambiguous_or_disabled_confirm_button_never_clicks(click_result):
    cap = _cap()
    page = _Page([click_result])

    assert cap._click_unique_new_message_box_confirm(page) is False
    script = page.scripts[0]
    # The strict visible predicate rejects display/visibility/aria/geometry cases;
    # button exactness and enabledness remain independently required.
    for token in ("display === 'none'", "visibility === 'hidden'", "aria-hidden", "rect.width > 0", "buttons.length !== 1", "!button.disabled"):
        assert token in script
