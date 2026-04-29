(() => {
  function renderStringInTrace(parent, s, keyHint, deps, siblingResponse) {
    const { openTraceDetailModal, traceStringPreviewChars } = deps;
    const wrap = document.createElement("span");
    wrap.className = "trace-json-str";
    const openQ = document.createElement("span");
    openQ.className = "trace-json-quote";
    openQ.textContent = '"';
    const inner = document.createElement("span");
    inner.className = "trace-json-str-inner";
    if (s.length <= traceStringPreviewChars) {
      inner.textContent = s;
    } else {
      inner.textContent = s.slice(0, traceStringPreviewChars) + "…";
      inner.classList.add("trace-json-str-inner--truncated");
      inner.setAttribute("role", "button");
      inner.tabIndex = 0;
      inner.title = `Open full text (${s.length} characters)`;
      const opts = siblingResponse !== undefined
        ? { message: s, response: siblingResponse }
        : undefined;
      const open = (e) => {
        e.preventDefault();
        e.stopPropagation();
        openTraceDetailModal(String(keyHint || "Field"), s, opts);
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

  function appendJsonValue(parent, value, keyHint, deps) {
    if (value === null) {
      const span = document.createElement("span");
      span.className = "trace-json-lit trace-json-null";
      span.textContent = "null";
      parent.appendChild(span);
      return;
    }
    const t = typeof value;
    if (t === "string") {
      renderStringInTrace(parent, value, keyHint, deps);
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
        appendJsonValue(cell, item, `${keyHint || "item"}[${i}]`, deps);
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
      // When an object has a "message" string and a "response" field, pass response
      // as context so the long-message popup shows both Message and Response tabs.
      const msgResponseCtx = (typeof value.message === "string" && "response" in value)
        ? value.response
        : undefined;
      for (const k of keys) {
        const sibResp = (k === "message" && msgResponseCtx !== undefined) ? msgResponseCtx : undefined;
        obj.appendChild(renderJsonKeyRow(k, value[k], keyHint ? `${keyHint}.${k}` : k, deps, sibResp));
      }
      parent.appendChild(obj);
    }
  }

  function renderJsonKeyRow(k, v, path, deps, siblingResponse) {
    const row = document.createElement("div");
    row.className = "trace-json-kv";
    const keyEl = document.createElement("span");
    keyEl.className = "trace-json-key";
    keyEl.textContent = `${k}: `;
    row.appendChild(keyEl);
    const valWrap = document.createElement("span");
    valWrap.className = "trace-json-val";
    if (typeof v === "string" && siblingResponse !== undefined) {
      renderStringInTrace(valWrap, v, path, deps, siblingResponse);
    } else {
      appendJsonValue(valWrap, v, path, deps);
    }
    row.appendChild(valWrap);
    return row;
  }

  function renderPayloadTree(payload, deps) {
    const root = document.createElement("div");
    root.className = "trace-payload-tree";
    if (payload === null || typeof payload !== "object" || Array.isArray(payload)) {
      appendJsonValue(root, payload, "payload", deps);
      return root;
    }
    const keys = Object.keys(payload);
    if (keys.length === 0) {
      root.textContent = "{}";
      return root;
    }
    for (const k of keys) {
      root.appendChild(renderJsonKeyRow(k, payload[k], k, deps));
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

  function eventChannel(event, el) {
    if (event && typeof event.channel === "string" && event.channel) return event.channel;
    if (el && typeof el.dataset?.channel === "string" && el.dataset.channel) return el.dataset.channel;
    return "runtime";
  }

  function eventLevel(event, el) {
    if (event && typeof event.level === "string" && event.level) {
      return event.level.trim().toLowerCase();
    }
    if (el && typeof el.dataset?.level === "string" && el.dataset.level) {
      return el.dataset.level.trim().toLowerCase();
    }
    return "info";
  }

  function selectedTraceLogLevel(traceLogLevelEl, levelOrder) {
    const v = traceLogLevelEl && "value" in traceLogLevelEl ? String(traceLogLevelEl.value) : "warning";
    const normalized = String(v).trim().toLowerCase();
    return Object.prototype.hasOwnProperty.call(levelOrder, normalized) ? normalized : "warning";
  }

  function passesLevelFilter(event, el, deps) {
    const { levelOrder, traceLogLevelEl } = deps;
    const channel = eventChannel(event, el);
    if (channel !== "log") return true;
    const level = eventLevel(event, el);
    return (levelOrder[level] ?? 20) >= (levelOrder[selectedTraceLogLevel(traceLogLevelEl, levelOrder)] ?? 30);
  }

  function setEntryVisible(entry, deps) {
    const visible = passesChannelFilters(entry.event) && passesLevelFilter(entry.event, entry.el, deps);
    entry.el.hidden = !visible;
    entry.el.style.display = visible ? "" : "none";
  }

  function reapplyFilters(entries, deps) {
    for (const entry of entries) {
      setEntryVisible(entry, deps);
    }
  }

  function depthForTraceEvent(event, runFrames) {
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

  function classifyEventKind(kind) {
    if (!kind) return null;
    if (kind.includes("tool_call")) return { icon: "🔧", cssClass: "trace-event--tool" };
    if (kind.includes("subagent_call")) return { icon: "↳", cssClass: "trace-event--subagent" };
    if (kind.includes("callback") || kind.includes("callback_requested") || kind.includes("callback_answered")) {
      return { icon: "↑", cssClass: "trace-event--callback" };
    }
    if (kind.includes("skill")) return { icon: "✦", cssClass: "trace-event--skill" };
    if (kind.includes("decision")) return { icon: "◎", cssClass: "trace-event--decision" };
    if (kind.includes("model_call")) return { icon: "⊡", cssClass: "trace-event--model" };
    if (kind.includes("context_updated")) return { icon: "≡", cssClass: "trace-event--context" };
    return null;
  }

  window.TracePrimitives = {
    appendJsonValue,
    applyRowPadding,
    channelEnabled,
    classifyEventKind,
    depthForTraceEvent,
    eventChannel,
    eventLevel,
    getContext,
    getEventRunId,
    getPayload,
    passesChannelFilters,
    passesLevelFilter,
    reapplyFilters,
    renderJsonKeyRow,
    renderPayloadTree,
    renderStringInTrace,
    selectedTraceLogLevel,
    setEntryVisible,
  };
})();
