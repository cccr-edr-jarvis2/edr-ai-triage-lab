"""조사 결과의 시간 관계와 잘림 여부를 독립된 메모리 데이터로 확인한다."""

import pytest

from soc.osclient import MemoryOpenSearch
from soc.telemetry import tools


@pytest.fixture
def client(monkeypatch):
    store = MemoryOpenSearch()
    monkeypatch.setattr(tools, "get_client", lambda: store)
    return store


def event(event_id, ts, pid, ppid, name, host="WS-001"):
    return {"event_id": event_id, "ts": ts, "host": host, "type": "process",
            "process": {"pid": pid, "ppid": ppid, "name": name, "cmdline": name}}


def test_parent_pid_reused_after_child_is_not_selected(client):
    docs = [event("parent", "2026-01-01T00:00:00Z", 10, 0, "cmd.exe"),
            event("child", "2026-01-01T00:00:01Z", 20, 10, "rundll32.exe"),
            event("reused", "2026-01-01T00:00:02Z", 10, 0, "other.exe")]
    for doc in docs:
        client.index(index=f"{tools.settings.index_events}-2026.01.01", id=doc["event_id"], body=doc)
    tree = tools.get_process_tree("WS-001", 20)
    assert [parent["event_id"] for parent in tree["ancestors"]] == ["parent"]


def test_timeline_reports_when_results_are_truncated(client):
    for i in range(101):
        doc = event(f"event-{i}", "2026-01-01T00:00:00Z", i, 0, "test.exe")
        client.index(index=f"{tools.settings.index_events}-2026.01.01", id=doc["event_id"], body=doc)
    result = tools.get_host_timeline("WS-001", "2026-01-01T00:00:00Z", 0)
    assert len(result["events"]) == 100
    assert result["truncated"] is True
