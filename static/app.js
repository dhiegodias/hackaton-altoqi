import { createTransfersUI } from "./transfers.js?v=busqi-1";
import { createStandardUI } from "./standard.js?v=busqi-1";
import { createCalibrationUI } from "./calibration.js?v=busqi-1";
import { createOutboundUI } from "./outbound.js?v=busqi-1";
import { createAuditUI } from "./audit.js?v=busqi-1";
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const paths = {
  radar:
    '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4"/><path d="M12 12V3m0 9 6-6"/>',
  users:
    '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2m20 0v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/><circle cx="9" cy="7" r="4"/>',
  checklist:
    '<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 3h6v3H9zM9 13l2 2 4-4"/>',
  phone:
    '<rect x="6" y="2" width="12" height="20" rx="3"/><path d="M10 18h4M10 5h4"/>',
  sliders:
    '<path d="M4 5h8m4 0h4M4 12h2m4 0h10M4 19h10m4 0h2"/><circle cx="14" cy="5" r="2"/><circle cx="8" cy="12" r="2"/><circle cx="16" cy="19" r="2"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  upload:
    '<path d="M12 16V3m-4 4 4-4 4 4M4 15v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5"/>',
  arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  spark:
    '<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  link: '<path d="m10 13 4-4M8 16l-1 1a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0m0 2 1-1a4 4 0 0 1 6 6l-5 5a4 4 0 0 1-6 0" transform="translate(2 1)"/>',
  shield:
    '<path d="m12 2 8 4v6c0 6-8 10-8 10S4 18 4 12V6l8-4Z"/><path d="m8 12 3 3 5-6"/>',
  cloud: '<path d="M7 18a5 5 0 0 1-1-10 7 7 0 0 1 13 1 4.5 4.5 0 0 1 0 9H7Z"/>',
  chart: '<path d="M4 19V5m0 14h16M8 15v-3m4 3V8m4 7V5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  edit: '<path d="m15 4 5 5M4 20l5-1L21 7a2 2 0 0 0-5-5L4 14v6Z"/>',
  download: '<path d="M12 3v12m-4-4 4 4 4-4M4 17v4h16v-4"/>',
  building:
    '<path d="M3 21h18M5 21V3h14v18M9 7h1m4 0h1M9 11h1m4 0h1M9 15h1m4 0h1M10 21v-3h4v3"/>',
};
const icon = (name) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.spark}</svg>`;
$$("[data-icon]").forEach((node) => (node.innerHTML = icon(node.dataset.icon)));
const state = {
  view: location.hash.slice(1) || "dashboard",
  leads: [],
  stats: {},
  config: { fields: {} },
  filter: "all",
  query: "",
  selected: null,
  leadPage: 1,
  pagination: { page: 1, pages: 1, total: 0 },
};
const names = {
  dashboard: "Radar da base",
  contacts: "Contatos",
  transfers: "Listas e exportações",
  outbound: "Prospecção outbound",
  review: "Revisão",
  backfill: "Backfill",
  calibration: "Pesos e calibração",
  sources: "Fontes e regras",
  standard: "Padrão de dados",
  audit: "Auditoria",
};
const sourceNames = {
  demo: "Demonstração fictícia",
  registry: "BrasilAPI · cadastro público",
  website: "Site informado",
  ai: "Extração por IA · revisar",
  consultant: "Declaração do consultor",
  import: "Lista importada",
  playbook: "Hipótese dos playbooks",
  backfill: "Pesquisa e inferência",
};
const actionNames = {
  created: "Contato recebido",
  suggested: "Sugestão encontrada",
  scanned: "Pesquisa concluída",
  approved: "Campo aprovado",
  rejected: "Sugestão rejeitada",
  simulated: "Envio simulado",
  sent: "Envio confirmado pela HubSpot",
  suppressed: "Contato incluído na exclusão",
  capture_merged: "Conversa adicionada ao contato",
  sources_updated: "Fontes atualizadas",
  backfill_policy_updated: "Política de backfill atualizada",
  backfill_applied: "Backfill aplicado à base local",
  scoring_published: "Pesos publicados",
  calibration_reviewed: "Exemplo conferido pela equipe",
  outbound_requested: "Pesquisa outbound solicitada",
  outbound_completed: "Pesquisa outbound concluída",
  outbound_added: "Contato de prospecção conferido",
};
const time = (value) =>
  value
    ? new Date(value).toLocaleString("pt-BR", {
        day: "2-digit",
        month: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "—";
const valueText = (field, value) =>
  value === null || value === undefined || value === ""
    ? "Não informado"
    : field === "capital_social"
      ? Number(value).toLocaleString("pt-BR", {
          style: "currency",
          currency: "BRL",
          maximumFractionDigits: 0,
        })
      : String(value);
function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.classList.remove("show"), 5000);
}
async function api(path, options = {}) {
  const opts = { ...options, headers: { ...options.headers } };
  if (opts.body && !(opts.body instanceof FormData)) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(opts.body);
  }
  const response = await fetch("/api" + path, opts);
  let data;
  try {
    data = await response.json();
  } catch {
    data = { detail: "Resposta inesperada do servidor." };
  }
  if (!response.ok) {
    if (response.status === 401) showLogin();
    const detail = Array.isArray(data.detail)
      ? data.detail.map((item) => item.msg).join("; ")
      : data.detail;
    throw new Error(detail || "Não foi possível concluir. Tente novamente.");
  }
  return data;
}
function safeLink(url) {
  try {
    const parsed = new URL(url);
    return ["https:", "http:"].includes(parsed.protocol)
      ? esc(parsed.href)
      : "#";
  } catch {
    return "#";
  }
}
function openModal(content) {
  $("#modal-content").innerHTML = content;
  const modal = $("#modal");
  if (!modal.open) modal.showModal();
}
const modalHead = (title) =>
  `<div class="modal-header"><h2>${esc(title)}</h2><button class="close-button" data-action="close-modal" aria-label="Fechar">${icon("close")}</button></div>`;
function showLogin() {
  openModal(
    `${modalHead("Entrar no Busqi")}<p>Use o token da equipe configurado neste ambiente.</p><form id="login-form"><label class="form-field">Token de acesso<input name="token" type="password" autocomplete="current-password" required></label><div class="modal-actions"><button class="button primary" type="submit">Entrar</button></div></form>`,
  );
}
let loadVersion = 0;
async function loadContacts() {
  const version = ++loadVersion;
  const params = new URLSearchParams({
    page: state.leadPage,
    page_size: 50,
    q: state.query,
    filter_by: state.view === "review" ? "pending" : state.filter,
  });
  const result = await api("/leads/page?" + params);
  if (version !== loadVersion) return;
  state.pagination = result;
  state.leads = result.items;
}
async function reload() {
  await Promise.all([
    loadContacts(),
    api("/stats").then((result) => (state.stats = result)),
  ]);
  $("#review-count").textContent = state.stats.suggestions.pending || 0;
  $("#live-indicator").innerHTML =
    "<i></i>Atualizado " +
    new Date().toLocaleTimeString("pt-BR", {
      hour: "2-digit",
      minute: "2-digit",
    });
  await render();
}
function heading(
  title,
  subtitle,
  buttons = "",
  eyebrow = "INTELIGÊNCIA COMERCIAL",
) {
  return `<div class="heading"><div><div class="eyebrow">${eyebrow}</div><h1>${title}</h1><p class="subtitle">${subtitle}</p></div><div class="actions">${buttons}</div></div>`;
}
function metric(label, number, caption, type, change = "") {
  return `<div class="stat"><div class="stat-head">${label}${icon(type)}</div><div class="stat-number">${number}${change ? `<span class="stat-change">${change}</span>` : ""}</div><div class="stat-caption">${caption}</div></div>`;
}
function fitStyle(group) {
  return group.startsWith("ICP")
    ? "teal"
    : group.includes("PBA")
      ? "gray"
      : group.includes("qualificar")
        ? "amber"
        : "";
}
function shortGroup(group) {
  return group
    .replace("ICP 1 Escritório M", "ICP · Escritório M")
    .replace("ICP 2 Escritório G", "ICP · Escritório G")
    .replace("PA2 Escritório P", "PA2 · Escritório P");
}
function filtered() {
  return state.leads;
}
function contactsPagination() {
  const p = state.pagination;
  return `<div class="table-foot" id="contacts-pagination"><span>${p.total.toLocaleString("pt-BR")} contatos · página ${p.page} de ${p.pages}</span><div class="filters"><button class="button small" data-action="contacts-prev" ${p.page <= 1 ? "disabled" : ""}>Anterior</button><button class="button small" data-action="contacts-next" ${p.page >= p.pages ? "disabled" : ""}>Próxima</button></div></div>`;
}
function tableRows() {
  const leads = filtered();
  if (!leads.length)
    return '<tr><td colspan="5"><div class="empty">Nenhum contato neste filtro.</div></td></tr>';
  return leads
    .map(
      (lead, i) =>
        `<tr><td><div class="company-cell"><span class="company-logo tone-${i % 5}">${esc(
          (lead.company || lead.name)
            .split(/\s/)
            .slice(0, 2)
            .map((x) => x[0])
            .join(""),
        )}</span><div><button class="company-name" data-action="open-lead" data-id="${lead.id}">${esc(lead.company || "Empresa a identificar")}</button><div class="company-sub">${esc(lead.name || lead.email || "Contato a identificar")}</div></div></div></td><td><span class="fit-tag ${fitStyle(lead.fit.group)}">${esc(lead.data.product_fit && !lead.fit.offers.length ? "Hipótese pública registrada" : shortGroup(lead.fit.group))}</span><div class="fit-line">${esc(lead.data.product_fit && !lead.fit.offers.length ? lead.data.product_fit : lead.fit.route)}</div></td><td class="coverage-cell"><div class="coverage"><div class="tiny-track"><span style="width:${lead.completeness}%"></span></div>${lead.completeness}%</div></td><td><span class="pending-label ${lead.pending ? "" : "clear"}">${lead.pending ? `${lead.pending} sugestões` : "A pesquisar"}</span></td><td><button class="open-lead" data-action="open-lead" data-id="${lead.id}" aria-label="Abrir ${esc(lead.company || lead.name)}">${icon("arrow")}</button></td></tr>`,
    )
    .join("");
}
function table() {
  return `<section class="panel"><div class="panel-head"><div><h2>Contatos no radar</h2><p>O contexto de cada empresa, em um só lugar.</p></div><span class="panel-tag">${state.pagination.total.toLocaleString("pt-BR")} contatos</span></div><div class="toolbar"><div class="filters">${[
    ["all", "Todos"],
    ["priority", "Prioridade"],
    ["pending", "Com sugestões"],
  ]
    .map(
      ([key, label]) =>
        `<button class="filter ${state.filter === key ? "active" : ""}" data-action="filter" data-value="${key}">${label}${key === "all" ? `<span>${state.stats.total}</span>` : ""}</button>`,
    )
    .join(
      "",
    )}</div><label class="search">${icon("search")}<input id="lead-search" placeholder="Buscar empresa ou contato" value="${esc(state.query)}" aria-label="Buscar contatos"></label></div><div class="table-wrap"><table class="lead-table"><thead><tr><th>EMPRESA / CONTATO</th><th>PERFIL E ENCAMINHAMENTO</th><th class="coverage-cell">COMPLETUDE</th><th>STATUS</th><th></th></tr></thead><tbody id="lead-rows">${tableRows()}</tbody></table></div><div class="table-foot"><span>Dados aprovados e hipóteses comerciais identificadas.</span><span>${state.stats.demo_count || 0} exemplos fictícios</span></div>${contactsPagination()}</section>`;
}
function coveragePanel() {
  const stats = state.stats;
  const selected = [
    "website",
    "role",
    "segment",
    "cnpj",
    "employee_count",
    "linkedin",
  ];
  return `<section class="panel coverage-panel"><h2>Qualidade da base</h2><p>Completude por campo</p><div class="coverage-legend"><span><i></i>Na entrada</span><span><i class="after"></i>Após aprovação</span></div><div class="bar-grid">${stats.fields
    .filter((item) => selected.includes(item.field))
    .map((item) => {
      const before = Math.round((item.before / (stats.total || 1)) * 100),
        after = Math.round((item.after / (stats.total || 1)) * 100);
      return `<div class="field-bar"><div class="field-bar-label"><span>${esc(item.label.replace("da empresa", "").trim())}</span><strong>${before}% <span class="muted">→</span> ${after}%</strong></div><div class="bar-track"><span class="bar-after" style="width:${after}%"></span><span class="bar-before" style="width:${before}%"></span></div></div>`;
    })
    .join(
      "",
    )}</div><p class="coverage-bottom">Campos entram nesta conta após revisão<br>ou aplicação da política de backfill.</p></section>`;
}
function dashboard() {
  const s = state.stats;
  return (
    heading(
      "Radar da base",
      "Dados atualizados. O contexto certo para a próxima conversa.",
      `<button class="button" data-action="import">${icon("upload")}Importar lista</button><button class="button primary" data-action="scan">${icon("radar")}Rodar radar</button>`,
    ) +
    `<div class="flow-line"><span class="step"><span class="step-dot">${icon("link")}</span><strong>Fontes e conversas</strong></span><span class="connector"></span><span class="step"><span class="step-dot">${s.suggestions.pending || 0}</span><strong>Revisão da equipe</strong></span><span class="connector"></span><span class="step"><span class="step-dot">${icon("check")}</span><strong>CRM atualizado</strong></span><span class="flow-note">${icon("shield")}Cada campo tem uma história.</span></div><div class="stats">${metric("Contatos na base", s.total, "Empresas e contatos identificados", "users")}${metric("Completude da base", s.completeness + "%", "Apenas campos presentes e aprovados", "chart", `+${s.completeness - s.baseline_completeness} p.p.`)}${metric("Sugestões em revisão", s.suggestions.pending || 0, "Com fonte para você conferir", "checklist")}${metric("Envios confirmados", s.syncs.sent || 0, `${s.syncs.simulated || 0} simulações · HubSpot ${state.config.hubspot_connected ? "conectada" : "não conectada"}`, "cloud")}</div><div class="dashboard-grid">${table()}<div class="side-stack">${coveragePanel()}<section class="conversation-card"><div class="symbol">${icon("spark")}</div><h2>O dado que falta pode estar na conversa.</h2><p>O consultor completa o contexto. O Busqi encontra o próximo passo.</p><a href="/campo">Capturar um contato <span>↗</span></a></section></div></div>`
  );
}
function contacts() {
  return (
    heading(
      "Contatos",
      "Uma base compartilhada entre pesquisa e conversas em campo.",
      `<button class="button" data-action="queue-export">${icon("download")}Exportar aprovados</button><button class="button" data-action="import">${icon("upload")}Importar lista</button><a class="button primary" href="/campo">${icon("plus")}Capturar contato</a>`,
    ) + table()
  );
}
function reviewPage() {
  const pending = state.leads.filter((lead) => lead.pending);
  return (
    heading(
      "Cada mudança, uma decisão.",
      "Confira origem, atualidade e identidade antes de atualizar sua base.",
      `<button class="button" data-action="queue-export">${icon("download")}Exportar aprovados</button>`,
      "FILA DE REVISÃO",
    ) +
    `<section class="panel"><div class="panel-head"><div><h2>${state.stats.suggestions.pending || 0} sugestões aguardando revisão</h2><p>Conflitos exigem confirmação individual. A pesquisa preserva o dado existente.</p></div></div><div class="review-list">${pending.map((lead, i) => `<div class="review-contact"><span class="company-logo tone-${i % 5}">${esc(lead.company.slice(0, 2).toUpperCase())}</span><div class="review-contact-info"><strong>${esc(lead.company)}</strong><p>${esc(lead.name)} · ${lead.pending} campos ${lead.conflicts ? `· ${lead.conflicts} com valor existente` : ""}</p></div><span class="fit-tag ${fitStyle(lead.fit.group)}">${esc(shortGroup(lead.fit.group))}</span><button class="button soft" data-action="open-lead" data-id="${lead.id}">Revisar ${icon("arrow")}</button></div>`).join("") || '<div class="empty"><h2>Revisão em dia.</h2>Rode o radar ou importe uma lista para encontrar novas sugestões.</div>'}</div>${contactsPagination()}</section>`
  );
}
async function sourcesPage() {
  const sources = await api("/sources");
  return (
    heading(
      "Fontes e regras",
      "Pesquisa delimitada, evidências visíveis e critérios do seu negócio.",
      "",
      "PADRÃO DE DADOS",
    ) +
    `<div class="notice">O modo de demonstração usa fontes fictícias identificadas. Os controles abaixo só alteram a coleta de contatos reais.</div><form id="sources-form"><div class="grid-two">${[
      [
        "registry",
        "building",
        "Cadastro público · BrasilAPI",
        "Consulta CNPJ numérico informado. Retorna capital social, CNAE, porte cadastral e cidade. Não informa quantidade de funcionários.",
      ],
      [
        "website",
        "link",
        "Site informado da empresa",
        "Lê a página pública informada. Extrai metadados da organização, CNPJ e links sociais corporativos. Não raspa perfis pessoais.",
      ],
      [
        "backfill",
        "radar",
        "Backfill com confiança e evidências",
        "Navega por páginas institucionais e de equipe. Sugere cargo, papel provável na compra, faixa de funcionários e fit inicial. Ative também Site informado; configure os limiares na aba Backfill.",
      ],
      [
        "ai",
        "spark",
        "Extração assistida por IA",
        "Com Backfill ativo, interpreta cargos e atividades nas páginas coletadas. Os trechos são verificados e a confiança é calculada por regras. A busca por nome pode ser ligada na aba Backfill.",
      ],
    ]
      .map(
        ([key, type, title, description]) =>
          `<section class="panel source-card">${icon(type)}<h2>${title}</h2><p>${description}</p><label class="switch-label"><input type="checkbox" name="${key}" ${sources[key] ? "checked" : ""}>${sources[key] ? "Fonte ativada" : "Ativar fonte"}${key === "ai" ? ` <span class="fit-tag gray">${state.config.ai_configured ? "Configurada" : "Chave não configurada"}</span>` : ""}</label></section>`,
      )
      .join(
        "",
      )}<section class="panel source-card">${icon("cloud")}<h2>HubSpot</h2><p>${state.config.hubspot_connected ? "Credencial configurada no servidor. Importe contatos e revise cada mudança antes de enviar." : "Integração preparada. Configure a credencial e o mapeamento de propriedades no servidor para conectar seu portal."}</p><span class="fit-tag ${state.config.hubspot_connected ? "teal" : "gray"}">${state.config.hubspot_connected ? "Conectada" : "Não conectada"}</span><button class="button small" type="button" data-action="hubspot-import" ${state.config.hubspot_connected ? "" : "disabled"}>Importar contatos</button></section></div><div class="actions" style="margin-top:20px"><button class="button primary" type="submit">Salvar fontes</button></div></form><section class="panel source-card" style="margin-top:25px"><h2>Um cadastro, duas leituras comerciais</h2><ul class="rules-list"><li>Escritórios: disciplinas e número de projetistas. Funcionários totais não substituem a equipe de projeto.</li><li>Gestão: capacidade de absorver o método, uso de BIM e dor concreta. Capital social não define o perfil.</li><li>Cargo não confirma poder de decisão. O consultor registra a alçada e a participação na compra.</li><li>Fit é uma hipótese revisável; não entra na completude factual.</li><li>O backfill pode preencher lacunas locais pela política de confiança. O envio à HubSpot mantém prévia e confirmação separadas.</li></ul><div class="actions"><a class="button small" href="https://solution-playbook.vercel.app/escritorios/icp.html" target="_blank" rel="noreferrer">Playbook de escritórios ↗</a><a class="button small" href="https://solutionplaybookgestaodaconstrucao.netlify.app/" target="_blank" rel="noreferrer">Playbook de gestão ↗</a><span class="muted" style="font-size:10px">Regras ${esc(state.config.rule_version)}</span></div></section>`
  );
}
function assessmentDetails(assessment) {
  if (!assessment?.version) return "";
  const kind =
    {
      fact: "Fato publicado",
      estimate: "Estimativa / faixa",
      inference: "Hipótese",
    }[assessment.kind] || "Sugestão";
  const labels = {
    autoridade_da_fonte: "Fonte",
    identidade: "Identificação",
    extração_ou_inferência: "Tipo do dado",
    atualidade: "Data",
    corroboração_independente: "Apoio de outra origem",
  };
  return `<div class="assessment"><span class="fit-tag ${assessment.kind === "inference" ? "amber" : "teal"}">${kind}</span><p>${esc(assessment.reason)}</p><details><summary>Evidências e cálculo da confiança</summary><div class="score-factors">${Object.entries(
    assessment.factors || {},
  )
    .map(
      ([key, value]) =>
        `<span>${esc(labels[key] || key)} <strong>${esc(value)}</strong></span>`,
    )
    .join(
      "",
    )}</div>${(assessment.evidence || []).map((proof) => `<blockquote><a href="${safeLink(proof.url)}" target="_blank" rel="noreferrer">${esc(proof.title || proof.url)} ↗</a><p>${esc(proof.quote)}</p><small>${proof.published_at ? `Publicação: ${esc(proof.published_at)}` : "Sem data de publicação"} · coletado ${time(proof.accessed_at)}</small></blockquote>`).join("")}${assessment.playbook ? `<a href="${safeLink(assessment.playbook)}" target="_blank" rel="noreferrer">Regra comercial no playbook ↗</a>` : ""}${assessment.missing_context?.length ? `<p>A qualificar: ${assessment.missing_context.map(esc).join(" · ")}</p>` : ""}</details></div>`;
}
async function backfillPage() {
  const [plan, sources] = await Promise.all([
    api("/backfill/preview"),
    api("/sources"),
  ]);
  state.backfillPlan = plan;
  const config = plan.policy;
  const overrides = [
    "role",
    "decision_role",
    "employee_count",
    "employee_range",
    "product_fit",
    "segment",
  ];
  return (
    heading(
      "Preencha as lacunas da base.",
      "Defina a confiança mínima. Confira os sinais. Aplique o backfill.",
      `<button class="button" data-view="calibration">Pesos e calibração</button><button class="button" data-action="refresh-backfill">Atualizar resultados</button><button class="button primary" data-action="scan-backfill" ${sources.backfill ? "" : "disabled"}>${icon("radar")}Pesquisar contatos reais</button>`,
      "BACKFILL COM EVIDÊNCIAS",
    ) +
    `${!sources.backfill ? '<div class="notice warning">Ative <strong>Site informado</strong> e <strong>Backfill</strong> em <button class="button text" data-view="sources">Fontes e regras</button> para pesquisar. Os limiares podem ser definidos agora.</div>' : ""}` +
    `<div class="backfill-layout"><section class="panel source-card"><h2>Política de preenchimento</h2><p>A pontuação ordena a força das evidências; 80/100 não significa 80% de chance de acerto.</p><form id="backfill-form"><div class="form-grid"><label class="form-field">Confiança mínima geral<input name="threshold" type="number" min="0" max="100" value="${config.threshold}" required></label><label class="form-field">Páginas por contato<input name="max_pages" type="number" min="1" max="10" value="${config.max_pages}" required></label></div><details class="threshold-overrides"><summary>Limiares por campo</summary><p class="muted">Deixe vazio para usar o limiar geral.</p><div class="form-grid">${overrides.map((field) => `<label class="form-field">${esc(state.config.fields[field])}<input type="number" min="0" max="100" name="limit_${field}" data-threshold-field="${field}" placeholder="Geral: ${config.threshold}" value="${config.field_thresholds[field] ?? ""}"></label>`).join("")}</div></details><label class="switch-label"><input name="auto_fill_empty" type="checkbox" ${config.auto_fill_empty ? "checked" : ""}>Preencher lacunas automaticamente após pesquisar</label><p class="muted">Aplica somente sugestões elegíveis à base local. Substituições e conflitos passam pela revisão individual.</p><label class="switch-label"><input name="web_search" type="checkbox" ${config.web_search ? "checked" : ""} ${state.config.ai_configured ? "" : "disabled"}>Buscar fontes na web pelo nome e pela empresa</label><p class="muted">${state.config.ai_configured ? "Busca configurada. O nome, a empresa e o CNPJ são enviados ao provedor de pesquisa." : "Busca web e interpretação por IA precisam de credencial no servidor. A pesquisa no site informado funciona sem IA."}</p><button class="button primary" type="submit">Salvar política e recalcular</button></form></section><section class="panel source-card backfill-summary"><span class="eyebrow">PRÉVIA ATUAL</span><div class="stat-number" id="backfill-eligible">${plan.eligible_count}<span class="muted"> / ${plan.total}</span></div><h2>campos elegíveis</h2><p>Confiança suficiente, campo vazio, identidade conciliada e fonte válida.</p><div class="notice">Fatos, estimativas e hipóteses mantêm seus rótulos e links após o preenchimento.</div><button class="button primary" data-action="preview-backfill" ${plan.eligible_count ? "" : "disabled"}>Revisar ${plan.eligible_count} preenchimentos</button><p class="muted">Esta ação atualiza o Busqi. A HubSpot tem sua própria prévia de envio.</p><p class="muted">${esc(plan.version)} · até 1.000 sugestões por lote</p></section></div><section class="panel backfill-results"><div class="panel-head"><div><h2>Sugestões e critérios</h2><p>${plan.total} sugestões · ${plan.eligible_count} elegíveis · ${plan.total - plan.eligible_count} para revisar ou pesquisar</p></div></div><div class="backfill-candidates">${plan.candidates.map((item) => `<article class="backfill-candidate" data-backfill-id="${item.id}"><div class="proposal-top"><div><button class="company-name" data-action="open-lead" data-id="${item.lead_id}">${esc(item.company || item.name)}</button><p class="muted">${esc(item.name)} · ${esc(state.config.fields[item.field])}</p></div><span class="fit-tag ${item.eligible ? "teal" : "amber"}">${item.confidence}/100 · mínimo ${item.threshold}</span></div><h3>${esc(valueText(item.field, item.value))}</h3><p class="eligibility ${item.eligible ? "eligible" : ""}">${item.eligible ? "Elegível para preencher campo vazio" : item.reasons.map(esc).join(" ")}</p>${assessmentDetails(item.assessment)}</article>`).join("") || '<div class="empty"><h2>Pronto para pesquisar.</h2>Ative as fontes e rode o backfill nos seus contatos. As hipóteses aparecerão aqui, com as evidências e o limiar de cada campo.</div>'}</div></section>`
  );
}
function previewBackfill() {
  const plan = state.backfillPlan;
  state.backfillApproval = plan.fingerprint;
  openModal(
    `${modalHead("Aplicar backfill à base local")}<p>Confira os ${plan.eligible_count} preenchimentos selecionados pela política salva.</p><div class="sync-rows">${plan.candidates
      .filter((item) => item.eligible)
      .map(
        (item) =>
          `<div class="sync-row"><span>${esc(item.name || item.company)}<br><small>${esc(state.config.fields[item.field])} · ${item.confidence}/100</small></span><strong>${esc(valueText(item.field, item.value))}</strong></div>`,
      )
      .join(
        "",
      )}</div><div class="notice">Campos vazios serão preenchidos com evidências e decisão na auditoria. Nenhuma escrita na HubSpot acontece nesta etapa.</div><div class="modal-actions"><button class="button" data-action="close-modal">Voltar</button><button class="button primary" data-action="apply-backfill">Aplicar ${plan.eligible_count} preenchimentos</button></div>`,
  );
}

let renderVersion = 0;
async function render() {
  const version = ++renderVersion;
  if (!names[state.view]) state.view = "dashboard";
  $("#breadcrumb").textContent = names[state.view];
  $$("[data-view]").forEach((button) =>
    button.classList.toggle("active", button.dataset.view === state.view),
  );
  const html =
    state.view === "dashboard"
      ? dashboard()
      : state.view === "transfers"
        ? await transfersUI.page()
        : state.view === "contacts"
          ? contacts()
          : state.view === "review"
            ? reviewPage()
            : state.view === "sources"
              ? await sourcesPage()
              : state.view === "backfill"
                ? await backfillPage()
                : state.view === "calibration"
                  ? await calibrationUI.page()
                  : state.view === "outbound"
                    ? await outboundUI.page()
                    : state.view === "standard"
                      ? await standardUI.page()
                      : await auditUI.page();
  if (version !== renderVersion) return;
  $("#main").innerHTML = html;
}
async function openLead(id) {
  state.selected = id;
  const lead = await api("/leads/" + id);
  state.detail = lead;
  const pending = lead.suggestions.filter((item) => item.status === "pending");
  const approved = lead.suggestions.filter(
    (item) => item.status === "approved",
  );
  $("#detail-content").innerHTML =
    `<header class="detail-head"><div><div class="eyebrow">${lead.demo ? "DEMONSTRAÇÃO FICTÍCIA" : "CONTATO DO BUSQI"}</div><h2>${esc(lead.company || lead.name)}</h2></div><button class="close-button" data-action="close-detail" aria-label="Fechar contato">${icon("close")}</button></header><div class="detail-body"><div class="profile-meta"><span class="company-logo">${esc((lead.company || lead.name).slice(0, 2).toUpperCase())}</span><div><strong>${esc(lead.name || "Nome a confirmar")}</strong><p>${esc(lead.email)} · ${esc(lead.data.segment || "Segmento a confirmar")}</p><p>${lead.completeness}% de completude · origem: ${esc(lead.origin)}</p></div></div><div class="actions"><button class="button small" data-action="scan-one" data-id="${id}">${icon("radar")}Pesquisar novamente</button><a class="button small" href="/campo?company=${encodeURIComponent(lead.company)}&name=${encodeURIComponent(lead.name)}&email=${encodeURIComponent(lead.email)}">${icon("phone")}Completar contexto</a><button class="button small soft" data-action="sync-preview" data-id="${id}">${icon("cloud")}Preparar envio</button></div><div class="detail-section"><div class="section-label">Próxima conversa</div>${lead.data.product_fit ? `<div class="notice"><strong>Fit registrado no Busqi</strong><p>${esc(lead.data.product_fit)}</p><small>Hipótese de pesquisa. Qualificação comercial completa abaixo.</small></div>` : ""}<div class="fit-box"><span class="fit-tag ${fitStyle(lead.fit.group)}">${esc(lead.fit.group)}</span><h3>${esc(lead.fit.route)}</h3><div class="fit-products">${esc(lead.fit.offers.join(" · ") || "Oferta depende de mais contexto")}</div>${lead.fit.reasons.map((reason) => `<p>${esc(reason)}</p>`).join("")}${lead.fit.questions.length ? `<ul>${lead.fit.questions.map((question) => `<li>${esc(question)}</li>`).join("")}</ul>` : ""}<p><small>Hipótese baseada em declarações · ${esc(lead.fit.version)}</small></p><footer><a href="${safeLink(lead.fit.playbook)}" target="_blank" rel="noreferrer">Consultar regra no playbook ↗</a>${lead.fit.offers.length ? `<button class="button small" data-action="propose-fit" data-id="${id}">Levar fit para revisão</button>` : ""}</footer></div></div><div class="detail-section"><div class="section-label">${pending.length} sugestões para revisar</div>${pending.length ? pending.map(proposal).join("") : `<div class="notice">Nenhuma sugestão pendente. ${lead.jobs[0]?.result?.notes?.map(esc).join(" ") || "Rode o radar para consultar novas fontes."}</div>`}</div>${standardUI.detail(lead)}<div class="detail-section"><div class="section-label">Dados atuais</div><dl class="facts">${Object.entries(
      state.config.fields,
    )
      .map(
        ([field, label]) =>
          `<div class="fact"><dt>${esc(label)}</dt><dd>${esc(valueText(field, lead.data[field]))}</dd></div>`,
      )
      .join("")}</dl></div>${
      approved.some((item) => item.assessment?.version)
        ? `<div class="detail-section"><div class="section-label">Evidências dos campos preenchidos</div>${approved
            .filter((item) => item.assessment?.version)
            .map(
              (item) =>
                `<article class="approved-proof"><strong>${esc(state.config.fields[item.field])}: ${esc(valueText(item.field, item.value))}</strong>${assessmentDetails(item.assessment)}</article>`,
            )
            .join("")}</div>`
        : ""
    }${approved.length ? `<div class="notice">${approved.length} campos aprovados localmente. A confirmação de envio à HubSpot aparece separadamente na auditoria.</div>` : ""}<div class="detail-section"><div class="section-label">Histórico recente</div>${lead.events
      .slice(0, 8)
      .map(
        (event) =>
          `<div class="audit-item">${icon("clock")}<div><strong>${esc(actionNames[event.action] || event.action)}</strong><p>${esc(event.actor)} ${event.detail.field ? " · " + esc(state.config.fields[event.detail.field]) : ""}</p></div><time>${time(event.created_at)}</time></div>`,
      )
      .join(
        "",
      )}</div><button class="button text danger" data-action="suppress-prompt" data-id="${id}">Bloquear próximas ações</button><button class="button text danger" data-action="erase-preview" data-id="${id}">Eliminar dados definitivamente</button></div>`;
  if (!$("#detail-dialog").open) $("#detail-dialog").showModal();
}
function proposal(item) {
  const replace = item.previous_value !== null;
  return `<article class="proposal" data-proposal="${item.id}"><div class="proposal-top"><strong>${esc(state.config.fields[item.field])}</strong><span class="evidence-grade">${item.confidence}/100 · qualidade da evidência</span></div><div class="proposal-values"><div class="old"><small>ATUAL</small>${esc(valueText(item.field, item.previous_value))}</div><span class="arrow">${icon("arrow")}</span><div class="new"><small>SUGERIDO</small>${esc(valueText(item.field, item.value))}</div></div><div class="proposal-source"><strong>${esc(sourceNames[item.source_kind] || item.source_kind)}</strong> · ${time(item.observed_at)}<p>${esc(item.evidence)}</p>${item.source_url ? (item.source_kind === "demo" ? "<small>Fonte fictícia · não há página pública para consultar</small>" : `<a href="${safeLink(item.source_url)}" target="_blank" rel="noreferrer">Abrir fonte ↗</a>`) : ""}</div>${assessmentDetails(item.assessment)}${item.standard_blockers?.length ? `<div class="notice warning">${item.standard_blockers.map(esc).join(" ")}</div>` : ""}<div class="proposal-footer">${replace ? `<label class="replace-label"><input type="checkbox" data-replace="${item.id}">Confirmo substituir o dado atual</label>` : ""}<button class="button small" data-action="reject" data-id="${item.id}">Rejeitar</button><button class="button small" data-action="edit" data-id="${item.id}" aria-label="Editar ${esc(state.config.fields[item.field])}">${icon("edit")}</button><button class="button small soft" data-action="approve" data-id="${item.id}" ${item.standard_blockers?.length ? "disabled" : ""}>${icon("check")}Aprovar</button></div></article>`;
}
async function decision(id, action, value = null, note = "") {
  const allow_replace = $(`[data-replace="${id}"]`)?.checked || false;
  await api("/suggestions/" + id + "/review", {
    method: "POST",
    body: { action, value, note, allow_replace },
  });
  toast(
    action === "approve"
      ? "Campo aprovado. A base local foi atualizada."
      : "Sugestão rejeitada. Dado atual preservado.",
  );
  await reload();
  await openLead(state.selected);
}
async function syncPreview(id) {
  const plan = await api("/leads/" + id + "/sync-preview");
  state.syncPlan = plan;
  openModal(
    `${modalHead("Revisar envio à HubSpot")}<p>Somente os valores aprovados abaixo compõem este envio. Os outros campos serão preservados.</p><div class="sync-rows">${
      Object.entries(plan.approved)
        .map(
          ([field, value]) =>
            `<div class="sync-row"><span>${esc(state.config.fields[field])}</span><strong>${esc(valueText(field, value))}</strong></div>`,
        )
        .join("") ||
      '<div class="empty">Nenhum campo aprovado e válido para envio.</div>'
    }</div>${plan.unmapped.length ? `<div class="notice warning">Ainda sem propriedade HubSpot mapeada: ${plan.unmapped.map((field) => esc(state.config.fields[field])).join(", ")}.</div>` : ""}<div class="notice">${plan.demo ? "Este contato é fictício. Apenas a simulação está disponível." : "A simulação registra o resultado localmente. O envio real exige portal conectado, ID e todos os campos mapeados."}</div><div class="modal-actions"><button class="button" data-action="sync-simulate" ${Object.keys(plan.approved).length ? "" : "disabled"}>Simular envio</button><button class="button primary" data-action="sync-real" ${!plan.demo && plan.real_enabled && plan.hubspot_id && !plan.unmapped.length && Object.keys(plan.approved).length ? "" : "disabled"}>Enviar à HubSpot</button></div>`,
  );
}
async function doSync(simulate) {
  const plan = state.syncPlan;
  const result = await api("/leads/" + plan.lead_id + "/sync", {
    method: "POST",
    body: { fingerprint: plan.fingerprint, simulate },
  });
  $("#modal").close();
  toast(
    result.status === "simulated"
      ? "Simulação registrada. Nenhum dado foi enviado à HubSpot."
      : "Valores confirmados na HubSpot.",
  );
  await reload();
  await openLead(plan.lead_id);
}
async function handleAction(button) {
  const action = button.dataset.action,
    id = button.dataset.id;
  if (await transfersUI.action(action, button)) return;
  if (await calibrationUI.action(action, button)) return;
  if (await outboundUI.action(action, button)) return;
  if (await auditUI.action(action)) return;
  if (await standardUI.action(action, id)) return;
  if (action === "close-modal") $("#modal").close();
  else if (action === "close-detail") $("#detail-dialog").close();
  else if (action === "open-lead") await openLead(id);
  else if (action === "filter") {
    state.filter = button.dataset.value;
    state.leadPage = 1;
    await reload();
  } else if (action === "contacts-prev" || action === "contacts-next") {
    state.leadPage += action === "contacts-next" ? 1 : -1;
    await reload();
  } else if (action === "refresh-backfill") await reload();
  else if (action === "scan-backfill") {
    const ids = state.leads
      .filter((lead) => !lead.demo && !lead.suppressed)
      .map((lead) => lead.id);
    if (!ids.length)
      return toast("Importe ou capture um contato real para pesquisar.");
    const result = await api("/scan", {
      method: "POST",
      body: { ids: ids.slice(0, 500) },
    });
    toast(
      `${result.queued} contatos reais na fila. Use Atualizar resultados para acompanhar.`,
    );
    await reload();
  } else if (action === "preview-backfill") previewBackfill();
  else if (action === "apply-backfill") {
    const result = await api("/backfill/apply", {
      method: "POST",
      body: { fingerprint: state.backfillApproval },
    });
    $("#modal").close();
    toast(
      `${result.applied} campos preenchidos no Busqi, com evidências e auditoria.`,
    );
    await reload();
  } else if (["scan", "scan-one", "scan-imported"].includes(action)) {
    const ids =
      action === "scan-one"
        ? [id]
        : action === "scan-imported"
          ? state.importIds
          : null;
    const result = await api("/scan", { method: "POST", body: { ids } });
    if (action === "scan-imported") $("#modal").close();
    toast(
      result.queued
        ? `${result.queued} contatos na fila de pesquisa.`
        : "Estes contatos já estão na fila.",
    );
    await reload();
  } else if (action === "approve" || action === "reject")
    await decision(id, action === "approve" ? "approve" : "reject");
  else if (action === "edit") {
    const item = state.detail.suggestions.find((x) => x.id === id);
    openModal(
      `${modalHead("Editar sugestão")}<form id="edit-form" data-id="${id}"><label class="form-field">${esc(state.config.fields[item.field])}<input name="value" value="${esc(item.value)}" required></label><label class="form-field">Justificativa da edição<textarea name="note" required placeholder="O que foi confirmado e com quem?"></textarea></label><div class="notice">${item.previous_value !== null ? "Confirme a substituição no cartão do campo antes de salvar." : "A alteração ficará atribuída à equipe na auditoria."}</div><div class="modal-actions"><button class="button primary">Salvar e aprovar</button></div></form>`,
    );
  } else if (action === "propose-fit") {
    const result = await api("/leads/" + id + "/fit", { method: "POST" });
    toast(
      result.created
        ? "Hipótese adicionada à fila de revisão."
        : "Esta hipótese já está registrada.",
    );
    await reload();
    await openLead(id);
  } else if (action === "sync-preview") await syncPreview(id);
  else if (action === "sync-simulate") await doSync(true);
  else if (action === "sync-real") await doSync(false);
  else if (action === "suppress-prompt") {
    openModal(
      `${modalHead("Excluir das próximas ações")}<p>O Busqi bloqueará pesquisas, revisão, exportação e envio deste contato. Jobs pendentes serão cancelados. O histórico será preservado; esta ação não elimina os dados.</p><div class="modal-actions"><button class="button" data-action="close-modal">Cancelar</button><button class="button danger" data-action="suppress" data-id="${id}">Confirmar exclusão das ações</button></div>`,
    );
  } else if (action === "suppress") {
    await api("/leads/" + id + "/suppress", { method: "POST" });
    $("#modal").close();
    $("#detail-dialog").close();
    toast("Contato bloqueado para novas ações.");
    await reload();
  }
}
document.addEventListener("click", async (event) => {
  const navigation = event.target.closest("[data-view]");
  if (navigation) {
    state.view = navigation.dataset.view;
    state.leadPage = 1;
    location.hash = state.view;
    try {
      await reload();
    } catch (error) {
      toast(error.message);
    }
    return;
  }
  const button = event.target.closest("[data-action]");
  if (!button || button.disabled) return;
  button.disabled = true;
  try {
    await handleAction(button);
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
});
let searchTimer;
document.addEventListener("input", (event) => {
  calibrationUI.input(event.target);
  outboundUI.input(event.target);
  if (event.target.id === "lead-search") {
    state.query = event.target.value;
    state.leadPage = 1;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(async () => {
      try {
        await loadContacts();
        if (!$("#lead-search")) return;
        $("#lead-rows").innerHTML = tableRows();
        $("#contacts-pagination").outerHTML = contactsPagination();
      } catch (error) {
        toast(error.message);
      }
    }, 300);
  }
});
document.addEventListener("change", async (event) => {
  calibrationUI.change(event.target);
  try {
    await outboundUI.change(event.target);
    await auditUI.change(event.target);
  } catch (error) {
    toast(error.message);
  }
  if (event.target.id === "import-file")
    try {
      await transfersUI.upload(event.target.files[0]);
    } catch (error) {
      toast(error.message);
    }
});
document.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const button = $('button[type="submit"],button.primary', form);
  if (button) button.disabled = true;
  try {
    if (await standardUI.submit(form)) {
      state.config = await api("/config");
      return;
    }
    if (await outboundUI.submit(form)) {
      return;
    } else if (await calibrationUI.submit(form)) {
      return;
    } else if (form.id === "sources-form") {
      await api("/sources", {
        method: "PUT",
        body: Object.fromEntries(
          ["registry", "website", "ai", "backfill"].map((key) => [
            key,
            form.elements[key].checked,
          ]),
        ),
      });
      toast("Fontes salvas.");
      await render();
    } else if (form.id === "backfill-form") {
      const field_thresholds = {
        ...state.backfillPlan.policy.field_thresholds,
      };
      $$("[data-threshold-field]", form).forEach((input) => {
        if (input.value === "")
          delete field_thresholds[input.dataset.thresholdField];
        else
          field_thresholds[input.dataset.thresholdField] = Number(input.value);
      });
      await api("/backfill/policy", {
        method: "PUT",
        body: {
          threshold: Number(form.elements.threshold.value),
          max_pages: Number(form.elements.max_pages.value),
          auto_fill_empty: form.elements.auto_fill_empty.checked,
          web_search: form.elements.web_search.checked,
          field_thresholds,
        },
      });
      toast(
        "Política salva. Elegibilidade recalculada com as evidências atuais.",
      );
      await render();
    } else if (form.id === "login-form") {
      await api("/session", {
        method: "POST",
        body: { token: form.elements.token.value },
      });
      form.reset();
      $("#modal").close();
      await reload();
    } else if (form.id === "edit-form") {
      await decision(
        form.dataset.id,
        "approve",
        form.elements.value.value,
        form.elements.note.value,
      );
      $("#modal").close();
    }
  } catch (error) {
    toast(error.message);
  } finally {
    if (button) button.disabled = false;
  }
});
window.addEventListener("hashchange", async () => {
  const view = location.hash.slice(1);
  if (view !== state.view && names[view]) {
    state.view = view;
    state.leadPage = 1;
    await reload();
  }
});
for (const dialog of [$("#detail-dialog"), $("#modal")])
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) {
      const rect = dialog.getBoundingClientRect();
      if (
        event.clientX < rect.left ||
        event.clientX > rect.right ||
        event.clientY < rect.top ||
        event.clientY > rect.bottom
      )
        dialog.close();
    }
  });
async function boot() {
  try {
    state.config = await api("/config");
    $("#mode").textContent = state.config.demo
      ? "Ambiente demo · origens identificadas"
      : "Ambiente conectado";
    await reload();
  } catch (error) {
    $("#main").innerHTML =
      `<div class="data-error"><h1>Vamos conectar seu Busqi.</h1><div class="notice error">${esc(error.message)}</div><p class="muted">Confira se os serviços estão disponíveis e recarregue a página.</p></div>`;
  }
}
const transfersUI = createTransfersUI({
  api,
  esc,
  heading,
  openModal,
  modalHead,
  toast,
  refresh: render,
  config: () => state.config,
  navigate: async () => {
    state.view = "transfers";
    location.hash = "transfers";
    await render();
  },
});
const standardUI = createStandardUI({
  api,
  esc,
  heading,
  openModal,
  modalHead,
  reload,
  openLead,
  toast,
  fields: () => state.config.fields,
  forgetLead: () => {
    state.detail = null;
    state.selected = null;
  },
});
const auditUI = createAuditUI({
  api,
  esc,
  icon,
  time,
  heading,
  actions: actionNames,
  fields: () => state.config.fields,
  refresh: render,
});
const calibrationUI = createCalibrationUI({
  api,
  esc,
  safeLink,
  time,
  heading,
  icon,
  openModal,
  modalHead,
  toast,
  refresh: render,
  fields: () => state.config.fields,
});
const outboundUI = createOutboundUI({
  api,
  esc,
  safeLink,
  time,
  heading,
  toast,
  refresh: render,
  openLead,
  fields: () => state.config.fields,
});
await boot();
setInterval(async () => {
  if (
    !document.hidden &&
    !$("#modal").open &&
    !$("#detail-dialog").open &&
    (state.view === "transfers" ||
      ((state.stats.jobs?.queued || state.stats.jobs?.running) &&
        ["dashboard", "contacts", "review"].includes(state.view)))
  ) {
    try {
      if (state.view === "transfers") await transfersUI.poll();
      else await reload();
    } catch (error) {
      $("#live-indicator").textContent = "Conexão interrompida";
    }
  }
}, 3000);
