const traceFeed = document.getElementById("trace-feed");
const appStatus = document.getElementById("app-status");
const promptInput = document.getElementById("prompt-input");
const sendChatButton = document.getElementById("send-chat-button");
const envPathInput = document.getElementById("env-path");
const agentInput = document.getElementById("agent-select");
const initializerInput = document.getElementById("initializer-select");
const agentModelOverrideInput = document.getElementById("agent-model-override");
const agentModelOverrideList = document.getElementById("agent-model-override-list");
const agentModelOverrideScopeSelect = document.getElementById("agent-model-override-scope");
const agentList = document.getElementById("agent-list");
const initializerList = document.getElementById("initializer-list");
const channelToggles = document.getElementById("channel-toggles");
const traceLogLevel = document.getElementById("trace-log-level");
const conversationThread = document.getElementById("conversation-thread");
const caseListSection = document.getElementById("case-list-section");
const caseList = document.getElementById("case-list");
const runAllCasesBtn = document.getElementById("run-all-cases-btn");
const runAllCasesModeBtn = document.getElementById("run-all-cases-mode-btn");
const caseRunModeLabel = document.getElementById("case-run-mode-label");
const batchProgress = document.getElementById("batch-progress");
const evaluatorPromptInput = document.getElementById("evaluator-prompt-input");
const evaluationPanel = document.getElementById("evaluation-panel");
const evalScoreBar = document.getElementById("eval-score-bar");
const evalScoreLabel = document.getElementById("eval-score-label");
const evaluationStatus = document.getElementById("evaluation-status");
const evaluationScoreTrigger = document.getElementById("evaluation-score-trigger");
const evaluationInlineDetail = document.getElementById("evaluation-inline-detail");
const evaluateButton = document.getElementById("evaluate-button");
const evalSingleSections = document.getElementById("eval-single-sections");
const evalLlmSection = document.getElementById("eval-llm-section");
const evalCodeSection = document.getElementById("eval-code-section");
const evalLlmScoreLabel = document.getElementById("eval-llm-score-label");
const evalCodeScoreBar = document.getElementById("eval-code-score-bar");
const evalCodeScoreLabel = document.getElementById("eval-code-score-label");
const evalLlmInlineDetail = document.getElementById("eval-llm-inline-detail");
const evalCodeInlineDetail = document.getElementById("eval-code-inline-detail");
const batchSummaryPanel = document.getElementById("batch-summary-panel");
const batchSummaryTableBody = document.getElementById("batch-summary-table-body");
const runUsageSummary = document.getElementById("run-usage-summary");
const batchAvgScoreBar = document.getElementById("batch-avg-score-bar");
const batchAvgScoreLabel = document.getElementById("batch-avg-score-label");
const evaluationDetailModal = document.getElementById("evaluation-detail-modal");
const evaluationDetailBody = document.getElementById("evaluation-detail-body");
const evaluationDetailTitle = document.getElementById("evaluation-detail-title");
const evaluationDetailClose = document.getElementById("evaluation-detail-close");
const tabChat = document.getElementById("tab-chat");
const tabEvaluation = document.getElementById("tab-evaluation");
const tabAgent = document.getElementById("tab-agent");
const tabFlow = document.getElementById("tab-flow");
const panelChat = document.getElementById("tab-panel-chat");
const panelEvaluation = document.getElementById("tab-panel-evaluation");
const panelAgent = document.getElementById("tab-panel-agent");
const panelFlow = document.getElementById("tab-panel-flow");
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
/** @typedef {{ title?: string, average_score?: number | null, error?: string, detail?: Record<string, unknown> | null }} BatchSummaryRow */

/** @type {null | { score: number, overall_verdict: string, evaluation: EvalCriterionRow[] }} */
let lastEvaluationPayload = null;

/** Last agent `result` payload from the server. @type {Record<string, unknown> | null} */
let lastAgentResultPayload = null;
/** @type {Record<string, unknown> | null} */
let lastUsageSummary = null;
/** @type {Map<string, Record<string, unknown>>} */
let liveRunUsage = new Map();

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

/** When set, a ``result`` / terminal ``error`` resolves this instead of running manual post-eval. */
/** @type {null | { resolve: (v: unknown) => void, reject: (e: unknown) => void }} */
let pendingAgentRun = null;

/** @type {{ index: number, title: string, prompt: string, criteria: string, has_code_evaluator: boolean }[]} */
let loadedCases = [];

/** API hint when cases are empty. */
let lastCaseListHint = "";

/** @type {"standard" | "no_callbacks"} */
let caseRunMode = "standard";
let pendingDefaultAgentModelOverride = "";
let pendingDefaultAgentModelOverrideScope = "root_only";

/** @type {HTMLElement | null} */
let caseRunMenuAnchor = null;

const CASE_RUN_HOLD_MS = 450;

/** @type {HTMLElement | null} */
let typingPlaceholderEl = null;

/** @type {Map<string, { details: HTMLElement, body: HTMLElement, spinner: HTMLElement, statusEl: HTMLElement, labelEl: HTMLElement, subEl: HTMLElement, agentName: string, lastStatus: string | null, depth: number, parentRunId: string | null, batchId: string | null }>} */
const runFrames = new Map();

/** @type {Map<string, { wrap: HTMLElement, childrenEl: HTMLElement, label: HTMLElement, mode: string, parentRunId: string, childRunIds: string[] }>} */
const batchFrames = new Map();

/** Maps parent run_id → currently active batch_id for that run. @type {Map<string, string>} */
const activeBatchForRun = new Map();

/** @type {{ el: HTMLElement, event: Record<string, unknown> }[]} */
const unifiedEntries = [];

