"""결정적(deterministic) 가짜 모델 — 스텁 서버로는 보여줄 수 없는 것을 보여준다.

``code/_tools/fake_openai_server.py``는 OpenAI 호환 REST 계약만 확인하는
스텁이다(사용자 메시지를 보지 않고, 요청받은 ``tools`` 목록의 첫 항목을
한 번만 부른다 — 8단계 ``docs/08-human-in-the-loop.md`` 5-3절에서 이미 확인한
사실). 이 장이 보여줘야 하는 것 — **계획을 세우고, 실행 중 계획을 수정하고,
하위 작업을 두 개의 스킬에 나눠 위임하고, 중간 결과를 파일로 저장한 뒤 그
파일을 다시 읽어 최종 답을 만드는** 여러 라운드짜리 흐름 — 은 그 스텁으로
재현할 수 없다.

그래서 8단계 ``scripts/demo_infinite_loop.py``가 가짜 ``chat_fn``으로 반복
차단을 시연했던 것과 같은 방법을 쓴다: LangChain의 ``BaseChatModel``을
상속한 **결정적** 가짜 모델을 만들어, 미리 정해둔 ``AIMessage`` 응답을
순서대로 돌려준다. 이 모델은 사용자 질문을 전혀 읽지 않는다 — 모델의
판단력을 검증하는 도구가 아니라, Deep Agents의 실행 메커니즘(계획 갱신,
``task`` 도구로의 위임, 파일 읽기/쓰기)이 실제로 동작하는지 확인하는
도구다. 이 한계는 장 하단 "검증 상태"에도 그대로 남긴다.
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class ScriptedChatModel(BaseChatModel):
    """미리 정해둔 ``AIMessage`` 목록을 호출 순서대로 돌려주는 가짜 모델.

    Deep Agents는 하위 에이전트(subagent)를 부를 때도 **같은 모델 인스턴스**를
    재사용한다(``task`` 도구가 새 모델을 만들지 않는다 — ``deepagents``
    0.7.13 실측). 그래서 이 클래스는 "메인 에이전트의 n번째 호출"과 "하위
    에이전트의 m번째 호출"을 구분하지 않고, **모델이 불릴 때마다** 하나의
    전역 순번을 소비한다. 시나리오를 짤 때 이 순서를 그대로 고려해야 한다
    (``scripts/demo_deep_agent_run.py`` 상단 주석에 실제 순서를 적어 뒀다).
    """

    responses: list[AIMessage]
    step: int = 0

    def bind_tools(self, tools: Any, *, tool_choice: str | None = None, **kwargs: Any) -> "ScriptedChatModel":
        """LangChain은 에이전트 루프 진입 시 ``model.bind_tools(tools)``를 부른다.

        실제 모델이라면 이 호출로 도구 스키마를 모델에 실어 보내지만, 이
        가짜 모델은 이미 어떤 도구를 어떤 인자로 부를지 스크립트에 정해
        놨으므로 도구 목록을 무시하고 자기 자신을 그대로 돌려준다.
        """

        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        if self.step >= len(self.responses):
            raise IndexError(
                f"스크립트에 정해둔 응답 {len(self.responses)}개를 모두 소비했다 — "
                "시나리오보다 모델이 더 많이 불렸다는 뜻이다."
            )
        message = self.responses[self.step]
        self.step += 1
        return ChatResult(generations=[ChatGeneration(message=message)])

    @property
    def _llm_type(self) -> str:
        return "docagent-scripted-chat-model"
