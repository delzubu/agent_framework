const traceTree = document.getElementById("trace-tree");
const logStrip = document.getElementById("log-strip");
const responseOutput = document.getElementById("response-output");
const runButton = document.getElementById("run-button");
const promptInput = document.getElementById("prompt-input");
const agentInput = document.getElementById("agent-select");
const setupPathInput = document.getElementById("setup-path");
const agentList = document.getElementById("agent-list");
const channelToggles = document.getElementById("channel-toggles");
const conversationThread = document.getElementById("conversation-thread");
const replyInput = document.getElementById("reply-input");
const sendReplyButton = document.getElementById("send-reply-button");

/** @type {Map<string, { details: HTMLElement, body: HTMLElement, spinner: HTMLElement, statusEl: HTMLElement, labelEl: HTMLElement, subEl: HTMLElement, agentName: string, lastStatus: string | null }>} */
const runFrames = new Map();

/** @type {{ el: HTMLElement, event: Record<string, unknown> }[]} */
const treeEntries = [];
/** @type {{ el: HTMLElement, event: Record<string, unknown> }[]} */
const logEntries = [];

/** Inline preview length before cropping (click opens modal with full text). ~80–100 chars. */
const TRACE_STRING_PREVIEW_CHARS = 90;

function looksLikeMarkdown(s) {
  if (typeof s !== "string" || s.length < 4) return false;
  return (
    /(^|\n)#{1,6}\s/m.test(s) ||
    /(^|\n)[-*+]\s/m.test(s) ||
    /```[\s\S]*?```/.test(s) ||
    /(^|\n)\|[^\n]+\|/m.test(s) ||
    /\[[^\]]+\]\([^)]+\)/.test(s)
  );
}

function getMarkedParse() {
  const m = globalThis.marked;
  if (m && typeof m.parse === "function") {
    return m.parse.bind(m);
  }
  return null;
}

function openTraceDetailModal(title, text) {
  const modal = document.getElementById("trace-detail-modal");
  const titleEl = document.getElementById("trace-detail-modal-title");
  const srcEl = document.getElementById("trace-detail-modal-source");
  const mdEl = document.getElementById("trace-detail-modal-md");
  const tabs = document.getElementById("trace-detail-dialog-tabs");
  const tabSrc = document.getElementById("trace-detail-tab-source");
  const tabMd = document.getElementById("trace-detail-tab-md");
  if (!modal || !titleEl || !srcEl || !mdEl) return;
  titleEl.textContent = title;
  srcEl.textContent = text;
  const parseMd = getMarkedParse();
  const showMd = Boolean(parseMd && looksLikeMarkdown(text));
  mdEl.innerHTML = "";
  if (showMd && parseMd) {
    try {
      mdEl.innerHTML = parseMd(text);
    } catch (_) {
      mdEl.innerHTML = "<p><em>Could not render as Markdown.</em></p>";
    }
  }
  if (tabs) tabs.hidden = !showMd;
  if (tabMd) tabMd.style.display = showMd ? "" : "none";
  if (tabSrc) tabSrc.classList.add("trace-detail-tab--active");
  if (tabMd) tabMd.classList.remove("trace-detail-tab--active");
  srcEl.classList.add("trace-detail-panel--active");
  mdEl.classList.remove("trace-detail-panel--active");
  modal.showModal();
}

