/* Presentation only: scoring, validation and comparisons are calculated by the API. */
export function createCalibrationUI({
  api,
  esc,
  safeLink,
  time,
  heading,
  icon,
  openModal,
  modalHead,
  toast,
  refresh,
  fields,
}) {
  const state = {
    draft: null,
    plan: null,
    reviewer: "",
    filter: "all",
    field: "",
    page: 0,
  };
  const judgmentLabels = {
    correct: "Correto",
    incorrect: "Incorreto",
    unknown: "Ainda não confirmei",
  };
  const statusLabels = {
    pending: "Em revisão no Busqi",
    approved: "Aprovado no Busqi",
    rejected: "Rejeitado no Busqi",
    superseded: "Substituído por outra sugestão",
  };
  const factorOrder = [
    "autoridade_da_fonte",
    "identidade",
    "extração_ou_inferência",
    "atualidade",
    "corroboração_independente",
  ];
  const percent = (value) =>
    value === null ? "Sem base" : `${value.toLocaleString("pt-BR")}%`;
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  function metricRows(current, proposed) {
    return [
      ["Exemplos conferidos", current.reviewed, proposed.reviewed],
      ["Passariam pelo mínimo", current.passed, proposed.passed],
      [
        "Corretos que passariam",
        current.correct_passed,
        proposed.correct_passed,
      ],
      [
        "Erros que passariam",
        current.incorrect_passed,
        proposed.incorrect_passed,
      ],
      [
        "Corretos que ficariam de fora",
        current.correct_held,
        proposed.correct_held,
      ],
      [
        "Acerto entre os que passariam",
        percent(current.accuracy),
        percent(proposed.accuracy),
      ],
      [
        "Bons dados aproveitados",
        percent(current.correct_recovery),
        percent(proposed.correct_recovery),
      ],
    ]
      .map(
        ([label, before, after]) =>
          `<tr><td>${label}</td><td>${before}</td><td>${after}</td></tr>`,
      )
      .join("");
  }
  function comparison() {
    const plan = state.plan;
    if (!plan)
      return '<div class="empty">Edite os pontos e clique em <strong>Comparar com exemplos revisados</strong>. A simulação não altera dados ou pesos salvos.</div>';
    const current = plan.current.overall,
      next = plan.proposed.overall;
    return `<div class="panel-head"><div><h2>Antes de salvar, compare</h2><p>${next.reviewed} exemplos conferidos · ${plan.unconfirmed} ainda sem confirmação · ${plan.pending_changes.length} sugestões pendentes mudariam de nota</p></div><span class="panel-tag">Versão atual ${plan.active_revision}</span></div><div class="calibration-pad">${!next.reviewed ? '<div class="notice warning">Nenhum exemplo foi marcado como correto ou incorreto. Você pode salvar os pesos, mas ainda não há dados para medir os acertos.</div>' : ""}<div class="table-wrap"><table class="calibration-table"><thead><tr><th>O que comparar</th><th>Pesos atuais</th><th>Pesos propostos</th></tr></thead><tbody>${metricRows(current, next)}</tbody></table></div><p class="calibration-help">Acerto = corretos que passariam ÷ todos que passariam × 100. Bons dados aproveitados = corretos que passariam ÷ todos os corretos × 100. Sem exemplos no divisor, mostramos “Sem base”.</p><p class="calibration-help">Mínimo geral: ${plan.policy.threshold} pontos.${
      Object.keys(plan.policy.field_thresholds).length
        ? " Exceções: " +
          Object.entries(plan.policy.field_thresholds)
            .map(
              ([field, value]) => `${esc(fields()[field] || field)}: ${value}`,
            )
            .join("; ") +
          "."
        : " Sem exceções por campo."
    } Esta conta compara a nota com o mínimo de cada campo. Identidade, data, conflitos e substituições continuam sendo verificados antes do preenchimento.</p><details><summary>Comparação por campo</summary><div class="table-wrap"><table class="calibration-table"><thead><tr><th>Campo</th><th>Exemplos</th><th>Acerto atual → proposto</th><th>Erros acima do mínimo</th><th>Bons dados aproveitados</th></tr></thead><tbody>${
      Object.entries(plan.proposed.fields)
        .map(
          ([field, count]) =>
            `<tr><td>${esc(fields()[field] || field)}</td><td>${count.reviewed}</td><td>${percent(plan.current.fields[field].accuracy)} → ${percent(count.accuracy)}</td><td>${plan.current.fields[field].incorrect_passed} → ${count.incorrect_passed}</td><td>${percent(plan.current.fields[field].correct_recovery)} → ${percent(count.correct_recovery)}</td></tr>`,
        )
        .join("") ||
      '<tr><td colspan="5">Nenhum campo com exemplo confirmado.</td></tr>'
    }</tbody></table></div></details><label class="form-field">Motivo desta mudança<textarea id="scoring-note" maxlength="1000" rows="2" placeholder="O que vocês observaram nos exemplos?"></textarea></label><button class="button primary" data-action="confirm-scoring">Revisar e publicar pesos</button><p class="calibration-help">A versão publicada vale para novas pesquisas e recalcula as sugestões pendentes. Aprovações anteriores e seus cálculos são preservados. Publicar não preenche campos nem envia à HubSpot.</p></div>`;
  }
  function filterRows() {
    return state.samples.items.filter(
      (item) =>
        (!state.field || item.field === state.field) &&
        (state.filter === "all" ||
          (state.filter === "unreviewed"
            ? !item.review
            : item.review?.judgment === state.filter)),
    );
  }
  function sampleCard(item) {
    const result = state.plan?.calculations.find((row) => row.id === item.id);
    const calculation = result?.proposed;
    const factors = calculation?.factors || item.assessment.factors || {};
    const score = calculation?.confidence ?? item.confidence;
    return `<article class="calibration-sample" data-sample-id="${item.id}"><div class="proposal-top"><div><strong>${esc(item.name || item.company)}</strong><p class="muted">${esc(item.company)} · ${esc(fields()[item.field] || item.field)}</p></div><span class="fit-tag ${item.review?.judgment === "correct" ? "teal" : "gray"}">${esc(judgmentLabels[item.review?.judgment] || "Não avaliado")}</span></div><h3>${esc(item.value)}</h3><p class="calibration-help">${esc(statusLabels[item.status])} · a avaliação de acerto abaixo é independente dessa decisão.</p>${!same(item.value, item.current_value) ? `<div class="notice">O valor foi editado para “${esc(item.current_value)}”. Avalie aqui a proposta original mostrada acima.</div>` : ""}<div class="calculation"><span>${calculation ? "Com os pesos propostos" : `Cálculo registrado${item.assessment.scoring_revision ? " · versão " + item.assessment.scoring_revision : ""}`}</span><strong>${factorOrder.map((key) => esc(factors[key])).join(" + ")} = ${score}</strong><small>Fonte + identificação + tipo + data + apoio de outra origem</small></div><details><summary>Conferir evidências e motivo</summary><p>${esc(item.assessment.reason)}</p>${(item.assessment.evidence || []).map((proof) => `<blockquote><a href="${safeLink(proof.url)}" target="_blank" rel="noreferrer">${esc(proof.title || proof.url)} ↗</a><p>${esc(proof.quote)}</p><small>${proof.published_at ? "Publicado em " + esc(proof.published_at) : "Sem data de publicação"} · coletado ${time(proof.accessed_at)}</small></blockquote>`).join("")}</details>${item.stale_review ? '<div class="notice warning">A proposta ou sua evidência mudou desde a última avaliação. Confira novamente para incluir este exemplo na comparação.</div>' : ""}${item.review ? `<p class="calibration-help">Última avaliação: ${esc(item.review.reviewer)} · ${time(item.review.created_at)}${item.review.note ? " · " + esc(item.review.note) : ""}</p>` : ""}<form data-calibration-review="${item.id}"><label class="form-field">O valor proposto está correto?<select name="judgment" required><option value="" ${!item.review ? "selected" : ""}>Escolha após conferir</option>${Object.entries(
      judgmentLabels,
    )
      .map(
        ([value, label]) =>
          `<option value="${value}" ${item.review?.judgment === value ? "selected" : ""}>${label}</option>`,
      )
      .join(
        "",
      )}</select></label><label class="form-field">O que foi conferido?<textarea name="note" rows="2" maxlength="1000" placeholder="Se estiver incorreto, explique o motivo.">${esc(item.review?.note || "")}</textarea></label><button class="button small" type="submit">Salvar avaliação deste exemplo</button></form></article>`;
  }
  function sampleList() {
    const items = filterRows(),
      pages = Math.max(1, Math.ceil(items.length / 6));
    state.page = Math.min(state.page, pages - 1);
    return `<div class="calibration-samples">${
      items
        .slice(state.page * 6, state.page * 6 + 6)
        .map(sampleCard)
        .join("") ||
      '<div class="empty">Nenhum exemplo neste filtro. Pesquise contatos reais com Backfill para obter sugestões com evidências. Dados demo ficam fora da calibração.</div>'
    }</div><div class="calibration-pagination"><span>${items.length} exemplos · página ${state.page + 1} de ${pages}</span><div class="actions"><button class="button small" data-action="calibration-previous" ${state.page ? "" : "disabled"}>Anterior</button><button class="button small" data-action="calibration-next" ${state.page < pages - 1 ? "" : "disabled"}>Próxima</button></div></div>`;
  }
  function updateList() {
    const list = document.querySelector("#calibration-sample-list");
    if (list) list.innerHTML = sampleList();
  }
  async function page() {
    [state.settings, state.samples] = await Promise.all([
      api("/scoring"),
      api("/calibration/samples"),
    ]);
    if (!state.draft) state.draft = { ...state.settings.weights };
    const reviewed = state.samples.items.filter((item) =>
      ["correct", "incorrect"].includes(item.review?.judgment),
    ).length;
    const reviewedFields = new Set(
      state.samples.items
        .filter((item) => item.review && item.review.judgment !== "unknown")
        .map((item) => item.field),
    ).size;
    return (
      heading(
        "Pesos que a equipe pode ajustar.",
        "Confira exemplos reais, compare o efeito dos pontos e publique uma versão.",
        `<button class="button" data-action="refresh-calibration">Atualizar exemplos</button>`,
        "PESOS E CALIBRAÇÃO",
      ) +
      `<div class="notice">A nota soma cinco partes. Em cada parte, apenas uma opção conta. “Correto” e “Incorreto” são avaliações explícitas da equipe; aprovar no CRM não marca um exemplo como correto.</div><div class="calibration-overview"><div class="panel source-card"><span class="eyebrow">EXEMPLOS CONFERIDOS</span><div class="stat-number">${reviewed}<span class="calibration-inline"> / ${state.samples.items.length}</span></div><p>${reviewedFields} campos com avaliações. Mais exemplos variados ajudam a entender os erros. As taxas descrevem esta amostra; não garantem o resultado em outros leads.</p></div><div class="panel source-card"><span class="eyebrow">RESPONSÁVEL PELA CONFERÊNCIA</span><label class="form-field">Seu nome<input id="calibration-reviewer" maxlength="100" value="${esc(state.reviewer)}" placeholder="Quem conferiu os exemplos?"></label><p>Fica registrado junto de cada avaliação e da publicação dos pesos.</p></div></div><section class="panel"><div class="panel-head"><div><h2>1. Ajuste os pontos</h2><p>Versão publicada: ${state.settings.revision} · máximo atual: ${state.settings.maximum}/100</p></div><button class="button small" data-action="reset-scoring">Voltar aos pesos publicados</button></div><form id="scoring-form" class="calibration-pad"><div class="weight-groups">${state.settings.groups.map((group) => `<fieldset class="weight-group"><legend>${esc(group.label)}</legend>${group.options.map((option) => `<label><span>${esc(option.label)}</span><input aria-label="${esc(option.label)}" type="number" min="0" max="100" required data-weight="${option.key}" value="${state.draft[option.key]}"></label>`).join("")}${group.options.length === 1 ? '<p class="calibration-help">Sem outra origem de apoio: 0 pontos.</p>' : ""}</fieldset>`).join("")}</div><p class="calibration-help">Os maiores valores dos cinco grupos devem somar no máximo 100. Dentro de cada grupo, evidência mais fraca não pode valer mais que a mais forte. A nota mínima para preencher continua em <button type="button" class="button text" data-view="backfill">Backfill → Política de preenchimento</button>.</p><button class="button primary" type="submit">Comparar com exemplos revisados</button></form></section><section class="panel" id="scoring-comparison">${comparison()}</section><section class="panel"><div class="panel-head"><div><h2>2. Confira os exemplos da equipe</h2><p>Leia o valor original e a fonte. “Ainda não confirmei” fica fora da conta de acertos.</p></div></div><div class="calibration-filters"><label class="form-field">Campo<select id="calibration-field-filter"><option value="">Todos os campos</option>${[
        ...new Set(state.samples.items.map((item) => item.field)),
      ]
        .sort()
        .map(
          (field) =>
            `<option value="${field}" ${state.field === field ? "selected" : ""}>${esc(fields()[field] || field)}</option>`,
        )
        .join(
          "",
        )}</select></label><label class="form-field">Avaliação<select id="calibration-label-filter">${Object.entries(
        { all: "Todas", unreviewed: "Não avaliados", ...judgmentLabels },
      )
        .map(
          ([value, label]) =>
            `<option value="${value}" ${state.filter === value ? "selected" : ""}>${label}</option>`,
        )
        .join(
          "",
        )}</select></label></div>${state.samples.available > state.samples.limit ? `<div class="notice warning">Mostrando os ${state.samples.limit} exemplos mais recentes de ${state.samples.available}; as comparações usam apenas essa seleção.</div>` : ""}<div id="calibration-sample-list">${sampleList()}</div></section><section class="panel"><div class="panel-head"><div><h2>Versões publicadas</h2><p>Carregar uma versão antiga abre um rascunho para comparar; não substitui os pesos atuais.</p></div></div><div class="calibration-pad">${state.settings.history.map((version) => `<div class="score-version"><div><strong>Versão ${version.revision}</strong><p>${esc(version.reviewer)} · ${time(version.created_at)}</p><p>${esc(version.note)}</p></div><button class="button small" data-action="load-scoring-version" data-revision="${version.revision}">Carregar para comparar</button></div>`).join("")}</div></section>`
    );
  }
  function invalidate() {
    state.plan = null;
    const node = document.querySelector("#scoring-comparison");
    if (node) node.innerHTML = comparison();
    updateList();
  }
  function input(target) {
    if (target.id === "calibration-reviewer") state.reviewer = target.value;
    if (target.dataset.weight) {
      state.draft[target.dataset.weight] = Number(target.value);
      invalidate();
    }
  }
  function change(target) {
    if (target.id === "calibration-field-filter") {
      state.field = target.value;
      state.page = 0;
      updateList();
    }
    if (target.id === "calibration-label-filter") {
      state.filter = target.value;
      state.page = 0;
      updateList();
    }
  }
  async function submit(form) {
    if (form.id === "scoring-form") {
      state.plan = await api("/scoring/preview", {
        method: "POST",
        body: { weights: state.draft },
      });
      document.querySelector("#scoring-comparison").innerHTML = comparison();
      updateList();
      document
        .querySelector("#scoring-comparison")
        .scrollIntoView({ block: "start" });
      return true;
    }
    if (form.dataset.calibrationReview) {
      if (!state.reviewer.trim())
        throw new Error(
          "Informe seu nome no campo Responsável pela conferência.",
        );
      const item = state.samples.items.find(
        (row) => row.id === form.dataset.calibrationReview,
      );
      await api("/calibration/samples/" + item.id + "/review", {
        method: "POST",
        body: {
          fingerprint: item.fingerprint,
          expected_review_id: item.last_review_id,
          judgment: form.elements.judgment.value,
          reviewer: state.reviewer,
          note: form.elements.note.value,
        },
      });
      invalidate();
      toast(
        "Avaliação salva. Ela será usada na próxima comparação; o cadastro não foi alterado.",
      );
      await refresh();
      return true;
    }
    return false;
  }
  async function action(action, button) {
    if (action === "calibration-next" || action === "calibration-previous") {
      state.page += action === "calibration-next" ? 1 : -1;
      updateList();
      document
        .querySelector("#calibration-sample-list")
        .scrollIntoView({ block: "start" });
      return true;
    }
    if (action === "refresh-calibration") {
      invalidate();
      await refresh();
      return true;
    }
    if (action === "reset-scoring") {
      state.draft = { ...state.settings.weights };
      invalidate();
      await refresh();
      return true;
    }
    if (action === "load-scoring-version") {
      state.draft = {
        ...state.settings.history.find(
          (row) => String(row.revision) === button.dataset.revision,
        ).weights,
      };
      invalidate();
      await refresh();
      return true;
    }
    if (action === "confirm-scoring") {
      if (!state.plan) throw new Error("Compare os pesos antes de publicar.");
      state.note = document.querySelector("#scoring-note").value.trim();
      if (!state.reviewer.trim() || !state.note)
        throw new Error("Informe seu nome e o motivo da mudança.");
      const labels = Object.fromEntries(
        state.settings.groups.flatMap((group) =>
          group.options.map((option) => [option.key, option.label]),
        ),
      );
      openModal(
        `${modalHead("Publicar os pesos conferidos")}<p>Responsável: ${esc(state.reviewer)}. ${state.plan.pending_changes.length} sugestões pendentes mudarão de nota. O cadastro e a HubSpot não serão preenchidos por esta ação.</p><div class="sync-rows">${
          Object.entries(state.plan.proposed_weights)
            .filter(([key, value]) => value !== state.plan.current_weights[key])
            .map(
              ([key, value]) =>
                `<div class="sync-row"><span>${esc(labels[key])}</span><strong>${state.plan.current_weights[key]} → ${value}</strong></div>`,
            )
            .join("") || "<p>Nenhum peso diferente da versão atual.</p>"
        }</div><p>${state.plan.proposed.overall.reviewed} exemplos conferidos · erros que passariam: ${state.plan.current.overall.incorrect_passed} → ${state.plan.proposed.overall.incorrect_passed}.</p><div class="modal-actions"><button class="button" data-action="close-modal">Voltar</button><button class="button primary" data-action="publish-scoring">Publicar esta versão</button></div>`,
      );
      return true;
    }
    if (action === "publish-scoring") {
      const result = await api("/scoring/publish", {
        method: "POST",
        body: {
          weights: state.plan.proposed_weights,
          fingerprint: state.plan.fingerprint,
          reviewer: state.reviewer,
          note: state.note,
        },
      });
      document.querySelector("#modal").close();
      state.draft = { ...result.weights };
      invalidate();
      toast(
        `Pesos publicados na versão ${result.revision}. ${result.pending_rescored} sugestões pendentes recalculadas.`,
      );
      await refresh();
      return true;
    }
    return false;
  }
  return { page, submit, action, input, change };
}
