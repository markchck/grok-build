// 단순 SSE 클라이언트. EventSource는 POST 바디를 못 보내므로 fetch +
// ReadableStream으로 event:/data: 블록을 직접 파싱한다(5·8단계와 같은 방식).
// 이 장에서 새로 하는 일은 todo 이벤트를 받아 계획 패널을 그리는 것뿐이다.

const form = document.getElementById("chat-form");
const messageInput = document.getElementById("message");
const statusEl = document.getElementById("status");
const logEl = document.getElementById("log");
const todoListEl = document.getElementById("todo-list");

function appendLog(line) {
  logEl.textContent += line + "\n";
}

function renderTodos(items) {
  todoListEl.innerHTML = "";
  if (!items.length) {
    todoListEl.innerHTML = '<li class="state-pending">(계획 없음)</li>';
    return;
  }
  for (const item of items) {
    const li = document.createElement("li");
    li.className = `state-${item.state}`;
    const marker = { pending: "☐", running: "▶", done: "☑", failed: "✕" }[item.state] || "?";
    li.textContent = `${marker} ${item.title}`;
    todoListEl.appendChild(li);
  }
}

async function streamRequest(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
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
    case "todo":
      renderTodos(data.items);
      break;
    case "token":
      appendLog(`답변: ${data.text}`);
      break;
    case "error":
      appendLog(`오류(${data.code}): ${data.message}`);
      break;
    case "done":
      statusEl.textContent += ` (종료: ${data.finish_reason})`;
      break;
    default:
      break;
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = messageInput.value.trim();
  if (!message) return;
  logEl.textContent = "";
  statusEl.textContent = "";
  renderTodos([]);
  await streamRequest("/chat", { message });
});
