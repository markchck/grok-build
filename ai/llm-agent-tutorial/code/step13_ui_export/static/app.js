// SSE 클라이언트. EventSource는 POST 바디를 못 보내므로 fetch + ReadableStream으로
// event:/data: 블록을 직접 파싱한다(5·8·12단계와 같은 방식). 이 파일이 새로 하는 일은
// 새 이벤트를 만드는 게 아니라, 이미 있는 이벤트들을 한 화면에 모아 보여주는 것이다.

const form = document.getElementById("chat-form");
const messageInput = document.getElementById("message");
const statusEl = document.getElementById("status");
const logEl = document.getElementById("log");
const answerEl = document.getElementById("answer");
const todoListEl = document.getElementById("todo-list");
const sourcesListEl = document.getElementById("sources-list");
const approvalBox = document.getElementById("approval-box");
const approvalReasonEl = document.getElementById("approval-reason");
const approvalArgsEl = document.getElementById("approval-args");
const finishBanner = document.getElementById("finish-banner");
const exportBtn = document.getElementById("export-btn");
const exportHint = document.getElementById("export-hint");
const tablePreviewEl = document.getElementById("table-preview");

let currentRunId = null;
let currentApprovalId = null;
let currentApprovalAction = null;
let latestSources = []; // [{id, doc, page, url, snippet}]
let approvalLocked = false; // 버튼 더블 클릭으로 두 번 제출하는 것을 막는다(화면 쪽 방어)

function appendLog(html, cls) {
  const div = document.createElement("div");
  if (cls) div.className = cls;
  div.textContent = html;
  logEl.appendChild(div);
  logEl.scrollTop = logEl.scrollHeight;
}

function clearRunUI() {
  logEl.innerHTML = "";
  statusEl.textContent = "";
  answerEl.textContent = "(답변을 준비하는 중...)";
  sourcesListEl.innerHTML = "";
  latestSources = [];
  finishBanner.style.display = "none";
  finishBanner.className = "";
  exportBtn.style.display = "none";
  exportHint.textContent = "";
  tablePreviewEl.innerHTML = "";
  hideApprovalBox();
  renderTodos([]);
}

// --- Todo 목록 --------------------------------------------------------------
// state는 pending/running/done/failed 넷뿐이다(PROJECT-SPEC.md 4절). "failed"는
// 모델이 알려준 게 아니라 애플리케이션이 도구 실패를 관측해서 붙인 표시다.
function renderTodos(items) {
  todoListEl.innerHTML = "";
  if (!items.length) {
    todoListEl.innerHTML = '<li class="state-pending">(계획 없음)</li>';
    return;
  }
  const marker = { pending: "☐", running: "▶", done: "☑", failed: "✕" };
  for (const item of items) {
    const li = document.createElement("li");
    li.className = `state-${item.state}`;
    li.textContent = `${marker[item.state] || "?"} ${item.title}`;
    todoListEl.appendChild(li);
  }
}

// --- 근거 링크 ---------------------------------------------------------------
function renderSources(items) {
  latestSources = items;
  sourcesListEl.innerHTML = "";
  if (!items.length) {
    sourcesListEl.innerHTML = "<li>이번 답변에 실제로 쓰인 근거가 없다.</li>";
    return;
  }
  for (const s of items) {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = s.url;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = `[${s.id}] ${s.doc} (p${s.page})`;
    const snippet = document.createElement("div");
    snippet.className = "snippet";
    snippet.textContent = s.snippet;
    li.appendChild(a);
    li.appendChild(snippet);
    sourcesListEl.appendChild(li);
  }
}

// 답변 텍스트의 [S1] 표시를 실제 근거 링크로 바꿔 보여준다. 여기서 만드는 링크는
// 전부 sources 이벤트로 받은(=애플리케이션이 검증한) URL이다. 모델이 답변 본문에
// 직접 쓴 URL은 애초에 서버 쪽 citations.py가 지워서 이 문자열에 남아있지 않다.
function renderAnswer(text) {
  const byLabel = new Map(latestSources.map((s) => [s.id, s]));
  const escaped = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  const linked = escaped.replace(/\[S(\d+)\]/g, (whole, num) => {
    const label = `S${num}`;
    const s = byLabel.get(label);
    if (!s) return whole;
    return `<a class="citation" href="${s.url}" target="_blank" rel="noopener">${whole}</a>`;
  });
  answerEl.innerHTML = linked.replace(/\n/g, "<br>");
}

