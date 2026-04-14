const traceTree = document.getElementById("trace-tree");
const logStrip = document.getElementById("log-strip");
const responseOutput = document.getElementById("response-output");
const runButton = document.getElementById("run-button");
const promptInput = document.getElementById("prompt-input");
const agentInput = document.getElementById("agent-select");
const setupPathInput = document.getElementById("setup-path");
const agentList = document.getElementById("agent-list");
const minLevelSelect = document.getElementById("trace-min-level");
const channelToggles = document.getElementById("channel-toggles");

const LEVEL_ORDER = { debug: 0, info: 1, warning: 2, error: 3 };

const bySpan = new Map();

/** @type {{ el: HTMLElement, event: Record<string, unknown> }[]} */
const treeEntries = [];
/** @type {{ el: HTMLElement, event: Record<string, unknown> }[]} */
const logEntries = [];

function channelEnabled(channel) {
  const id = `ch-${channel}`;
  const box = document.getElementById(id);
  return box ? box.checked : true;
}

function minLevelThreshold() {
  const v = minLevelSelect?.value || "warning";
  return LEVEL_ORDER[v] ?? LEVEL_ORDER.warning;
}

function eventLevelOrder(event) {
  const lv = typeof event.level === "string" ? event.level : "info";
  return LEVEL_ORDER[lv] ?? LEVEL_ORDER.info;
}

function passesFilters(event) {
  const ch = typeof event.channel === "string" ? event.channel : "runtime";
  if (!channelEnabled(ch)) {
    return false;
  }
  return eventLevelOrder(event) >= minLevelThreshold();
}

function setEntryVisible(entry) {
  entry.el.style.display = passesFilters(entry.event) ? "" : "none";
}

function reapplyFilters() {
  for (const e of treeEntries) {
    setEntryVisible(e);
  }
  for (const e of logEntries) {
    setEntryVisible(e);
  }
}

function clearTraceUi() {
  traceTree.innerHTML = "";
  logStrip.innerHTML = "";
  bySpan.clear();
  treeEntries.length = 0;
  logEntries.length = 0;
}

function appendLogLine(event) {
  const row = document.createElement("div");
  row.className = "log-line";
  const payload = event.payload && typeof event.payload === "object" ? event.payload : {};
  const loggerName =
    typeof payload.logger_name === "string" ? payload.logger_name : "";
  const message =
    typeof payload.message === "string"
      ? payload.message
      : typeof event.title === "string"
        ? event.title
        : "";
  const lvl = typeof event.level === "string" ? event.level : "info";

  const badge = document.createElement("span");
  badge.className = `log-badge log-${lvl}`;
  badge.textContent = lvl;

  const logger = document.createElement("span");
  logger.className = "log-logger";
  logger.textContent = loggerName;
  logger.title = loggerName;

  const msg = document.createElement("span");
  msg.className = "log-msg";
  msg.textContent = message;

  row.appendChild(badge);
  row.appendChild(logger);
  row.appendChild(msg);

  logStrip.appendChild(row);
  const entry = { el: row, event };
  logEntries.push(entry);
  setEntryVisible(entry);
  logStrip.scrollTop = logStrip.scrollHeight;
}

function appendTraceNode(event) {
  const node = document.createElement("details");
  const summary = document.createElement("summary");
  const ch = typeof event.channel === "string" ? event.channel : "";
  const lvl = typeof event.level === "string" ? event.level : "";
  summary.textContent = `${ch ? `[${ch}] ` : ""}${event.kind}: ${event.title}`;
  if (lvl) {
    const badge = document.createElement("span");
    badge.className = `trace-level-badge trace-level-${lvl}`;
    badge.textContent = lvl;
    summary.appendChild(document.createTextNode(" "));
    summary.appendChild(badge);
  }
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
  const entry = { el: node, event };
  treeEntries.push(entry);
  setEntryVisible(entry);
}

function routeTraceEvent(event) {
  const channel = typeof event.channel === "string" ? event.channel : "runtime";
  if (channel === "log") {
    appendLogLine(event);
  } else {
    appendTraceNode(event);
  }
}

minLevelSelect?.addEventListener("change", reapplyFilters);
channelToggles?.addEventListener("change", reapplyFilters);

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
      routeTraceEvent(msg.event);
    }
    if (msg.type === "result" && msg.payload) {
      responseOutput.textContent = JSON.stringify(msg.payload, null, 2);
      runButton.disabled = false;
    }
    if (msg.type === "error") {
      const et = msg.error_type || "Error";
      const lines = [`[${et}] ${msg.message || ""}`];
      if (msg.path) {
        lines.push(`File: ${msg.path}`);
      }
      if (msg.hint) {
        lines.push(msg.hint);
      }
      responseOutput.textContent = lines.join("\n\n");
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
  clearTraceUi();
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
