(() => {
  function createSessionClient(config) {
    const {
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
      onError,
      onOutboxItem,
      onResult,
      onTraceEvent,
      promptInput,
      refreshComposerState,
      refreshInitializerCases,
      setAppStatus,
      updateEvaluateUi,
    } = config;

    let socket = null;
    let sessionId = null;
    let pendingDefaultAgentModelOverride = "";
    let pendingDefaultAgentModelOverrideScope = "root_only";
    let envRefreshTimer = null;

    function getSessionId() {
      return sessionId;
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

    function onSocketMessage(ev) {
      const msg = JSON.parse(ev.data);
      if (msg.type === "trace" && msg.event) {
        onTraceEvent(msg.event);
      }
      if (msg.type === "result" && msg.payload) {
        onResult(msg);
      }
      if (msg.type === "error") {
        onError(msg);
      }
      if (msg.type === "outbox" && msg.item) {
        onOutboxItem(msg.item);
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

    async function refreshCatalogs() {
      const ep = getEnvPath();
      await loadAgentCatalog(ep);
      await loadInitializerCatalog(ep);
      await loadEvaluatorModelOptions(ep);
    }

    async function ensureConnected() {
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

    async function recreateSession() {
      detachWebSocket();
      if (sessionId) {
        await fetch(`/api/sessions/${sessionId}/close`, { method: "POST" }).catch(() => {});
        sessionId = null;
      }
      await ensureConnected();
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

    async function onInitializerChanged() {
      if (!initializerInput) return;
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
      await refreshInitializerCases();
    }

    function closeSessionOnLeave() {
      if (!sessionId) return;
      fetch(`/api/sessions/${sessionId}/close`, { method: "POST", keepalive: true }).catch(() => {});
    }

    async function init() {
      try {
        const dr = await fetch("/api/evaluator-defaults");
        const defs = await dr.json();
        if (envPathInput) envPathInput.value = defs.env_path || ".env";
        if (defs.agent && agentInput) agentInput.value = defs.agent;
        if (defs.initializer && initializerInput) initializerInput.value = defs.initializer;
        pendingDefaultAgentModelOverride = String(defs.agent_model_override || "");
        pendingDefaultAgentModelOverrideScope = String(defs.agent_model_override_scope || "root_only");
        await refreshCatalogs();
        await ensureConnected();
        await refreshInitializerCases();
        refreshComposerState();
        adjustPromptInputHeight();
      } catch (err) {
        setAppStatus(`Failed to start session: ${err}`);
      }
    }

    async function postUserInput(promptId, text) {
      if (!sessionId || !promptId) return;
      const res = await fetch(`/api/sessions/${sessionId}/user-input`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt_id: promptId, text }),
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
    }

    async function sendRun(payload) {
      if (!socket || socket.readyState !== WebSocket.OPEN) {
        throw new Error("No WebSocket after reconnect.");
      }
      socket.send(JSON.stringify(payload));
    }

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
    initializerInput?.addEventListener("change", () => {
      void onInitializerChanged();
    });
    window.addEventListener("beforeunload", () => {
      closeSessionOnLeave();
    });

    return {
      closeSessionOnLeave,
      ensureConnected,
      getAgentModelOverride,
      getAgentModelOverrideScope,
      getEnvPath,
      getSessionId,
      init,
      maybeApplyInitializerPrompt,
      postUserInput,
      recreateSession,
      refreshCatalogs,
      sendRun,
    };
  }

  window.SessionClient = {
    createSessionClient,
  };
})();
