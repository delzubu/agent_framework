(() => {
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

  function renderMdInto(el, text) {
    el.textContent = "";
    const parseMd = getMarkedParse();
    if (parseMd) {
      try {
        const html = parseMd(text, { breaks: true });
        const frag = document.createRange().createContextualFragment(html);
        el.appendChild(frag);
      } catch (_) {
        el.textContent = text;
      }
    } else {
      el.style.whiteSpace = "pre-wrap";
      el.style.overflowWrap = "anywhere";
      el.textContent = text;
    }
  }

  /**
   * Open the trace detail modal.
   * @param {string} title
   * @param {string} text  Raw string always shown in the Source tab.
   * @param {object} [opts]
   * @param {string} [opts.message]  Show a Message tab with markdown rendering.
   * @param {unknown} [opts.response] Show a Response tab with prettified JSON.
   */
  function openTraceDetailModal(title, text, opts) {
    const modal = document.getElementById("trace-detail-modal");
    const titleEl = document.getElementById("trace-detail-modal-title");
    const srcEl = document.getElementById("trace-detail-modal-source");
    const mdEl = document.getElementById("trace-detail-modal-md");
    const msgEl = document.getElementById("trace-detail-modal-message");
    const respEl = document.getElementById("trace-detail-modal-response");
    const tabs = document.getElementById("trace-detail-dialog-tabs");
    const tabMd = document.getElementById("trace-detail-tab-md");
    const tabMsg = document.getElementById("trace-detail-tab-message");
    const tabResp = document.getElementById("trace-detail-tab-response");
    if (!modal || !titleEl || !srcEl || !mdEl) return;

    const hasMessage = opts && typeof opts.message === "string";
    const hasResponse = opts && opts.response != null;

    titleEl.textContent = title;
    srcEl.textContent = text;

    if (msgEl && tabMsg) {
      if (hasMessage) {
        renderMdInto(msgEl, opts.message);
        tabMsg.hidden = false;
      } else {
        msgEl.textContent = "";
        tabMsg.hidden = true;
      }
    }

    if (respEl && tabResp) {
      if (hasResponse) {
        try { respEl.textContent = JSON.stringify(opts.response, null, 2); }
        catch (_) { respEl.textContent = String(opts.response); }
        tabResp.hidden = false;
      } else {
        respEl.textContent = "";
        tabResp.hidden = true;
      }
    }

    const parseMd = getMarkedParse();
    const showMd = Boolean(!hasMessage && parseMd && looksLikeMarkdown(text));
    mdEl.textContent = "";
    if (showMd && parseMd) {
      renderMdInto(mdEl, text);
    }
    if (tabMd) tabMd.style.display = showMd ? "" : "none";

    const anyExtra = hasMessage || hasResponse || showMd;
    if (tabs) tabs.hidden = !anyExtra;

    _activatePanel(hasMessage ? "message" : "source");
    modal.showModal();
  }

  function _activatePanel(which) {
    const panelIds = ["source", "md", "message", "response"];
    for (const id of panelIds) {
      const panel = document.getElementById(`trace-detail-modal-${id}`);
      const btn = document.getElementById(`trace-detail-tab-${id}`);
      panel?.classList.toggle("trace-detail-panel--active", id === which);
      btn?.classList.toggle("trace-detail-tab--active", id === which);
    }
  }

  function wireTraceDetailModal() {
    const modal = document.getElementById("trace-detail-modal");
    const closeBtn = document.getElementById("trace-detail-modal-close");
    if (!modal || !closeBtn) return;
    closeBtn.addEventListener("click", () => modal.close());
    modal.addEventListener("click", (e) => {
      if (e.target === modal) modal.close();
    });
    for (const id of ["source", "md", "message", "response"]) {
      document.getElementById(`trace-detail-tab-${id}`)
        ?.addEventListener("click", () => _activatePanel(id));
    }
  }

  window.getMarkedParse = getMarkedParse;
  window.openTraceDetailModal = openTraceDetailModal;
  wireTraceDetailModal();
})();
