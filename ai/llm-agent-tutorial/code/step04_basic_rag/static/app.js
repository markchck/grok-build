// 아주 단순한 SSE 클라이언트. fetch + ReadableStream으로 event-stream을 직접 파싱한다.
// (EventSource는 POST 바디를 보낼 수 없어서 fetch 스트림 방식을 쓴다.)

document.getElementById("ingestBtn").addEventListener("click", async () => {
  const el = document.getElementById("ingestResult");
  el.textContent = "적재 중...";
  const res = await fetch("/api/ingest", { method: "POST" });
  const data = await res.json();
  el.textContent = JSON.stringify(data);
});

document.getElementById("askBtn").addEventListener("click", async () => {
  const question = document.getElementById("question").value;
  const statusEl = document.getElementById("status");
  const answerEl = document.getElementById("answer");
  const sourcesEl = document.getElementById("sources");
  statusEl.textContent = "";
  answerEl.textContent = "";
  sourcesEl.innerHTML = "";

  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let frameEnd;
    while ((frameEnd = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, frameEnd);
      buffer = buffer.slice(frameEnd + 2);
      handleFrame(frame, { statusEl, answerEl, sourcesEl });
    }
  }
});

function handleFrame(frame, els) {
  const lines = frame.split("\n");
  let event = "message";
  let dataStr = "";
  for (const line of lines) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) dataStr += line.slice(5).trim();
  }
  if (!dataStr) return;
  const data = JSON.parse(dataStr);

  if (event === "status") {
    els.statusEl.textContent = `[${data.stage}] ${data.message}`;
  } else if (event === "token") {
    els.answerEl.textContent += data.text;
  } else if (event === "sources") {
    els.sourcesEl.innerHTML = data.items
      .map((s) => `<li><b>${s.doc}</b> (${s.id}) - ${s.snippet}</li>`)
      .join("");
  } else if (event === "error") {
    els.statusEl.textContent = `오류: ${data.code} - ${data.message}`;
  } else if (event === "done") {
    els.statusEl.textContent += ` (종료: ${data.finish_reason})`;
  }
}