const TRACE_STRING_PREVIEW_CHARS = 90;
const LEVEL_ORDER = { debug: 10, info: 20, warning: 30, error: 40 };
const {
  adjustPromptInputHeight: adjustPromptInputHeightUi,
  appendConversationBubble: appendConversationBubbleUi,
  clearAppStatus: clearAppStatusUi,
  copyTextToClipboard: copyTextToClipboardUi,
  removeTypingPlaceholder: removeTypingPlaceholderUi,
  setAppStatus: setAppStatusUi,
  setComposerEnabled: setComposerEnabledUi,
  showTypingPlaceholder: showTypingPlaceholderUi,
} = window.ConversationUi;
const { fillEvaluationDetailDom, renderEvalScoreBar } = window.EvaluationRendering;
const {
  displayBatchSummary: displayBatchSummaryUi,
  displayCaseEvaluation: displayCaseEvaluationUi,
  hideCaseEvalSubpanels: hideCaseEvalSubpanelsUi,
  openBatchCaseEvaluationModal: openBatchCaseEvaluationModalUi,
  openEvaluationDetailModal: openEvaluationDetailModalUi,
  syncInlineEvaluationDetail: syncInlineEvaluationDetailUi,
} = window.EvaluationUi;
const {
  applyRowPadding,
  classifyEventKind: classifyEventKindPrimitive,
  depthForTraceEvent,
  getContext,
  getEventRunId,
  getPayload,
  passesChannelFilters,
  passesLevelFilter,
  reapplyFilters: reapplyFiltersForEntries,
  renderPayloadTree: renderPayloadTreePrimitive,
  selectedTraceLogLevel: selectedTraceLogLevelPrimitive,
  setEntryVisible: setEntryVisiblePrimitive,
} = window.TracePrimitives;
const {
  buildUsageLinesBlock,
  formatLlmUsageLine,
  formatRunUsageLines,
  normalizeUsageTotals,
  renderAgentBubbleUsage,
  renderSessionUsageSummary: renderSessionUsageSummaryForTarget,
} = window.UsageRendering;

function wireEvaluationDetailModal() {
  if (!evaluationDetailModal || !evaluationDetailClose) return;
  const resetTitle = () => {
    if (evaluationDetailTitle) evaluationDetailTitle.textContent = "Evaluation details";
  };
  evaluationDetailClose.addEventListener("click", () => {
    evaluationDetailModal.close();
    resetTitle();
  });
  evaluationDetailModal.addEventListener("click", (e) => {
    if (e.target === evaluationDetailModal) {
      evaluationDetailModal.close();
      resetTitle();
    }
  });
}

wireEvaluationDetailModal();

function getEvaluationUiDeps() {
  return {
    batchAvgScoreBar,
    batchAvgScoreLabel,
    batchSummaryPanel,
    batchSummaryTableBody,
    evalCodeInlineDetail,
    evalCodeScoreBar,
    evalCodeScoreLabel,
    evalCodeSection,
    evalLlmInlineDetail,
    evalLlmScoreLabel,
    evalLlmSection,
    evalScoreBar,
    evalScoreLabel,
    evalSingleSections,
    evaluationDetailBody,
    evaluationDetailModal,
    evaluationDetailTitle,
    evaluationInlineDetail,
    evaluationPanel,
    evaluationStatus,
    fillEvaluationDetailDom,
    hideCaseEvalSubpanels,
    normalizeUsageTotals,
    openBatchCaseEvaluationModal,
    renderEvalScoreBar,
    setLastEvaluationPayload: (payload) => {
      lastEvaluationPayload = payload;
    },
    updateEvaluateUi,
  };
}

function getTracePrimitiveDeps() {
  return {
    levelOrder: LEVEL_ORDER,
    openTraceDetailModal,
    traceLogLevelEl: traceLogLevel,
    traceStringPreviewChars: TRACE_STRING_PREVIEW_CHARS,
  };
}

function renderSessionUsageSummary(summary) {
  renderSessionUsageSummaryForTarget(runUsageSummary, summary);
}

function renderTraceUsagePanel(summary, selectedRunId = null) {
  void summary;
  void selectedRunId;
}

function syncTraceRunUsage(runId) {
  if (!runId) return;
  const fr = runFrames.get(runId);
  if (!fr || !fr.usageEl) return;
  const runMap = lastUsageSummary && typeof lastUsageSummary === "object" && lastUsageSummary.runs && typeof lastUsageSummary.runs === "object"
    ? lastUsageSummary.runs
    : {};
  const runEntry = liveRunUsage.get(runId) || runMap[runId];
  const lines = formatRunUsageLines(runEntry);
  fr.usageEl.innerHTML = "";
  fr.usageEl.hidden = lines.length === 0;
  if (lines.length > 0) {
    fr.usageEl.appendChild(buildUsageLinesBlock(lines, "trace-agent-call-usage"));
  }
}

function syncAllTraceRunUsage() {
  for (const runId of runFrames.keys()) {
    syncTraceRunUsage(runId);
  }
}

function absorbUsageSummary(summary) {
  if (!summary || typeof summary !== "object") return;
  const runMap = summary.runs && typeof summary.runs === "object" ? summary.runs : {};
  for (const [runId, runEntry] of Object.entries(runMap)) {
    if (runEntry && typeof runEntry === "object") {
      liveRunUsage.set(runId, /** @type {Record<string, unknown>} */ (runEntry));
    }
  }
}

function openEvaluationDetailModal() {
  openEvaluationDetailModalUi(lastEvaluationPayload, getEvaluationUiDeps());
}

function syncInlineEvaluationDetail() {
  syncInlineEvaluationDetailUi(lastEvaluationPayload, getEvaluationUiDeps());
}

function resetEvaluationPanel() {
  lastEvaluationPayload = null;
  hideCaseEvalSubpanels();
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
  return lastAgentResultPayload != null;
}

function updateEvaluateUi() {
  const hasOut = hasAgentOutputForEval();
  const critOk = Boolean(evaluatorPromptInput?.value?.trim());
  const canRun = hasOut && critOk && !evaluationInFlight;
  if (evaluateButton) {
    evaluateButton.disabled = !canRun && !evaluationInFlight;
    evaluateButton.classList.toggle("evaluate-button--running", evaluationInFlight);
    evaluateButton.textContent = evaluationInFlight ? "Evaluating…" : "Evaluate";
  }
}

