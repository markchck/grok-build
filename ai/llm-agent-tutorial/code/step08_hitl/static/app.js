// 단순 SSE 클라이언트 + 승인 UI. EventSource는 POST 바디를 못 보내므로
// fetch + ReadableStream으로 event:/data: 블록을 직접 파싱한다(5단계와 같은 방식).

const form = document.getElementById("chat-form");
const messageInput = document.getElementById("message");
const statusEl = document.getElementById("status");
const logEl = document.getElementById("log");
const approvalBox = document.getElementById("approval-box");
const approvalReasonEl = document.getElementById("approval-reason");
const approvalArgsEl = document.getElementById("approval-args");

let currentRunId = null;
let currentApprovalId = null;
let currentApprovalAction = null;

function appendLog(line) {
  logEl.textContent += line + "\n";
}

function hideApprovalBox() {
  approvalBox.style.display = "none";
  currentApprovalId = null;
}

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
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sepIndex;
    while ((sepIndex = buffer.indexOf("\n\n")) !== -1) {
      const rawEvent = buffer.slice(0, sepIndex);
      buffer = buffer.slice(sepIndex + 2);
      handleEvent(rawEvent);
    }
  }
}

function handleEvent(rawEvent) {
  let eventName = "message";
  let dataLine = "";
  for (const line of rawEvent.split("\n")) {
    if (line.startsWith("event:")) eventName = line.slice(6).trim();
    if (line.startsWith("data:")) dataLine += line.slice(5).trim();
  }
  if (!dataLine) return;
  const data = JSON.parse(dataLine);

  switch (eventName) {
    case "status":
      statusEl.textContent = `[${data.stage}] ${data.message}`;
      break;
    case "tool_call":
      appendLog(`-> 도구 호출: ${data.name}(${JSON.stringify(data.args)})`);
      break;
    case "tool_result":
      appendLog(`<- 결과(ok=${data.ok}): ${data.summary}`);
      break;
    case "approval_request":
      appendLog(`** 승인 필요: ${data.action} — ${data.reason}`);
      currentApprovalId = data.id;
      currentApprovalAction = data.action;
      approvalReasonEl.textContent = `${data.action}: ${data.reason}`;
      approvalArgsEl.value = JSON.stringify(data.args, null, 2);
      approvalBox.style.display = "block";
      break;
    case "token":
      appendLog(`답변: ${data.text}`);
      break;
    case "error":
      appendLog(`오류(${data.code}): ${data.message}`);
      break;
    case "done":
      statusEl.textContent += ` (종료: ${data.finish_reason})`;
      if (data.partial) {
        appendLog(`[done] finish_reason=${data.finish_reason} steps=${data.partial.steps_used} tokens=${data.partial.tokens_used}${data.partial.tokens_estimated ? "(추정)" : ""}`);
      }
      break;
    default:
      break;
  }
}

async function submitDecision(action, extra) {
  if (!currentApprovalId) return;
  const body = { action, ...extra };
  await fetch(`/approvals/${currentApprovalId}/decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  hideApprovalBox();
  appendLog(`(사용자 결정 제출: ${action})`);
  await streamRequest(`/chat/resume/${currentRunId}`, { tool_names: [currentApprovalAction] });
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = messageInput.value.trim();
  if (!message) return;
  logEl.textContent = "";
  statusEl.textContent = "";
  hideApprovalBox();
  await streamRequest("/chat", { message });
});

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
