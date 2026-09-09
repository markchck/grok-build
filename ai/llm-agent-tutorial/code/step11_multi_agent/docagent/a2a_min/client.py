"""``a2a_min.server``를 부르는 클라이언트.

내부 ``verifier`` 노드(``multiagent/agents.py``)와 이 클라이언트가 하는 일은
겉으로는 비슷해 보인다(둘 다 "검증 결과"를 돌려준다). 그러나 이 클라이언트가
넘는 경계는 완전히 다르다.

| | 내부 verifier 노드 | 이 클라이언트가 부르는 외부 에이전트 |
| --- | --- | --- |
| 실행 위치 | 같은 파이썬 프로세스, 같은 그래프 | 별도 프로세스(``a2a_min/server.py``), 보통 별도 배포 |
| 상태 공유 | ``CollabState``를 직접 읽고 쓴다 | 공유 상태가 없다. HTTP 요청·응답에 담긴 값만 오간다 |
| 호출 방식 | 파이썬 함수 호출(``make_verifier_node(deps)(state)``) | HTTP로 작업을 제출하고 결과를 폴링한다 |
| 실패 모드 | 파이썬 예외 | 연결 실패, 타임아웃, 알 수 없는 task_id, 프로토콜 불일치 |
| 발견 방법 | import로 코드에 고정 | ``/agent-card``로 능력을 조회(할 수 있다) |

이 장 2-7절에서 이 표를 MCP까지 포함한 3단 비교로 확장한다.
"""

from __future__ import annotations

import time
from typing import Any

import httpx


class A2AClientError(RuntimeError):
    """외부 에이전트 호출 실패(연결 오류, 타임아웃, 예상과 다른 응답)."""


def fetch_agent_card(base_url: str, *, timeout: float = 5.0) -> dict[str, Any]:
    try:
        resp = httpx.get(f"{base_url.rstrip('/')}/agent-card", timeout=timeout)
        resp.raise_for_status()
    except httpx.TimeoutException as exc:
        raise A2AClientError(f"에이전트 카드 조회 시간 초과: {base_url}") from exc
    except httpx.HTTPError as exc:
        raise A2AClientError(f"에이전트 카드를 가져올 수 없다({base_url}): {exc}") from exc
    return resp.json()


def submit_and_wait(
    base_url: str,
    *,
    skill_id: str,
    task_input: dict[str, Any],
    timeout: float = 5.0,
    poll_interval: float = 0.2,
    max_polls: int = 25,
) -> dict[str, Any]:
    """작업을 제출하고 완료될 때까지 폴링한다. 최종 ``result``를 돌려준다.

    이 최소 흉내의 서버는 작업을 즉시 끝내므로 폴링이 보통 한 번에 끝나지만,
    "제출과 결과 조회가 분리돼 있다"는 A2A의 비동기적 성격 자체는 그대로
    유지했다 — 실제 A2A는 SSE 스트리밍이나 푸시 알림으로 더 정교하게
    처리한다(이 흉내는 하지 않는다).
    """

    url = base_url.rstrip("/")
    try:
        resp = httpx.post(f"{url}/tasks", json={"skill_id": skill_id, "input": task_input}, timeout=timeout)
        resp.raise_for_status()
    except httpx.TimeoutException as exc:
        raise A2AClientError(f"작업 제출 시간 초과: {url}") from exc
    except httpx.HTTPError as exc:
        raise A2AClientError(f"작업 제출 실패({url}): {exc}") from exc

    task_id = resp.json()["task_id"]

    for _ in range(max_polls):
        try:
            poll_resp = httpx.get(f"{url}/tasks/{task_id}", timeout=timeout)
            poll_resp.raise_for_status()
        except httpx.TimeoutException as exc:
            raise A2AClientError(f"작업 상태 조회 시간 초과: {url}/tasks/{task_id}") from exc
        except httpx.HTTPError as exc:
            raise A2AClientError(f"작업 상태 조회 실패: {exc}") from exc

        data = poll_resp.json()
        if data["status"] == "completed":
            return data["result"]
        time.sleep(poll_interval)

    raise A2AClientError(f"작업이 제한 시간 안에 끝나지 않았다: task_id={task_id}")
