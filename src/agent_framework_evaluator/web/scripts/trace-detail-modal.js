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

  window.getMarkedParse = getMarkedParse;
  window.openTraceDetailModal = openTraceDetailModal;
  wireTraceDetailModal();
})();
