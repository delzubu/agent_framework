const traceFeed = document.getElementById("trace-feed");
const appStatus = document.getElementById("app-status");
const promptInput = document.getElementById("prompt-input");
const sendChatButton = document.getElementById("send-chat-button");
const envPathInput = document.getElementById("env-path");
const agentInput = document.getElementById("agent-select");
const initializerInput = document.getElementById("initializer-select");
const agentList = document.getElementById("agent-list");
const initializerList = document.getElementById("initializer-list");
const channelToggles = document.getElementById("channel-toggles");
const conversationThread = document.getElementById("conversation-thread");
const modeSelect = document.getElementById("mode-select");
const testsetHint = document.getElementById("testset-hint");
const evaluatorPromptInput = document.getElementById("evaluator-prompt-input");
const evaluationPanel = document.getElementById("evaluation-panel");
const evalScoreBar = document.getElementById("eval-score-bar");
const evalScoreLabel = document.getElementById("eval-score-label");
const evaluationStatus = document.getElementById("evaluation-status");
const evaluationScoreTrigger = document.getElementById("evaluation-score-trigger");
const evaluationInlineDetail = document.getElementById("evaluation-inline-detail");
const evaluateButton = document.getElementById("evaluate-button");
const evaluationDetailModal = document.getElementById("evaluation-detail-modal");
const evaluationDetailBody = document.getElementById("evaluation-detail-body");
const evaluationDetailClose = document.getElementById("evaluation-detail-close");
const tabChat = document.getElementById("tab-chat");
const tabEvaluation = document.getElementById("tab-evaluation");
const tabAgent = document.getElementById("tab-agent");
const panelChat = document.getElementById("tab-panel-chat");
const panelEvaluation = document.getElementById("tab-panel-evaluation");
const panelAgent = document.getElementById("tab-panel-agent");
const agentPromptDisplay = document.getElementById("agent-prompt-display");
const agentPromptEmpty = document.getElementById("agent-prompt-empty");
const agentPromptRaw = document.getElementById("agent-prompt-raw");
const agentPromptExpanded = document.getElementById("agent-prompt-expanded");
const agentUserSection = document.getElementById("agent-user-section");
const agentUserPrimaryDisplay = document.getElementById("agent-user-primary-display");
const agentUserEmpty = document.getElementById("agent-user-empty");
const agentUserExtra = document.getElementById("agent-user-extra");
const agentUserExtraHeading = document.getElementById("agent-user-extra-heading");
const agentUserEnteredBtn = document.getElementById("agent-user-entered");
const agentUserActualBtn = document.getElementById("agent-user-actual");

/** @typedef {{ criteria: string, passed: boolean, reason: string }} EvalCriterionRow */

/** @type {null | { score: number, overall_verdict: string, evaluation: EvalCriterionRow[] }} */
let lastEvaluationPayload = null;

/** Last agent `result` payload from the server. @type {Record<string, unknown> | null} */
let lastAgentResultPayload = null;

/**
 * Snapshots from the last run (first LLM request).
 * @type {{
 *   system_prompt: string,
 *   user_prompt: string,
 *   instruction_entered: string,
 *   user_messages: string[],
 * } | null}
 */
let lastPromptSnapshots = null;

/** Primary user block: text typed in UI vs first user message sent to the model. */
let userPrimaryMode = "entered";

let evaluationInFlight = false;
/** True while a run is in flight until result, error, or outbox (HITL). */
let agentRunInProgress = false;

/** @type {HTMLElement | null} */
let typingPlaceholderEl = null;

/** @type {Map<string, { details: HTMLElement, body: HTMLElement, spinner: HTMLElement, statusEl: HTMLElement, labelEl: HTMLElement, subEl: HTMLElement, agentName: string, lastStatus: string | null, depth: number }>} */
const runFrames = new Map();

/** @type {{ el: HTMLElement, event: Record<string, unknown> }[]} */
const unifiedEntries = [];

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

function wireEvaluationDetailModal() {
  if (!evaluationDetailModal || !evaluationDetailClose) return;
  evaluationDetailClose.addEventListener("click", () => evaluationDetailModal.close());
  evaluationDetailModal.addEventListener("click", (e) => {
    if (e.target === evaluationDetailModal) evaluationDetailModal.close();
  });
}

wireEvaluationDetailModal();