function wireTraceDetailModal() {
  const modal = document.getElementById("trace-detail-modal");
  const closeBtn = document.getElementById("trace-detail-modal-close");
  const tabSrc = document.getElementById("trace-detail-tab-source");
  const tabMd = document.getElementById("trace-detail-tab-md");
  const srcEl = document.getElementById("trace-detail-modal-source");
  const mdEl = document.getElementById("trace-detail-modal-md");
  if (!modal || !closeBtn) return;
  closeBtn.addEventListener("click", () => modal.close());
  modal.addEventListener("click", (e) => {
    if (e.target === modal) modal.close();
  });
  function showPanel(which) {
    const showSrc = which === "source";
    srcEl?.classList.toggle("trace-detail-panel--active", showSrc);
    mdEl?.classList.toggle("trace-detail-panel--active", !showSrc);
    tabSrc?.classList.toggle("trace-detail-tab--active", showSrc);
    tabMd?.classList.toggle("trace-detail-tab--active", !showSrc);
  }
  tabSrc?.addEventListener("click", () => showPanel("source"));
  tabMd?.addEventListener("click", () => showPanel("md"));
}

wireTraceDetailModal();

function renderStringInTrace(parent, s, keyHint) {
  const wrap = document.createElement("span");
  wrap.className = "trace-json-str";
  const openQ = document.createElement("span");
  openQ.className = "trace-json-quote";
  openQ.textContent = '"';
  const inner = document.createElement("span");
  inner.className = "trace-json-str-inner";
  if (s.length <= TRACE_STRING_PREVIEW_CHARS) {
    inner.textContent = s;
  } else {
    inner.textContent = s.slice(0, TRACE_STRING_PREVIEW_CHARS) + "…";
    inner.classList.add("trace-json-str-inner--truncated");
    inner.setAttribute("role", "button");
    inner.tabIndex = 0;
    inner.title = `Open full text (${s.length} characters)`;
    const open = (e) => {
      e.preventDefault();
      e.stopPropagation();
      openTraceDetailModal(String(keyHint || "Field"), s);
    };
    inner.addEventListener("click", open);
    inner.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        e.stopPropagation();
        open(e);
      }
    });
  }
  const closeQ = document.createElement("span");
  closeQ.className = "trace-json-quote";
  closeQ.textContent = '"';
  wrap.appendChild(openQ);
  wrap.appendChild(inner);
  wrap.appendChild(closeQ);
  parent.appendChild(wrap);
}

function appendJsonValue(parent, value, keyHint) {
  if (value === null) {
    const span = document.createElement("span");
    span.className = "trace-json-lit trace-json-null";
    span.textContent = "null";
    parent.appendChild(span);
    return;
  }
  const t = typeof value;
  if (t === "string") {
    renderStringInTrace(parent, value, keyHint);
    return;
  }
  if (t === "number" || t === "boolean") {
    const span = document.createElement("span");
    span.className = "trace-json-lit";
    span.textContent = JSON.stringify(value);
    parent.appendChild(span);
    return;
  }
  if (Array.isArray(value)) {
    const block = document.createElement("div");
    block.className = "trace-json-array";
    if (value.length === 0) {
      block.textContent = "[]";
      parent.appendChild(block);
      return;
    }
    value.forEach((item, i) => {
      const row = document.createElement("div");
      row.className = "trace-json-array-row";
      const idx = document.createElement("span");
      idx.className = "trace-json-idx";
      idx.textContent = `${i}: `;
      row.appendChild(idx);
      const cell = document.createElement("span");
      cell.className = "trace-json-array-cell";
      appendJsonValue(cell, item, `${keyHint || "item"}[${i}]`);
      row.appendChild(cell);
      block.appendChild(row);
    });
    parent.appendChild(block);
    return;
  }
  if (t === "object") {
    const keys = Object.keys(value);
    if (keys.length === 0) {
      const span = document.createElement("span");
      span.className = "trace-json-lit";
      span.textContent = "{}";
      parent.appendChild(span);
      return;
    }
    const obj = document.createElement("div");
    obj.className = "trace-json-obj";
    for (const k of keys) {
      obj.appendChild(renderJsonKeyRow(k, value[k], keyHint ? `${keyHint}.${k}` : k));
    }
    parent.appendChild(obj);
  }
}

