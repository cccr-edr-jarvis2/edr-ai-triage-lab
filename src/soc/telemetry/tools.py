"""🔍 조사 도구: 에이전트가 PC 기록을 조사할 때 부르는 함수들.

도구 계약 (docs/CONTRACT.md)
- 모든 결과의 기록 하나하나에 `event_id`가 있다 → 가드레일이 판정 근거를 검증하는 데 씀
- 명령줄은 앞부분만 (요약) → LLM에게 넘기는 글자 수가 곧 비용과 속도

함수의 설명(docstring)은 LLM이 '어떤 도구를 부를지' 고를 때 읽는 글입니다. 신경 써서 쓰세요.

    pytest tests/test_telemetry_tools.py -q
"""
from __future__ import annotations

from datetime import datetime, timedelta

from langchain_core.tools import tool

from ..config import settings
from ..osclient import get_client

EVENTS = f"{settings.index_events}-*"
CMD_MAX = 120           # 명령줄 요약 길이
MAX_ANCESTORS = 5
MAX_CHILDREN = 5
ACTIVITY_LIMIT = 50
TIMELINE_LIMIT = 100


def _search(body: dict) -> list[dict]:
    res = get_client().search(index=EVENTS, body=body)
    return [h["_source"] for h in res["hits"]["hits"]]


def summarize(e: dict) -> dict:
    """기록 하나를 LLM에게 넘길 모양으로 줄입니다. (이미 만들어 둔 도우미)"""
    p = e.get("process", {})
    out = {"event_id": e["event_id"], "ts": e["ts"], "type": e["type"],
           "pid": p.get("pid"), "ppid": p.get("ppid"), "name": p.get("name"),
           "cmdline": (p.get("cmdline") or "")[:CMD_MAX], "user": p.get("user"), "signed": p.get("signed")}
    if e.get("network"):
        n = e["network"]
        out["detail"] = f"{n['dst_ip']}:{n['dst_port']} ({n.get('bytes_out', 0)} bytes)"
    elif e.get("file"):
        f = e["file"]
        out["detail"] = f"{f['action']} {f['path']}" + (f" → {f['new_path']}" if f.get("new_path") else "")
    elif e.get("registry"):
        out["detail"] = f"{e['registry']['key']} = {e['registry']['value']}"
    elif e.get("script_text"):
        out["detail"] = e["script_text"][:200]
    return out


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def _find_process(host: str, pid: int, before: str | None = None) -> dict | None:
    """host에서 pid를 가진 가장 최근 프로세스 생성 기록 (before가 있으면 그 시각 이전)."""
    filters = [{"term": {"host": host}}, {"term": {"type": "process"}}, {"term": {"process.pid": pid}}]
    if before is not None:
        filters.append({"range": {"ts": {"lte": before}}})
    rows = _search({"size": 1, "sort": [{"ts": "desc"}], "query": {"bool": {"filter": filters}}})
    return rows[0] if rows else None


def get_process_tree(host: str, pid: int) -> dict:
    """프로세스의 족보를 봅니다: 누가 이 프로세스를 실행했는지(부모, 조부모 …)와 이 프로세스가 실행한 자식들.
    '누가 실행했나', '정상 프로그램이 띄운 것인가'를 확인할 때 씁니다.

    Args:
        host: PC 이름 (예: WS-007)
        pid: 프로세스 번호 (알림의 pid)
    """
    process = _find_process(host, pid)
    result = {"host": host, "pid": pid, "process": summarize(process) if process else None,
              "ancestors": [], "children": []}
    if process is None:
        result["note"] = f"프로세스(pid {pid})의 생성 기록이 없습니다."
        return result

    current = process
    visited = {pid}
    for _ in range(MAX_ANCESTORS):
        parent_pid = current["process"].get("ppid")
        if not parent_pid:
            break
        if parent_pid in visited:
            result["note"] = f"부모 PID {parent_pid}가 반복되어 탐색을 중단했습니다."
            break
        visited.add(parent_pid)
        parent = _find_process(host, parent_pid, before=current["ts"])
        if parent is None:
            result["note"] = f"부모 프로세스(pid {parent_pid})의 생성 기록이 없습니다."
            break
        result["ancestors"].append(summarize(parent))
        current = parent

    children = _search({"size": MAX_CHILDREN, "sort": [{"ts": "asc"}], "query": {"bool": {"filter": [
        {"term": {"host": host}}, {"term": {"type": "process"}}, {"term": {"process.ppid": pid}},
        {"range": {"ts": {"gte": process["ts"]}}}]}}})
    result["children"] = [summarize(child) for child in children]
    return result


def get_process_activity(host: str, pid: int) -> dict:
    """한 프로세스가 한 일을 시간순으로 봅니다: 네트워크 연결, 파일 생성·변경, 레지스트리 변경, 실행한 스크립트.
    '이 프로세스가 무엇을 남겼나(파일, 통신)'를 확인할 때 씁니다.

    Args:
        host: PC 이름 (예: WS-007)
        pid: 프로세스 번호
    """
    events = _search({"size": ACTIVITY_LIMIT, "sort": [{"ts": "asc"}], "query": {"bool": {"filter": [
        {"term": {"host": host}}, {"term": {"process.pid": pid}},
        {"terms": {"type": ["network", "file", "registry", "script"]}}]}}})
    return {"host": host, "pid": pid, "events": [summarize(event) for event in events]}


def get_host_timeline(host: str, around_ts: str, minutes: int = 10) -> dict:
    """특정 시각 앞뒤로 그 PC에서 일어난 모든 일을 시간순으로 봅니다.
    알림 직전·직후에 다른 수상한 활동(다른 프로그램, 다른 통신)이 있었는지 넓게 볼 때 씁니다.

    Args:
        host: PC 이름 (예: WS-007)
        around_ts: 기준 시각 (ISO 형식, 예: 2026-11-16T09:12:02Z). 보통 알림 시각
        minutes: 기준 시각 앞뒤로 몇 분까지 볼지 (기본 10)
    """
    if minutes < 0:
        raise ValueError("minutes는 0 이상이어야 합니다")
    center = _parse_ts(around_ts)
    if center.utcoffset() is None:
        raise ValueError("around_ts에는 시간대가 필요합니다 (예: Z 또는 +09:00)")
    start, end = _iso(center - timedelta(minutes=minutes)), _iso(center + timedelta(minutes=minutes))
    events = _search({"size": TIMELINE_LIMIT + 1, "sort": [{"ts": "asc"}], "query": {"bool": {"filter": [
        {"term": {"host": host}}, {"range": {"ts": {"gte": start, "lte": end}}}]}}})
    return {"host": host, "from": start, "to": end,
            "events": [summarize(event) for event in events[:TIMELINE_LIMIT]],
            "truncated": len(events) > TIMELINE_LIMIT}




TELEMETRY_TOOLS = [tool(get_process_tree), tool(get_process_activity), tool(get_host_timeline)]
