(() => {
  function setComposerEnabled(promptInput, sendChatButton, enabled) {
    if (promptInput) promptInput.disabled = !enabled;
    if (sendChatButton) sendChatButton.disabled = !enabled;
  }

  function scrollThreadToBottom(conversationThread) {
    if (conversationThread) conversationThread.scrollTop = conversationThread.scrollHeight;
  }

  function adjustPromptInputHeight(promptInput) {
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

  async function copyTextToClipboard(text) {
    const value = typeof text === "string" ? text : String(text ?? "");
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return;
    }
    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.setAttribute("readonly", "true");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    textarea.style.pointerEvents = "none";
    document.body.appendChild(textarea);
    textarea.select();
    try {
      document.execCommand("copy");
    } finally {
      document.body.removeChild(textarea);
    }
  }

  function appendConversationBubble(conversationThread, role, text, opts, deps) {
    if (!conversationThread || typeof text !== "string") return;
    const { copyTextToClipboard, getMarkedParse } = deps;
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
    const controls = document.createElement("div");
    controls.className = "conv-msg-controls";
    const copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "conv-msg-copy";
    copyBtn.textContent = "Copy";
    copyBtn.setAttribute("aria-label", `Copy ${roleKey} message`);
    copyBtn.addEventListener("click", async () => {
      const originalLabel = "Copy";
      try {
        await copyTextToClipboard(text);
        copyBtn.textContent = "Copied";
        copyBtn.classList.add("conv-msg-copy--ok");
        window.setTimeout(() => {
          copyBtn.textContent = originalLabel;
          copyBtn.classList.remove("conv-msg-copy--ok");
        }, 1200);
      } catch (_) {
        copyBtn.textContent = "Failed";
        copyBtn.classList.add("conv-msg-copy--error");
        window.setTimeout(() => {
          copyBtn.textContent = originalLabel;
          copyBtn.classList.remove("conv-msg-copy--error");
        }, 1200);
      }
    });
    controls.appendChild(copyBtn);
    wrap.appendChild(meta);
    wrap.appendChild(controls);
    wrap.appendChild(body);
    conversationThread.appendChild(wrap);
    scrollThreadToBottom(conversationThread);
  }

  function setAppStatus(appStatus, message) {
    if (appStatus) appStatus.textContent = message || "";
  }

  function clearAppStatus(appStatus) {
    if (appStatus) appStatus.textContent = "";
  }

  function removeTypingPlaceholder(currentEl) {
    if (currentEl?.parentNode) currentEl.parentNode.removeChild(currentEl);
    return null;
  }

  function showTypingPlaceholder(currentEl, conversationThread) {
    const existing = removeTypingPlaceholder(currentEl);
    void existing;
    if (!conversationThread) return null;
    const wrap = document.createElement("div");
    wrap.className = "conv-msg conv-msg--assistant conv-msg--typing";
    const meta = document.createElement("div");
    meta.className = "conv-msg-meta";
    meta.textContent = "Agent";
    const dots = document.createElement("div");
    dots.className = "typing-dots";
    for (let i = 0; i < 3; i++) {
      const d = document.createElement("span");
      d.className = "typing-dot";
      dots.appendChild(d);
    }
    wrap.appendChild(meta);
    wrap.appendChild(dots);
    conversationThread.appendChild(wrap);
    scrollThreadToBottom(conversationThread);
    return wrap;
  }

  window.ConversationUi = {
    adjustPromptInputHeight,
    appendConversationBubble,
    clearAppStatus,
    copyTextToClipboard,
    removeTypingPlaceholder,
    scrollThreadToBottom,
    setAppStatus,
    setComposerEnabled,
    showTypingPlaceholder,
  };
})();