async function runPostEvaluation() {
  const crit = evaluatorPromptInput?.value?.trim() ?? "";
  if (!crit) {
    resetEvaluationPanel();
    updateEvaluateUi();
    return;
  }
  if (!evaluationPanel || !evalScoreBar || !evalScoreLabel) return;
  hideCaseEvalSubpanels();
  evaluationInFlight = true;
  updateEvaluateUi();
  evaluationPanel.hidden = false;
  if (evaluationStatus) evaluationStatus.textContent = "Scoring…";
  renderEvalScoreBar(evalScoreBar, 0);
  evalScoreLabel.textContent = "…";
  try {
    const res = await fetch("/api/evaluate-result", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId ?? "",
        evaluator_prompt: crit,
        log_level: selectedTraceLogLevel(),
      }),
    });
    if (!res.ok) {
      const t = await res.text();
      throw new Error(t || res.statusText);
    }
    /** @type {{ score: number, overall_verdict?: string, evaluation?: { criteria?: string, passed?: boolean, reason?: string }[] }} */
    const data = await res.json();
    const sc = Number(data.score);
    const scoreN = Number.isFinite(sc) ? Math.min(10, Math.max(0, sc)) : 7.5;
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


function hideCaseEvalSubpanels() {
  hideCaseEvalSubpanelsUi(getEvaluationUiDeps());
}

function closeCaseRunMenu() {
  const menu = document.getElementById("case-run-menu");
  if (!menu) return;
  menu.hidden = true;
  menu.setAttribute("aria-hidden", "true");
  caseRunMenuAnchor = null;
}

/**
 * @param {HTMLElement} anchorEl
 */
function openCaseRunMenu(anchorEl) {
  const menu = document.getElementById("case-run-menu");
  if (!menu) return;
  caseRunMenuAnchor = anchorEl;
  const r = anchorEl.getBoundingClientRect();
  const mw = 220;
  let left = r.left;
  if (left + mw > window.innerWidth - 8) left = window.innerWidth - mw - 8;
  if (left < 8) left = 8;
  menu.style.position = "fixed";
  menu.style.left = `${left}px`;
  menu.style.top = `${r.bottom + 4}px`;
  menu.style.minWidth = `${Math.max(r.width, 160)}px`;
  menu.hidden = false;
  menu.setAttribute("aria-hidden", "false");
  updateCaseRunMenuSelection();
}

function updateCaseRunMenuSelection() {
  const menu = document.getElementById("case-run-menu");
  if (!menu) return;
  for (const item of menu.querySelectorAll(".case-run-menu-item[data-mode]")) {
    const mode = item.getAttribute("data-mode");
    const on = mode === caseRunMode;
    item.classList.toggle("case-run-menu-item--selected", on);
    item.setAttribute("aria-checked", on ? "true" : "false");
  }
  const modeLabel = caseRunMode === "no_callbacks" ? "No callbacks" : "Standard";
  const modeIcon = caseRunMode === "no_callbacks" ? "⚡" : "◎";
  if (runAllCasesBtn) {
    runAllCasesBtn.title = `Run all (${modeLabel}). Click to run; hold to choose mode.`;
  }
  if (runAllCasesModeBtn) {
    runAllCasesModeBtn.title = `Choose mode (current: ${modeLabel})`;
  }
  if (caseRunModeLabel) {
    caseRunModeLabel.textContent = modeIcon;
    caseRunModeLabel.title = `Run mode: ${modeLabel}. Use ▾ to change.`;
  }
}

function initCaseRunMenu() {
  const menu = document.getElementById("case-run-menu");
  if (!menu) return;
  for (const item of menu.querySelectorAll(".case-run-menu-item[data-mode]")) {
    item.addEventListener("click", (e) => {
      e.stopPropagation();
      const m = item.getAttribute("data-mode");
      if (m === "standard" || m === "no_callbacks") {
        caseRunMode = m;
        updateCaseRunMenuSelection();
      }
      closeCaseRunMenu();
    });
  }
  document.addEventListener(
    "pointerdown",
    (e) => {
      if (!menu || menu.hidden) return;
      if (menu.contains(/** @type {Node} */ (e.target))) return;
      closeCaseRunMenu();
    },
    true,
  );
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeCaseRunMenu();
  });
  runAllCasesModeBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    if (runAllCasesModeBtn.disabled) return;
    if (!menu.hidden) {
      closeCaseRunMenu();
      return;
    }
    openCaseRunMenu(runAllCasesModeBtn);
  });
  updateCaseRunMenuSelection();
}

/**
 * Short click runs; hold opens the mode menu.
 * @param {HTMLButtonElement} btn
 * @param {() => void} onShortRun
 */
function bindHoldShortRun(btn, onShortRun) {
  let holdTimer = null;
  let menuOpenedThisGesture = false;

  btn.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    menuOpenedThisGesture = false;
    holdTimer = window.setTimeout(() => {
      holdTimer = null;
      menuOpenedThisGesture = true;
      openCaseRunMenu(btn);
    }, CASE_RUN_HOLD_MS);
  });

  const clearHold = () => {
    if (holdTimer != null) {
      window.clearTimeout(holdTimer);
      holdTimer = null;
    }
  };

  btn.addEventListener("pointerup", (e) => {
    if (e.button !== 0) return;
    clearHold();
    if (!menuOpenedThisGesture) {
      closeCaseRunMenu();
      onShortRun();
    }
    menuOpenedThisGesture = false;
  });

  btn.addEventListener("pointerleave", () => {
    clearHold();
  });

  btn.addEventListener("pointercancel", () => {
    clearHold();
    menuOpenedThisGesture = false;
  });
}

function caseControlsDisabled(disabled) {
  if (runAllCasesBtn) runAllCasesBtn.disabled = disabled;
  if (runAllCasesModeBtn) runAllCasesModeBtn.disabled = disabled;
  if (caseList) {
    for (const btn of caseList.querySelectorAll(".run-case-btn")) {
      /** @type {HTMLButtonElement} */ (btn).disabled = disabled;
    }
  }
}