// --- CSV 표 미리보기 ----------------------------------------------------------
function renderTablePreview(rows) {
  if (!rows || !rows.length) {
    tablePreviewEl.innerHTML = "";
    return;
  }
  const cols = Object.keys(rows[0]);
  const labels = { date: "날짜", product: "제품", region: "지역", units: "수량", revenue: "매출" };
  let out = '<table class="csv-preview"><thead><tr>';
  for (const c of cols) out += `<th>${labels[c] || c}</th>`;
  out += "</tr></thead><tbody>";
  for (const row of rows.slice(0, 10)) {
    out += "<tr>" + cols.map((c) => `<td>${row[c]}</td>`).join("") + "</tr>";
  }
  out += "</tbody></table>";
  if (rows.length > 10) out += `<div class="hint">처음 10행만 미리 보여준다. 전체 ${rows.length}행은 CSV에 있다.</div>`;
  tablePreviewEl.innerHTML = out;
}

// --- finish_reason 표시 ------------------------------------------------------
// PROJECT-SPEC.md 7절이 정의한 8개 값. 사용자에게 "왜 여기서 멈췄는지"를
// 있는 그대로 설명하고, partial이 있으면 부분 결과도 보여준다.
const FINISH_MESSAGES = {
  stop: { cls: "ok", text: "정상적으로 끝났다." },
  max_steps: { cls: "warn", text: "최대 실행 단계 수를 넘어 중단했다. 지금까지의 결과는 부분 결과다." },
  timeout: { cls: "warn", text: "최대 실행 시간을 넘어 중단했다. 지금까지의 결과는 부분 결과다." },
  repeated_tool_call: { cls: "warn", text: "같은 도구를 같은 인자로 반복 호출해 중단했다(무한루프 방지)." },
  no_progress: { cls: "warn", text: "인자를 바꿔도 결과가 달라지지 않아 중단했다(진전 없음 감지)." },
  rejected_by_user: { cls: "bad", text: "사용자가 작업을 거절해 실행을 중단했다." },
  cancelled: { cls: "bad", text: "오류가 발생했거나 연결이 끊겨 실행이 취소됐다." },
  token_budget: { cls: "warn", text: "누적 토큰 예산을 넘어 중단했다. 지금까지의 결과는 부분 결과다." },
};

function renderFinish(finishReason, partial) {
  const info = FINISH_MESSAGES[finishReason] || { cls: "warn", text: `알 수 없는 종료 사유: ${finishReason}` };
  finishBanner.className = info.cls;
  let text = `[종료: ${finishReason}] ${info.text}`;
  if (partial) {
    const tokenNote = partial.tokens_estimated ? "(추정치)" : "";
    text += ` — 단계 ${partial.steps_used}회, 토큰 ${partial.tokens_used}${tokenNote} 사용.`;
  }
  finishBanner.textContent = text;
  finishBanner.style.display = "block";

  if (partial && partial.csv_available) {
    exportBtn.style.display = "inline-block";
    exportBtn.onclick = () => {
      window.location.href = `/export/${currentRunId}.csv`;
    };
    exportHint.textContent = "분석에 쓰인 표를 CSV로 받는다(Excel 한글 깨짐 방지: UTF-8 BOM 포함).";
  }
}

// --- 승인 UI ------------------------------------------------------------------
function hideApprovalBox() {
  approvalBox.style.display = "none";
  currentApprovalId = null;
  approvalLocked = false;
  for (const id of ["btn-approve", "btn-edit", "btn-reject-alt", "btn-reject-abort"]) {
    document.getElementById(id).disabled = false;
  }
}

