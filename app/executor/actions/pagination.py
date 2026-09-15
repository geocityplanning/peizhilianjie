"""Small, UI-agnostic helpers for verifying paginated table transitions."""

import math


def wait_for_page_change(
    page,
    previous_state,
    direction,
    read_state,
    timeout_ms=6000,
    poll_interval_ms=250,
):
    """Wait for the expected page and a changed, non-empty table to stabilize."""
    previous_page = previous_state.get("page_number")
    previous_table = previous_state.get("table_signature")
    if not isinstance(previous_page, int) or direction not in {-1, 1}:
        return False

    expected_page = previous_page + direction
    stable_candidate = None
    stable_reads = 0
    polls = max(1, math.ceil(timeout_ms / poll_interval_ms))

    for _ in range(polls):
        page.wait_for_timeout(poll_interval_ms)
        current = read_state(page)
        if (
            current.get("page_number") != expected_page
            or current.get("table_signature") == previous_table
            or current.get("row_count", 0) <= 0
        ):
            stable_candidate = None
            stable_reads = 0
            continue

        candidate = (
            current["page_number"],
            current["table_signature"],
            current["row_count"],
        )
        if candidate == stable_candidate:
            stable_reads += 1
        else:
            stable_candidate = candidate
            stable_reads = 1
        if stable_reads >= 2:
            return True

    return False
