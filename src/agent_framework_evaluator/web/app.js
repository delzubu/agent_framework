const traceTree = document.getElementById("trace-tree");
const responseOutput = document.getElementById("response-output");
const runButton = document.getElementById("run-button");
const promptInput = document.getElementById("prompt-input");
const agentInput = document.getElementById("agent-select");
const setupPathInput = document.getElementById("setup-path");
const agentList = document.getElementById("agent-list");

const bySpan = new Map();

function appendTraceNode(event) {
  const node = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = `${event.kind}: ${event.title}`;
  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(event.payload ?? {}, null, 2);
  node.appendChild(summary);
  node.appendChild(pre);
  const sid = event.span_id || event.event_id;
  if (sid) {
    bySpan.set(sid, node);
  }
  const pid = event.parent_span_id;
  if (pid && bySpan.has(pid)) {
    bySpan.get(pid).appendChild(node);
  } else {
    traceTree.appendChild(node);
  }
}

let socket = null;
let sessionId = null;

async function loadAgentCatalog() {
  try {
    const res = await fetch("/api/agents");
    const data = await res.json();
    agentList.innerHTML = "";
    for (const id of data.agents || []) {
      const opt = document.createElement("option");
      opt.value = id;
      agentList.appendChild(opt);
    }
  } catch (_) {
    /* ignore catalog failures */
  }
}

setupPathInput.addEventListener("change", async () => {
  const p = setupPathInput.value.trim();
  if (!p) return;
  try {
    const res = await fetch(`/api/setup-template?path=${encodeURIComponent(p)}`);
    const data = await res.json();
    if (data.template && !promptInput.value.trim()) {
      promptInput.value = data.template;
    }
  } catch (_) {
    /* ignore */
  }
});

function closeSessionOnLeave() {
  if (!sessionId) return;
  fetch(`/api/sessions/${sessionId}/close`, { method: "POST", keepalive: true }).catch(() => {});
}

window.addEventListener("beforeunload", () => {
  closeSessionOnLeave();
});

async function initSession() {
  await loadAgentCatalog();
  const res = await fetch("/api/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  const data = await res.json();
  sessionId = data.session_id;
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${proto}://${window.location.host}/ws/${sessionId}`);
  socket.addEventListener("message", (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "trace" && msg.event) {
      appendTraceNode(msg.event);
    }
    if (msg.type === "result" && msg.payload) {
      responseOutput.textContent = JSON.stringify(msg.payload, null, 2);
      runButton.disabled = false;
    }
    if (msg.type === "outbox" && msg.item && msg.item.kind === "prompt") {
      const answer = window.prompt(msg.item.prompt || "Your answer:");
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "user_input", text: answer }));
      }
    }
  });
}

runButton.addEventListener("click", () => {
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    return;
  }
  const agentId = agentInput.value.trim() || "root";
  const setupPath = setupPathInput.value.trim();
  runButton.disabled = true;
  responseOutput.textContent = "Running…";
  socket.send(
    JSON.stringify({
      type: "run",
      agent_id: agentId,
      prompt: promptInput.value,
      setup_path: setupPath || null,
    }),
  );
});

initSession().catch((err) => {
  responseOutput.textContent = `Failed to start session: ${err}`;
});