/**
 * @param {HTMLElement} container
 * @param {number} score
 */
function renderEvalScoreBar(container, score) {
  if (!container) return;
  container.innerHTML = "";
  const s = Math.min(10, Math.max(0, Number(score)));
  for (let i = 0; i < 10; i++) {
    const amt = Math.min(1, Math.max(0, s - i));
    const hue = (i / 9) * 120;
    const wrap = document.createElement("span");
    wrap.className = "eval-segment";
    wrap.style.setProperty("--eval-lit", String(amt));
    const glow = document.createElement("span");
    glow.className = "eval-segment-glow";
    glow.style.background = `hsl(${hue} 82% 48%)`;
    const inner = document.createElement("span");
    inner.className = "eval-segment-inner";
    inner.style.background = `linear-gradient(to bottom, hsl(${hue} 74% 50%), hsl(${hue} 62% 34%))`;
    wrap.appendChild(glow);
    wrap.appendChild(inner);
    container.appendChild(wrap);
  }
}

/**
 * @param {HTMLElement} target
 * @param {null | { score: number, overall_verdict: string, evaluation: EvalCriterionRow[] }} d
 */
function fillEvaluationDetailDom(target, d) {
  if (!target || !d) return;
  const parseMd = getMarkedParse();
  target.innerHTML = "";

  const hOverall = document.createElement("h4");
  hOverall.textContent = "Overall result";
  target.appendChild(hOverall);
  const overallDiv = document.createElement("div");
  overallDiv.className = "evaluation-detail-section";
  const ov = d.overall_verdict || "";
  if (parseMd) {
    try {
      overallDiv.innerHTML = parseMd(ov);
    } catch (_) {
      overallDiv.textContent = ov;
    }
  } else {
    overallDiv.textContent = ov;
  }
  target.appendChild(overallDiv);

  const hCrit = document.createElement("h4");
  hCrit.textContent = "Criteria";
  target.appendChild(hCrit);

  const table = document.createElement("table");
  table.className = "eval-detail-table";
  const thead = document.createElement("thead");
  const hr = document.createElement("tr");
  for (const label of ["Criteria", "Passed", "Reason"]) {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = label;
    hr.appendChild(th);
  }
  thead.appendChild(hr);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  const rows = Array.isArray(d.evaluation) ? d.evaluation : [];
  if (rows.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 3;
    td.className = "eval-detail-empty";
    td.textContent = "No criteria rows returned.";
    tr.appendChild(td);
    tbody.appendChild(tr);
  } else {
    for (const row of rows) {
      const tr = document.createElement("tr");
      const tdC = document.createElement("td");
      tdC.className = "eval-detail-criteria";
      tdC.textContent = row.criteria ?? "";
      const tdP = document.createElement("td");
      tdP.className = "eval-detail-passed";
      const icon = document.createElement("span");
      const ok = Boolean(row.passed);
      icon.className = ok ? "eval-pass-icon" : "eval-fail-icon";
      icon.setAttribute("role", "img");
      icon.setAttribute("aria-label", ok ? "Passed" : "Failed");
      icon.textContent = ok ? "\u2713" : "\u2717";
      tdP.appendChild(icon);
      const tdR = document.createElement("td");
      tdR.className = "eval-detail-reason";
      tdR.textContent = row.reason ?? "";
      tr.appendChild(tdC);
      tr.appendChild(tdP);
      tr.appendChild(tdR);
      tbody.appendChild(tr);
    }
  }
  table.appendChild(tbody);
  target.appendChild(table);
}

function openEvaluationDetailModal() {
  if (!evaluationDetailModal || !evaluationDetailBody || !lastEvaluationPayload) return;
  fillEvaluationDetailDom(evaluationDetailBody, lastEvaluationPayload);
  evaluationDetailModal.showModal();
}

function syncInlineEvaluationDetail() {
  if (!evaluationInlineDetail) return;
  if (lastEvaluationPayload) {
    evaluationInlineDetail.hidden = false;
    fillEvaluationDetailDom(evaluationInlineDetail, lastEvaluationPayload);
  } else {
    evaluationInlineDetail.innerHTML = "";
    evaluationInlineDetail.hidden = true;
  }
}

