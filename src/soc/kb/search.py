"""📘 플레이북 검색 (RAG의 R): 키워드 검색 + 벡터 검색을 RRF로 합칩니다.

- 키워드(BM25): 질문의 단어가 그대로 들어 있는 조각을 잘 찾음. 한국어 질문 → 영어 문서는 못 찾음
- 벡터(kNN)   : 뜻이 비슷한 조각을 찾음. 정확한 숫자·이름은 놓치기도 함
- RRF        : 두 검색의 점수는 단위가 달라 더할 수 없으니, 순위로 합칩니다. 1 / (60 + 순위)를 더함

도구 계약: search_playbook(query, k) → [{"source", "page", "category", "text", "score"}]
"""
from __future__ import annotations

from langchain_core.tools import tool

from ..config import settings
from ..osclient import get_client
from .embed import embed_query

RRF_K = 60
MAX_K = 5


def _hits(res: dict) -> list[dict]:
    out = []
    for h in res["hits"]["hits"]:
        s = h["_source"]
        out.append({"id": h["_id"], "source": s.get("source"), "page": s.get("page"),
                    "category": s.get("category"), "text": s.get("text", ""), "score": h.get("_score")})
    return out


def keyword_search(query: str, k: int = 10) -> list[dict]:
    body = {"size": k, "_source": {"excludes": ["embedding"]}, "query": {"match": {"text": query}}}
    return _hits(get_client().search(index=settings.index_kb, body=body))


def vector_search(query: str, k: int = 10) -> list[dict]:
    body = {"size": k, "_source": {"excludes": ["embedding"]},
            "query": {"knn": {"embedding": {"vector": embed_query(query), "k": k}}}}
    return _hits(get_client().search(index=settings.index_kb, body=body))


def rrf_fuse(result_lists: list[list[dict]], top_n: int) -> list[dict]:
    """여러 검색 결과를 순위로 합칩니다. 결과 하나하나에는 "id"가 있습니다."""
    # TODO[DOC-5]: 결과 목록마다 순위(1부터)를 매겨 문서 id별로 1 / (RRF_K + 순위)를 더하고,
    # 합계가 큰 순서로 top_n개를 돌려줍니다. 돌려줄 때 "score"를 RRF 점수로 바꿉니다.
    # 문서 id별 RRF 점수 합계와, 그 문서의 원래 정보(첫 등장)를 모은다
    totals: dict[str, float] = {}
    first_seen: dict[str, dict] = {}
    for results in result_lists:                       # 키워드 결과, 벡터 결과를 차례로
        for rank, hit in enumerate(results, start=1):  # 순위는 1부터
            doc_id = hit["id"]
            totals[doc_id] = totals.get(doc_id, 0.0) + 1 / (RRF_K + rank)
            first_seen.setdefault(doc_id, hit)
    # 합계가 큰 순서로 top_n개만 고르고, score를 RRF 점수로 바꿔 돌려준다
    best_ids = sorted(totals, key=lambda doc_id: totals[doc_id], reverse=True)[:top_n]
    return [first_seen[doc_id] | {"score": totals[doc_id]} for doc_id in best_ids]


def search(query: str, k: int = 3, mode: str = "hybrid") -> list[dict]:
    """mode: keyword / vector / hybrid. 평가(rag_eval)에서 세 방식을 비교합니다."""
    if mode == "keyword":
        return keyword_search(query, k)
    if mode == "vector":
        return vector_search(query, k)
    n = max(k * 3, 10)
    return rrf_fuse([keyword_search(query, n), vector_search(query, n)], top_n=k)


def search_playbook(query: str, k: int = 3) -> list[dict]:
    """회사의 대응 플레이북·정책 문서에서 질문과 관련된 조각을 찾습니다.
    판정 기준, 조치별 승인권자, 공격 유형별 대응 절차, 오탐 예외 규정을 확인할 때 씁니다.
    결과마다 source(파일 이름)가 있으니, 권고에는 반드시 출처를 붙이세요.

    Args:
        query: 찾을 내용 (예: "LSASS 덤프 대응 승인권자")
        k: 돌려받을 조각 수 (기본 3, 최대 5)
    """
    # 계약 상한을 지켜 LLM이 큰 k를 보내도 오류 없이 최대 5개로 자릅니다.
    k = max(1, min(k, MAX_K))
    return [{key: h[key] for key in ("source", "page", "category", "text", "score")} for h in search(query, k)]


KB_TOOLS = [tool(search_playbook)]
