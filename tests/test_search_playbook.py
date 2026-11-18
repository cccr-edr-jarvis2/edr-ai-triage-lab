"""📘 search_playbook 도구 계약 테스트.

docs/CONTRACT.md의 search_playbook 행을 칸마다 하나씩 테스트로 옮겼습니다.
입력(k 기본 3), 출력 칸 5개, 결과 개수 상한(최대 5), 실패 처리(결과 없음 [] / 오류는 예외 전달).
진짜 함수와 가짜 함수(mocks.py) 둘 다 같은 계약을 지키는지 봅니다. OpenSearch, OpenAI 없이 돕니다.

    pytest tests/test_search_playbook.py -q
"""
import pytest

from soc import mocks
from soc.kb import search as kb_search

CONTRACT_KEYS = {"text", "source", "page", "category", "score"}   # 계약의 "출력 (꼭 있어야 하는 칸)"
DEFAULT_K, MAX_K = 3, 5                                           # 계약의 "입력", "결과 개수 상한"


def _fake_search(calls: list):
    # 받은 k만큼 결과를 만드는 가짜 검색. 내부 전용 칸 id가 밖으로 새지 않는지도 같이 본다
    def fake(query, k):
        calls.append((query, k))
        return [{"id": f"hit-{i}", "source": "playbook.pdf", "page": i + 1, "category": "Table",
                 "text": f"hit {i}", "score": 1.0} for i in range(k)]
    return fake


@pytest.mark.parametrize("kwargs, expected_k", [({}, DEFAULT_K), ({"k": 3}, 3), ({"k": 5}, 5),
                                                ({"k": 7}, MAX_K), ({"k": 0}, 1)])
def test_k_default_and_bounds(monkeypatch, kwargs, expected_k):
    calls = []
    monkeypatch.setattr(kb_search, "search", _fake_search(calls))
    result = kb_search.search_playbook("격리 승인", **kwargs)
    assert calls == [("격리 승인", expected_k)]                       # 질문은 그대로, k만 제한
    assert len(result) == expected_k


def test_output_has_exactly_contract_keys(monkeypatch):
    monkeypatch.setattr(kb_search, "search", _fake_search([]))
    assert all(set(hit) == CONTRACT_KEYS for hit in kb_search.search_playbook("격리 승인"))


def test_no_result_returns_empty_list(monkeypatch):
    monkeypatch.setattr(kb_search, "search", lambda _query, _k: [])
    assert kb_search.search_playbook("없는 내용") == []


def test_search_error_is_raised(monkeypatch):
    # 계약: OpenSearch나 임베딩 오류는 삼키지 않고 예외로 전달
    def broken(_query, _k):
        raise ConnectionError("opensearch down")
    monkeypatch.setattr(kb_search, "search", broken)
    with pytest.raises(ConnectionError):
        kb_search.search_playbook("격리 승인")


def test_mock_follows_same_contract():
    # 그래프 테스트는 가짜 함수를 쓰므로, 가짜도 같은 출력 칸과 상한을 지켜야 한다
    default = mocks.search_playbook("격리 승인")
    assert 0 < len(default) <= DEFAULT_K
    assert len(mocks.search_playbook("격리 승인", k=7)) <= MAX_K
    assert all(set(hit) == CONTRACT_KEYS for hit in default)
