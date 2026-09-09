// 이 단계의 /chat은 POST + SSE다. 표준 EventSource는 GET만 지원하므로
// fetch()의 스트리밍 body를 직접 읽어서 "event: ...\ndata: ...\n\n" 블록을 파싱한다.

const logEl = document.getElementById("log");
const form = document.getElementById("chat-form");
const input = document.getElementById("message");

function appendLine(cls, text) {
  const div = document.createElement("div");
  div.className = "event " + cls;
  div.textContent = text;
  logEl.appendChild(div);
  logEl.scrollTop = logEl.scrollHeight;
}

function handleEvent(eventName, dataText) {
  let data;
  try {
    data = JSON.parse(dataText);
  } catch (e) {
    appendLine("status", `[파싱 실패] ${eventName}: ${dataText}`);
    return;
  }

  switch (eventName) {
    case "status":
      appendLine("status", `[${data.stage}] ${data.message}`);
      break;
    case "tool_call":
      appendLine("tool_call", `도구 호출 -> ${data.name}(${JSON.stringify(data.args)})`);
      break;
    case "tool_result":
      appendLine(
        data.ok ? "tool_result_ok" : "tool_result_fail",
        `도구 결과 (${data.ok ? "성공" : "실패"}) ${data.summary}`
      );
      break;
    case "token":
      appendLine("token", data.text);
      break;
    case "error":
      appendLine("tool_result_fail", `오류[${data.code}] ${data.message}`);
      break;
    case "done":
      appendLine("done", `종료: finish_reason=${data.finish_reason}`);
      break;
    default:
      appendLine("status", `(알 수 없는 이벤트) ${eventName}: ${dataText}`);
  }
}

async function sendMessage(message) {
  appendLine("token", `> ${message}`);

  const resp = await fetch("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });

  const reader = resp.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let sep;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);

      let eventName = "message";
      let dataText = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) eventName = line.slice(6).trim();
        else if (line.startsWith("data:")) dataText += line.slice(5).trim();
      }
      if (dataText) handleEvent(eventName, dataText);
    }
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  sendMessage(message).catch((err) => appendLine("tool_result_fail", `요청 실패: ${err}`));
});
