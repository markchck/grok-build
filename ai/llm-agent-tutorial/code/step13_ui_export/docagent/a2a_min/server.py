"""A2A가 다루는 문제를 최소한으로 흉내 낸 "검증 에이전트" 서버.

**이 파일은 A2A 프로토콜을 구현한 것이 아니다.** 실제 A2A(Agent2Agent)
프로토콜은 Linux Foundation이 관리하는 별도 명세이고(2-7절에서 근거와 함께
설명한다), 이 문서를 쓰는 시점(2026-09-09)의 최신 사양은 버전 1.0(0.3과의
호환 모드 포함)이며 JSON-RPC 2.0 기반 메시지 교환, Agent Card를 통한
탐색, Task/Message/Artifact 구조, SSE 스트리밍·푸시 알림을 포함한 훨씬 큰
범위를 다룬다. 공식 파이썬 구현(``a2a-sdk``, PyPI 확인 버전 1.1.2)도 있다.

이 서버는 그중 **"에이전트가 다른 독립 프로세스의 에이전트에게 작업을
맡기고, 그 에이전트는 카드로 자기소개를 하며, 내부 상태를 공유하지 않고
결과만 주고받는다"**는 A2A의 핵심 아이디어만 아주 작게 흉내 낸다:

- ``GET /agent-card`` — 이 에이전트가 무엇을 하는지 알리는 최소한의 카드
  (실제 A2A의 Agent Card는 훨씬 많은 필드와 표준 경로
  ``/.well-known/agent-card.json``을 쓴다 — 이 서버는 흉내이므로 경로와
  필드를 단순화했다).
- ``POST /tasks`` — 작업(질문 + 근거 데이터)을 제출하면 작업 ID를 즉시
  돌려준다(비동기 처리를 흉내 낸다).
- ``GET /tasks/{task_id}`` — 작업 상태와 결과를 폴링한다.

JSON-RPC가 아니라 평범한 REST/JSON을 쓰고, 인증·스트리밍·Artifact 타입
협상은 전혀 없다. 학습용 최소 흉내이지 A2A 서버가 아니다.
"""

from __future__ import annotations

import argparse
import itertools
import time
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Minimal Verifier Agent (A2A 개념 최소 흉내)")

_TASKS: dict[str, dict[str, Any]] = {}
_counter = itertools.count(1)


AGENT_CARD: dict[str, Any] = {
    "name": "verifier-agent-min",
    "description": "매출 분석 결과를 원본 데이터로 재검증하는 독립 에이전트(최소 흉내 구현).",
    "version": "0.1.0-min-imitation",
    "skills": [
        {
            "id": "verify_quarterly_analysis",
            "description": "제품별 분기 매출 분석(analyst 보고)과 원본 행을 받아 일치 여부를 판정한다.",
        }
    ],
    # 실제 A2A Agent Card는 capabilities(streaming, push notification 등),
    # 인증 스킴, 여러 skill을 표준 스키마로 담는다. 이 값들은 여기 없다 —
    # 그래서 이 서버가 A2A 서버가 아니라고 문서 본문에 명시한다.
    "note": "이것은 A2A 프로토콜 준수 Agent Card가 아니다. 개념을 보이기 위한 최소 흉내다.",
}


class TaskSubmission(BaseModel):
    skill_id: str
    input: dict[str, Any]


@app.get("/agent-card")
def get_agent_card() -> dict[str, Any]:
    return AGENT_CARD


def _verify(payload: dict[str, Any]) -> dict[str, Any]:
    analysis = payload.get("analysis", {})
    rows = payload.get("rows", [])

    totals: dict[str, dict[str, int]] = {}
    for r in rows:
        month = int(str(r["date"]).split("-")[1])
        q = f"Q{(month - 1) // 3 + 1}"
        bucket = totals.setdefault(q, {"units": 0, "revenue": 0})
        bucket["units"] += int(r["units"])
        bucket["revenue"] += int(r["revenue"])

    q1 = totals.get("Q1", {"units": 0, "revenue": 0})
    q2 = totals.get("Q2", {"units": 0, "revenue": 0})
    ok = q1 == analysis.get("q1") and q2 == analysis.get("q2")
    return {
        "ok": ok,
        "reason": "재계산한 분기별 합계가 analyst 보고와 일치한다" if ok else "재계산한 합계가 analyst 보고와 다르다",
        "recomputed_q1": q1,
        "recomputed_q2": q2,
    }


@app.post("/tasks")
def submit_task(submission: TaskSubmission) -> dict[str, Any]:
    if submission.skill_id != "verify_quarterly_analysis":
        raise HTTPException(status_code=400, detail=f"지원하지 않는 skill_id: {submission.skill_id}")

    task_id = f"task_{next(_counter)}_{uuid.uuid4().hex[:8]}"
    # 실제 서비스라면 백그라운드에서 처리하겠지만, 이 최소 흉내는 즉시 계산한다.
    result = _verify(submission.input)
    _TASKS[task_id] = {"status": "completed", "created_at": time.time(), "result": result}
    return {"task_id": task_id, "status": "submitted"}


@app.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    task = _TASKS.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="알 수 없는 task_id")
    return {"task_id": task_id, **task}


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="최소 검증 에이전트 서버(A2A 개념 흉내)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8292)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
