from app.executor.actions.pagination import wait_for_page_change


class FakePage:
    def __init__(self, states):
        self.states = list(states)
        self.index = 0

    def wait_for_timeout(self, _milliseconds):
        if self.index < len(self.states) - 1:
            self.index += 1


def read_state(page):
    return page.states[page.index]


def state(page_number, table_signature, row_count=2):
    return {
        "page_number": page_number,
        "table_signature": table_signature,
        "row_count": row_count,
    }


def test_waits_for_table_after_page_number_changes():
    page = FakePage([
        state(1, "A"),
        state(2, "A"),
        state(2, "B"),
        state(2, "B"),
    ])
    assert wait_for_page_change(
        page, state(1, "A"), 1, read_state, timeout_ms=1000, poll_interval_ms=1
    ) is True


def test_page_number_only_change_times_out():
    page = FakePage([state(1, "A"), state(2, "A"), state(2, "A")])
    assert wait_for_page_change(
        page, state(1, "A"), 1, read_state, timeout_ms=5, poll_interval_ms=1
    ) is False


def test_table_change_without_expected_page_change_is_rejected():
    page = FakePage([state(1, "A"), state(1, "B"), state(1, "B")])
    assert wait_for_page_change(
        page, state(1, "A"), 1, read_state, timeout_ms=5, poll_interval_ms=1
    ) is False


def test_page_and_table_change_then_stabilize_succeeds():
    page = FakePage([state(2, "B"), state(2, "B"), state(2, "B")])
    assert wait_for_page_change(
        page, state(1, "A"), 1, read_state, timeout_ms=1000, poll_interval_ms=1
    ) is True


def test_previous_page_transition_requires_page_and_table_change():
    page = FakePage([state(3, "C"), state(2, "C"), state(2, "B"), state(2, "B")])
    assert wait_for_page_change(
        page, state(3, "C"), -1, read_state, timeout_ms=1000, poll_interval_ms=1
    ) is True
