// 2단계 웹 UI. 빌드 도구 없이 순수 HTML+JS로 동작한다.
//
// 브라우저 표준 EventSource는 GET 요청만 보낼 수 있고 헤더나 바디를 커스터마이즈할 수 없다.
// 이 앱은 대화 메시지(JSON 바디)를 POST로 보내야 하므로 EventSource를 쓸 수 없고,
// 대신 fetch()로 POST 요청을 보낸 뒤 response.body를 ReadableStream으로 직접 읽어
// "event: ...\ndata: ...\n\n" 프레임을 수동으로 파싱한다.

const logEl = document.getElementById("log");
const formEl = document.getElementById("chat-form");
const inputEl = document.getElementById("message");
const sendBtn = document.getElementById("send");
const cancelBtn = document.getElementById("cancel");

let sessionId = null;
let currentAbortController = null;
let currentAssistantEl = null;

function appendLine(role, text) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;
  logEl.appendChild(div);
  logEl.scrollTop = logEl.scrollHeight;
  return div;
}

function appendStatus(text) {
  const div = document.createElement("div");
  div.className = "status";
  div.textContent = `· ${text}`;
  logEl.appendChild(div);
  logEl.scrollTop = logEl.scrollHeight;
}

function appendError(text) {
  const div = document.createElement("div");
  div.className = "error";
  div.textContent = `[오류] ${text}`;
  logEl.appendChild(div);
  logEl.scrollTop = logEl.scrollHeight;
}

// fetch 응답 스트림에서 "event: ...\ndata: ...\n\n" 단위(SSE 프레임)를 잘라 꺼낸다.
// SSE 프레임 구분자는 빈 줄(\n\n)이다. 서버가 보낸 청크가 프레임 경계와 맞지 않게
// 잘려 올 수 있으므로 버퍼에 계속 이어붙이면서 완성된 프레임만 꺼내 쓴다.
async function* parseSSE(reader) {
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let sep;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      yield parseFrame(frame);
    }
  }
}

function parseFrame(frame) {
  let event = "message";
  const dataLines = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trim());
    }
  }
  const dataText = dataLines.join("\n");
  let data = {};
  try {
    data = dataText ? JSON.parse(dataText) : {};
  } catch (err) {
    console.error("SSE data JSON 파싱 실패", dataText, err);
  }
  return { event, data };
}

async function sendMessage(message) {
  currentAbortController = new AbortController();
  sendBtn.disabled = true;
  cancelBtn.disabled = false;
  currentAssistantEl = null;

  let response;
  try {
    response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, session_id: sessionId }),
      signal: currentAbortController.signal,
    });
  } catch (err) {
    if (err.name === "AbortError") {
      appendStatus("요청을 취소했다");
    } else {
      appendError(`요청을 보낼 수 없다: ${err}`);
    }
    resetControls();
    return;
  }

  if (!response.ok || !response.body) {
    appendError(`서버 오류 (status=${response.status})`);
    resetControls();
    return;
  }

  const newSessionId = response.headers.get("X-Session-Id");
  if (newSessionId) sessionId = newSessionId;

  const reader = response.body.getReader();
  try {
    for await (const { event, data } of parseSSE(reader)) {
      handleEvent(event, data);
    }
  } catch (err) {
    if (err.name === "AbortError") {
      appendStatus("요청을 취소했다");
    } else {
      appendError(`스트림 읽기 오류: ${err}`);
    }
  } finally {
    resetControls();
  }
}

function handleEvent(event, data) {
  switch (event) {
    case "status":
      appendStatus(`[${data.stage}] ${data.message}`);
      break;
    case "token":
      if (!currentAssistantEl) {
        currentAssistantEl = appendLine("assistant", "");
      }
      currentAssistantEl.textContent += data.text;
      logEl.scrollTop = logEl.scrollHeight;
      break;
    case "error":
      appendError(`${data.code}: ${data.message}`);
      break;
    case "done":
      appendStatus(`종료 (finish_reason=${data.finish_reason})`);
      break;
    default:
      console.warn("알 수 없는 이벤트", event, data);
  }
}

function resetControls() {
  sendBtn.disabled = false;
  cancelBtn.disabled = true;
  currentAbortController = null;
}

formEl.addEventListener("submit", (ev) => {
  ev.preventDefault();
  const message = inputEl.value.trim();
  if (!message) return;
  appendLine("user", message);
  inputEl.value = "";
  sendMessage(message);
});

cancelBtn.addEventListener("click", () => {
  // AbortController.abort()는 fetch를 즉시 실패시키고 브라우저가 TCP 연결을 닫는다.
  // 서버는 request.is_disconnected() 또는 StreamingResponse의 자동 취소로 이를 감지한다.
  if (currentAbortController) currentAbortController.abort();
});
