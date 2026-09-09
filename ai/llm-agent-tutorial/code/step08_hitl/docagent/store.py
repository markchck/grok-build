"""승인 대기 상태와 실행 상태를 SQLite 파일에 저장한다.

이 장의 요구사항 중 "승인 대기 상태는 프로세스가 죽어도 살아남아야 한다"를
지키는 곳이다. 메모리 딕셔너리에 상태를 두면 서버 프로세스가 재시작될 때
사라진다. 여기서는 표준 라이브러리 ``sqlite3``만 쓴다 — 새 의존성을 추가하지
않고도 파일에 커밋된 상태를 그대로 보여줄 수 있어서다(추가 서버나 드라이버가
필요 없다).

테이블 세 개:
- ``runs``: 진행 중인 에이전트 실행 하나의 전체 상태(메시지, 단계 수, 반복
  감지용 카운터, 누적 토큰 사용량, 동시 재개를 막는 처리 중 플래그).
- ``approvals``: 승인 요청 하나하나의 기록과 최종 결정.
- ``tool_cache``: 멱등성 키 -> 실행 결과. 같은 키로 다시 실행을 요청하면
  실제 함수를 다시 부르지 않고 이 캐시를 그대로 돌려준다.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # 여러 요청이 같은 파일을 오가도 덜 다친다
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,               -- running | paused_approval | done
            processing INTEGER NOT NULL DEFAULT 0,
            messages_json TEXT NOT NULL,
            step INTEGER NOT NULL DEFAULT 0,
            start_time REAL NOT NULL,
            call_counts_json TEXT NOT NULL DEFAULT '{}',
            progress_hashes_json TEXT NOT NULL DEFAULT '[]',
            usage_prompt INTEGER NOT NULL DEFAULT 0,
            usage_completion INTEGER NOT NULL DEFAULT 0,
            usage_total INTEGER NOT NULL DEFAULT 0,
            usage_estimated INTEGER NOT NULL DEFAULT 0,
            finish_reason TEXT,
            final_text TEXT,
            pending_approval_id TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS approvals (
            approval_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            call_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            args_json TEXT NOT NULL,
            reason TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected | edited
            decision_args_json TEXT,
            decision_mode TEXT,
            decided_at REAL,
            created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tool_cache (
            idempotency_key TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            args_json TEXT NOT NULL,
            result_ok INTEGER NOT NULL,
            result_content TEXT NOT NULL,
            executed_at REAL NOT NULL
        );
        """
    )
    conn.commit()


# ---------------------------------------------------------------------------
# RunState
# ---------------------------------------------------------------------------


@dataclass
class RunState:
    run_id: str
    status: str
    messages: list[dict[str, Any]]
    step: int = 0
    start_time: float = field(default_factory=time.time)
    call_counts: dict[str, int] = field(default_factory=dict)
    progress_hashes: list[str] = field(default_factory=list)
    usage_prompt: int = 0
    usage_completion: int = 0
    usage_total: int = 0
    usage_estimated: bool = False
    finish_reason: str | None = None
    final_text: str | None = None
    pending_approval_id: str | None = None


def new_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:12]}"


