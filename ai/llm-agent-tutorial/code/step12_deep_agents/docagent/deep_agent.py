"""Deep Agents 조립: 계획(write_todos) + 파일시스템 + 스킬 + 하위 에이전트 위임.

이 파일이 12단계의 핵심이다. ``deepagents.create_deep_agent()`` 한 번의
호출이 LangGraph 그래프 하나를 조립한다 — 7단계(``code/step07_langgraph/``)에서
``StateGraph``에 노드·간선을 하나씩 붙였던 것과 결과물의 종류는 같다(컴파일된
그래프). 다른 점은 **누가 그 그래프의 모양을 정하는가**다.

- 7단계: classify/retrieve/verify/answer라는 마디와 그 사이의 조건 분기를
  이 저장소의 집필자가 코드로 고정했다. "다음에 어느 노드로 가는가"의
  일부(분기 조건)만 모델이 돕는다.
- 이 장: 마디는 훨씬 적다(에이전트 노드 + 도구 실행 노드 정도). 대신 "이번
  요청에 어떤 하위 작업이 필요한가", "그 작업을 스스로 할지 하위 에이전트에
  맡길지", "계획을 어떻게 바꿀지"를 **모델이 도구 호출로 직접 결정**한다.
  Deep Agents가 미리 만들어 붙여주는 것은 그 결정에 쓸 수 있는 도구
  집합(계획 관리, 파일 읽기/쓰기, 하위 에이전트 호출, 스킬 목록)이지, 흐름
  자체가 아니다.

이 장이 실제로 설치해 확인한 패키지(2026-09-09, 이 문서 하단 "참고 문서"):
``deepagents`` 0.7.13, ``langchain`` 1.4.0, ``langchain-core`` 1.6.2,
``langgraph`` 1.2.11(7단계와 같은 버전대), ``langchain-openai`` 1.6.1.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import LocalShellBackend
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from docagent.config import Settings, get_settings

# 이 백엔드 루트 아래(skills/, data/, results/)만 에이전트가 파일을 읽고 쓰고
# 스크립트를 실행할 수 있다. config.agent_workdir을 벗어난 경로는 다루지 않는다.
SKILLS_SOURCE = "skills/"

ROOT_SYSTEM_PROMPT = """너는 문서와 CSV를 분석하는 사내 에이전트다.

가진 도구:
- write_todos: 3단계 이상 걸리는 요청이면 먼저 계획을 세우고, 진행하면서
  상태를 갱신한다. 실행 중 새로 알게 된 사실로 계획이 바뀌면 주저하지 말고
  write_todos를 다시 불러 계획을 고친다.
- task: 아래 스킬이 다루는 종류의 작업은 직접 계산·검색하지 말고 해당
  스킬을 아는 하위 에이전트에게 위임한다.
    - csv-analyst: sales.csv 수치 집계(csv-analysis 스킬)
    - doc-researcher: 문서에서 원인 근거 찾기(doc-research 스킬)
- read_file / write_file / ls / glob / grep: 중간 결과는 반드시 results/
  아래 파일로 저장하고, 다음 단계에서는 그 파일을 다시 읽어 쓴다. 검색한
  문서나 CSV 전체를 대화에 그대로 옮기지 않는다.

절대 숫자를 스스로 계산해 답하지 않는다 — csv-analyst가 낸 결과 파일의
숫자만 인용한다. 원인 설명도 doc-researcher가 찾은 근거 문장만 인용한다."""

CSV_SUBAGENT_PROMPT = """너는 CSV 분석 전문 하위 에이전트다. csv-analysis
스킬(SKILL.md)의 절차를 그대로 따른다: execute 도구로
skills/csv-analysis/analyze_sales.py를 돌리고, --out으로 결과를
results/ 아래 파일에 저장한 뒤, 그 파일 경로와 핵심 수치를 요약해 보고한다."""

DOC_SUBAGENT_PROMPT = """너는 문서 조사 전문 하위 에이전트다. doc-research
스킬(SKILL.md)의 절차를 그대로 따른다: execute 도구로
skills/doc-research/find_evidence.py를 돌려 관련 키워드로 근거 문단을 찾고,
--out으로 결과를 results/ 아래 파일에 저장한 뒤, 찾은 근거의 출처
꼬리표(`{doc_id}#p{page}`)와 함께 요약해 보고한다. 근거가 없으면 없다고
그대로 보고한다."""


def build_chat_model(settings: Settings | None = None) -> ChatOpenAI:
    """Deep Agents가 요구하는 LangChain ``BaseChatModel``을 만든다.

    ``docagent.llm``(PROJECT-SPEC.md 9절 인터페이스)과 같은 설정
    (``get_settings()``)을 읽되, 반환 타입은 다르다 — 이유는
    ``docagent/llm.py`` 상단 설명을 본다.
    """

    settings = settings or get_settings()
    return ChatOpenAI(
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
        model=settings.chat_model,
        timeout=settings.request_timeout_seconds,
    )


def build_agent(
    *,
    model: BaseChatModel | None = None,
    settings: Settings | None = None,
    workdir: str | Path | None = None,
):
    """Deep Agent 그래프를 조립한다.

    Args:
        model: 주입할 모델. 생략하면 ``build_chat_model()``(실제 OpenAI 호환
            서버 호출)을 쓴다. 테스트·데모에서는 ``docagent.fake_model.
            ScriptedChatModel``을 주입한다.
        settings: 생략하면 ``get_settings()``.
        workdir: 파일시스템 백엔드의 루트. 생략하면
            ``settings.agent_workdir``.
    """

    settings = settings or get_settings()
    resolved_model = model if model is not None else build_chat_model(settings)
    root = Path(workdir if workdir is not None else settings.agent_workdir)

    backend = LocalShellBackend(root_dir=root)

    return create_deep_agent(
        model=resolved_model,
        system_prompt=ROOT_SYSTEM_PROMPT,
        backend=backend,
        skills=[SKILLS_SOURCE],
        subagents=[
            {
                "name": "csv-analyst",
                "description": (
                    "sales.csv에서 제품별·지역별·분기별 매출 합계와 증감률을 계산한다. "
                    "숫자 집계가 필요하면 이 하위 에이전트에게 맡긴다."
                ),
                "system_prompt": CSV_SUBAGENT_PROMPT,
                "skills": [SKILLS_SOURCE],
            },
            {
                "name": "doc-researcher",
                "description": (
                    "data/docs의 분기 리포트에서 매출 변화의 원인이 되는 문장을 찾는다. "
                    "'왜' 바뀌었는지 근거가 필요하면 이 하위 에이전트에게 맡긴다."
                ),
                "system_prompt": DOC_SUBAGENT_PROMPT,
                "skills": [SKILLS_SOURCE],
            },
        ],
        # write_todos는 deepagents의 기본 미들웨어 스택에 없다(0.7.13 실측 —
        # graph.py가 import하는 미들웨어 목록에 TodoListMiddleware가 없다).
        # langchain 쪽 planning 미들웨어를 명시적으로 얹어야 write_todos 도구가
        # 생긴다. 이유는 이 장 2-2절에서 설명한다.
        middleware=[TodoListMiddleware()],
    )
