"""LangChain 모델 인터페이스 — "프레임워크 구현" 경로.

``docagent.llm``(5단계 그대로)이 ``httpx``로 ``/chat/completions``,
``/embeddings``를 직접 호출했다면, 이 모듈은 같은 ``OPENAI_BASE_URL`` /
``OPENAI_API_KEY`` / ``CHAT_MODEL`` / ``EMBEDDING_MODEL``을 그대로 재사용해서
LangChain의 표준 모델 인터페이스(``BaseChatModel``, ``Embeddings``)를 만든다.

핵심은 "OpenAI 호환 서버에 붙는다"는 전제를 LangChain도 그대로 갖는다는
점이다 — ``ChatOpenAI(base_url=..., api_key=..., model=...)``는 OpenAI사의
서버가 아니라 이 프로젝트가 붙이는 로컬/사내 모델 서버를 가리킨다. 그 서버가
도구 호출이나 구조화 출력을 지원하지 않으면 LangChain을 쓰더라도 그 기능은
쓸 수 없다 — LangChain은 호출 방식을 표준화할 뿐 모델 서버의 기능을 새로
만들어주지 않는다(PROJECT-SPEC.md 정확성 규칙, 1단계 ``check_server_features.py``
참고).
"""

from __future__ import annotations

from functools import lru_cache

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from docagent.config import Settings, get_settings


def build_chat_model(settings: Settings | None = None, *, temperature: float = 0.2) -> ChatOpenAI:
    """대화형 채팅 모델(LangChain ``BaseChatModel``)을 만든다.

    ``docagent.llm.chat_completion``과 같은 세 값(``base_url``, ``api_key``,
    ``model``)만 있으면 된다 — 나머지(재시도, 스트리밍, 도구 호출 인코딩)는
    ``langchain_openai``가 openai 파이썬 SDK 위에서 대신 처리한다.
    """

    s = settings or get_settings()
    return ChatOpenAI(
        base_url=s.openai_base_url,
        api_key=s.openai_api_key,
        model=s.chat_model,
        temperature=temperature,
        timeout=s.request_timeout_seconds,
    )


def build_embeddings(settings: Settings | None = None) -> OpenAIEmbeddings:
    """임베딩 모델(LangChain ``Embeddings``)을 만든다.

    ``check_embedding_ctx_length=False``가 반드시 필요하다 — 기본값(``True``)이면
    ``OpenAIEmbeddings``가 토큰 길이를 세려고 ``tiktoken.get_encoding("cl100k_base")``를
    호출하는데, 이 인코딩 파일이 로컬에 없으면 OpenAI가 호스팅하는 blob 저장소로
    **네트워크 요청을 보낸다.** 사내망이나 오프라인 환경, 혹은 이 저장소의
    스텁 서버·Milvus Lite 조합처럼 외부 접속이 막힌 환경에서는 이 요청이
    실패하며 임베딩 자체가 되지 않는다(직접 재현해 확인한 사실 — 6절 "실패
    상황 실습" 참고). ``check_embedding_ctx_length=False``를 주면 토큰 수를
    세지 않고 원문 문자열을 그대로 ``/embeddings``에 보낸다 — 4·5단계의
    ``embed_texts``가 애초에 하던 방식과 같다.
    """

    s = settings or get_settings()
    return OpenAIEmbeddings(
        base_url=s.openai_base_url,
        api_key=s.openai_api_key,
        model=s.embedding_model,
        check_embedding_ctx_length=False,
    )


@lru_cache(maxsize=1)
def get_chat_model() -> ChatOpenAI:
    """``get_settings()``와 동일한 lru_cache 캐싱 규칙을 따르는 기본 인스턴스."""

    return build_chat_model()


@lru_cache(maxsize=1)
def get_embeddings() -> OpenAIEmbeddings:
    return build_embeddings()