function setRunCaseButtonBusy(caseIndex, busy) {
  if (!caseList) return;
  const btn = caseList.querySelector(`.run-case-btn[data-case-index="${caseIndex}"]`);
  if (!btn) return;
  btn.classList.toggle("run-case-btn--running", busy);
  btn.setAttribute("aria-busy", busy ? "true" : "false");
  btn.textContent = busy ? "…" : "▶";
}

async function recreateSessionForNewRun() {
  detachWebSocket();
  if (sessionId) {
    await fetch(`/api/sessions/${sessionId}/close`, { method: "POST" }).catch(() => {});
    sessionId = null;
  }
  await ensureSessionConnected();
}

function waitForAgentRunResult() {
  return new Promise((resolve, reject) => {
    pendingAgentRun = { resolve, reject };
  });
}

async function refreshInitializerCases() {
  const hintEl = document.getElementById("case-list-hint");
  const init = initializerInput?.value.trim();
  lastCaseListHint = "";
  if (!init || !caseListSection) {
    loadedCases = [];
    caseListSection.hidden = true;
    if (hintEl) {
      hintEl.textContent = "";
      hintEl.hidden = true;
    }
    return;
  }
  try {
    const r = await fetch(
      `/api/initializer-cases?env_path=${encodeURIComponent(getEnvPath())}&initializer=${encodeURIComponent(init)}`,
    );
    if (!r.ok) {
      loadedCases = [];
      caseListSection.hidden = true;
      if (hintEl) {
        hintEl.textContent = "";
        hintEl.hidden = true;
      }
      return;
    }
    const data = await r.json();
    loadedCases = Array.isArray(data.cases) ? data.cases : [];
    lastCaseListHint = typeof data.hint === "string" ? data.hint : "";
    renderCaseList();
  } catch (_) {
    loadedCases = [];
    lastCaseListHint = "";
    caseListSection.hidden = true;
    if (hintEl) {
      hintEl.textContent = "";
      hintEl.hidden = true;
    }
  }
}

function renderCaseList() {
  if (!caseList || !caseListSection) return;
  const hintEl = document.getElementById("case-list-hint");
  caseList.innerHTML = "";
  if (loadedCases.length === 0) {
    if (hintEl) {
      hintEl.textContent = lastCaseListHint || "";
      hintEl.hidden = !lastCaseListHint;
    }
    caseListSection.hidden = !lastCaseListHint;
    if (runAllCasesBtn) runAllCasesBtn.disabled = true;
    if (runAllCasesModeBtn) runAllCasesModeBtn.disabled = true;
    return;
  }
  if (hintEl) {
    hintEl.textContent = "";
    hintEl.hidden = true;
  }
  caseListSection.hidden = false;
  if (runAllCasesBtn) runAllCasesBtn.disabled = false;
  if (runAllCasesModeBtn) runAllCasesModeBtn.disabled = false;
  for (const c of loadedCases) {
    const li = document.createElement("li");
    li.className = "case-list-item";
    const t = document.createElement("span");
    t.className = "case-list-item-title";
    t.textContent = c.title || `Case ${c.index}`;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "run-case-btn";
    btn.dataset.caseIndex = String(c.index);
    btn.setAttribute("aria-label", `Run ${c.title || "case"}`);
    btn.textContent = "▶";
    const idx = c.index;
    bindHoldShortRun(btn, () => void playCase(idx));
    li.appendChild(t);
    li.appendChild(btn);
    caseList.appendChild(li);
  }
}

/**
 * @param {Record<string, unknown>} data
 */
function displayCaseEvaluation(data) {
  displayCaseEvaluationUi(data, getEvaluationUiDeps());
}

/**
 * @param {BatchSummaryRow[]} rows
 */
function displayBatchSummary(rows) {
  displayBatchSummaryUi(rows, getEvaluationUiDeps());
}

/**
 * @param {number} caseIndex
 * @param {{ batch?: boolean }} [opts]
 */
async function playCase(caseIndex, opts = {}) {
  const batch = Boolean(opts.batch);
  const init = initializerInput?.value.trim();
  if (!init || !loadedCases.length) return;
  const c = loadedCases.find((x) => x.index === caseIndex);
  if (!c) return;
  if (!batch) caseControlsDisabled(true);
  if (!batch) setRunCaseButtonBusy(caseIndex, true);
  evaluationInFlight = true;
  try {
    await ensureSessionConnected();
    if (!batch) {
      clearStoredAgentResult();
      resetEvaluationPanel();
    }
    await recreateSessionForNewRun();
    clearTraceUi();
    const runPrompt = c.prompt || "";
    appendConversationBubble("user", runPrompt || "(empty prompt)");
    agentRunInProgress = true;
    refreshComposerState();
    showTypingPlaceholder();
    setAppStatus("Running…");
    if (!socket || socket.readyState !== WebSocket.OPEN) throw new Error("No WebSocket");
    const pRun = waitForAgentRunResult();
    socket.send(
      JSON.stringify({
        type: "run",
        agent_id: agentInput?.value.trim() || "root",
        prompt: runPrompt,
        initializer: init,
        case_run_mode: caseRunMode,
        agent_model_override: getAgentModelOverride(),
        agent_model_override_scope: getAgentModelOverrideScope(),
      }),
    );
    await pRun;
    const res = await fetch("/api/evaluate-case", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId ?? "",
        initializer: init,
        case_index: caseIndex,
        log_level: selectedTraceLogLevel(),
      }),
    });
    if (!res.ok) {
      const t = await res.text();
      throw new Error(t || res.statusText);
    }
    const data = await res.json();
    if (!batch) {
      displayCaseEvaluation(data);
      setActiveTab("evaluation");
    }
    return data;
  } catch (err) {
    if (!batch && evaluationStatus) evaluationStatus.textContent = String(err);
    if (!batch) setAppStatus(String(err));
    throw err;
  } finally {
    agentRunInProgress = false;
    evaluationInFlight = false;
    removeTypingPlaceholder();
    refreshComposerState();
    if (!batch) caseControlsDisabled(false);
    if (!batch) setRunCaseButtonBusy(caseIndex, false);
    if (!batch) clearAppStatus();
  }
}