function renderJsonKeyRow(k, v, path) {
  const row = document.createElement("div");
  row.className = "trace-json-kv";
  const keyEl = document.createElement("span");
  keyEl.className = "trace-json-key";
  keyEl.textContent = `${k}: `;
  row.appendChild(keyEl);
  const valWrap = document.createElement("span");
  valWrap.className = "trace-json-val";
  appendJsonValue(valWrap, v, path);
  row.appendChild(valWrap);
  return row;
}

function renderPayloadTree(payload) {
  const root = document.createElement("div");
  root.className = "trace-payload-tree";
  if (payload === null || typeof payload !== "object" || Array.isArray(payload)) {
    appendJsonValue(root, payload, "payload");
    return root;
  }
  const keys = Object.keys(payload);
  if (keys.length === 0) {
    root.textContent = "{}";
    return root;
  }
  for (const k of keys) {
    root.appendChild(renderJsonKeyRow(k, payload[k], k));
  }
  return root;
}

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
  if (conversationThread) conversationThread.innerHTML = "";
  setAwaitingPrompt(null);
}

/** @type {string | null} */
let awaitingPromptId = null;

function setAwaitingPrompt(promptId) {
  awaitingPromptId = promptId;
  const active = Boolean(promptId);
  if (replyInput) {
    replyInput.disabled = !active;
    replyInput.placeholder = active
      ? "Type your answer and press Send (submitted over HTTP)"
      : "Your answer appears here when the agent asks for input…";
  }
  if (sendReplyButton) sendReplyButton.disabled = !active;
  if (active && replyInput) replyInput.focus();
}

function appendConversationBubble(role, text) {
  if (!conversationThread || typeof text !== "string") return;
  const wrap = document.createElement("div");
  wrap.className = `conv-msg conv-msg--${role}`;
  const meta = document.createElement("div");
  meta.className = "conv-msg-meta";
  meta.textContent = role === "user" ? "You" : "Agent";
  const body = document.createElement("div");
  body.className = "conv-msg-body";
  body.textContent = text;
  wrap.appendChild(meta);
  wrap.appendChild(body);
  conversationThread.appendChild(wrap);
  conversationThread.scrollTop = conversationThread.scrollHeight;
}

/**
 * @param {Record<string, unknown>} item
 */
function handleOutboxItem(item) {
  const kind = item.kind;
  const pid = typeof item.prompt_id === "string" ? item.prompt_id : null;
  if (!pid) return;

  let text = "";
  if (kind === "prompt") {
    text = typeof item.prompt === "string" ? item.prompt : "";
  } else if (kind === "question") {
    const opts = Array.isArray(item.options) ? item.options.join(", ") : "";
    text = `${item.prompt || ""}${opts ? `\nOptions: ${opts}` : ""}`;
  } else if (kind === "confirmation") {
    text = typeof item.prompt === "string" ? item.prompt : "Confirm?";
  } else if (kind === "permission") {
    const req = item.request;
    if (req && typeof req === "object" && "summary" in req) {
      text = `Permission: ${String(/** @type {{ summary?: string }} */ (req).summary || "")}`;
    } else {
      text = "Permission request";
    }
  } else {
    return;
  }
  appendConversationBubble("assistant", text);
  setAwaitingPrompt(pid);
}

async function postUserInputHttp(text) {
  if (!sessionId || !awaitingPromptId) return;
  const res = await fetch(`/api/sessions/${sessionId}/user-input`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt_id: awaitingPromptId, text }),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      if (typeof j.detail === "string") detail = j.detail;
    } catch (_) {
      /* ignore */
    }
    throw new Error(detail);
  }
  appendConversationBubble("user", text ?? "");
  setAwaitingPrompt(null);
  if (replyInput) replyInput.value = "";
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

/**
 * Spans show runtime failures; the Logs strip only subscribed to channel=log (Python logging).
 * Duplicate runtime error/warning rows into Logs with channel=log so filters stay consistent.
 */
