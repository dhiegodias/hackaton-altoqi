/* Search and review controls. Identity checks and enrichment live on the server. */
export function createOutboundUI({
  api,
  esc,
  safeLink,
  time,
  heading,
  toast,
  refresh,
  openLead,
  fields,
}) {
  const state = {
    selected: null,
    detail: null,
    query: { mode: "role_company", query: "", company: "", website: "" },
    key: null,
    keyBody: null,
  };
  const statusLabel = {
    queued: "Na fila",
    running: "Pesquisando",
    done: "Pesquisa concluída",
    failed: "Pesquisa não concluída",
  };
  function resultCard(item) {
    const existing = item.existing || [];
    const possible = item.possible_duplicates || [];
    const blocked =
      existing.some((row) => row.suppressed || row.demo) ||
      possible.some((row) => row.suppressed);
    const target =
      item.promoted_lead_id ||
      (!blocked && existing.length === 1 ? existing[0].id : null);
    const labels = { ...fields(), product_fit: "Fit de produto (hipótese)" };
    const proposed = new Map(item.proposals.map((p) => [p.field, p]));
    const standard = [
      "linkedin_person",
      "linkedin",
      "instagram",
      "website",
      "role",
      "decision_role",
      "segment",
      "cnpj",
      "capital_social",
      "employee_count",
      "employee_range",
      "product_fit",
    ];
    const rows = standard
      .map((field) => {
        const record = proposed.get(field);
        const identity =
          field === "website" || field === "linkedin_person" ? item[field] : "";
        return `<tr><td>${esc(labels[field] || field)}</td><td>${record ? esc(record.value) : identity ? esc(identity) : '<span class="muted">Não localizado</span>'}</td><td>${record ? `${record.confidence} pontos` : identity ? "Conferir vínculo" : "—"}</td><td>${record ? `<a href="${safeLink(record.source_url)}" target="_blank" rel="noreferrer">Fonte ↗</a><details><summary>Trecho e cálculo</summary><p>${esc(record.evidence)}</p><p>${esc(record.assessment.reason)}</p><p>Fonte + identificação + tipo + data + apoio: ${["autoridade_da_fonte", "identidade", "extração_ou_inferência", "atualidade", "corroboração_independente"].map((key) => esc(record.assessment.factors[key])).join(" + ")} = ${record.confidence}</p></details>` : "—"}</td></tr>`;
      })
      .join("");
    return `<article class="panel outbound-result" data-outbound-candidate="${item.id}"><div class="panel-head"><div><h2>${esc(item.name || item.email)}</h2><p>${esc(item.company)}${item.role ? " · Cargo encontrado na busca: " + esc(item.role) : ""}</p></div><span class="panel-tag">${target ? "Já está no Busqi" : "Novo possível contato"}</span></div><div class="calibration-pad"><p>${item.email ? `E-mail: ${esc(item.email)}` : "E-mail profissional não localizado."}${item.linkedin_person ? ` · <a href="${safeLink(item.linkedin_person)}" target="_blank" rel="noreferrer">LinkedIn da pessoa ↗</a>` : ""}</p><div class="notice ${item.identity_verified ? "" : "warning"}">${esc(item.identity_note)}${item.email_only ? " O nome da pessoa ainda não foi localizado; o e-mail foi informado por você." : ""}</div><blockquote><a href="${safeLink(item.source_url)}" target="_blank" rel="noreferrer">Conferir origem da identificação ↗</a><p>${esc(item.quote || "Pesquisa do domínio do e-mail informado; sem atribuir nome ou cargo à pessoa.")}</p><small>Coletado ${time(item.collected_at)}</small></blockquote><details open><summary>Campos para a abordagem comercial · ${item.proposals.length} sugestões</summary><div class="table-wrap"><table class="calibration-table"><thead><tr><th>Campo</th><th>Encontrado</th><th>Confiança</th><th>Evidência</th></tr></thead><tbody>${rows}</tbody></table></div></details>${item.notes.map((note) => `<p class="calibration-help">${esc(note)}</p>`).join("")}${blocked ? '<div class="notice warning">Este identificador já pertence a um contato demo ou em exclusão. A inclusão está bloqueada.</div>' : existing.length > 1 ? '<div class="notice warning">Os identificadores apontam para contatos diferentes. Confira os cadastros existentes.</div>' : target ? `<button class="button" data-action="outbound-open-lead" data-id="${target}">Abrir contato existente</button>` : possible.length ? `<div class="notice warning">Há um cadastro com o mesmo nome e empresa. Confira se é a mesma pessoa.</div>${possible.map((row) => `<button class="button" data-action="outbound-open-lead" data-id="${row.id}">Conferir ${esc(row.name)}</button>`).join("")}` : `<form data-outbound-add="${item.id}"><label class="form-field">Responsável pela conferência<input name="reviewer" maxlength="100" required placeholder="Seu nome"></label><label class="outbound-check"><input name="identity_confirmed" type="checkbox" required>Conferi a pessoa, a empresa e os links desta identificação.</label><button class="button primary" type="submit">Adicionar ao Busqi para revisão</button><p class="calibration-help">Site e perfil pessoal entram como identificação conferida. Os demais campos ficam como sugestões. A criação na HubSpot ainda depende de um fluxo externo; este botão cria somente no Busqi.</p></form>`}</div></article>`;
  }
  function results() {
    const search = state.detail;
    if (!search)
      return '<div class="empty">Informe um ponto de partida para procurar um novo contato.</div>';
    if (["queued", "running"].includes(search.status))
      return `<div class="notice"><strong>${statusLabel[search.status]}</strong><p>Você pode continuar usando o Busqi. Clique em Atualizar resultados para acompanhar; a pesquisa fica salva ao recarregar.</p></div>`;
    if (search.status === "failed")
      return `<div class="notice warning"><strong>Não foi possível concluir esta pesquisa</strong><p>${esc(search.error)}</p><p>Confira os dados ou a configuração e inicie uma nova pesquisa.</p></div>`;
    const items = search.result.candidates || [];
    return `<p class="calibration-help">${items.length} possíveis contatos · ${esc(search.result.provider || "Pesquisa pública")} · nenhum envio à HubSpot</p>${(search.result.notes || []).map((note) => `<div class="notice warning">${esc(note)}</div>`).join("")}${items.map((item) => resultCard(item)).join("") || '<div class="empty"><h2>Nenhum contato localizado com esse ponto de partida.</h2><p>Tente informar o site da empresa, outro cargo ou um identificador mais específico. Isso não significa que a pessoa não exista.</p></div>'}`;
  }
  async function page() {
    const data = await api("/outbound");
    if (!state.selected && data.items.length) state.selected = data.items[0].id;
    if (state.selected) state.detail = await api("/outbound/" + state.selected);
    return (
      heading(
        "Encontre a próxima conversa.",
        "Comece por LinkedIn, e-mail ou cargo e empresa. Pesquise antes de cadastrar.",
        "",
        "PROSPECÇÃO OUTBOUND",
      ) +
      `<section class="panel"><div class="calibration-pad"><form id="outbound-form"><div class="outbound-form-grid"><label class="form-field">Ponto de partida<select name="mode"><option value="role_company" ${state.query.mode === "role_company" ? "selected" : ""}>Cargo e empresa</option><option value="linkedin" ${state.query.mode === "linkedin" ? "selected" : ""}>LinkedIn da pessoa</option><option value="email" ${state.query.mode === "email" ? "selected" : ""}>E-mail</option></select></label><label class="form-field">O que você já sabe?<input name="query" value="${esc(state.query.query)}" required maxlength="350" placeholder="BIM Manager da Empresa X, e-mail ou linkedin.com/in/…"></label><label class="form-field">Empresa, se souber<input name="company" value="${esc(state.query.company)}" maxlength="250" placeholder="Ex.: AltoQi"></label><label class="form-field">Site da empresa, se souber<input name="website" value="${esc(state.query.website)}" maxlength="350" placeholder="https://empresa.com.br"></label></div><p class="calibration-help">Busca em fontes profissionais públicas. O identificador informado será enviado ao provedor de busca quando configurado. O Busqi apresenta até três candidatos e mantém as fontes para conferência.</p>${!data.search_configured ? '<div class="notice warning">Busca web ainda não configurada. A pesquisa direta funciona com empresa e site; um e-mail corporativo também permite consultar o domínio. Para descobrir pessoas somente por LinkedIn ou nome da empresa, configure o provedor no servidor.</div>' : ""}<button type="submit" class="button primary">Pesquisar novo contato</button></form></div></section><section class="panel"><div class="panel-head"><div><h2>Suas pesquisas</h2><p>A pesquisa não cadastra pessoas automaticamente.</p></div><button class="button small" data-action="outbound-refresh">Atualizar resultados</button></div><div class="calibration-pad"><label class="form-field">Pesquisa selecionada<select id="outbound-history"><option value="">Escolha uma pesquisa</option>${data.items.map((row) => `<option value="${row.id}" ${state.selected === row.id ? "selected" : ""}>${esc(row.query.query)}${row.query.company ? " · " + esc(row.query.company) : ""} — ${statusLabel[row.status]} · ${time(row.created_at)}</option>`).join("")}</select></label></div></section><div id="outbound-results">${results()}</div>`
    );
  }
  function input(target) {
    if (target.closest("#outbound-form"))
      state.query[target.name] = target.value;
  }
  async function change(target) {
    input(target);
    if (target.id === "outbound-history") {
      state.selected = target.value;
      state.detail = null;
      await refresh();
    }
  }
  async function submit(form) {
    if (form.id === "outbound-form") {
      const body = Object.fromEntries(new FormData(form));
      const signature = JSON.stringify(body);
      if (state.keyBody !== signature) {
        state.key = crypto.randomUUID();
        state.keyBody = signature;
      }
      const result = await api("/outbound", {
        method: "POST",
        headers: { "Idempotency-Key": state.key },
        body,
      });
      state.query = body;
      state.selected = result.id;
      state.key = null;
      state.keyBody = null;
      await refresh();
      toast("Pesquisa iniciada. Os resultados ficarão disponíveis nesta tela.");
      return true;
    }
    if (form.dataset.outboundAdd) {
      const item = state.detail.result.candidates.find(
        (row) => row.id === form.dataset.outboundAdd,
      );
      const result = await api(
        `/outbound/${state.detail.id}/candidates/${item.id}/add`,
        {
          method: "POST",
          body: {
            fingerprint: item.fingerprint,
            identity_confirmed: form.elements.identity_confirmed.checked,
            reviewer: form.elements.reviewer.value,
          },
        },
      );
      toast(
        result.duplicate
          ? "Contato existente localizado; nenhum cadastro duplicado."
          : "Contato adicionado ao Busqi. Confira os campos na revisão.",
      );
      await refresh();
      await openLead(result.id);
      return true;
    }
    return false;
  }
  async function action(action, button) {
    if (action === "outbound-refresh") {
      await refresh();
      return true;
    }
    if (action === "outbound-open-lead") {
      await openLead(button.dataset.id);
      return true;
    }
    return false;
  }
  return { page, input, change, submit, action };
}
