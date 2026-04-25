(() => {
  /**
   * @param {HTMLElement} container
   * @param {number} score
   */
  function renderEvalScoreBar(container, score) {
    if (!container) return;
    container.innerHTML = "";
    const s = Math.min(10, Math.max(0, Number(score)));
    for (let i = 0; i < 10; i++) {
      const amt = Math.min(1, Math.max(0, s - i));
      const hue = (i / 9) * 120;
      const wrap = document.createElement("span");
      wrap.className = "eval-segment";
      wrap.style.setProperty("--eval-lit", String(amt));
      const glow = document.createElement("span");
      glow.className = "eval-segment-glow";
      glow.style.background = `hsl(${hue} 82% 48%)`;
      const inner = document.createElement("span");
      inner.className = "eval-segment-inner";
      inner.style.background = [
        "linear-gradient(to bottom,",
        `hsl(${hue} 74% 50%),`,
        `hsl(${hue} 62% 34%))`,
      ].join(" ");
      wrap.appendChild(glow);
      wrap.appendChild(inner);
      container.appendChild(wrap);
    }
  }

  /**
   * @param {HTMLElement} target
   * @param {null | { score: number, overall_verdict: string, evaluation: unknown[] }} d
   */
  function fillEvaluationDetailDom(target, d) {
    if (!target || !d) return;
    const parseMd =
      typeof globalThis.getMarkedParse === "function" ? globalThis.getMarkedParse() : null;
    target.innerHTML = "";

    const hOverall = document.createElement("h4");
    hOverall.textContent = "Overall result";
    target.appendChild(hOverall);
    const overallDiv = document.createElement("div");
    overallDiv.className = "evaluation-detail-section";
    const ov = d.overall_verdict || "";
    if (parseMd) {
      try {
        overallDiv.innerHTML = parseMd(ov);
      } catch (_) {
        overallDiv.textContent = ov;
      }
    } else {
      overallDiv.textContent = ov;
    }
    target.appendChild(overallDiv);

    const hCrit = document.createElement("h4");
    hCrit.textContent = "Criteria";
    target.appendChild(hCrit);

    const table = document.createElement("table");
    table.className = "eval-detail-table";
    const thead = document.createElement("thead");
    const hr = document.createElement("tr");
    for (const label of ["Criteria", "Passed", "Reason"]) {
      const th = document.createElement("th");
      th.scope = "col";
      th.textContent = label;
      hr.appendChild(th);
    }
    thead.appendChild(hr);
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    const rows = Array.isArray(d.evaluation) ? d.evaluation : [];
    if (rows.length === 0) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 3;
      td.className = "eval-detail-empty";
      td.textContent = "No criteria rows returned.";
      tr.appendChild(td);
      tbody.appendChild(tr);
    } else {
      for (const row of rows) {
        const tr = document.createElement("tr");
        const tdC = document.createElement("td");
        tdC.className = "eval-detail-criteria";
        tdC.textContent = row.criteria ?? "";
        const tdP = document.createElement("td");
        tdP.className = "eval-detail-passed";
        const icon = document.createElement("span");
        const ok = Boolean(row.passed);
        icon.className = ok ? "eval-pass-icon" : "eval-fail-icon";
        icon.setAttribute("role", "img");
        icon.setAttribute("aria-label", ok ? "Passed" : "Failed");
        icon.textContent = ok ? "\u2713" : "\u2717";
        tdP.appendChild(icon);
        const tdR = document.createElement("td");
        tdR.className = "eval-detail-reason";
        tdR.textContent = row.reason ?? "";
        tr.appendChild(tdC);
        tr.appendChild(tdP);
        tr.appendChild(tdR);
        tbody.appendChild(tr);
      }
    }
    table.appendChild(tbody);
    target.appendChild(table);
  }

  window.EvaluationRendering = {
    fillEvaluationDetailDom,
    renderEvalScoreBar,
  };
})();
