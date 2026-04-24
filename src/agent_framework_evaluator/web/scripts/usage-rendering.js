(() => {
  function normalizeUsageTotals(totals) {
    const t = totals && typeof totals === "object" ? totals : {};
    return {
      input_tokens: Number(t.input_tokens ?? 0) || 0,
      input_cached_tokens: Number(t.input_cached_tokens ?? 0) || 0,
      output_tokens: Number(t.output_tokens ?? 0) || 0,
      output_cached_tokens: Number(t.output_cached_tokens ?? 0) || 0,
      total_tokens: Number(t.total_tokens ?? 0) || 0,
    };
  }

  function renderUsageTotals(target, totals, opts = {}) {
    if (!target) return;
    const label = typeof opts.label === "string" ? opts.label : "";
    const t = normalizeUsageTotals(totals);
    target.innerHTML = "";
    if (label) {
      const heading = document.createElement("div");
      heading.className = "usage-summary-heading";
      heading.textContent = label;
      target.appendChild(heading);
    }
    const grid = document.createElement("div");
    grid.className = "usage-summary-grid";
    const rows = [
      ["Input", t.input_tokens],
      ["Input cached", t.input_cached_tokens],
      ["Output", t.output_tokens],
      ["Output cached", t.output_cached_tokens],
      ["Total", t.total_tokens],
    ];
    for (const [name, value] of rows) {
      const card = document.createElement("div");
      card.className = "usage-summary-card";
      const k = document.createElement("span");
      k.className = "usage-summary-key";
      k.textContent = String(name);
      const v = document.createElement("strong");
      v.className = "usage-summary-value";
      v.textContent = String(value);
      card.appendChild(k);
      card.appendChild(v);
      grid.appendChild(card);
    }
    target.appendChild(grid);
  }

  function renderSessionUsageSummary(target, summary) {
    if (!target) return;
    if (!summary || typeof summary !== "object") {
      target.hidden = true;
      target.innerHTML = "";
      return;
    }
    renderUsageTotals(target, summary.session_totals, { label: "Session usage" });
    target.hidden = false;
  }

  function formatTokenCount(value) {
    const n = Number(value ?? 0) || 0;
    return n.toLocaleString();
  }

  function formatUsageLine(totals, opts = {}) {
    const t = normalizeUsageTotals(totals);
    const suffix = typeof opts.suffix === "string" ? opts.suffix : "";
    return [
      `I${suffix}: ${formatTokenCount(t.input_tokens)}`,
      `Ic${suffix}: ${formatTokenCount(t.input_cached_tokens)}`,
      `O${suffix}: ${formatTokenCount(t.output_tokens)}`,
      `Oc${suffix}: ${formatTokenCount(t.output_cached_tokens)}`,
    ].join("   ");
  }

  function buildUsageLinesBlock(lines, className) {
    const wrap = document.createElement("div");
    wrap.className = className;
    for (const lineText of lines) {
      const line = document.createElement("div");
      line.className = `${className}-line`;
      line.textContent = lineText;
      wrap.appendChild(line);
    }
    return wrap;
  }

  function formatRunUsageLines(runEntry) {
    if (!runEntry || typeof runEntry !== "object") return [];
    const selfTotals = normalizeUsageTotals(runEntry.self_totals);
    const inclTotals = normalizeUsageTotals(runEntry.inclusive_totals);
    return [
      formatUsageLine(selfTotals),
      formatUsageLine(inclTotals, { suffix: "s" }),
    ];
  }

  function formatLlmUsageLine(usage) {
    if (!usage || typeof usage !== "object") return "";
    return formatUsageLine(normalizeUsageTotals(usage));
  }

  function renderAgentBubbleUsage(usageEl, usageSelf, usageInclusive) {
    if (!usageEl) return;
    const lines = [
      formatUsageLine(usageSelf),
      formatUsageLine(usageInclusive, { suffix: "s" }),
    ];
    usageEl.innerHTML = "";
    usageEl.hidden = false;
    for (const lineText of lines) {
      const line = document.createElement("div");
      line.className = "trace-agent-call-usage-line";
      line.textContent = lineText;
      usageEl.appendChild(line);
    }
  }

  window.UsageRendering = {
    buildUsageLinesBlock,
    formatLlmUsageLine,
    formatRunUsageLines,
    formatTokenCount,
    formatUsageLine,
    normalizeUsageTotals,
    renderAgentBubbleUsage,
    renderSessionUsageSummary,
    renderUsageTotals,
  };
})();