function resetEvaluationPanel() {
  lastEvaluationPayload = null;
  if (evaluationPanel) evaluationPanel.hidden = true;
  if (evalScoreBar) evalScoreBar.innerHTML = "";
  if (evalScoreLabel) evalScoreLabel.textContent = "";
  if (evaluationStatus) evaluationStatus.textContent = "";
  syncInlineEvaluationDetail();
}

function clearStoredAgentResult() {
  lastAgentResultPayload = null;
  updateEvaluateUi();
}

function hasAgentOutputForEval() {
  return (
    lastAgentResultPayload != null && agentMessageOnly(lastAgentResultPayload).trim().length > 0
  );
}

function updateEvaluateUi() {
  const hasOut = hasAgentOutputForEval();
  const critOk = Boolean(evaluatorPromptInput?.value?.trim());
  const canRun = hasOut && critOk && !evaluationInFlight;
  if (evaluateButton) evaluateButton.disabled = !canRun;
}

/**
 * @param {Record<string, unknown> | null | undefined} payload
 */
function agentMessageOnly(payload) {
  if (payload == null) return "";
  if (typeof payload === "string") return payload;
  if (typeof payload === "object" && payload !== null && "message" in payload) {
    const m = /** @type {Record<string, unknown>} */ (payload).message;
    if (m == null) return "";
    if (typeof m === "string") return m;
    try {
      return JSON.stringify(m);
    } catch (_) {
      return String(m);
    }
  }
  return "";
}

/**
 * @param {Record<string, unknown> | null | undefined} agentResultPayload
 */
async function runPostEvaluation(agentResultPayload) {
  const crit = evaluatorPromptInput?.value?.trim() ?? "";
  if (!crit) {
    resetEvaluationPanel();
    updateEvaluateUi();
    return;
  }
  if (!evaluationPanel || !evalScoreBar || !evalScoreLabel) return;
  evaluationInFlight = true;
  updateEvaluateUi();
  evaluationPanel.hidden = false;
  if (evaluationStatus) evaluationStatus.textContent = "Scoring…";
  renderEvalScoreBar(evalScoreBar, 0);
  evalScoreLabel.textContent = "…";
  const agentMessage = agentMessageOnly(agentResultPayload);
  try {
    const res = await fetch("/api/evaluate-result", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId ?? "",
        evaluator_prompt: crit,
        agent_message: agentMessage,
      }),
    });
    if (!res.ok) {
      const t = await res.text();
      throw new Error(t || res.statusText);
    }
    /** @type {{ score: number, overall_verdict?: string, evaluation?: { criteria?: string, passed?: boolean, reason?: string }[] }} */
    const data = await res.json();
    const sc = Number(data.score);
    const scoreN = Number.isFinite(sc) ? Math.min(10, Math.max(1, sc)) : 7.5;
    const rawEval = Array.isArray(data.evaluation) ? data.evaluation : [];
    /** @type {EvalCriterionRow[]} */
    const evaluation = rawEval.map((row) => ({
      criteria: String(row?.criteria ?? ""),
      passed: Boolean(row?.passed),
      reason: String(row?.reason ?? ""),
    }));
    lastEvaluationPayload = {
      score: scoreN,
      overall_verdict: String(data.overall_verdict ?? ""),
      evaluation,
    };
    renderEvalScoreBar(evalScoreBar, scoreN);
    evalScoreLabel.textContent = scoreN.toFixed(1);
    if (evaluationStatus) evaluationStatus.textContent = "";
    syncInlineEvaluationDetail();
  } catch (err) {
    lastEvaluationPayload = null;
    renderEvalScoreBar(evalScoreBar, 0);
    evalScoreLabel.textContent = "";
    if (evaluationStatus) evaluationStatus.textContent = `Evaluation failed: ${err}`;
    syncInlineEvaluationDetail();
  } finally {
    evaluationInFlight = false;
    updateEvaluateUi();
  }
}

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

function passesChannelFilters(event) {
  const ch = typeof event.channel === "string" ? event.channel : "runtime";
  return channelEnabled(ch);
}

function setEntryVisible(entry) {
  entry.el.style.display = passesChannelFilters(entry.event) ? "" : "none";
}

function reapplyFilters() {
  for (const e of unifiedEntries) {
    setEntryVisible(e);
  }
}

