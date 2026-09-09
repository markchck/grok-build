"""그래프 조립: State + Node + Edge, 재시도 정책, 체크포인터 연결.

``build_graph()``가 만드는 그래프 모양:

```
START --> classify --+--(chitchat)--> chitchat_answer --> END
                      |
                      +--(factual)--> retrieve_dense  --+
                                       retrieve_sparse --+--> verify --+--(충분)------> answer --> END
                                             ^                        |
                                             |                        +--(부족·라운드 남음)--> expand_query --+
                                             +--------------------------------------------------------------+
```

``retrieve_dense``와 ``retrieve_sparse``는 같은 슈퍼스텝에서 병렬로
실행되고 ``verify``로 함께 모인다(docs/07-langgraph.md 2-4절). ``verify``에서
근거가 부족하고 아직 재검색 라운드가 남아 있으면 ``expand_query``를 거쳐
다시 ``retrieve_dense``/``retrieve_sparse``로 돌아간다 — 이 되돌아가는
간선이 이 장의 "반복(loop)"이다.

**노드별 오류 처리 전략은 노드마다 다르다** (docs/07-langgraph.md 6절에서
그 이유를 설명한다):

- ``classify``/``verify``: 모델 호출 실패를 노드 함수 안에서 직접 잡아
  규칙 기반 휴리스틱으로 대체한다(``docagent/graph/nodes.py``). 예외를
  밖으로 내보내지 않으므로 여기 붙이는 ``retry_policy``는 실행되지 않는다
  — 그래서 붙이지 않는다.
- ``retrieve_dense``/``retrieve_sparse``: 노드 함수 안에서 직접 최대 3회
  재시도하고, 그래도 실패하면 그 검색만 빈 결과로 남기고 계속 진행한다
  (``docagent/graph/nodes.py``의 ``_search_with_retry``). LangGraph의
  ``retry_policy``/``error_handler`` 대신 노드 안에서 직접 처리하는 이유는
  아래 "확인한 문제"를 본다.
- ``answer``/``chitchat_answer``: LangGraph의 ``retry_policy``(``_LLM_RETRY``)를
  그대로 쓴다. 이 두 노드는 자신의 슈퍼스텝에서 유일한 노드라 병렬 형제가
  없다 — 아래 문제가 적용되지 않는다.

**확인한 문제(langgraph 1.2.11, 실제로 재현)**: 같은 슈퍼스텝에서 병렬로
실행되는 형제 노드가 있을 때, 그중 하나가 재시도를 모두 소진하고
``error_handler``로 복구되더라도(핸들러 함수는 정상 호출되고 ``Command``도
만든다) 그 슈퍼스텝을 실행한 스레드 풀 실행기가 원래 예외를 다시 던져
``graph.invoke()`` 전체가 실패로 끝난다. 형제 노드가 없는 단독 노드에서는
``error_handler``가 기대대로 동작한다. 재현 스크립트와 트레이스백은
docs/07-langgraph.md 6절과 이 장의 "검증 상태"에 남겨 두었다. 이 문제 때문에
``retrieve_dense``/``retrieve_sparse``에는 ``error_handler``를 붙이지 않고
노드 함수 안에서 직접 예외를 잡는다.
"""

from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import RetryPolicy

from docagent.graph.nodes import (
    NodeDeps,
    expand_query,
    make_answer_node,
    make_chitchat_answer_node,
    make_classify_node,
    make_retrieve_dense_node,
    make_retrieve_sparse_node,
    make_route_after_verify,
    make_verify_node,
    route_after_classify,
)
from docagent.graph.state import GraphState
from docagent.llm import LLMError

# LLM을 호출하는 노드 중 실패를 그대로 노드 실패로 올리는 노드(answer,
# chitchat_answer)에 쓰는 재시도 정책. LLMError(모델 서버 타임아웃·5xx 등)만
# 재시도하고, 프롬프트나 코드 결함으로 생기는 다른 예외(TypeError, KeyError
# 등)는 재시도해 봐야 같은 결과가 나올 뿐이므로 그대로 올린다.
_LLM_RETRY = RetryPolicy(
    max_attempts=3,
    initial_interval=0.3,
    backoff_factor=2.0,
    retry_on=lambda exc: isinstance(exc, LLMError),
)


def build_graph(
    deps: NodeDeps,
    *,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """State + Node + Edge를 조립해 컴파일된 그래프를 만든다.

    ``checkpointer``를 주면 노드 실행 사이의 상태가 저장되고, 같은
    ``thread_id``로 다시 ``invoke``/``stream``을 호출하면(``None``을 입력으로
    주면) 마지막으로 성공한 노드 다음부터 재개한다(docs/07-langgraph.md
    5절 실행 재개 실습). ``checkpointer=None``이면 매 호출이 독립적으로
    처음부터 실행된다(체크포인트가 필요 없는 단순 테스트에서 쓴다).
    """

    graph = StateGraph(GraphState)

    graph.add_node("classify", make_classify_node(deps))
    graph.add_node("retrieve_dense", make_retrieve_dense_node(deps))
    graph.add_node("retrieve_sparse", make_retrieve_sparse_node(deps))
    graph.add_node("verify", make_verify_node(deps))
    graph.add_node("expand_query", expand_query)
    graph.add_node("answer", make_answer_node(deps), retry_policy=_LLM_RETRY)
    graph.add_node("chitchat_answer", make_chitchat_answer_node(deps), retry_policy=_LLM_RETRY)

    graph.add_edge(START, "classify")
    graph.add_conditional_edges(
        "classify",
        route_after_classify,
        {
            "chitchat_answer": "chitchat_answer",
            "retrieve_dense": "retrieve_dense",
            "retrieve_sparse": "retrieve_sparse",
        },
    )
    graph.add_edge("retrieve_dense", "verify")
    graph.add_edge("retrieve_sparse", "verify")
    graph.add_conditional_edges(
        "verify",
        make_route_after_verify(deps),
        {"answer": "answer", "expand_query": "expand_query"},
    )
    graph.add_edge("expand_query", "retrieve_dense")
    graph.add_edge("expand_query", "retrieve_sparse")
    graph.add_edge("answer", END)
    graph.add_edge("chitchat_answer", END)

    return graph.compile(checkpointer=checkpointer)