def create_run(conn: sqlite3.Connection, messages: list[dict[str, Any]]) -> RunState:
    run = RunState(run_id=new_run_id(), status="running", messages=messages, start_time=time.time())
    now = time.time()
    conn.execute(
        """INSERT INTO runs (run_id, status, processing, messages_json, step, start_time,
               call_counts_json, progress_hashes_json, usage_prompt, usage_completion,
               usage_total, usage_estimated, finish_reason, final_text, pending_approval_id,
               created_at, updated_at)
           VALUES (?,?,0,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run.run_id,
            run.status,
            json.dumps(run.messages, ensure_ascii=False),
            run.step,
            run.start_time,
            json.dumps(run.call_counts),
            json.dumps(run.progress_hashes),
            run.usage_prompt,
            run.usage_completion,
            run.usage_total,
            int(run.usage_estimated),
            run.finish_reason,
            run.final_text,
            run.pending_approval_id,
            now,
            now,
        ),
    )
    conn.commit()
    return run


def load_run(conn: sqlite3.Connection, run_id: str) -> RunState | None:
    row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        return None
    return RunState(
        run_id=row["run_id"],
        status=row["status"],
        messages=json.loads(row["messages_json"]),
        step=row["step"],
        start_time=row["start_time"],
        call_counts=json.loads(row["call_counts_json"]),
        progress_hashes=json.loads(row["progress_hashes_json"]),
        usage_prompt=row["usage_prompt"],
        usage_completion=row["usage_completion"],
        usage_total=row["usage_total"],
        usage_estimated=bool(row["usage_estimated"]),
        finish_reason=row["finish_reason"],
        final_text=row["final_text"],
        pending_approval_id=row["pending_approval_id"],
    )


def save_run(conn: sqlite3.Connection, run: RunState) -> None:
    conn.execute(
        """UPDATE runs SET status=?, messages_json=?, step=?, call_counts_json=?,
               progress_hashes_json=?, usage_prompt=?, usage_completion=?, usage_total=?,
               usage_estimated=?, finish_reason=?, final_text=?, pending_approval_id=?,
               updated_at=?
           WHERE run_id=?""",
        (
            run.status,
            json.dumps(run.messages, ensure_ascii=False),
            run.step,
            json.dumps(run.call_counts),
            json.dumps(run.progress_hashes),
            run.usage_prompt,
            run.usage_completion,
            run.usage_total,
            int(run.usage_estimated),
            run.finish_reason,
            run.final_text,
            run.pending_approval_id,
            time.time(),
            run.run_id,
        ),
    )
    conn.commit()


def try_acquire(conn: sqlite3.Connection, run_id: str) -> bool:
    """이 run_id를 지금 이 요청이 처리해도 되는지 원자적으로 확인한다.

    두 개의 재개(resume) 요청이 동시에 같은 run을 진행시키면 메시지 목록이
    엉킨다. ``processing=0`` 상태에서만 ``1``로 바꾸는 조건부 UPDATE로
    "먼저 온 요청만 통과"를 보장한다. 락을 SQLite 파일 자체에 두므로 이
    보장은 프로세스 재시작과도 무관하게 유지된다.
    """

    cur = conn.execute("UPDATE runs SET processing=1 WHERE run_id=? AND processing=0", (run_id,))
    conn.commit()
    return cur.rowcount == 1


def release(conn: sqlite3.Connection, run_id: str) -> None:
    conn.execute("UPDATE runs SET processing=0 WHERE run_id=?", (run_id,))
    conn.commit()


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------


def new_approval_id() -> str:
    return f"appr_{uuid.uuid4().hex[:12]}"


def create_approval(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    call_id: str,
    tool_name: str,
    args: dict[str, Any],
    reason: str,
    idempotency_key: str,
) -> str:
    approval_id = new_approval_id()
    conn.execute(
        """INSERT INTO approvals (approval_id, run_id, call_id, tool_name, args_json, reason,
               idempotency_key, status, created_at)
           VALUES (?,?,?,?,?,?,?, 'pending', ?)""",
        (
            approval_id,
            run_id,
            call_id,
            tool_name,
            json.dumps(args, ensure_ascii=False),
            reason,
            idempotency_key,
            time.time(),
        ),
    )
    conn.commit()
    return approval_id


def get_approval(conn: sqlite3.Connection, approval_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
    return dict(row) if row else None


def decide_approval(
    conn: sqlite3.Connection,
    approval_id: str,
    *,
    status: str,
    decision_args: dict[str, Any] | None,
    mode: str | None,
) -> tuple[dict[str, Any], bool]:
    """승인/거절/수정 결정을 기록한다.

    ``status='pending'``일 때만 갱신되도록 조건부 UPDATE를 쓴다 — 이것이
    **중복 승인 방지**의 핵심이다. 같은 승인 요청에 대해 사용자가 승인
    버튼을 두 번 누르거나(더블 클릭), 클라이언트가 네트워크 오류로 같은
    요청을 재시도해도, 두 번째 호출은 아무것도 바꾸지 못하고 이미 저장된
    첫 번째 결정을 그대로 돌려받는다.

    Returns:
        (approval_dict, already_decided) — already_decided가 True면 이번
        호출로는 아무것도 바뀌지 않았고, 과거에 내려진 결정을 돌려준 것이다.
    """

    cur = conn.execute(
        """UPDATE approvals SET status=?, decision_args_json=?, decision_mode=?, decided_at=?
           WHERE approval_id=? AND status='pending'""",
        (
            status,
            json.dumps(decision_args, ensure_ascii=False) if decision_args is not None else None,
            mode,
            time.time(),
            approval_id,
        ),
    )
    conn.commit()
    already_decided = cur.rowcount == 0
    approval = get_approval(conn, approval_id)
    if approval is None:
        raise KeyError(f"승인 요청을 찾을 수 없다: {approval_id}")
    return approval, already_decided


# ---------------------------------------------------------------------------
# 멱등성 캐시 (도구 중복 실행 방지)
# ---------------------------------------------------------------------------


def cache_get(conn: sqlite3.Connection, idempotency_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM tool_cache WHERE idempotency_key=?", (idempotency_key,)
    ).fetchone()
    return dict(row) if row else None


def cache_set(
    conn: sqlite3.Connection,
    *,
    idempotency_key: str,
    run_id: str,
    tool_name: str,
    args: dict[str, Any],
    ok: bool,
    content: str,
) -> None:
    """멱등성 키로 실행 결과를 저장한다. 이미 있으면 조용히 무시한다(``INSERT OR IGNORE``).

    ``INSERT OR IGNORE``를 쓰는 이유: 두 요청이 거의 동시에 같은 키로 실제
    실행까지 마쳤더라도(락으로 대부분 막히지만, 방어적으로) 캐시에는 먼저
    쓴 결과만 남기고 나중 것은 버린다 — "이 키의 결과는 하나여야 한다"는
    불변식을 지킨다.
    """

    conn.execute(
        """INSERT OR IGNORE INTO tool_cache
               (idempotency_key, run_id, tool_name, args_json, result_ok, result_content, executed_at)
           VALUES (?,?,?,?,?,?,?)""",
        (idempotency_key, run_id, tool_name, json.dumps(args, ensure_ascii=False), int(ok), content, time.time()),
    )
    conn.commit()