function depthForTraceEvent(event) {
  const rid = getEventRunId(event);
  if (rid && runFrames.has(rid)) {
    return runFrames.get(rid).depth + 1;
  }
  const p = getPayload(event);
  const pr = p.parent_run_id != null && p.parent_run_id !== "" ? String(p.parent_run_id) : null;
  if (pr && runFrames.has(pr)) {
    return runFrames.get(pr).depth + 1;
  }
  return 0;
}

function applyRowPadding(el, depth) {
  el.style.paddingLeft = `${4 + depth * 12}px`;
}

function clearTraceUi() {
  if (traceFeed) traceFeed.innerHTML = "";
  runFrames.clear();
  unifiedEntries.length = 0;
  if (conversationThread) conversationThread.innerHTML = "";
  removeTypingPlaceholder();
  setAwaitingPrompt(null);
}

/** @type {string | null} */
let awaitingPromptId = null;

function setComposerEnabled(enabled) {
  if (promptInput) promptInput.disabled = !enabled;
  if (sendChatButton) sendChatButton.disabled = !enabled;
}

/**
 * Waiting for user reply: enabled. Running LLM: disabled. Idle: enabled.
 */
function refreshComposerState() {
  if (awaitingPromptId) {
    setComposerEnabled(true);
    return;
  }
  setComposerEnabled(!agentRunInProgress);
}

function setAwaitingPrompt(promptId) {
  awaitingPromptId = promptId;
  if (promptInput) {
    promptInput.placeholder = promptId
      ? "Type your reply and press Send…"
      : "Message…";
  }
  refreshComposerState();
  if (promptId && promptInput) promptInput.focus();
}

function removeTypingPlaceholder() {
  if (typingPlaceholderEl && typingPlaceholderEl.parentNode) {
    typingPlaceholderEl.remove();
  }
  typingPlaceholderEl = null;
}

function showTypingPlaceholder() {
  if (!conversationThread) return;
  removeTypingPlaceholder();
  const wrap = document.createElement("div");
  wrap.className = "conv-msg conv-msg--typing";
  wrap.setAttribute("aria-live", "polite");
  wrap.setAttribute("aria-busy", "true");
  const sp = document.createElement("span");
  sp.className = "typing-spinner";
  const lab = document.createElement("span");
  lab.textContent = "Thinking…";
  wrap.appendChild(sp);
  wrap.appendChild(lab);
  conversationThread.appendChild(wrap);
  typingPlaceholderEl = wrap;
  conversationThread.scrollTop = conversationThread.scrollHeight;
}

function scrollThreadToBottom() {
  if (conversationThread) conversationThread.scrollTop = conversationThread.scrollHeight;
}

/** Grow the composer like ChatGPT; only show a scrollbar when content exceeds the cap. */
function adjustPromptInputHeight() {
  const el = promptInput;
  if (!el) return;
  const maxH = Math.min(200, Math.floor(window.innerHeight * 0.28));
  el.style.maxHeight = `${maxH}px`;
  el.style.height = "auto";
  const sh = el.scrollHeight;
  const h = Math.min(sh, maxH);
  el.style.height = `${h}px`;
  el.style.overflowY = sh > maxH ? "auto" : "hidden";
}

let promptResizeTimer = null;
window.addEventListener("resize", () => {
  if (promptResizeTimer) clearTimeout(promptResizeTimer);
  promptResizeTimer = setTimeout(() => adjustPromptInputHeight(), 100);
});

/**
 * @param {"user" | "assistant" | "error"} role
 * @param {string} text
 * @param {{ markdown?: boolean } | undefined} opts
 */
function appendConversationBubble(role, text, opts) {
  if (!conversationThread || typeof text !== "string") return;
  const wrap = document.createElement("div");
  const roleKey = role === "error" ? "error" : role;
  wrap.className = `conv-msg conv-msg--${roleKey}`;
  const meta = document.createElement("div");
  meta.className = "conv-msg-meta";
  if (role === "user") meta.textContent = "You";
  else if (role === "error") meta.textContent = "Error";
  else meta.textContent = "Agent";
  const body = document.createElement("div");
  body.className = "conv-msg-body";
  const useMd =
    role === "assistant" && opts && typeof opts === "object" && opts.markdown === true;
  if (useMd) {
    const parse = getMarkedParse();
    body.classList.add("conv-msg-body--markdown");
    if (parse) {
      try {
        body.innerHTML = parse(text);
      } catch (_) {
        body.textContent = text;
      }
    } else {
      body.textContent = text;
    }
  } else {
    body.textContent = text;
  }
  wrap.appendChild(meta);
  wrap.appendChild(body);
  conversationThread.appendChild(wrap);
  scrollThreadToBottom();
}

