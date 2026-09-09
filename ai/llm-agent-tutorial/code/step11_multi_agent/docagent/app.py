"""FastAPI 엔드포인트.

이 장은 SSE 스트리밍의 세부(취소 처리 등)를 새로 다루지 않는다 — 그 주제는
2단계에서 이미 다뤘다. 여기서는 PROJECT-SPEC.md 4절의 이벤트 스키마 위에
이 장의 세 그래프(슈퍼바이저/핸드오프/병렬)를 얹어 "협업이 SSE로도 그대로
관찰된다"는 것만 보여준다. 실행은 동기 그래프를 스레드에서 돌리고, 얻은
``trace``를 순서대로 이벤트로 변환하는 가장 단순한 방식을 쓴다.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from docagent.config import get_settings
from docagent.events import done_event, error_event, status_event
from docagent.multiagent.agents import AgentDeps
from docagent.multiagent.graph_supervisor import build_supervisor_graph, initial_state

app = FastAPI(title="docagent step11 - multi-agent")


class CollabRequest(BaseModel):
    message: str
    external_verifier: bool = False  # True면 A2A 최소 흉내 서버로 검증을 위임한다


def _run_supervisor_sync(message: str, external_verifier: bool) -> dict[str, Any]:
    settings = get_settings()
    deps = AgentDeps(settings=settings)
    graph = build_supervisor_graph(deps, external_verifier=external_verifier)
    state = initial_state(message)
    return graph.invoke(state, config={"recursion_limit": 50})


@app.post("/collab/supervisor")
async def collab_supervisor(req: CollabRequest) -> StreamingResponse:
    async def event_stream():
        try:
            result = await asyncio.to_thread(_run_supervisor_sync, req.message, req.external_verifier)
        except Exception as exc:  # noqa: BLE001 - 그래프 실행 실패를 error 이벤트로 알린다
            yield error_event("collab_failed", str(exc))
            return

        for entry in result["trace"]:
            yield status_event(entry["stage"], entry["message"])
        finish_reason = result.get("finish_reason") or "stop"
        yield done_event(
            finish_reason,
            partial={
                "text": result.get("final_text", ""),
                "steps_used": result.get("hops_used", 0),
                "tokens_used": result.get("tokens_used", 0),
                "tokens_estimated": result.get("tokens_estimated", False),
            },
        )

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}
