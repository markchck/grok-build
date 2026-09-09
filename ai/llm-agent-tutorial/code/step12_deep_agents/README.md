# step12_deep_agents — Deep Agents와 Skills

## 설치

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

확인된 기준선(2026-09-09 실제 설치): `deepagents` 0.7.13, `langchain` 1.4.0,
`langchain-core` 1.6.2, `langgraph` 1.2.11, `langchain-openai` 1.6.1,
`openai` 3.10.0.

## 환경 변수

```bash
cp .env.example .env
```

이 장에서 새로 쓰는 값은 `DOCAGENT_AGENT_WORKDIR`뿐이다 — Deep Agents의
파일시스템 백엔드가 파일을 읽고 쓰고 스크립트를 실행하는 루트 디렉터리다.
기본값 `.`은 이 폴더(`code/step12_deep_agents/`) 자체를 가리킨다. 그 아래
`skills/`, `data/`, `results/`만 에이전트가 건드릴 수 있다.

## 단위 테스트 (모델 서버 없이)

```bash
PYTHONPATH=. pytest tests -q
```

## 결정적 시나리오로 전체 흐름 보기 (모델 서버 없이)

스텁 서버로는 재현할 수 없는 "계획 → 위임 → 계획 수정 → 위임 → 파일
재읽기 → 답변" 여러 라운드짜리 흐름을, 미리 정해둔 가짜 모델 응답으로
**실제 Deep Agents 그래프**에 흘려보내 확인한다(스크립트·파일 I/O는
전부 진짜다 — 모델 응답만 스크립트로 고정했다).

```bash
PYTHONPATH=. python scripts/demo_deep_agent_run.py
```

## Skill 스크립트를 단독으로 실행해 보기

```bash
python3 skills/csv-analysis/analyze_sales.py data/sales.csv --out /tmp/csv_summary.md
python3 skills/doc-research/find_evidence.py data/docs 노트북 부산 --out /tmp/doc_findings.md
```

## 스텁 서버로 기본 연결 확인 (실제 모델 판단은 검증하지 않는다)

```bash
# 터미널 1
python ../_tools/fake_openai_server.py --port 8192

# 터미널 2
OPENAI_BASE_URL=http://127.0.0.1:8192/v1 OPENAI_API_KEY=sk-test CHAT_MODEL=stub-model \
DOCAGENT_AGENT_WORKDIR=$(pwd) uvicorn docagent.app:app --port 8193 --app-dir .
```

```bash
curl -sN http://127.0.0.1:8193/chat -H 'content-type: application/json' \
  -d '{"message":"csv 분석해줘"}'
```

스텁은 사용자 메시지를 읽지 않고 도구 목록의 첫 항목(`ls`)을 인자 없이
한 번 부른다 — 실제 요청 형식·SSE 스트리밍 배선이 맞는지만 확인할 수 있고,
계획 수립이나 스킬 선택 같은 모델 판단은 이 서버로 검증할 수 없다. 자세한
내용은 `docs/12-deep-agents-skills.md`를 본다.