function setAppStatus(message) {
  if (appStatus) appStatus.textContent = message || "";
}

function clearAppStatus() {
  if (appStatus) appStatus.textContent = "";
}

/**
 * @param {Record<string, unknown>} item
 */
function handleOutboxItem(item) {
  removeTypingPlaceholder();
  agentRunInProgress = false;
  refreshComposerState();

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
  appendConversationBubble("assistant", text, { markdown: true });
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
  if (promptInput) promptInput.value = "";
  adjustPromptInputHeight();
  agentRunInProgress = true;
  showTypingPlaceholder();
  refreshComposerState();
}

function appendLogLine(event) {
  const row = document.createElement("div");
  row.className = "log-line trace-feed-row";
  applyRowPadding(row, 0);
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

  traceFeed.appendChild(row);
  const entry = { el: row, event };
  unifiedEntries.push(entry);
  setEntryVisible(entry);
  traceFeed.scrollTop = traceFeed.scrollHeight;
}

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

  const parentDepth =
    parentRunId && runFrames.has(parentRunId) ? runFrames.get(parentRunId).depth : -1;
  const depth = parentDepth >= 0 ? parentDepth + 1 : 0;

  const details = document.createElement("details");
  details.className = "trace-agent-call trace-agent-call--running trace-feed-row";
  applyRowPadding(details, depth);
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
  traceFeed.appendChild(details);

  runFrames.set(runId, {
    details,
    body,
    spinner,
    statusEl,
    labelEl,
    subEl,
    agentName,
    lastStatus: null,
    depth,
  });

  const entry = { el: details, event };
  unifiedEntries.push(entry);
  setEntryVisible(entry);
  traceFeed.scrollTop = traceFeed.scrollHeight;
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
  node.className = "trace-event-row trace-feed-row";
  const d = depthForTraceEvent(event);
  applyRowPadding(node, d);
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
  const node = buildTraceDetails(event);
  traceFeed.appendChild(node);
  const entry = { el: node, event };
  unifiedEntries.push(entry);
  setEntryVisible(entry);
  traceFeed.scrollTop = traceFeed.scrollHeight;
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
    clearAppStatus();
    removeTypingPlaceholder();
    agentRunInProgress = false;
    refreshComposerState();

    const p = /** @type {Record<string, unknown>} */ (msg.payload);
    lastAgentResultPayload = p;
    if (msg.prompt_snapshots && typeof msg.prompt_snapshots === "object") {
      lastPromptSnapshots = normalizePromptSnapshots(
        /** @type {Record<string, unknown>} */ (msg.prompt_snapshots),
      );
    }
    updateEvaluateUi();
    void refreshAgentPromptView();

    let messageText = "";
    let asMarkdown = false;
    if (typeof p.message === "string") {
      messageText = p.message;
      asMarkdown = true;
    } else if (p.message != null) {
      try {
        messageText = JSON.stringify(p.message, null, 2);
      } catch (_) {
        messageText = String(p.message);
      }
    }
    appendConversationBubble("assistant", messageText, { markdown: asMarkdown });
    void runPostEvaluation(p);
  }
  if (msg.type === "error") {
    clearAppStatus();
    removeTypingPlaceholder();
    agentRunInProgress = false;
    refreshComposerState();

    const et = msg.error_type || "Error";
    const lines = [`[${et}] ${msg.message || ""}`];
    if (msg.path) {
      lines.push(`File: ${msg.path}`);
    }
    if (msg.hint) {
      lines.push(msg.hint);
    }
    appendConversationBubble("error", lines.join("\n\n"));
    clearStoredAgentResult();
    resetEvaluationPanel();
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

function getEnvPath() {
  return (envPathInput && envPathInput.value.trim()) || ".env";
}

async function refreshCatalogs() {
  const ep = getEnvPath();
  await loadAgentCatalog(ep);
  await loadInitializerCatalog(ep);
}

async function ensureSessionConnected() {
  const ep = getEnvPath();
  const health = await fetch(`/api/agents?env_path=${encodeURIComponent(ep)}`);
  if (!health.ok) throw new Error("Server unreachable");
  const needNew = !sessionId || !socket || socket.readyState !== WebSocket.OPEN;
  if (needNew) {
    if (sessionId) {
      await fetch(`/api/sessions/${sessionId}/close`, { method: "POST" }).catch(() => {});
    }
    await refreshCatalogs();
    const res = await fetch("/api/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ env_path: ep }),
    });
    if (!res.ok) throw new Error("Failed to create session");
    const data = await res.json();
    sessionId = data.session_id;
    await connectWebSocket();
  }
}

