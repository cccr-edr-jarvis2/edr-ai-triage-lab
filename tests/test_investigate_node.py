"""📘 조사 노드 테스트 (TODO[AG-1]).

nodes.py의 TODO[AG-1] 지시와 ARCHITECTURE의 조사 노드 칸(읽는 상태 alert, messages / 쓰는 상태 messages, trace, usage)을
항목마다 하나씩 확인합니다. 그래프 엣지(TODO[AG-3])와 권고 노드(TODO[AG-2]) 없이 노드 함수를 직접 이어 부르고,
상태 합치기는 그래프와 같은 리듀서(add_messages, merge_usage)를 씁니다.
가짜 LLM과 가짜 조사 도구를 쓰므로 API 키와 OpenSearch가 필요 없습니다.

    pytest tests/test_investigate_node.py -q
"""
import operator
from typing import cast

import pytest
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph.message import add_messages

from soc.agent import nodes
from soc.agent.prompts import prompt
from soc.agent.state import TriageState, merge_usage
from soc.agent.toolset import telemetry_tools

ALERT = {"alert_id": "al-test-investigate", "ts": "2026-11-16T09:12:02Z", "host": "WS-007", "source": "rule",
         "rule_id": "R003", "title": "comsvcs.dll MiniDump를 이용한 LSASS 메모리 덤프 의심", "severity": "critical",
         "techniques": ["T1003.001"], "event_ids": ["evt-00000023"], "pid": 40702}
REDUCERS = {"messages": add_messages, "trace": operator.add, "seen_event_ids": operator.add, "usage": merge_usage}


def _start(**extra) -> TriageState:
    return cast(TriageState, {"alert": ALERT, "messages": [], **extra})


def _merge(state: TriageState, update: dict) -> TriageState:
    # 그래프가 노드 결과를 상태에 합치는 방식과 같게: 리듀서가 있는 칸은 리듀서로, 나머지는 덮어쓴다
    out: dict = dict(state)
    for key, value in update.items():
        if key in REDUCERS:
            out[key] = REDUCERS[key](out.get(key) or ({} if key == "usage" else []), value)
        else:
            out[key] = value
    return cast(TriageState, out)


def _unanswered_tool_calls(messages: list[AnyMessage]) -> set[str]:
    # 판정 LLM에 넘기기 전 대화 규약: 모든 도구 요청 id에 대응하는 ToolMessage가 있어야 한다
    asked = {c["id"] for m in messages if isinstance(m, AIMessage) for c in m.tool_calls}
    answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
    return {i for i in asked if i} - answered


class _Recorder:
    """LLM에 무엇이 넘어갔는지 기록하는 가짜. 정해 둔 응답을 차례로 돌려준다."""

    def __init__(self, replies: list[AIMessage]):
        self.replies: list[AIMessage] = replies
        self.calls: list[list[AnyMessage]] = []
        self.bound: list[str] = []

    def bind_tools(self, tools, **_):
        self.bound = [t.name for t in tools]
        return self

    def invoke(self, messages, config=None):
        _ = config
        self.calls.append(list(messages))
        return self.replies.pop(0)


def test_first_call_adds_alert_then_asks_for_tools():
    update = nodes.investigate(_start())
    first, last = update["messages"][0], update["messages"][-1]
    assert isinstance(first, HumanMessage) and ALERT["alert_id"] in first.content   # 알림이 첫 메시지
    assert isinstance(last, AIMessage) and last.tool_calls                          # 도구를 요청함
    bound = {t.name for t in telemetry_tools()}
    assert {c["name"] for c in last.tool_calls} <= bound                            # 연결한 조사 도구만 요청
    assert update["trace"] == ["조사"]
    assert update["usage"]["llm_calls"] == 1


def test_llm_gets_system_prompt_first_but_state_does_not_keep_it(monkeypatch):
    rec = _Recorder([AIMessage(content="조사 완료")])
    monkeypatch.setattr(nodes, "get_llm", lambda: rec)
    update = nodes.investigate(_start())
    sent = rec.calls[0]
    assert isinstance(sent[0], SystemMessage) and sent[0].content == prompt("investigate")
    assert isinstance(sent[1], HumanMessage) and ALERT["alert_id"] in str(sent[1].content)
    assert rec.bound == [t.name for t in telemetry_tools()]                         # 조사 도구 전부를 연결
    assert not any(isinstance(m, SystemMessage) for m in update["messages"])


def test_loop_tools_then_finish_goes_to_judge():
    state = _merge(_start(), nodes.investigate(_start()))
    assert nodes.after_investigate(state) == "tools"
    state = _merge(state, nodes.run_tools(state))
    assert state.get("tool_rounds") == 1 and state.get("seen_event_ids")            # 도구 결과의 event_id 기록
    update = nodes.investigate(state)
    assert len(update["messages"]) == 1                                             # 두 번째부터 알림을 다시 넣지 않음
    state = _merge(state, update)
    done = state.get("messages", [])[-1]
    assert isinstance(done, AIMessage) and not done.tool_calls                      # 조사 완료
    assert nodes.after_investigate(state) == "judge"
    assert not _unanswered_tool_calls(state.get("messages", []))
    assert state.get("trace") == ["조사", "도구", "조사"]
    assert state.get("usage", {}).get("llm_calls") == 2                             # 사용량이 호출마다 쌓임


def test_guardrail_retry_passes_full_history_without_second_alert(monkeypatch):
    # 가드레일이 재조사를 시키면 피드백(HumanMessage)이 대화 끝에 붙은 채 조사 노드가 다시 불린다
    rec = _Recorder([AIMessage(content="조사 완료"), AIMessage(content="다시 확인 완료")])
    monkeypatch.setattr(nodes, "get_llm", lambda: rec)
    state = _merge(_start(), nodes.investigate(_start()))
    feedback = HumanMessage(content="판정이 검증을 통과하지 못했습니다: 조회하지 않은 근거")
    state = _merge(state, {"messages": [feedback]})
    update = nodes.investigate(state)
    sent = rec.calls[1]
    assert [type(m) for m in sent] == [SystemMessage, HumanMessage, AIMessage, HumanMessage]
    assert sent[-1].content == feedback.content                                     # 피드백이 마지막
    assert len(update["messages"]) == 1                                             # 알림을 다시 넣지 않음


def test_round_limit_drops_tool_request_before_judge(monkeypatch):
    monkeypatch.setattr(nodes.settings, "max_tool_rounds", 1)
    state = _merge(_start(tool_rounds=1), nodes.investigate(_start(tool_rounds=1)))
    last = state.get("messages", [])[-1]
    assert isinstance(last, AIMessage) and not last.tool_calls                      # 상한이면 도구 요청을 버림
    assert nodes.after_investigate(state) == "judge"
    assert not _unanswered_tool_calls(state.get("messages", []))                    # 답 없는 도구 요청이 남지 않음


def test_missing_alert_on_first_call_stops_before_llm(monkeypatch):
    rec = _Recorder([])
    monkeypatch.setattr(nodes, "get_llm", lambda: rec)
    with pytest.raises(ValueError):
        nodes.investigate(cast(TriageState, {"messages": []}))
    assert rec.calls == []                                                          # LLM을 부르지 않음
