"""병렬 조사 + 결과 통합 데모(모델 서버 불필요 - salesdata만 쓴다)."""

from __future__ import annotations

from docagent.multiagent.graph_parallel import build_parallel_graph, initial_parallel_state

if __name__ == "__main__":
    graph = build_parallel_graph()
    state = initial_parallel_state("노트북이랑 모니터 매출 비교해줘")
    result = graph.invoke(state)
    print("조사된 제품:", list(result["investigations"].keys()))
    print(result["final_text"])