async function loadAgentCatalog(envPath) {
  if (!agentList) return;
  try {
    const res = await fetch(`/api/agents?env_path=${encodeURIComponent(envPath)}`);
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

async function loadInitializerCatalog(envPath) {
  if (!initializerList) return;
  try {
    const res = await fetch(`/api/initializers?env_path=${encodeURIComponent(envPath)}`);
    const data = await res.json();
    initializerList.innerHTML = "";
    for (const id of data.initializers || []) {
      const opt = document.createElement("option");
      opt.value = id;
      initializerList.appendChild(opt);
    }
  } catch (_) {
    /* ignore catalog failures */
  }
}

function applyInitializerResponseFields(data) {
  if (data.template && promptInput && !promptInput.value.trim()) {
    promptInput.value = data.template;
  }
  if (data.evaluator_criteria && evaluatorPromptInput && !evaluatorPromptInput.value.trim()) {
    evaluatorPromptInput.value = data.evaluator_criteria;
  }
  if (data.agent && agentInput && !agentInput.value.trim()) {
    agentInput.value = data.agent;
  }
}

async function maybeApplyInitializerPrompt() {
  if (!initializerInput) return;
  const init = initializerInput.value.trim();
  if (!init) return;
  const needPrompt = promptInput && !promptInput.value.trim();
  const needEval = evaluatorPromptInput && !evaluatorPromptInput.value.trim();
  const needAgent = agentInput && !agentInput.value.trim();
  if (!needPrompt && !needEval && !needAgent) return;
  try {
    const r = await fetch(
      `/api/initializer-template?env_path=${encodeURIComponent(getEnvPath())}&initializer=${encodeURIComponent(init)}`,
    );
    if (!r.ok) return;
    const data = await r.json();
    applyInitializerResponseFields(data);
    updateEvaluateUi();
    adjustPromptInputHeight();
  } catch (_) {
    /* leave fields empty */
  }
}

initializerInput?.addEventListener("change", async () => {
  const raw = initializerInput.value.trim();
  if (!raw) return;
  const needPrompt = promptInput && !promptInput.value.trim();
  const needEval = evaluatorPromptInput && !evaluatorPromptInput.value.trim();
  const needAgent = agentInput && !agentInput.value.trim();
  if (!needPrompt && !needEval && !needAgent) return;
  try {
    const ir = await fetch(
      `/api/initializer-template?env_path=${encodeURIComponent(getEnvPath())}&initializer=${encodeURIComponent(raw)}`,
    );
    if (ir.ok) {
      const data = await ir.json();
      applyInitializerResponseFields(data);
      updateEvaluateUi();
      adjustPromptInputHeight();
      return;
    }
  } catch (_) {
    /* try setup-template for absolute paths */
  }
  try {
    const res = await fetch(`/api/setup-template?path=${encodeURIComponent(raw)}`);
    const data = await res.json();
    applyInitializerResponseFields(data);
    updateEvaluateUi();
    adjustPromptInputHeight();
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
    const dr = await fetch("/api/evaluator-defaults");
    const defs = await dr.json();
    if (envPathInput) envPathInput.value = defs.env_path || ".env";
    if (defs.agent && agentInput) agentInput.value = defs.agent;
    if (defs.initializer && initializerInput) initializerInput.value = defs.initializer;
    await refreshCatalogs();
    await ensureSessionConnected();
    refreshComposerState();
    adjustPromptInputHeight();
  } catch (err) {
    setAppStatus(`Failed to start session: ${err}`);
  }
}

let envRefreshTimer = null;
envPathInput?.addEventListener("input", () => {
  if (envRefreshTimer) clearTimeout(envRefreshTimer);
  envRefreshTimer = setTimeout(() => {
    refreshCatalogs().catch(() => {});
  }, 400);
});
envPathInput?.addEventListener("change", () => {
  refreshCatalogs().catch(() => {});
});

async function sendChatOrRun() {
  if (awaitingPromptId) {
    const text = promptInput ? promptInput.value : "";
    try {
      await postUserInputHttp(text);
    } catch (err) {
      setAppStatus(`Reply failed: ${err}`);
    }
    return;
  }

  try {
    await ensureSessionConnected();
    await maybeApplyInitializerPrompt();
  } catch (err) {
    setAppStatus(`Cannot reach server: ${err}`);
    return;
  }
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    setAppStatus("No WebSocket after reconnect.");
    return;
  }
  const agentId = agentInput.value.trim() || "root";
  const initializerPath = initializerInput ? initializerInput.value.trim() : "";
  const promptText = promptInput ? promptInput.value : "";
  clearStoredAgentResult();
  resetEvaluationPanel();
  clearTraceUi();
  appendConversationBubble("user", promptText || "(empty prompt)");
  agentRunInProgress = true;
  refreshComposerState();
  showTypingPlaceholder();
  setAppStatus("Running…");
  socket.send(
    JSON.stringify({
      type: "run",
      agent_id: agentId,
      prompt: promptText,
      initializer: initializerPath || null,
    }),
  );
  if (promptInput) {
    promptInput.value = "";
    adjustPromptInputHeight();
  }
}

sendChatButton?.addEventListener("click", () => {
  void sendChatOrRun();
});

promptInput?.addEventListener("input", () => {
  adjustPromptInputHeight();
});

promptInput?.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    void sendChatOrRun();
  }
});

