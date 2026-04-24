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

/** @type {HTMLElement | null} */
let caseRunMenuAnchor = null;

const CASE_RUN_HOLD_MS = 450;

/** @type {HTMLElement | null} */
let typingPlaceholderEl = null;

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
  normalizeUsageTotals,
} = window.UsageRendering;
const { createSessionClient } = window.SessionClient;
const { selectedTraceLogLevel: selectedTraceLogLevelPrimitive } = window.TracePrimitives;
const { createTraceController, LEVEL_ORDER: TRACE_LEVEL_ORDER } = window.TraceUi;

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

const traceController = createTraceController({
  channelToggles,
  flowPanel: panelFlow,
  openTraceDetailModal,
  runUsageSummary,
  traceFeed,
  traceLogLevel,
});

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
        session_id: sessionClient.getSessionId() ?? "",
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
  await sessionClient.recreateSession();
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
      `/api/initializer-cases?env_path=${encodeURIComponent(sessionClient.getEnvPath())}&initializer=${encodeURIComponent(init)}`,
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
    await sessionClient.ensureConnected();
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
    const pRun = waitForAgentRunResult();
    await sessionClient.sendRun({
      type: "run",
      agent_id: agentInput?.value.trim() || "root",
      prompt: runPrompt,
      initializer: init,
      case_run_mode: caseRunMode,
      agent_model_override: sessionClient.getAgentModelOverride(),
      agent_model_override_scope: sessionClient.getAgentModelOverrideScope(),
    });
    await pRun;
    const res = await fetch("/api/evaluate-case", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionClient.getSessionId() ?? "",
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
    await sessionClient.ensureConnected();
    const res = await fetch("/api/evaluate-batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionClient.getSessionId() ?? "",
        initializer: init,
        log_level: selectedTraceLogLevel(),
        case_run_mode: caseRunMode,
        agent_model_override: sessionClient.getAgentModelOverride(),
        agent_model_override_scope: sessionClient.getAgentModelOverrideScope(),
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

function selectedTraceLogLevel() {
  return selectedTraceLogLevelPrimitive(traceLogLevel, TRACE_LEVEL_ORDER);
}

function clearTraceUi() {
  traceController.clear();
  if (conversationThread) conversationThread.innerHTML = "";
  removeTypingPlaceholder();
  setAwaitingPrompt(null);
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
  if (!awaitingPromptId) return;
  await sessionClient.postUserInput(awaitingPromptId, text);
  appendConversationBubble("user", text ?? "");
  setAwaitingPrompt(null);
  if (promptInput) promptInput.value = "";
  adjustPromptInputHeight();
  agentRunInProgress = true;
  showTypingPlaceholder();
  refreshComposerState();
}

function handleSessionResult(msg) {
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
  traceController.setUsageSummary(
    msg.usage_summary && typeof msg.usage_summary === "object"
      ? /** @type {Record<string, unknown>} */ (msg.usage_summary)
      : null,
  );
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

function handleSessionError(msg) {
  clearAppStatus();
  removeTypingPlaceholder();
  agentRunInProgress = false;
  refreshComposerState();
  traceController.setUsageSummary(
    msg.usage_summary && typeof msg.usage_summary === "object"
      ? /** @type {Record<string, unknown>} */ (msg.usage_summary)
      : null,
  );

  const et = msg.error_type || "Error";
  const lines = [`[${et}] ${msg.message || ""}`];
  if (msg.path) {
    lines.push(`File: ${msg.path}`);
  }
  if (msg.hint) {
    lines.push(msg.hint);
  }
  appendConversationBubble("error", lines.join("\n\n"));

  if (pendingAgentRun) {
    const pr = pendingAgentRun;
    pendingAgentRun = null;
    pr.reject(new Error(lines.join("\n")));
    return;
  }

  clearStoredAgentResult();
  resetEvaluationPanel();
}

const sessionClient = createSessionClient({
  adjustPromptInputHeight,
  agentInput,
  agentList,
  agentModelOverrideInput,
  agentModelOverrideList,
  agentModelOverrideScopeSelect,
  envPathInput,
  evaluatorPromptInput,
  initializerInput,
  initializerList,
  onError: handleSessionError,
  onOutboxItem: handleOutboxItem,
  onResult: handleSessionResult,
  onTraceEvent: (event) => traceController.routeEvent(event),
  promptInput,
  refreshComposerState,
  refreshInitializerCases,
  setAppStatus,
  updateEvaluateUi,
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
    await sessionClient.ensureConnected();
    await sessionClient.maybeApplyInitializerPrompt();
  } catch (err) {
    setAppStatus(`Cannot reach server: ${err}`);
    return;
  }
  const agentId = agentInput.value.trim() || "root";
  const initializerPath = initializerInput ? initializerInput.value.trim() : "";
  const promptText = promptInput ? promptInput.value : "";
  clearStoredAgentResult();
  resetEvaluationPanel();
  traceController.setUsageSummary(null);
  clearTraceUi();
  appendConversationBubble("user", promptText || "(empty prompt)");
  agentRunInProgress = true;
  refreshComposerState();
  showTypingPlaceholder();
  setAppStatus("Running…");
  await sessionClient.sendRun({
    type: "run",
    agent_id: agentId,
    prompt: promptText,
    initializer: initializerPath || null,
    case_run_mode: caseRunMode,
    agent_model_override: sessionClient.getAgentModelOverride(),
    agent_model_override_scope: sessionClient.getAgentModelOverrideScope(),
  });
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
  const ep = sessionClient.getEnvPath();
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
  const sessionId = sessionClient.getSessionId();
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

sessionClient.init().catch((err) => {
  setAppStatus(`Failed to start session: ${err}`);
});
