# LLM 애플리케이션·에이전트 튜토리얼

프레임워크 없이 LLM API를 직접 부르는 것에서 시작해, 하나의 문서 분석 에이전트에 기능을
단계별로 누적해 가는 한국어 튜토리얼이다.

대상 독자는 Python, FastAPI, 문서 처리, Milvus, OpenAI 호환 모델 서버를 다뤄본 개발자다.

## 읽는 순서

| 단계 | 문서 | 코드 | 주제 |
| --- | --- | --- | --- |
| — | [커리큘럼](docs/00-curriculum.md) | — | 전체 설계와 완료 기준 |
| 1 | [LLM API 직접 호출](docs/01-llm-api-direct.md) | `code/step01_raw_api/` | HTTP·SDK, 스트리밍, 구조화 출력 |
| 2 | [FastAPI와 대화 화면](docs/02-fastapi-chat.md) | `code/step02_fastapi_chat/` | SSE, 요청 취소, 미들웨어 |
| 3 | [Tool Calling과 에이전트 루프](docs/03-tool-calling.md) | `code/step03_tool_calling/` | 도구 정의, 반복 실행, 실행 한도 |
| 4 | [기본 RAG](docs/04-basic-rag.md) | `code/step04_basic_rag/` | 청킹, 임베딩, Milvus |
| 5 | [하이브리드 검색과 근거 링크](docs/05-hybrid-search-citations.md) | `code/step05_hybrid_rag/` | Dense·Sparse, RRF, 가짜 출처 차단 |
| 6 | [LangChain으로 재구성](docs/06-langchain.md) | `code/step06_langchain/` | 프레임워크가 대신하는 것과 아닌 것 |
| 7 | [LangGraph와 오케스트레이션](docs/07-langgraph.md) | `code/step07_langgraph/` | State·Node·Edge, 체크포인트, 재개 |
| 8 | [Human in the Loop](docs/08-human-in-the-loop.md) | `code/step08_hitl/` | 승인·거절·수정, 무한루프 차단 |
| 9 | [트레이싱과 품질 평가](docs/09-tracing-eval.md) | `code/step09_tracing_eval/` | Trace·Span, 검색/답변 품질 분리 평가 |
| 10 | [MCP로 외부 도구 연결](docs/10-mcp.md) | `code/step10_mcp/` | Host·Client·Server, Tools·Resources·Prompts |
| 11 | [멀티에이전트와 A2A](docs/11-multi-agent.md) | `code/step11_multi_agent/` | 위임, Handoff, 내부 협업과 A2A의 차이 |
| 12 | [Deep Agents와 Skills](docs/12-deep-agents-skills.md) | `code/step12_deep_agents/` | 계획, 파일, Skill과 Tool의 차이 |
| 13 | [진행 화면과 CSV 다운로드](docs/13-ui-and-export.md) | `code/step13_ui_export/` | 최종 통합, Todo·승인 UI, CSV |

각 장은 같은 8개 절로 구성된다: 학습 목표 → 핵심 개념 → 실습 준비 → 단계별 구현 →
실행과 결과 확인 → 실패 상황 실습 → 응용 과제 → 핵심 정리와 확인 질문.

## 실행 준비

각 단계 폴더의 `README.md`에 그 단계의 실행 절차가 있다. 공통적으로 필요한 것은 다음과 같다.

- Python 3.11
- OpenAI 호환 모델 서버 (vLLM, Ollama, TGI 등) 또는 아래의 학습용 스텁 서버
- Milvus (4단계부터). 학습용으로는 Milvus Lite를 파일 경로로 쓸 수 있다

```bash
cd code/step01_raw_api
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # 값을 자신의 환경에 맞게 고친다
```

### 모델 서버 없이 확인하기

`code/_tools/fake_openai_server.py`는 실제 모델 서버 없이 각 단계 코드가 **요청을 올바르게
만들고 응답을 올바르게 해석하는지**만 확인하기 위한 스텁 서버다.

```bash
python code/_tools/fake_openai_server.py --port 8111
```

모델이 아니므로 **답변 품질과 실제 서버의 기능 지원 범위는 확인할 수 없다.**
자세한 범위는 [`code/_tools/README.md`](code/_tools/README.md)에 있다.

실제 서버가 도구 호출·구조화 출력·스트리밍을 지원하는지는
`code/step01_raw_api/scripts/check_server_features.py`를 **자신의 서버에 대고** 실행해 확인한다.

## 이 문서들의 신뢰 범위

- 각 장 맨 아래 **"검증 상태"** 절에 그 장에서 실제로 실행해 확인한 것과 확인하지 못한 것이
  구분되어 있다. 읽기 전에 그 절을 먼저 보면 무엇을 믿고 무엇을 직접 확인해야 하는지 알 수 있다.
- 확인하지 못한 항목은 본문에 `[확인 필요: ...]`로 표시했다. 지어낸 값을 채우지 않았다.
- [`VERIFIED-FINDINGS.md`](VERIFIED-FINDINGS.md)에 **실행으로 확인한 사실만** 따로 모았다.
  패키지 버전, 라이브러리 API, 그리고 실제로 재현한 함정(환경 변수 이름 충돌, Milvus 검색
  결과의 기본 키 접근, 컬렉션 load 상태, 한국어 BM25 토크나이저 제약)이 여기 있다.
- 실제 모델 서버와 Milvus standalone을 대상으로 한 검증은 하지 못했다. 대부분의 실행 검증은
  위 스텁 서버와 Milvus Lite로 했다.

## 집필·기여 규칙

- [`WRITING-GUIDE.md`](WRITING-GUIDE.md) — 장별 절 구성, 문체, 정확성 규칙
- [`PROJECT-SPEC.md`](PROJECT-SPEC.md) — 모듈 경로, 환경 변수, SSE 이벤트 스키마, 근거 링크 규약,
  Milvus 스키마, 실행 한도, `config.py`/`llm.py` 공통 인터페이스, 의존성 버전 정책

새 장을 쓰거나 기존 장을 고칠 때 이 두 문서를 먼저 읽는다. 특히 단계마다 같은 일을 하는
함수에 다른 이름을 붙이지 않는다 — 그렇게 하면 "기능을 누적한다"는 이 튜토리얼의 전제가 깨진다.