evaluateButton?.addEventListener("click", () => {
  if (!lastAgentResultPayload || evaluateButton?.disabled) return;
  void runPostEvaluation(lastAgentResultPayload);
});

evaluatorPromptInput?.addEventListener("input", () => {
  updateEvaluateUi();
});

evaluationScoreTrigger?.addEventListener("click", () => {
  if (lastEvaluationPayload) openEvaluationDetailModal();
});

evaluationScoreTrigger?.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    if (lastEvaluationPayload) openEvaluationDetailModal();
  }
});

function setActiveTab(which) {
  const tabs = [
    { id: "chat", btn: tabChat, panel: panelChat },
    { id: "evaluation", btn: tabEvaluation, panel: panelEvaluation },
    { id: "agent", btn: tabAgent, panel: panelAgent },
  ];
  for (const t of tabs) {
    const active = t.id === which;
    if (t.btn) {
      t.btn.classList.toggle("main-tab--active", active);
      t.btn.setAttribute("aria-selected", active ? "true" : "false");
    }
    if (t.panel) {
      t.panel.classList.toggle("tab-panel--active", active);
      t.panel.hidden = !active;
    }
  }
  if (which === "agent") {
    void refreshAgentPromptView();
  }
}

tabChat?.addEventListener("click", () => setActiveTab("chat"));
tabEvaluation?.addEventListener("click", () => setActiveTab("evaluation"));
tabAgent?.addEventListener("click", () => setActiveTab("agent"));

let agentPromptMode = "raw";
let cachedRawSystemPrompt = "";

/**
 * @param {Record<string, unknown>} ps
 */
function normalizePromptSnapshots(ps) {
  const system_prompt = String(ps.system_prompt ?? "");
  const user_prompt = String(ps.user_prompt ?? "");
  let user_messages = ps.user_messages;
  if (!Array.isArray(user_messages)) {
    user_messages = user_prompt ? [user_prompt] : [];
  } else {
    user_messages = user_messages.map((x) => String(x));
  }
  return {
    system_prompt,
    user_prompt,
    instruction_entered: String(ps.instruction_entered ?? ""),
    user_messages,
  };
}

function syncUserPrimaryToggleUi() {
  const entered = userPrimaryMode === "entered";
  agentUserEnteredBtn?.classList.toggle("agent-toggle--active", entered);
  agentUserActualBtn?.classList.toggle("agent-toggle--active", !entered);
}

