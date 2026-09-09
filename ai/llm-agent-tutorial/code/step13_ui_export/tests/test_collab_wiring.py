"""멀티에이전트 협업(11단계)과 A2A 최소 흉내 서버 연결이 최종 앱 실행 경로에
실제로 있는지 확인한다 — 최종 통합 과제 5·6번.

``docagent/multiagent/graph_supervisor.py``(11단계에서 그대로 옮긴 코드)를
``docagent/app.py``의 ``/collab/supervisor`` 엔드포인트가 그대로 부른다.
내부 검증(verifier, 같은 프로세스의 파이썬 함수 호출)과 A2A 최소 흉내
검증(``docagent/a2a_min/server.py``, 별도 프로세스에 HTTP로 위임)을 같은
그래프 구조 위에서 ``external_verifier`` 플래그 하나로 갈라 둘 다 실제로
실행해 본다.

모델 서버가 없는 상태(``conftest.py``가 존재하지 않는 주소로 고정)이므로
투자·분석·검증 노드는 모두 규칙 기반(heuristic) 경로를 탄다 — 이것은
11단계가 이미 검증한 대로 "모델 실패가 협업 실패로 이어지지 않는다"는 것을
이 장에서도 다시 보여준다.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from docagent.config import load_settings
from docagent.multiagent.agents import AgentDeps
from docagent.multiagent.graph_supervisor import build_supervisor_graph, initial_state

BASE_DIR = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def a2a_server():
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "docagent.a2a_min.server", "--port", str(port)],
        cwd=str(BASE_DIR),
    )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(50):
            try:
                httpx.get(f"{url}/agent-card", timeout=1.0).raise_for_status()
                break
            except (httpx.HTTPError, httpx.ConnectError):
                time.sleep(0.1)
        else:
            proc.terminate()
            pytest.fail("A2A 최소 흉내 서버가 제한 시간 안에 뜨지 않았다")
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def _deps(**overrides):
    settings = load_settings()
    if overrides:
        settings = settings.__class__(**{**settings.__dict__, **overrides})
    return AgentDeps(settings=settings)


def test_internal_verifier_path_completes_without_external_process():
    """external_verifier=False(기본값) — 검증이 같은 프로세스의 파이썬 함수 호출이다."""

    deps = _deps()
    graph = build_supervisor_graph(deps, external_verifier=False)
    state = initial_state("노트북 매출이 왜 늘었는지 조사해줘")
    result = graph.invoke(state, config={"recursion_limit": 50})

    assert result["finish_reason"] == "stop"
    assert result["verification"]["source"] == "heuristic"  # 모델이 없으므로 규칙 기반
    verifier_notes = [n for n in result["shared_notes"] if n.startswith("[verifier]")]
    assert verifier_notes  # "[verifier(A2A)]"가 아니라 "[verifier]" — 내부 호출 경로 표시


def test_external_a2a_verifier_path_uses_real_separate_process(a2a_server):
    """external_verifier=True — 검증을 별도 프로세스(A2A 최소 흉내 서버)에 HTTP로 위임한다.
    실제로 그 프로세스를 띄우고, 그 프로세스가 실제로 응답한 결과를 쓰는지 확인한다."""

    deps = _deps(a2a_verifier_url=a2a_server)
    graph = build_supervisor_graph(deps, external_verifier=True)
    state = initial_state("노트북 매출이 왜 늘었는지 조사해줘")
    result = graph.invoke(state, config={"recursion_limit": 50})

    assert result["finish_reason"] == "stop"
    assert result["verification"]["source"] == "a2a_agent"
    verifier_notes = [n for n in result["shared_notes"] if n.startswith("[verifier(A2A)]")]
    assert verifier_notes  # 로그에서 내부 호출과 구분되는 표시


def test_external_a2a_verifier_unreachable_degrades_gracefully():
    """A2A 서버가 응답하지 않아도 협업 전체가 죽지 않고 검증 실패로 마무리된다
    (3·8단계와 같은 원칙: 외부 호출 실패가 곧 애플리케이션 실패는 아니다)."""

    deps = _deps(a2a_verifier_url="http://127.0.0.1:1")  # 아무도 듣지 않는 주소
    graph = build_supervisor_graph(deps, external_verifier=True)
    state = initial_state("모니터 매출 확인해줘")
    result = graph.invoke(state, config={"recursion_limit": 50})

    assert result["verification"]["source"] == "a2a_agent_unreachable"
    assert result["verification"]["ok"] is False


# ---------------------------------------------------------------------------
# app.py의 /collab/supervisor 엔드포인트가 실제로 이 그래프를 부르는지
# ---------------------------------------------------------------------------


def test_collab_endpoint_streams_sse_with_internal_verifier():
    from fastapi.testclient import TestClient

    from docagent.app import app

    with TestClient(app) as client:
        with client.stream(
            "POST", "/collab/supervisor", json={"message": "노트북 매출이 왜 늘었는지 조사해줘"}
        ) as resp:
            assert resp.status_code == 200
            body = "".join(resp.iter_text())

    assert "event: status" in body
    assert "event: done" in body
    assert '"finish_reason": "stop"' in body


def test_collab_endpoint_streams_sse_with_a2a_verifier(a2a_server, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("A2A_VERIFIER_URL", a2a_server)
    from docagent.config import reset_settings_cache

    reset_settings_cache()
    from docagent.app import app

    with TestClient(app) as client:
        with client.stream(
            "POST",
            "/collab/supervisor",
            json={"message": "노트북 매출이 왜 늘었는지 조사해줘", "external_verifier": True},
        ) as resp:
            assert resp.status_code == 200
            body = "".join(resp.iter_text())

    assert "event: done" in body
    assert '"finish_reason": "stop"' in body
    reset_settings_cache()
