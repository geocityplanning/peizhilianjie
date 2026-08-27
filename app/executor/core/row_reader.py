# -*- coding: utf-8 -*-
"""
行读取工具：动态读表头 + 整行全量提取，返回 {列名: 值} 字典。
不硬编码列索引，139后台改列顺序也不影响。
"""
from typing import Optional


def read_row_full(page, row_locator) -> dict:
    """读取表格的一整行，返回 {表头列名: 单元格文本} 的完整字典。

    Args:
        page: Playwright page 对象
        row_locator: 已定位到目标行的 locator

    Returns:
        {"序号": "1", "ID": "11945", "应用名称": "...", "所属渠道": "...",
         "应用链接": "...", "长连接": "...", ...所有列...,
         "_status": "ON/OFF", "_row_html_len": 1234}
    """
    result = {}

    # 1. 读表头
    headers = []
    th_count = page.locator("th").count()
    for i in range(th_count):
        try:
            h = page.locator("th").nth(i).inner_text().strip()
            headers.append(h if h else f"col_{i}")
        except Exception:
            headers.append(f"col_{i}")

    # 2. 读每个单元格
    cells = row_locator.locator("td")
    ncells = cells.count()
    for ci in range(min(ncells, len(headers))):
        try:
            txt = cells.nth(ci).inner_text().strip()
            result[headers[ci]] = txt
        except Exception:
            result[headers[ci]] = ""

    # 3. 额外提取：状态开关（el-switch 在操作列里，文本读不到）
    try:
        sw = row_locator.locator(".el-switch").first
        if sw.count() > 0:
            result["_status"] = "ON" if "is-checked" in (sw.get_attribute("class") or "") else "OFF"
        else:
            result["_status"] = ""
    except Exception:
        result["_status"] = ""

    return result
