// 아주 단순한 SSE 클라이언트. EventSource는 POST 바디를 못 보내므로 fetch +
// ReadableStream으로 event: / data: 블록을 직접 파싱한다.
const form = document.getElementById("chat-form");
const messageInput = document.getElementById("message");
const modeSelect = document.getElementById("search-mode");
const statusEl = document.getElementById("status");
const answerEl = document.getElementById("answer");
const sourcesEl = document.getElementById("sources");

let latestSources = [];

function resetView() {
  statusEl.textContent = "";
  answerEl.textContent = "";
  sourcesEl.innerHTML = "";
  latestSources = [];
}

// 답변 텍스트 안의 "[S1]" 같은 표시를 실제 근거 링크로 바꿔서 보여준다.
// sources 이벤트로 받은 목록(검증을 통과한 것만)에 있는 라벨만 링크가 된다.
function renderAnswerWithLinks(text) {
  const byLabel = Object.fromEntries(latestSources.map((s) => [s.id, s]));
  const html = text.replace(/\[S(\d+)\]/g, (match, num) => {
    const label = "S" + num;
    const src = byLabel[label];
    if (!src) return match; // 검증에서 이미 걸러진 라벨은 여기 나타나지 않아야 정상이다.
    return `<a href="${src.url}" target="_blank" title="${src.doc} p${src.page}">${match}</a>`;
  });
  answerEl.innerHTML = html;
}

function renderSourcesPanel() {
  if (!latestSources.length) {
    sourcesEl.innerHTML = "";
    return;
  }
  const items = latestSources
    .map(
      (s) =>
        `<a href="${s.url}" target="_blank">${s.id} · ${s.doc} (p${s.page}) — ${s.snippet.slice(0, 60)}...</a>`
    )
    .join("");
  sourcesEl.innerHTML = "<hr/><strong>근거</strong>" + items;
}

async function submitQuestion(message, searchMode) {
  resetView();
  const response = await fetch("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, search_mode: searchMode, top_k: 5 }),
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

    // SSE 이벤트는 빈 줄로 구분된다.
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
    case "token":
      answerEl.textContent += data.text;
      break;
    case "sources":
      latestSources = data.items;
      renderAnswerWithLinks(answerEl.textContent);
      renderSourcesPanel();
      break;
    case "error":
      statusEl.textContent = `오류(${data.code}): ${data.message}`;
      break;
    case "done":
      statusEl.textContent += ` (종료: ${data.finish_reason})`;
      break;
    default:
      break;
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const message = messageInput.value.trim();
  if (!message) return;
  submitQuestion(message, modeSelect.value);
});
