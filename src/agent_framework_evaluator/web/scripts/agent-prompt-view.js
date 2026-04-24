(() => {
  function createAgentPromptView(config) {
    const {
      agentInput,
      agentPromptDisplay,
      agentPromptEmpty,
      agentPromptExpanded,
      agentPromptRaw,
      agentUserActualBtn,
      agentUserEmpty,
      agentUserEnteredBtn,
      agentUserExtra,
      agentUserExtraHeading,
      agentUserPrimaryDisplay,
      agentUserSection,
      getEnvPath,
      getSessionId,
    } = config;

    let lastPromptSnapshots = null;
    let userPrimaryMode = "entered";
    let agentPromptMode = "raw";
    let cachedRawSystemPrompt = "";

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

      const primaryText = userPrimaryMode === "entered" ? entered : firstSent;
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
      const sessionId = getSessionId();
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

    async function refresh() {
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

    function setPromptSnapshots(ps) {
      lastPromptSnapshots = normalizePromptSnapshots(ps);
    }

    agentPromptRaw?.addEventListener("click", () => {
      agentPromptMode = "raw";
      void refresh();
    });
    agentPromptExpanded?.addEventListener("click", () => {
      agentPromptMode = "expanded";
      void refresh();
    });
    agentInput?.addEventListener("change", () => {
      if (agentPromptMode === "raw") void refresh();
    });
    agentUserEnteredBtn?.addEventListener("click", () => {
      userPrimaryMode = "entered";
      renderAgentUserSection();
    });
    agentUserActualBtn?.addEventListener("click", () => {
      userPrimaryMode = "actual";
      renderAgentUserSection();
    });

    return {
      refresh,
      setPromptSnapshots,
    };
  }

  window.AgentPromptView = {
    createAgentPromptView,
  };
})();