async function runAllCasesPlay() {
  const init = initializerInput?.value.trim();
  if (!init || !loadedCases.length) return;
  clearStoredAgentResult();
  resetEvaluationPanel();
  caseControlsDisabled(true);
  if (batchProgress) {
    batchProgress.hidden = false;
    batchProgress.textContent = "Batch running…";
  }
  /** @type {BatchSummaryRow[]} */
  const summaryRows = [];
  try {
    await ensureSessionConnected();
    const res = await fetch("/api/evaluate-batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId ?? "",
        initializer: init,
        log_level: selectedTraceLogLevel(),
        case_run_mode: caseRunMode,
        agent_model_override: getAgentModelOverride(),
        agent_model_override_scope: getAgentModelOverrideScope(),
      }),
    });
    if (!res.ok) {
      const t = await res.text();
      throw new Error(t || res.statusText);
    }
    const reader = res.body?.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    if (reader) {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.trim()) continue;
          try {
            const row = /** @type {Record<string, unknown>} */ (JSON.parse(line));
            const idx = typeof row.case_index === "number" ? row.case_index : -1;
            const caseEntry = loadedCases.find((x) => x.index === idx);
            const title = String(row.title || caseEntry?.title || `Case ${idx}`);
            if (batchProgress) {
              batchProgress.textContent = `Batch run — ${summaryRows.length + 1} / ${loadedCases.length}: ${title}`;
            }
            if (row.error) {
              summaryRows.push({
                title,
                average_score: null,
                error: String(row.error),
                detail: { __batchError: true, message: String(row.error) },
              });
            } else {
              const av = typeof row.average_score === "number" ? row.average_score : NaN;
              summaryRows.push({
                title,
                average_score: Number.isFinite(av) ? av : null,
                detail: row,
              });
            }
          } catch (_) {
            // skip malformed NDJSON line
          }
        }
      }
    }
    displayBatchSummary(summaryRows);
    setActiveTab("evaluation");
  } catch (err) {
    if (evaluationStatus) evaluationStatus.textContent = String(err);
  } finally {
    if (batchProgress) batchProgress.hidden = true;
    caseControlsDisabled(false);
    clearAppStatus();
  }
}

function renderPayloadTree(payload) {
  return renderPayloadTreePrimitive(payload, getTracePrimitiveDeps());
}

function selectedTraceLogLevel() {
  return selectedTraceLogLevelPrimitive(traceLogLevel, LEVEL_ORDER);
}

function setEntryVisible(entry) {
  setEntryVisiblePrimitive(entry, getTracePrimitiveDeps());
}

function reapplyFilters() {
  reapplyFiltersForEntries(unifiedEntries, getTracePrimitiveDeps());
}

function classifyEventKind(kind) {
  return classifyEventKindPrimitive(kind);
}

function clearTraceUi() {
  if (traceFeed) traceFeed.innerHTML = "";
  runFrames.clear();
  batchFrames.clear();
  activeBatchForRun.clear();
  unifiedEntries.length = 0;
  if (conversationThread) conversationThread.innerHTML = "";
  removeTypingPlaceholder();
  setAwaitingPrompt(null);
  rebuildFlowTab();
}

/** @type {string | null} */
let awaitingPromptId = null;

function setComposerEnabled(enabled) {
  setComposerEnabledUi(promptInput, sendChatButton, enabled);
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
  typingPlaceholderEl = removeTypingPlaceholderUi(typingPlaceholderEl);
}

function showTypingPlaceholder() {
  typingPlaceholderEl = showTypingPlaceholderUi(typingPlaceholderEl, conversationThread);
}

/** Grow the composer like ChatGPT; only show a scrollbar when content exceeds the cap. */
function adjustPromptInputHeight() {
  adjustPromptInputHeightUi(promptInput);
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
  appendConversationBubbleUi(conversationThread, role, text, opts, {
    copyTextToClipboard,
    getMarkedParse,
  });
}

function setAppStatus(message) {
  setAppStatusUi(appStatus, message);
}

function clearAppStatus() {
  clearAppStatusUi(appStatus);
}

