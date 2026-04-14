const traceTree = document.getElementById("trace-tree");
const logStrip = document.getElementById("log-strip");
const responseOutput = document.getElementById("response-output");
const runButton = document.getElementById("run-button");
const promptInput = document.getElementById("prompt-input");
const agentInput = document.getElementById("agent-select");
const setupPathInput = document.getElementById("setup-path");
const agentList = document.getElementById("agent-list");
const channelToggles = document.getElementById("channel-toggles");

/** @type {Map<string, { details: HTMLElement, body: HTMLElement, spinner: HTMLElement, statusEl: HTMLElement, labelEl: HTMLElement, subEl: HTMLElement, agentName: string, lastStatus: string | null }>} */
const runFrames = new Map();

/** @type {{ el: HTMLElement, event: Record<string, unknown> }[]} */
const treeEntries = [];
/** @type {{ el: HTMLElement, event: Record<string, unknown> }[]} */
const logEntries = [];

function getPayload(event) {
  return event.payload && typeof event.payload === "object" ? event.payload : {};
}

function getContext(event) {
  return event.context && typeof event.context === "object" ? event.context : {};
}

function getEventRunId(event) {
  const ctx = getContext(event);
  if (typeof ctx.run_id === "string" && ctx.run_id) {
    return ctx.run_id;
  }
  const p = getPayload(event);
  if (typeof p.run_id === "string" && p.run_id) {
    return p.run_id;
  }
  return null;
}

function channelEnabled(channel) {
  const id = `ch-${channel}`;
  const box = document.getElementById(id);
  return box ? box.checked : true;
}

/** Spans and log strip: channel checkboxes only (trace event level is display-only). */
function passesChannelFilters(event) {
  const ch = typeof event.channel === "string" ? event.channel : "runtime";
  return channelEnabled(ch);
}

function setEntryVisible(entry) {
  entry.el.style.display = passesChannelFilters(entry.event) ? "" : "none";
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
  runFrames.clear();
  treeEntries.length = 0;
  logEntries.length = 0;
}

function appendLogLine(event) {
  const row = document.createElement("div");
  row.className = "log-line";
  const payload = getPayload(event);
  const loggerName = typeof payload.logger_name === "string" ? payload.logger_name : "";
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

function getSpanContainer(event) {
  const rid = getEventRunId(event);
  if (rid && runFrames.has(rid)) {
    return runFrames.get(rid).body;
  }
  return traceTree;
}

function mapStatusToOutcome(statusRaw) {
  const s = String(statusRaw || "").toLowerCase();
  if (s === "completed" || s === "success") {
    return { text: String(statusRaw || "completed"), cls: "trace-agent-call--success" };
  }
  if (s === "failed" || s === "error") {
    return { text: String(statusRaw || "failed"), cls: "trace-agent-call--error" };
  }
  if (s === "stopped" || s === "cancelled" || s === "canceled") {
    return { text: String(statusRaw || "stopped"), cls: "trace-agent-call--stopped" };
  }
  if (s) {
    return { text: String(statusRaw), cls: "trace-agent-call--neutral" };
  }
  return { text: "completed", cls: "trace-agent-call--success" };
}

function applyFrameOutcome(detailsEl, fr, statusRaw) {
  const { text, cls } = mapStatusToOutcome(statusRaw);
  detailsEl.classList.remove(
    "trace-agent-call--running",
    "trace-agent-call--success",
    "trace-agent-call--error",
    "trace-agent-call--stopped",
    "trace-agent-call--neutral",
  );
  detailsEl.classList.add(cls);
  fr.spinner.style.display = "none";
  fr.spinner.setAttribute("aria-hidden", "true");
  fr.statusEl.textContent = text;
  fr.labelEl.textContent = fr.agentName;
  if (fr.subEl) {
    fr.subEl.textContent = "";
  }
}

function beginAgentCallFrame(event) {
  const p = getPayload(event);
  const runId = typeof p.run_id === "string" ? p.run_id : null;
  const parentRunId = p.parent_run_id != null && p.parent_run_id !== "" ? String(p.parent_run_id) : null;
  const agentName = typeof p.agent_name === "string" ? p.agent_name : "agent";
  if (!runId) {
    appendTraceEventRow(event);
    return;
  }

  let parentContainer = traceTree;
  if (parentRunId && runFrames.has(parentRunId)) {
    parentContainer = runFrames.get(parentRunId).body;
  }

  const details = document.createElement("details");
  details.className = "trace-agent-call trace-agent-call--running";
  details.open = false;

  const summary = document.createElement("summary");
  summary.className = "trace-agent-call-summary";

  const spinner = document.createElement("span");
  spinner.className = "trace-agent-call-spinner";
  spinner.setAttribute("aria-hidden", "false");

  const statusEl = document.createElement("span");
  statusEl.className = "trace-agent-call-status";
  statusEl.setAttribute("aria-live", "polite");

  const labelEl = document.createElement("span");
  labelEl.className = "trace-agent-call-label";
  labelEl.textContent = agentName;

  const subEl = document.createElement("span");
  subEl.className = "trace-agent-call-sub";
  subEl.textContent = "running…";

  summary.appendChild(spinner);
  summary.appendChild(statusEl);
  summary.appendChild(document.createTextNode(" "));
  summary.appendChild(labelEl);
  summary.appendChild(document.createTextNode(" — "));
  summary.appendChild(subEl);

  const body = document.createElement("div");
  body.className = "trace-agent-call-body";

  details.appendChild(summary);
  details.appendChild(body);
  parentContainer.appendChild(details);

  runFrames.set(runId, {
    details,
    body,
    spinner,
    statusEl,
    labelEl,
    subEl,
    agentName,
    lastStatus: null,
  });

  treeEntries.push({ el: details, event });
  setEntryVisible(treeEntries[treeEntries.length - 1]);
}

function endAgentCallFrame(event) {
  const p = getPayload(event);
  const runId = typeof p.run_id === "string" ? p.run_id : null;
  if (!runId) {
    return;
  }
  const fr = runFrames.get(runId);
  if (!fr) {
    return;
  }
  if (fr.details.classList.contains("trace-agent-call--running")) {
    applyFrameOutcome(fr.details, fr, fr.lastStatus || "completed");
  }
}

function onAgentFinished(event) {
  const rid = getEventRunId(event);
  if (!rid || !runFrames.has(rid)) {
    return;
  }
  const st = getPayload(event).status;
  const fr = runFrames.get(rid);
  if (st != null && st !== "") {
    fr.lastStatus = String(st);
  }
  applyFrameOutcome(fr.details, fr, fr.lastStatus || st || "completed");
}

function buildTraceDetails(event) {
  const node = document.createElement("details");
  node.className = "trace-event-row";
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
  return node;
}

function appendTraceEventRow(event) {
  const container = getSpanContainer(event);
  const node = buildTraceDetails(event);
  container.appendChild(node);
  const entry = { el: node, event };
  treeEntries.push(entry);
  setEntryVisible(entry);
}

function routeTraceEvent(event) {
  const channel = typeof event.channel === "string" ? event.channel : "runtime";
  if (channel === "log") {
    appendLogLine(event);
    return;
  }

  const kind = typeof event.kind === "string" ? event.kind : "";

  if (kind === "runtime.audit.agent_call_started") {
    beginAgentCallFrame(event);
    return;
  }
  if (kind === "runtime.audit.agent_call_finished") {
    endAgentCallFrame(event);
    return;
  }

  if (kind === "runtime.agent_finished") {
    onAgentFinished(event);
  }

  appendTraceEventRow(event);
}

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
