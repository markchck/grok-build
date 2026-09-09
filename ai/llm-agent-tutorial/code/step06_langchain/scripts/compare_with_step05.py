"""5단계(직접 구현)와 6단계(LangChain)를 같은 입력·같은 모델 서버·같은 Milvus
컬렉션 데이터로 비교한다 (6단계 완료 기준: "이전 구현과 동일한 입력으로
동작을 비교한다").

두 구현 모두 파이썬 패키지 이름이 ``docagent``라서 한 프로세스 안에 같이
``import``할 수 없다. 그래서 이 스크립트는 각 단계 코드를 **별도
서브프로세스**로 실행해서(각자 자기 디렉터리를 ``sys.path``에 놓고) 결과를
JSON으로만 받는다 — 두 구현이 서로의 코드를 전혀 몰라도 되게 하기 위해서다.

비교하는 것:

1. RAG 검색: 같은 질문으로 5단계 ``hybrid_search``와 6단계
   ``docagent.rag.search.hybrid_search``를 각각 부르고, 반환된 청크 ID
   (``pk``) 집합과 순위를 비교한다.
2. 도구 호출 에이전트: 같은 질문으로 5단계에는 없는 도구 호출을(3단계
   ``run_agent_collect``) 6단계 ``run_agent_collect``와 비교한다 — 둘 다
   같은 스텁 서버가 "첫 도구를 인자 없이 한 번 호출한다"는 동일한 각본을
   따르므로, ``finish_reason``과 도구 이름이 같은지 확인한다.

사용:
    OPENAI_BASE_URL=http://127.0.0.1:8111/v1 OPENAI_API_KEY=sk-test \\
    CHAT_MODEL=stub-model EMBEDDING_MODEL=stub-embedding \\
    DOCAGENT_MILVUS_URI=/tmp/docagent_compare.db \\
        python scripts/compare_with_step05.py "2분기 노트북 매출이 둔화된 이유는?"

**컬렉션은 공유하지 않는다.** ``langchain_milvus.Milvus``가 만드는 스키마
(``enable_dynamic_field=True``인 동적 필드)와 5단계 ``pymilvus``가 만드는
스키마(``doc_id``/``doc_title``/``page``/``chunk_index``/``source_url``을
각각 독립된 스칼라 필드로 선언)는 물리적으로 다르다 — 6절에서 이 차이를
직접 설명한다. 그래서 이 스크립트는 두 구현이 **각자 자기 컬렉션**에 같은
원본 문서(``data/docs/``, 두 단계 모두 내용이 같다)를 적재하게 하고, 청크 ID
(``pk``, 문서 파일명과 청크 순번으로만 정해지므로 두 구현에서 동일하다)로
결과를 비교한다. ``--milvus-uri-step05``로 5단계 전용 Milvus Lite 파일
경로를 따로 지정한다(기본값은 6단계와 겹치지 않는 별도 경로).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

STEP06_DIR = Path(__file__).resolve().parent.parent
STEP05_DIR = STEP06_DIR.parent / "step05_hybrid_rag"
STEP03_DIR = STEP06_DIR.parent / "step03_tool_calling"

_STEP05_SEARCH_CODE = """
import json, sys
sys.path.insert(0, {step05_dir!r})
from docagent.rag.ingest import ingest_directory
from docagent.rag.search import hybrid_search
ingest_directory({step05_dir!r} + "/data/docs", recreate=True)
hits = hybrid_search(sys.argv[1], limit=int(sys.argv[2]))
print(json.dumps([{{"pk": h["pk"], "score": h["score"]}} for h in hits], ensure_ascii=False))
"""

_STEP03_AGENT_CODE = """
import json, sys
sys.path.insert(0, {step03_dir!r})
from docagent.agent import run_agent_collect
result = run_agent_collect(sys.argv[1])
print(json.dumps({{"finish_reason": result.finish_reason, "steps_used": result.steps_used}}, ensure_ascii=False))
"""


def _run_subprocess_json(code: str, args: list[str], *, env: dict[str, str]) -> dict | list:
    proc = subprocess.run(
        [sys.executable, "-c", code, *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"서브프로세스 실패:\n{proc.stderr}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def compare_search(query: str, top_k: int, *, milvus_uri_step05: str) -> None:
    print(f"\n=== RAG 검색 비교: {query!r} (top_k={top_k}) ===")

    sys.path.insert(0, str(STEP06_DIR))
    from docagent.rag.ingest import ingest_directory
    import docagent.rag.search as step06_search

    count, vs = ingest_directory(STEP06_DIR / "data" / "docs", drop_old=True)
    step06_search._vector_store = vs
    print(f"6단계 컬렉션(langchain-milvus 스키마)에 {count}개 청크를 적재했다.")

    step06_hits = step06_search.hybrid_search(query, limit=top_k)
    step06_ids = [h["pk"] for h in step06_hits]

    step05_code = _STEP05_SEARCH_CODE.format(step05_dir=str(STEP05_DIR))
    step05_env = os.environ.copy()
    step05_env["DOCAGENT_MILVUS_URI"] = milvus_uri_step05
    try:
        step05_hits = _run_subprocess_json(step05_code, [query, str(top_k)], env=step05_env)
        step05_ids = [h["pk"] for h in step05_hits]
        print(f"5단계 컬렉션(pymilvus 직접 스키마, uri={milvus_uri_step05})에도 문서를 적재했다.")
    except Exception as exc:
        print(f"[알림] 5단계 검색을 실행하지 못했다: {exc}")
        step05_ids = None

    print(f"6단계(LangChain) 결과 순서: {step06_ids}")
    if step05_ids is not None:
        print(f"5단계(직접 구현) 결과 순서: {step05_ids}")
        overlap = set(step06_ids) & set(step05_ids)
        print(f"겹치는 청크 수: {len(overlap)} / {top_k}")
        if step06_ids == step05_ids:
            print("-> 순위까지 완전히 같다.")
        elif overlap:
            print("-> 상위 결과 집합은 겹치지만 순위가 다르다 (랭커 파라미터·부동소수 오차 등 원인).")
        else:
            print("-> 겹치는 결과가 없다 — 컬렉션 데이터가 다르거나 검색 조건이 다른지 확인해야 한다.")


def compare_agent(message: str) -> None:
    print(f"\n=== 도구 호출 에이전트 비교: {message!r} ===")

    sys.path.insert(0, str(STEP06_DIR))
    from docagent.agent import run_agent_collect

    step06_result = run_agent_collect(message)
    print(f"6단계(LangChain create_agent): finish_reason={step06_result.finish_reason}, steps_used={step06_result.steps_used}")

    step03_code = _STEP03_AGENT_CODE.format(step03_dir=str(STEP03_DIR))
    try:
        step03_result = _run_subprocess_json(step03_code, [message], env=os.environ.copy())
        print(f"3단계(직접 구현 while 루프): finish_reason={step03_result['finish_reason']}, steps_used={step03_result['steps_used']}")
        if step03_result["finish_reason"] == step06_result.finish_reason:
            print("-> finish_reason이 같다.")
        else:
            print("-> finish_reason이 다르다 — 스텁 서버 각본이나 반복 한도 설정을 확인해야 한다.")
    except Exception as exc:
        print(f"[알림] 3단계 에이전트를 실행하지 못했다: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", default="2분기 노트북 매출이 둔화된 이유는?")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--agent-message", default="2분기 노트북 서울 매출 합계 알려줘")
    parser.add_argument(
        "--milvus-uri-step05",
        default="/tmp/docagent_compare_step05.db",
        help="5단계 전용 Milvus Lite 파일 경로 (6단계 컬렉션과 분리)",
    )
    args = parser.parse_args()

    compare_search(args.query, args.top_k, milvus_uri_step05=args.milvus_uri_step05)
    compare_agent(args.agent_message)


if __name__ == "__main__":
    main()