async function copyTextToClipboard(text) {
  await copyTextToClipboardUi(text);
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
  const autoReply =
    item.evaluator_auto_reply_text != null && typeof item.evaluator_auto_reply_text === "string"
      ? item.evaluator_auto_reply_text
      : null;
  if (autoReply !== null) {
    appendConversationBubble("user", autoReply);
    setAwaitingPrompt(null);
    agentRunInProgress = true;
    showTypingPlaceholder();
    refreshComposerState();
    return;
  }
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

function resolveRunContainer(runId) {
  if (runId && runFrames.has(runId)) {
    return runFrames.get(runId).body;
  }
  return traceFeed;
}

function appendLogLine(event) {
  const entryEvent = {
    ...event,
    channel: typeof event.channel === "string" ? event.channel : "log",
    level: typeof event.level === "string" ? event.level.trim().toLowerCase() : "info",
  };
  const row = document.createElement("div");
  row.className = "log-line trace-feed-row";
  row.dataset.channel = entryEvent.channel;
  row.dataset.level = entryEvent.level;
  applyRowPadding(row, 0);
  const payload = getPayload(entryEvent);
  const loggerName = typeof payload.logger_name === "string" ? payload.logger_name : "";
  const message =
    typeof payload.message === "string"
      ? payload.message
      : typeof event.title === "string"
        ? event.title
        : "";
  const lvl = typeof entryEvent.level === "string" ? entryEvent.level : "info";

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
  const entry = { el: row, event: entryEvent };
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

  const usageEl = document.createElement("div");
  usageEl.className = "trace-agent-call-usage";
  usageEl.hidden = true;

  summary.appendChild(spinner);
  summary.appendChild(statusEl);
  summary.appendChild(document.createTextNode(" "));
  summary.appendChild(labelEl);
  summary.appendChild(document.createTextNode(" — "));
  summary.appendChild(subEl);
  summary.appendChild(usageEl);

  const body = document.createElement("div");
  body.className = "trace-agent-call-body";

  details.appendChild(summary);
  details.appendChild(body);

  const activeBatchId = parentRunId ? activeBatchForRun.get(parentRunId) : null;
  let container = traceFeed;
  if (activeBatchId && batchFrames.has(activeBatchId)) {
    container = batchFrames.get(activeBatchId).childrenEl;
  } else if (parentRunId && runFrames.has(parentRunId)) {
    container = runFrames.get(parentRunId).body;
  }
  container.appendChild(details);

  runFrames.set(runId, {
    details,
    body,
    spinner,
    statusEl,
    labelEl,
    subEl,
    usageEl,
    agentName,
    lastStatus: null,
    depth,
    parentRunId: parentRunId || null,
    batchId: activeBatchId || null,
  });

  if (activeBatchId && batchFrames.has(activeBatchId)) {
    const bf = batchFrames.get(activeBatchId);
    if (!bf.childRunIds) bf.childRunIds = [];
    bf.childRunIds.push(runId);
  }

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
  liveRunUsage.set(runId, {
    agent_id: fr.agentName,
    run_id: runId,
    self_totals: p.usage_self,
    inclusive_totals: p.usage_inclusive,
  });
  renderAgentBubbleUsage(fr.usageEl, p.usage_self, p.usage_inclusive);
  if (fr.details.classList.contains("trace-agent-call--running")) {
    applyFrameOutcome(fr.details, fr, fr.lastStatus || "completed");
  }
  rebuildFlowTab();
}

function beginBatchFrame(event) {
  const ctx = getContext(event);
  const runId = typeof ctx.run_id === "string" ? ctx.run_id : null;
  const p = getPayload(event);
  const batchEvent = p.event && typeof p.event === "object" ? p.event : {};
  const batchId = typeof batchEvent.batch_id === "string" ? batchEvent.batch_id : null;
  const mode = typeof batchEvent.mode === "string" ? batchEvent.mode : "parallel";
  const count = typeof batchEvent.count === "number" ? batchEvent.count : 0;
  if (!batchId || !runId) return;

  const wrap = document.createElement("div");
  wrap.className = `trace-batch trace-batch--${mode} trace-batch--running`;
  wrap.dataset.batchId = batchId;

  const header = document.createElement("div");
  header.className = "trace-batch-header";
  const modeIcon = mode === "parallel" ? "⊕" : "→";
  const label = document.createElement("span");
  label.className = "trace-batch-label";
  label.textContent = `${modeIcon} ${mode}: ${count} agent${count !== 1 ? "s" : ""}`;
  header.appendChild(label);

  const childrenEl = document.createElement("div");
  childrenEl.className = `trace-batch-children trace-batch-children--${mode}`;

  wrap.appendChild(header);
  wrap.appendChild(childrenEl);

  const parentFrame = runFrames.get(runId);
  const container = parentFrame ? parentFrame.body : traceFeed;
  container.appendChild(wrap);

  batchFrames.set(batchId, { wrap, childrenEl, label, mode, parentRunId: runId, childRunIds: [] });
  activeBatchForRun.set(runId, batchId);

  const entry = { el: wrap, event };
  unifiedEntries.push(entry);
  setEntryVisible(entry);
  traceFeed.scrollTop = traceFeed.scrollHeight;
}

function endBatchFrame(event) {
  const ctx = getContext(event);
  const runId = typeof ctx.run_id === "string" ? ctx.run_id : null;
  const p = getPayload(event);
  const batchEvent = p.event && typeof p.event === "object" ? p.event : {};
  const batchId = typeof batchEvent.batch_id === "string" ? batchEvent.batch_id : null;
  const status = typeof batchEvent.status === "string" ? batchEvent.status : "ok";
  if (!batchId) return;

  const bf = batchFrames.get(batchId);
  if (bf) {
    bf.wrap.classList.remove("trace-batch--running");
    bf.wrap.classList.add(status === "ok" ? "trace-batch--success" : "trace-batch--error");
    const completed = typeof batchEvent.completed === "number" ? batchEvent.completed : 0;
    const failed = typeof batchEvent.failed === "number" ? batchEvent.failed : 0;
    const timedOut = typeof batchEvent.timed_out === "number" ? batchEvent.timed_out : 0;
    bf.label.textContent = `${bf.mode === "parallel" ? "⊕" : "→"} ${bf.mode}: ${completed} ok${failed ? `, ${failed} failed` : ""}${timedOut ? `, ${timedOut} timed out` : ""}`;
  }
  if (runId) activeBatchForRun.delete(runId);
  rebuildFlowTab();
}

function onAgentFinished(event) {
  const rid = getEventRunId(event);
  if (!rid || !runFrames.has(rid)) {
    return;
  }
  const payload = getPayload(event);
  const st = payload.status;
  const fr = runFrames.get(rid);
  if (st != null && st !== "") {
    fr.lastStatus = String(st);
  }
  liveRunUsage.set(rid, {
    agent_id: getContext(event).agent_id || fr.agentName,
    run_id: rid,
    self_totals: payload.usage_self,
    inclusive_totals: payload.usage_inclusive,
  });
  applyFrameOutcome(fr.details, fr, fr.lastStatus || st || "completed");
  syncTraceRunUsage(rid);
}

function buildTraceDetails(event) {
  const node = document.createElement("details");
  const kind = typeof event.kind === "string" ? event.kind : "";
  const lvl = typeof event.level === "string" ? event.level : "";
  const classification = classifyEventKind(kind);
  let rowClass = "trace-event-row trace-feed-row";
  if (classification) rowClass += ` ${classification.cssClass}`;
  if (lvl === "error") rowClass += " trace-event--error";
  node.className = rowClass;
  const ch = typeof event.channel === "string" ? event.channel : "";
  const summary = document.createElement("summary");
  const prefix = classification ? `${classification.icon} ` : (ch ? `[${ch}] ` : "");
  summary.textContent = `${prefix}${event.kind}: ${event.title}`;
  if (lvl) {
    const badge = document.createElement("span");
    badge.className = `trace-level-badge trace-level-${lvl}`;
    badge.textContent = lvl;
    summary.appendChild(document.createTextNode(" "));
    summary.appendChild(badge);
  }
  const payload = event.payload && typeof event.payload === "object" ? event.payload : {};
  if (kind === "llm.response" || kind === "llm.error") {
    const usageLine = formatLlmUsageLine(payload.usage);
    if (usageLine) {
      const usageBlock = buildUsageLinesBlock([usageLine], "trace-event-usage");
      summary.appendChild(usageBlock);
    }
  }
  const body = document.createElement("div");
  body.className = "trace-event-body";
  body.appendChild(renderPayloadTree(payload));
  node.appendChild(summary);
  node.appendChild(body);
  return node;
}

function appendTraceEventRow(event) {
  const entryEvent = {
    ...event,
    channel: typeof event.channel === "string" ? event.channel : "runtime",
    level: typeof event.level === "string" ? event.level.trim().toLowerCase() : "info",
  };
  const node = buildTraceDetails(entryEvent);
  node.dataset.channel = entryEvent.channel;
  node.dataset.level = entryEvent.level;
  const rid = getEventRunId(entryEvent);
  const p = getPayload(entryEvent);
  const pr = p.parent_run_id != null && p.parent_run_id !== "" ? String(p.parent_run_id) : null;
  const container = resolveRunContainer(rid || pr);
  container.appendChild(node);
  const entry = { el: node, event: entryEvent };
  unifiedEntries.push(entry);
  setEntryVisible(entry);
  traceFeed.scrollTop = traceFeed.scrollHeight;
}

function buildFlowNode(runId, depth) {
  const fr = runFrames.get(runId);
  if (!fr) return null;

  const node = document.createElement("div");
  node.className = "flow-node";
  node.style.marginLeft = `${depth * 20}px`;

  const pill = document.createElement("button");
  pill.type = "button";
  pill.className = `flow-pill${fr.lastStatus === "completed" || fr.lastStatus === null ? " flow-pill--success" : fr.lastStatus === "failed" ? " flow-pill--error" : " flow-pill--neutral"}`;
  let pillLabel = fr.agentName;
  if (lastUsageSummary && lastUsageSummary.runs && lastUsageSummary.runs[runId]) {
    const totals = normalizeUsageTotals(lastUsageSummary.runs[runId].inclusive_totals);
    pillLabel += ` (${totals.total_tokens})`;
  }
  pill.textContent = pillLabel;
  pill.title = `run: ${runId}`;
  pill.addEventListener("click", () => {
    fr.details.scrollIntoView({ behavior: "smooth", block: "nearest" });
    fr.details.open = true;
    fr.details.classList.add("flow-highlight");
    renderTraceUsagePanel(lastUsageSummary, runId);
    setTimeout(() => fr.details.classList.remove("flow-highlight"), 1500);
  });
  node.appendChild(pill);

  const childrenWithBatch = [];
  for (const [bid, bf] of batchFrames) {
    if (bf.parentRunId === runId) {
      childrenWithBatch.push({ type: "batch", batchId: bid, bf });
    }
  }
  const batchChildRunIds = new Set();
  for (const { bf } of childrenWithBatch) {
    for (const cid of (bf.childRunIds || [])) batchChildRunIds.add(cid);
  }
  const singleChildren = [];
  for (const [cid, cfr] of runFrames) {
    if (cfr.parentRunId === runId && !batchChildRunIds.has(cid)) {
      singleChildren.push(cid);
    }
  }

  for (const { batchId, bf } of childrenWithBatch) {
    const batchWrap = document.createElement("div");
    batchWrap.className = `flow-batch flow-batch--${bf.mode}`;
    batchWrap.style.marginLeft = `${(depth + 1) * 20}px`;
    const batchLabel = document.createElement("span");
    batchLabel.className = "flow-batch-label";
    batchLabel.textContent = `${bf.mode === "parallel" ? "⊕" : "→"} ${bf.mode}`;
    batchWrap.appendChild(batchLabel);
    const batchRow = document.createElement("div");
    batchRow.className = `flow-batch-row flow-batch-row--${bf.mode}`;
    for (const cid of (bf.childRunIds || [])) {
      const childNode = buildFlowNode(cid, 0);
      if (childNode) batchRow.appendChild(childNode);
    }
    batchWrap.appendChild(batchRow);
    node.appendChild(batchWrap);
  }

  for (const cid of singleChildren) {
    const childNode = buildFlowNode(cid, depth + 1);
    if (childNode) node.appendChild(childNode);
  }

  return node;
}

function rebuildFlowTab() {
  const panel = document.getElementById("tab-panel-flow");
  if (!panel) return;
  const flowTree = panel.querySelector(".flow-tree");
  if (!flowTree) return;
  while (flowTree.firstChild) flowTree.removeChild(flowTree.firstChild);

  if (runFrames.size === 0) {
    const empty = document.createElement("p");
    empty.className = "flow-empty";
    empty.textContent = "No agent runs yet.";
    flowTree.appendChild(empty);
    return;
  }

  for (const [runId, fr] of runFrames) {
    if (!fr.parentRunId) {
      const node = buildFlowNode(runId, 0);
      if (node) flowTree.appendChild(node);
    }
  }
}

function routeTraceEvent(event) {
  const channel = typeof event.channel === "string" ? event.channel : "runtime";
  if (channel === "log") {
    const kind = typeof event.kind === "string" ? event.kind : "";
    if (kind && kind !== "log.record") {
      appendTraceEventRow(event);
      return;
    }
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

  if (kind === "runtime.audit.named_event") {
    const p = getPayload(event);
    const batchEvent = p.event && typeof p.event === "object" ? p.event : {};
    const eventType = typeof batchEvent.type === "string" ? batchEvent.type : "";
    if (eventType === "subagent_batch_started") {
      beginBatchFrame(event);
      return;
    }
    if (eventType === "subagent_batch_finished") {
      endBatchFrame(event);
      return;
    }
  }

  if (kind === "runtime.agent_finished") {
    onAgentFinished(event);
  }

  appendTraceEventRow(event);
}

channelToggles?.addEventListener("change", reapplyFilters);
traceLogLevel?.addEventListener("change", reapplyFilters);
traceLogLevel?.addEventListener("input", reapplyFilters);

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
    lastUsageSummary =
      msg.usage_summary && typeof msg.usage_summary === "object"
        ? /** @type {Record<string, unknown>} */ (msg.usage_summary)
        : null;
    absorbUsageSummary(lastUsageSummary);
    renderSessionUsageSummary(lastUsageSummary);
    syncAllTraceRunUsage();
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
    if (pendingAgentRun) {
      const pr = pendingAgentRun;
      pendingAgentRun = null;
      pr.resolve(msg);
      return;
    }
    void runPostEvaluation();
  }
  if (msg.type === "error") {
    clearAppStatus();
    removeTypingPlaceholder();
    agentRunInProgress = false;
    refreshComposerState();
    if (msg.usage_summary && typeof msg.usage_summary === "object") {
      lastUsageSummary = /** @type {Record<string, unknown>} */ (msg.usage_summary);
      absorbUsageSummary(lastUsageSummary);
    }
    renderSessionUsageSummary(lastUsageSummary);
    syncAllTraceRunUsage();

    if (pendingAgentRun) {
      const pr = pendingAgentRun;
      pendingAgentRun = null;
      const et = msg.error_type || "Error";
      const lines = [`[${et}] ${msg.message || ""}`];
      if (msg.path) {
        lines.push(`File: ${msg.path}`);
      }
      if (msg.hint) {
        lines.push(msg.hint);
      }
      appendConversationBubble("error", lines.join("\n\n"));
      pr.reject(new Error(lines.join("\n")));
      return;
    }

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

function getAgentModelOverride() {
  return (agentModelOverrideInput && agentModelOverrideInput.value.trim()) || "";
}

function getAgentModelOverrideScope() {
  return (agentModelOverrideScopeSelect && agentModelOverrideScopeSelect.value) || "root_only";
}

async function refreshCatalogs() {
  const ep = getEnvPath();
  await loadAgentCatalog(ep);
  await loadInitializerCatalog(ep);
  await loadEvaluatorModelOptions(ep);
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

async function loadEvaluatorModelOptions(envPath) {
  if (!agentModelOverrideInput || !agentModelOverrideList) return;
  const current = agentModelOverrideInput.value.trim();
  try {
    const res = await fetch(`/api/evaluator-model-options?env_path=${encodeURIComponent(envPath)}`);
    const data = await res.json();
    const options = Array.isArray(data.model_options) ? data.model_options : [];
    agentModelOverrideList.innerHTML = "";
    for (const model of options) {
      const opt = document.createElement("option");
      opt.value = String(model);
      agentModelOverrideList.appendChild(opt);
    }
    const preferred = current || pendingDefaultAgentModelOverride;
    agentModelOverrideInput.value = preferred || "";
    if (agentModelOverrideScopeSelect) {
      agentModelOverrideScopeSelect.value = pendingDefaultAgentModelOverrideScope || "root_only";
    }
  } catch (_) {
    /* ignore model option failures */
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
  if (
    data.agent_model_override &&
    agentModelOverrideInput &&
    !agentModelOverrideInput.value.trim()
  ) {
    agentModelOverrideInput.value = data.agent_model_override;
  }
  if (data.agent_model_override_scope && agentModelOverrideScopeSelect && !getAgentModelOverride()) {
    agentModelOverrideScopeSelect.value = data.agent_model_override_scope;
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
  if (needPrompt || needEval || needAgent) {
    try {
      const ir = await fetch(
        `/api/initializer-template?env_path=${encodeURIComponent(getEnvPath())}&initializer=${encodeURIComponent(raw)}`,
      );
      if (ir.ok) {
        const data = await ir.json();
        applyInitializerResponseFields(data);
        updateEvaluateUi();
        adjustPromptInputHeight();
      } else {
        try {
          const res = await fetch(`/api/setup-template?path=${encodeURIComponent(raw)}`);
          const data = await res.json();
          applyInitializerResponseFields(data);
          updateEvaluateUi();
          adjustPromptInputHeight();
        } catch (_) {
          /* ignore */
        }
      }
    } catch (_) {
      try {
        const res = await fetch(`/api/setup-template?path=${encodeURIComponent(raw)}`);
        const data = await res.json();
        applyInitializerResponseFields(data);
        updateEvaluateUi();
        adjustPromptInputHeight();
      } catch (_) {
        /* ignore */
      }
    }
  }
  void refreshInitializerCases();
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
    pendingDefaultAgentModelOverride = String(defs.agent_model_override || "");
    pendingDefaultAgentModelOverrideScope = String(defs.agent_model_override_scope || "root_only");
    await refreshCatalogs();
    await ensureSessionConnected();
    await refreshInitializerCases();
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
    void refreshInitializerCases();
  }, 400);
});
envPathInput?.addEventListener("change", () => {
  refreshCatalogs().catch(() => {});
  void refreshInitializerCases();
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
  lastUsageSummary = null;
  liveRunUsage = new Map();
  renderSessionUsageSummary(null);
  syncAllTraceRunUsage();
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
      case_run_mode: caseRunMode,
      agent_model_override: getAgentModelOverride(),
      agent_model_override_scope: getAgentModelOverrideScope(),
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
  void runPostEvaluation();
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
    { id: "flow", btn: tabFlow, panel: panelFlow },
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
  if (which === "flow") {
    rebuildFlowTab();
  }
}

tabChat?.addEventListener("click", () => setActiveTab("chat"));
tabEvaluation?.addEventListener("click", () => setActiveTab("evaluation"));
tabAgent?.addEventListener("click", () => setActiveTab("agent"));
tabFlow?.addEventListener("click", () => setActiveTab("flow"));

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

if (runAllCasesBtn) {
  bindHoldShortRun(runAllCasesBtn, () => void runAllCasesPlay());
}
initCaseRunMenu();

initSession().catch((err) => {
  setAppStatus(`Failed to start session: ${err}`);
});