function mirrorRuntimeSeverityToLogStrip(event) {
  const channel = typeof event.channel === "string" ? event.channel : "runtime";
  if (channel !== "runtime") {
    return;
  }
  const lvl = typeof event.level === "string" ? event.level : "";
  if (lvl !== "error" && lvl !== "warning") {
    return;
  }
  const p = getPayload(event);
  const message =
    (typeof p.message === "string" && p.message) ||
    (typeof p.error === "string" && p.error) ||
    (typeof p.detail === "string" && p.detail) ||
    (typeof event.summary === "string" && event.summary) ||
    (typeof event.title === "string" && event.title) ||
    lvl;
  const kind = typeof event.kind === "string" ? event.kind : "runtime";
  appendLogLine({
    channel: "log",
    level: lvl,
    kind: `${kind}.ui_mirror`,
    title: typeof event.title === "string" ? event.title : message,
    payload: {
      logger_name: kind,
      message,
    },
  });
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
  const body = document.createElement("div");
  body.className = "trace-event-body";
  body.appendChild(renderPayloadTree(event.payload ?? {}));
  node.appendChild(summary);
  node.appendChild(body);
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

  mirrorRuntimeSeverityToLogStrip(event);

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

function onSocketMessage(ev) {
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
  if (msg.type === "outbox" && msg.item) {
    handleOutboxItem(msg.item);
  }
}

function detachWebSocket() {
  if (socket) {
    try {
      socket.removeEventListener("message", onSocketMessage);
      socket.close();
    } catch (_) {
      /* ignore */
    }
    socket = null;
  }
}

async function connectWebSocket() {
  if (!sessionId) throw new Error("no session");
  detachWebSocket();
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${window.location.host}/ws/${sessionId}`);
  socket = ws;
  ws.addEventListener("message", onSocketMessage);
  await new Promise((resolve, reject) => {
    const to = setTimeout(() => reject(new Error("WebSocket open timeout")), 15000);
    ws.addEventListener(
      "open",
      () => {
        clearTimeout(to);
        resolve(undefined);
      },
      { once: true },
    );
    ws.addEventListener(
      "error",
      () => {
        clearTimeout(to);
        reject(new Error("WebSocket error"));
      },
      { once: true },
    );
  });
}

async function ensureSessionConnected() {
  const health = await fetch("/api/agents");
  if (!health.ok) throw new Error("Server unreachable");
  const needNew = !sessionId || !socket || socket.readyState !== WebSocket.OPEN;
  if (needNew) {
    if (sessionId) {
      await fetch(`/api/sessions/${sessionId}/close`, { method: "POST" }).catch(() => {});
    }
    await loadAgentCatalog();
    const res = await fetch("/api/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    if (!res.ok) throw new Error("Failed to create session");
    const data = await res.json();
    sessionId = data.session_id;
    await connectWebSocket();
  }
}

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
  try {
    await ensureSessionConnected();
  } catch (err) {
    responseOutput.textContent = `Failed to start session: ${err}`;
  }
}

runButton.addEventListener("click", async () => {
  try {
    await ensureSessionConnected();
  } catch (err) {
    responseOutput.textContent = `Cannot reach server: ${err}`;
    return;
  }
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    responseOutput.textContent = "No WebSocket after reconnect.";
    return;
  }
  const agentId = agentInput.value.trim() || "root";
  const setupPath = setupPathInput.value.trim();
  runButton.disabled = true;
  clearTraceUi();
  appendConversationBubble("user", promptInput.value || "(empty prompt)");
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

sendReplyButton?.addEventListener("click", async () => {
  if (!awaitingPromptId || !replyInput) return;
  const text = replyInput.value;
  try {
    await postUserInputHttp(text);
  } catch (err) {
    responseOutput.textContent = `Reply failed: ${err}`;
  }
});

replyInput?.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendReplyButton?.click();
  }
});

initSession().catch((err) => {
  responseOutput.textContent = `Failed to start session: ${err}`;
});
