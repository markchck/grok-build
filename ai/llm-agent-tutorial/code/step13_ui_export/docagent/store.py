"""승인 대기 상태와 실행 상태를 SQLite 파일에 저장한다 (8단계 store.py 확장).

8단계와 같은 테이블 세 개(``runs``/``approvals``/``tool_cache``)를 그대로
쓰고, ``runs``에 이 장에서 필요한 컬럼 세 개를 추가했다:

- ``table_json``: 마지막으로 성공한 ``lookup_sales_rows`` 결과 행. CSV
  다운로드(``docagent/csv_export.py``)의 원재료다.
- ``sources_json``: 이번 run에서 하이브리드 검색이 실제로 반환한 청크 목록
  (``citations.RetrievedSource``를 JSON으로 직렬화한 것). 답변 검증과
  근거 링크가 이 목록만 신뢰한다(5단계 규약).
- ``plan_json`` / ``todo_state_json``: ``write_plan`` 도구가 선언한 계획과
  ``docagent/todo.py``의 ``TodoIdAssigner`` 상태(재개해도 같은 id를 유지하기
  위해 저장한다).

기존 컬럼의 이름은 하나도 바꾸지 않았다(PROJECT-SPEC.md 9절 원칙).
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
    conn.execute("PRAGMA journal_mode=WAL")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
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
            table_json TEXT NOT NULL DEFAULT 'null',
            sources_json TEXT NOT NULL DEFAULT '[]',
            plan_json TEXT NOT NULL DEFAULT '[]',
            todo_state_json TEXT NOT NULL DEFAULT '{}',
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
            status TEXT NOT NULL DEFAULT 'pending',
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
    table: list[dict[str, Any]] | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)
    plan: list[dict[str, Any]] = field(default_factory=list)
    todo_state: dict[str, Any] = field(default_factory=dict)


def new_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:12]}"


def create_run(conn: sqlite3.Connection, messages: list[dict[str, Any]]) -> RunState:
    run = RunState(run_id=new_run_id(), status="running", messages=messages, start_time=time.time())
    now = time.time()
    conn.execute(
        """INSERT INTO runs (run_id, status, processing, messages_json, step, start_time,
               call_counts_json, progress_hashes_json, usage_prompt, usage_completion,
               usage_total, usage_estimated, finish_reason, final_text, pending_approval_id,
               table_json, sources_json, plan_json, todo_state_json, created_at, updated_at)
           VALUES (?,?,0,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
            json.dumps(run.table, ensure_ascii=False),
            json.dumps(run.sources, ensure_ascii=False),
            json.dumps(run.plan, ensure_ascii=False),
            json.dumps(run.todo_state, ensure_ascii=False),
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
        table=json.loads(row["table_json"]),
        sources=json.loads(row["sources_json"]),
        plan=json.loads(row["plan_json"]),
        todo_state=json.loads(row["todo_state_json"]),
    )


def save_run(conn: sqlite3.Connection, run: RunState) -> None:
    conn.execute(
        """UPDATE runs SET status=?, messages_json=?, step=?, call_counts_json=?,
               progress_hashes_json=?, usage_prompt=?, usage_completion=?, usage_total=?,
               usage_estimated=?, finish_reason=?, final_text=?, pending_approval_id=?,
               table_json=?, sources_json=?, plan_json=?, todo_state_json=?, updated_at=?
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
            json.dumps(run.table, ensure_ascii=False),
            json.dumps(run.sources, ensure_ascii=False),
            json.dumps(run.plan, ensure_ascii=False),
            json.dumps(run.todo_state, ensure_ascii=False),
            time.time(),
            run.run_id,
        ),
    )
    conn.commit()


def try_acquire(conn: sqlite3.Connection, run_id: str) -> bool:
    cur = conn.execute("UPDATE runs SET processing=1 WHERE run_id=? AND processing=0", (run_id,))
    conn.commit()
    return cur.rowcount == 1


def release(conn: sqlite3.Connection, run_id: str) -> None:
    conn.execute("UPDATE runs SET processing=0 WHERE run_id=?", (run_id,))
    conn.commit()


# ---------------------------------------------------------------------------
# Approval (8단계와 동일)
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
    """멱등적 결정 기록. ``status='pending'``일 때만 갱신되므로 같은 승인에 두 번
    응답해도(더블 클릭 포함) 첫 결정만 유지된다. ``already_decided``로 그 사실을 알린다."""

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
# 멱등성 캐시 (8단계와 동일)
# ---------------------------------------------------------------------------


def cache_get(conn: sqlite3.Connection, idempotency_key: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM tool_cache WHERE idempotency_key=?", (idempotency_key,)).fetchone()
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
    conn.execute(
        """INSERT OR IGNORE INTO tool_cache
               (idempotency_key, run_id, tool_name, args_json, result_ok, result_content, executed_at)
           VALUES (?,?,?,?,?,?,?)""",
        (idempotency_key, run_id, tool_name, json.dumps(args, ensure_ascii=False), int(ok), content, time.time()),
    )
    conn.commit()