async function submitDecision(action, extra) {
  if (!currentApprovalId || approvalLocked) return; // 이미 제출했으면 더 이상 보내지 않는다
  approvalLocked = true;
  for (const id of ["btn-approve", "btn-edit", "btn-reject-alt", "btn-reject-abort"]) {
    document.getElementById(id).disabled = true;
  }
  const body = { action, ...extra };
  const resp = await fetch(`/approvals/${currentApprovalId}/decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const result = await resp.json();
  if (result.already_decided) {
    appendLog(`(이미 결정된 승인이다 — 서버가 첫 결정을 그대로 유지했다: ${result.approval.status})`, "entry-result-bad");
  } else {
    appendLog(`(사용자 결정 제출: ${action})`, "entry-tool");
  }
  hideApprovalBox();
  await streamRequest(`/chat/resume/${currentRunId}`, { tool_names: [currentApprovalAction] });
}

document.getElementById("btn-approve").addEventListener("click", () => submitDecision("approve", {}));
document.getElementById("btn-reject-alt").addEventListener("click", () =>
  submitDecision("reject", { mode: "alternative", note: "사용자가 거절함" })
);
document.getElementById("btn-reject-abort").addEventListener("click", () =>
  submitDecision("reject", { mode: "abort", note: "사용자가 거절하여 중단" })
);
document.getElementById("btn-edit").addEventListener("click", () => {
  let args;
  try {
    args = JSON.parse(approvalArgsEl.value);
  } catch (e) {
    alert("인자가 올바른 JSON이 아니다: " + e.message);
    return;
  }
  submitDecision("edit", { args });
});

// --- SSE 파싱 ------------------------------------------------------------------
async function streamRequest(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const runId = response.headers.get("X-Run-Id");
  if (runId) currentRunId = runId;

  if (!response.ok || !response.body) {
    statusEl.textContent = `요청 실패: HTTP ${response.status}`;
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let answerText = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sepIndex;
    while ((sepIndex = buffer.indexOf("\n\n")) !== -1) {
      const rawEvent = buffer.slice(0, sepIndex);
      buffer = buffer.slice(sepIndex + 2);
      answerText = handleEvent(rawEvent, answerText);
    }
  }
}

function handleEvent(rawEvent, answerText) {
  let eventName = "message";
  let dataLine = "";
  for (const line of rawEvent.split("\n")) {
    if (line.startsWith("event:")) eventName = line.slice(6).trim();
    if (line.startsWith("data:")) dataLine += line.slice(5).trim();
  }
  if (!dataLine) return answerText;
  const data = JSON.parse(dataLine);

  switch (eventName) {
    case "status":
      statusEl.textContent = `[${data.stage}] ${data.message}`;
      break;
    case "tool_call":
      appendLog(`-> 도구 호출: ${data.name}(${JSON.stringify(data.args)})`, "entry-tool");
      break;
    case "tool_result":
      appendLog(`<- 결과(ok=${data.ok}): ${data.summary}`, data.ok ? "entry-result-ok" : "entry-result-bad");
      break;
    case "todo":
      renderTodos(data.items);
      break;
    case "approval_request":
      appendLog(`** 승인 필요: ${data.action} — ${data.reason}`, "entry-tool");
      currentApprovalId = data.id;
      currentApprovalAction = data.action;
      approvalReasonEl.textContent = `${data.action}: ${data.reason}`;
      approvalArgsEl.value = JSON.stringify(data.args, null, 2);
      approvalBox.style.display = "block";
      break;
    case "sources":
      renderSources(data.items);
      renderAnswer(answerText); // 소스가 답변보다 늦게 오므로 도착 시점에 다시 링크를 건다
      break;
    case "token":
      answerText += data.text;
      renderAnswer(answerText);
      break;
    case "error":
      appendLog(`오류(${data.code}): ${data.message}`, "entry-result-bad");
      break;
    case "done":
      statusEl.textContent += ` (종료: ${data.finish_reason})`;
      renderFinish(data.finish_reason, data.partial);
      if (data.partial && data.partial.text) {
        answerText = data.partial.text;
        renderAnswer(answerText);
      }
      fetchTablePreview();
      break;
    default:
      break;
  }
  return answerText;
}

async function fetchTablePreview() {
  if (!currentRunId) return;
  try {
    const resp = await fetch(`/runs/${currentRunId}`);
    if (!resp.ok) return;
    const run = await resp.json();
    if (run.table_row_count > 0) {
      // 미리보기 전용 조회: /runs는 표 자체를 돌려주지 않으므로 CSV를 내려받지 않고는
      // 서버가 계산한 표를 다시 볼 수 없다. 이 장의 데모 UI는 "행 개수"만 보여주고
      // 실제 표는 CSV 다운로드로 확인하게 한다 — 표를 굳이 다시 JSON으로 노출하는
      // 새 엔드포인트를 만들지 않기 위한 선택이다.
      tablePreviewEl.innerHTML = `<div class="hint">분석 표 ${run.table_row_count}행이 준비됐다. "CSV로 다운로드"로 받는다.</div>`;
    }
  } catch (e) {
    // 미리보기는 선택 기능이므로 실패해도 조용히 넘어간다.
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = messageInput.value.trim();
  if (!message) return;
  clearRunUI();
  await streamRequest("/chat", { message });
});