function renderAgentUserSection() {
  if (!agentUserSection || !agentUserPrimaryDisplay || !agentUserExtra) return;
  if (!lastPromptSnapshots) {
    agentUserSection.hidden = true;
    return;
  }
  agentUserSection.hidden = false;
  const entered = lastPromptSnapshots.instruction_entered;
  const userMessages = lastPromptSnapshots.user_messages;
  const firstSent = userMessages[0] ?? "";

  let primaryText = userPrimaryMode === "entered" ? entered : firstSent;
  agentUserPrimaryDisplay.textContent = primaryText;
  const showUserEmpty = !String(primaryText).trim();
  if (agentUserEmpty) {
    agentUserEmpty.hidden = !showUserEmpty;
  }

  agentUserExtra.innerHTML = "";
  const rest = userMessages.slice(1);
  if (agentUserExtraHeading) {
    agentUserExtraHeading.hidden = rest.length === 0;
  }
  for (let i = 0; i < rest.length; i++) {
    const cap = document.createElement("div");
    cap.className = "agent-user-additional-cap";
    cap.textContent = `Additional user message ${i + 2}`;
    const sub = document.createElement("pre");
    sub.className = "agent-prompt-display agent-user-additional";
    sub.textContent = rest[i];
    agentUserExtra.appendChild(cap);
    agentUserExtra.appendChild(sub);
  }
  syncUserPrimaryToggleUi();
}

async function fetchRawSystemPrompt() {
  const aid = agentInput?.value.trim() || "root";
  const ep = getEnvPath();
  const res = await fetch(
    `/api/agent-system-prompt?env_path=${encodeURIComponent(ep)}&agent_id=${encodeURIComponent(aid)}`,
  );
  if (!res.ok) {
    const t = await res.text();
    throw new Error(t || res.statusText);
  }
  const data = await res.json();
  cachedRawSystemPrompt = String(data.system_prompt ?? "");
}

async function ensurePromptSnapshotsFromApi() {
  if (lastPromptSnapshots || !sessionId) return;
  try {
    const r = await fetch(`/api/sessions/${sessionId}/last-prompts`);
    if (!r.ok) return;
    const d = await r.json();
    lastPromptSnapshots = normalizePromptSnapshots(/** @type {Record<string, unknown>} */ (d));
  } catch (_) {
    /* ignore */
  }
}

async function refreshAgentPromptView() {
  if (!agentPromptDisplay || !agentPromptEmpty) return;
  await ensurePromptSnapshotsFromApi();
  if (agentPromptMode === "expanded") {
    agentPromptRaw?.classList.remove("agent-toggle--active");
    agentPromptExpanded?.classList.add("agent-toggle--active");
    const sys = lastPromptSnapshots?.system_prompt;
    if (sys && sys.length > 0) {
      agentPromptDisplay.textContent = sys;
      agentPromptEmpty.classList.remove("is-visible");
    } else {
      agentPromptDisplay.textContent = "";
      agentPromptEmpty.classList.add("is-visible");
    }
  } else {
    agentPromptRaw?.classList.add("agent-toggle--active");
    agentPromptExpanded?.classList.remove("agent-toggle--active");
    agentPromptEmpty.classList.remove("is-visible");
    try {
      await fetchRawSystemPrompt();
      agentPromptDisplay.textContent = cachedRawSystemPrompt;
    } catch (err) {
      agentPromptDisplay.textContent = `Could not load agent: ${err}`;
    }
  }
  renderAgentUserSection();
}

agentPromptRaw?.addEventListener("click", () => {
  agentPromptMode = "raw";
  void refreshAgentPromptView();
});
agentPromptExpanded?.addEventListener("click", () => {
  agentPromptMode = "expanded";
  void refreshAgentPromptView();
});

agentInput?.addEventListener("change", () => {
  if (agentPromptMode === "raw") void refreshAgentPromptView();
});

agentUserEnteredBtn?.addEventListener("click", () => {
  userPrimaryMode = "entered";
  renderAgentUserSection();
});
agentUserActualBtn?.addEventListener("click", () => {
  userPrimaryMode = "actual";
  renderAgentUserSection();
});

modeSelect?.addEventListener("change", () => {
  const v = modeSelect.value;
  if (testsetHint) testsetHint.hidden = v !== "testset";
});
if (modeSelect && testsetHint) testsetHint.hidden = modeSelect.value !== "testset";

initSession().catch((err) => {
  setAppStatus(`Failed to start session: ${err}`);
});
