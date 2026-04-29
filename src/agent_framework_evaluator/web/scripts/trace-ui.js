(() => {
  const TRACE_STRING_PREVIEW_CHARS = 90;
  const LEVEL_ORDER = { debug: 10, info: 20, warning: 30, error: 40 };

  const {
    applyRowPadding,
    classifyEventKind,
    getContext,
    getEventRunId,
    getPayload,
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

  function createTraceController(config) {
    const {
      channelToggles,
      flowPanel,
      openTraceDetailModal,
      runUsageSummary,
      traceFeed,
      traceLogLevel,
    } = config;

    /** @type {Map<string, Record<string, unknown>>} */
    let liveRunUsage = new Map();
    /** @type {Record<string, unknown> | null} */
    let lastUsageSummary = null;
    /** @type {Map<string, { details: HTMLElement, body: HTMLElement, spinner: HTMLElement, statusEl: HTMLElement, labelEl: HTMLElement, subEl: HTMLElement, usageEl: HTMLElement, agentName: string, lastStatus: string | null, depth: number, parentRunId: string | null, batchId: string | null }>} */
    const runFrames = new Map();
    /** @type {Map<string, { wrap: HTMLElement, childrenEl: HTMLElement, label: HTMLElement, mode: string, parentRunId: string, childRunIds: string[] }>} */
    const batchFrames = new Map();
    /** @type {Map<string, string>} */
    const activeBatchForRun = new Map();
    /** @type {{ el: HTMLElement, event: Record<string, unknown> }[]} */
    const unifiedEntries = [];

    function getTracePrimitiveDeps() {
      return {
        levelOrder: LEVEL_ORDER,
        openTraceDetailModal,
        traceLogLevelEl: traceLogLevel,
        traceStringPreviewChars: TRACE_STRING_PREVIEW_CHARS,
      };
    }

    function renderPayloadTree(payload) {
      return renderPayloadTreePrimitive(payload, getTracePrimitiveDeps());
    }

    function setEntryVisible(entry) {
      setEntryVisiblePrimitive(entry, getTracePrimitiveDeps());
    }

    function reapplyFilters() {
      reapplyFiltersForEntries(unifiedEntries, getTracePrimitiveDeps());
    }

    function renderSessionUsageSummary(summary) {
      renderSessionUsageSummaryForTarget(runUsageSummary, summary);
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
        setTimeout(() => fr.details.classList.remove("flow-highlight"), 1500);
      });
      node.appendChild(pill);

      const childrenWithBatch = [];
      for (const [bid, bf] of batchFrames) {
        if (bf.parentRunId === runId) {
          childrenWithBatch.push({ batchId: bid, bf });
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

      for (const { bf } of childrenWithBatch) {
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
      const flowTree = flowPanel?.querySelector(".flow-tree");
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

    function routeEvent(event) {
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

    function setUsageSummary(summary) {
      lastUsageSummary = summary && typeof summary === "object" ? summary : null;
      if (lastUsageSummary) {
        absorbUsageSummary(lastUsageSummary);
      }
      renderSessionUsageSummary(lastUsageSummary);
      syncAllTraceRunUsage();
      rebuildFlowTab();
    }

    function clear() {
      if (traceFeed) traceFeed.innerHTML = "";
      runFrames.clear();
      batchFrames.clear();
      activeBatchForRun.clear();
      unifiedEntries.length = 0;
      liveRunUsage = new Map();
      rebuildFlowTab();
    }

    channelToggles?.addEventListener("change", reapplyFilters);
    traceLogLevel?.addEventListener("change", reapplyFilters);
    traceLogLevel?.addEventListener("input", reapplyFilters);

    return {
      clear,
      routeEvent,
      setUsageSummary,
    };
  }

  window.TraceUi = {
    createTraceController,
    LEVEL_ORDER,
  };
})();
