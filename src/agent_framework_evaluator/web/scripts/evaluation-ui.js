(() => {
  function scoreToHue(score) {
    const s = Math.min(10, Math.max(0, Number(score)));
    return (s / 10) * 120;
  }

  function fillBatchCaseEvaluationDetailBody(target, apiData, deps) {
    if (!target) return;
    const { fillEvaluationDetailDom } = deps;
    target.innerHTML = "";
    if (!apiData || typeof apiData !== "object") {
      target.textContent = "No detail available.";
      return;
    }
    if (apiData.__batchError === true) {
      const p = document.createElement("p");
      p.className = "batch-detail-error";
      p.textContent = String(apiData.message ?? "Error");
      target.appendChild(p);
      return;
    }

    const avg = apiData.average_score;
    const avgP = document.createElement("p");
    avgP.className = "batch-detail-avg-line";
    avgP.textContent =
      typeof avg === "number" && Number.isFinite(avg)
        ? `Average score (this case): ${avg.toFixed(1)}`
        : "Average score: —";
    target.appendChild(avgP);

    const llm = apiData.llm_result;
    if (llm && typeof llm === "object") {
      const h = document.createElement("h4");
      h.className = "batch-detail-subheading";
      h.textContent = "LLM evaluation";
      target.appendChild(h);
      const wrap = document.createElement("div");
      const le = /** @type {Record<string, unknown>} */ (llm);
      fillEvaluationDetailDom(wrap, {
        score: Number(le.score),
        overall_verdict: String(le.overall_verdict ?? ""),
        evaluation: Array.isArray(le.evaluation) ? le.evaluation : [],
      });
      target.appendChild(wrap);
    }

    const code = apiData.code_result;
    if (code && typeof code === "object") {
      const h = document.createElement("h4");
      h.className = "batch-detail-subheading";
      h.textContent = "Programmatic evaluation";
      target.appendChild(h);
      const wrap = document.createElement("div");
      const ce = /** @type {Record<string, unknown>} */ (code);
      fillEvaluationDetailDom(wrap, {
        score: Number(ce.score),
        overall_verdict: String(ce.overall_verdict ?? ""),
        evaluation: Array.isArray(ce.evaluation) ? ce.evaluation : [],
      });
      target.appendChild(wrap);
    }

    if (!llm && !code) {
      const p = document.createElement("p");
      p.className = "batch-detail-empty";
      p.textContent = "No evaluation sections in response.";
      target.appendChild(p);
    }
  }

  function openBatchCaseEvaluationModal(caseTitle, detail, deps) {
    const { evaluationDetailBody, evaluationDetailModal, evaluationDetailTitle } = deps;
    if (!evaluationDetailModal || !evaluationDetailBody) return;
    if (evaluationDetailTitle) evaluationDetailTitle.textContent = caseTitle || "Test case";
    fillBatchCaseEvaluationDetailBody(evaluationDetailBody, detail, deps);
    evaluationDetailModal.showModal();
  }

  function openEvaluationDetailModal(lastEvaluationPayload, deps) {
    const { evaluationDetailBody, evaluationDetailModal, evaluationDetailTitle, fillEvaluationDetailDom } = deps;
    if (!evaluationDetailModal || !evaluationDetailBody || !lastEvaluationPayload) return;
    if (evaluationDetailTitle) evaluationDetailTitle.textContent = "Evaluation details";
    fillEvaluationDetailDom(evaluationDetailBody, lastEvaluationPayload);
    evaluationDetailModal.showModal();
  }

  function syncInlineEvaluationDetail(lastEvaluationPayload, deps) {
    const { evaluationInlineDetail, fillEvaluationDetailDom } = deps;
    if (!evaluationInlineDetail) return;
    if (lastEvaluationPayload) {
      evaluationInlineDetail.hidden = false;
      fillEvaluationDetailDom(evaluationInlineDetail, lastEvaluationPayload);
    } else {
      evaluationInlineDetail.innerHTML = "";
      evaluationInlineDetail.hidden = true;
    }
  }

  function hideCaseEvalSubpanels(deps) {
    const {
      batchSummaryPanel,
      evalCodeInlineDetail,
      evalCodeSection,
      evalLlmInlineDetail,
      evalLlmSection,
    } = deps;
    if (evalLlmSection) evalLlmSection.hidden = true;
    if (evalCodeSection) evalCodeSection.hidden = true;
    if (batchSummaryPanel) batchSummaryPanel.hidden = true;
    if (evalLlmInlineDetail) {
      evalLlmInlineDetail.innerHTML = "";
      evalLlmInlineDetail.hidden = true;
    }
    if (evalCodeInlineDetail) {
      evalCodeInlineDetail.innerHTML = "";
      evalCodeInlineDetail.hidden = true;
    }
  }

  function displayCaseEvaluation(data, deps) {
    const {
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
      evaluationInlineDetail,
      evaluationPanel,
      evaluationStatus,
      fillEvaluationDetailDom,
      hideCaseEvalSubpanels,
      renderEvalScoreBar,
      setLastEvaluationPayload,
      updateEvaluateUi,
    } = deps;
    hideCaseEvalSubpanels(deps);
    if (!evaluationPanel) return;
    const llm = /** @type {{ score: number, overall_verdict?: string, evaluation?: unknown[] } | null} */ (
      data.llm_result || null
    );
    const code = /** @type {{ score: number, overall_verdict?: string, evaluation?: unknown[] } | null} */ (
      data.code_result || null
    );
    const avg = Number(data.average_score);
    const avgN = Number.isFinite(avg) ? Math.min(10, Math.max(0, avg)) : 7.5;

    let lastEvaluationPayload = null;
    if (llm) {
      const rawEval = Array.isArray(llm.evaluation) ? llm.evaluation : [];
      const evaluation = rawEval.map((row) => ({
        criteria: String(/** @type {Record<string, unknown>} */ (row)?.criteria ?? ""),
        passed: Boolean(/** @type {Record<string, unknown>} */ (row)?.passed),
        reason: String(/** @type {Record<string, unknown>} */ (row)?.reason ?? ""),
      }));
      lastEvaluationPayload = {
        score: Number(llm.score),
        overall_verdict: String(llm.overall_verdict ?? ""),
        evaluation,
      };
    }
    setLastEvaluationPayload(lastEvaluationPayload);

    evaluationPanel.hidden = false;
    if (evalSingleSections) evalSingleSections.hidden = false;
    renderEvalScoreBar(evalScoreBar, avgN);
    if (evalScoreLabel) evalScoreLabel.textContent = avgN.toFixed(1);
    if (evaluationStatus) evaluationStatus.textContent = "";

    if (llm && evalLlmSection && evalLlmScoreLabel && evalLlmInlineDetail) {
      evalLlmSection.hidden = false;
      const ls = Number(llm.score);
      const lsN = Number.isFinite(ls) ? Math.min(10, Math.max(0, ls)) : 7.5;
      evalLlmScoreLabel.textContent = lsN.toFixed(1);
      evalLlmInlineDetail.hidden = false;
      fillEvaluationDetailDom(evalLlmInlineDetail, {
        score: lsN,
        overall_verdict: String(llm.overall_verdict ?? ""),
        evaluation: lastEvaluationPayload ? lastEvaluationPayload.evaluation : [],
      });
    }

    if (code && evalCodeSection && evalCodeScoreBar && evalCodeScoreLabel && evalCodeInlineDetail) {
      evalCodeSection.hidden = false;
      const cs = Number(code.score);
      const csN = Number.isFinite(cs) ? Math.min(10, Math.max(0, cs)) : 7.5;
      renderEvalScoreBar(evalCodeScoreBar, csN);
      evalCodeScoreLabel.textContent = csN.toFixed(1);
      const rawEval = Array.isArray(code.evaluation) ? code.evaluation : [];
      const codeRows = rawEval.map((row) => ({
        criteria: String(/** @type {Record<string, unknown>} */ (row)?.criteria ?? ""),
        passed: Boolean(/** @type {Record<string, unknown>} */ (row)?.passed),
        reason: String(/** @type {Record<string, unknown>} */ (row)?.reason ?? ""),
      }));
      evalCodeInlineDetail.hidden = false;
      fillEvaluationDetailDom(evalCodeInlineDetail, {
        score: csN,
        overall_verdict: String(code.overall_verdict ?? ""),
        evaluation: codeRows,
      });
    }

    if (evaluationInlineDetail) {
      evaluationInlineDetail.hidden = true;
      evaluationInlineDetail.innerHTML = "";
    }
    updateEvaluateUi();
  }

  function displayBatchSummary(rows, deps) {
    const {
      batchAvgScoreBar,
      batchAvgScoreLabel,
      batchSummaryPanel,
      batchSummaryTableBody,
      evalScoreBar,
      evalScoreLabel,
      evalSingleSections,
      evaluationPanel,
      evaluationStatus,
      hideCaseEvalSubpanels,
      normalizeUsageTotals,
      openBatchCaseEvaluationModal,
      renderEvalScoreBar,
      setLastEvaluationPayload,
      updateEvaluateUi,
    } = deps;
    hideCaseEvalSubpanels(deps);
    if (!evaluationPanel || !batchSummaryPanel || !batchSummaryTableBody) return;
    const nums = rows.map((r) => r.average_score).filter((x) => typeof x === "number" && Number.isFinite(x));
    const mean = nums.length ? nums.reduce((a, b) => a + b, 0) / nums.length : 0;
    evaluationPanel.hidden = false;
    if (evalSingleSections) evalSingleSections.hidden = true;
    batchSummaryPanel.hidden = false;
    renderEvalScoreBar(batchAvgScoreBar, mean);
    if (batchAvgScoreLabel) batchAvgScoreLabel.textContent = nums.length ? mean.toFixed(1) : "—";

    batchSummaryTableBody.innerHTML = "";
    for (const r of rows) {
      const tr = document.createElement("tr");
      tr.className = "batch-summary-table-row";

      const titleTd = document.createElement("td");
      titleTd.className = "batch-summary-td-title";
      titleTd.textContent = r.title || "Case";

      const scoreTd = document.createElement("td");
      scoreTd.className = "batch-summary-td-score";
      const usageTd = document.createElement("td");
      usageTd.className = "batch-summary-td-usage";

      let detailPayload = r.detail != null ? r.detail : null;
      if (r.error && detailPayload == null) {
        detailPayload = { __batchError: true, message: r.error };
      }

      const hasErr = Boolean(r.error) && typeof r.average_score !== "number";
      const hasScore = typeof r.average_score === "number" && Number.isFinite(r.average_score);

      if (hasErr) {
        scoreTd.textContent = "Error";
        scoreTd.classList.add("batch-summary-score--error");
      } else if (hasScore) {
        const sc = /** @type {number} */ (r.average_score);
        scoreTd.textContent = sc.toFixed(1);
        scoreTd.style.color = `hsl(${scoreToHue(sc)} 78% 58%)`;
      } else {
        scoreTd.textContent = "—";
        scoreTd.classList.add("batch-summary-score--na");
      }
      const usageTotals =
        r.detail && typeof r.detail === "object" && r.detail.usage_summary && typeof r.detail.usage_summary === "object"
          ? normalizeUsageTotals(r.detail.usage_summary.session_totals)
          : null;
      usageTd.textContent = usageTotals ? String(usageTotals.total_tokens) : "—";

      tr.tabIndex = 0;
      tr.setAttribute("role", "button");
      const label = `${r.title || "Case"}${hasScore ? `, score ${(/** @type {number} */ (r.average_score)).toFixed(1)}` : hasErr ? ", error" : ""}. Open details.`;
      tr.setAttribute("aria-label", label);

      const openDetail = () => {
        openBatchCaseEvaluationModal(
          r.title || "Test case",
          detailPayload ?? { __batchError: true, message: r.error || "No evaluation data" },
          deps,
        );
      };
      tr.addEventListener("click", openDetail);
      tr.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          openDetail();
        }
      });

      tr.appendChild(titleTd);
      tr.appendChild(scoreTd);
      tr.appendChild(usageTd);
      batchSummaryTableBody.appendChild(tr);
    }

    renderEvalScoreBar(evalScoreBar, mean);
    if (evalScoreLabel) evalScoreLabel.textContent = nums.length ? mean.toFixed(1) : "—";
    if (evaluationStatus) evaluationStatus.textContent = "";
    setLastEvaluationPayload(null);
    updateEvaluateUi();
  }

  window.EvaluationUi = {
    displayBatchSummary,
    displayCaseEvaluation,
    fillBatchCaseEvaluationDetailBody,
    hideCaseEvalSubpanels,
    openBatchCaseEvaluationModal,
    openEvaluationDetailModal,
    scoreToHue,
    syncInlineEvaluationDetail,
  };
})();
