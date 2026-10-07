export function createStandardUI({
  api,
  esc,
  heading,
  openModal,
  modalHead,
  reload,
  openLead,
  toast,
  fields,
  forgetLead,
}) {
  let configuration;
  let erasure;
  const input = (label, name, value, hint = "") =>
    `<label class="form-field">${label}<textarea name="${name}" rows="9" required>${esc(value)}</textarea>${hint ? `<small>${hint}</small>` : ""}</label>`;
  async function page() {
    const [standard, privacy] = await Promise.all([
      api("/standard"),
      api("/privacy/overview"),
    ]);
    configuration = standard.configuration;
    return (
      heading(
        "Padrão de dados",
        "Campos com formato, vocabulário e fonte definidos. Dados recebidos aguardam comprovação antes de virar preenchimento aprovado.",
        "",
        "CONFIGURAÇÃO",
      ) +
      `<section class="panel source-card"><h2>Regras de preenchimento</h2><div class="file-preview standard-rules"><table><thead><tr><th>Campo</th><th>Regra</th><th>Fonte</th></tr></thead><tbody>
      <tr><td>Nome completo</td><td>Capitalização, nome e sobrenome por extenso; iniciais recusadas</td><td>Entrada da equipe, lista ou HubSpot</td></tr>
      <tr><td>E-mail corporativo</td><td>Formato e DNS ativo; domínio pessoal bloqueado</td><td>Entrada da equipe, lista ou HubSpot</td></tr>
      ${Object.entries(standard.sources)
        .map(
          ([key, sources]) =>
            `<tr><td>${esc(fields()[key])}</td><td>${esc(key === "phone" ? "+55 (DDD) número" : key === "role" || key === "segment" ? "Lista controlada abaixo" : key === "city" || key === "state" ? "Conferido no IBGE" : "Valor cadastral conferido com a empresa e o CNPJ")}</td><td>${esc(sources.join(" ou "))}</td></tr>`,
        )
        .join("")}</tbody></table></div>
      <p class="muted">CNPJ alfanumérico é validado localmente. Se o provedor não conseguir consultá-lo, a comprovação permanece pendente. Domínio ativo não confirma que a caixa de e-mail existe.</p></section>
      <form id="standard-form" class="panel source-card" style="margin-top:24px"><h2>Listas da equipe · versão ${configuration.revision}</h2><p>Uma opção por linha. Cargos aceitam variações: <strong>Engenheiro civil | Eng. civil, Engenheira civil</strong>. Alterar uma lista não reescreve dados antigos; a revisão e o envio conferem a versão atual.</p><div class="grid-two">
      ${input("Cargos e variações", "roles", configuration.roles.map((item) => item.label + (item.aliases.length ? " | " + item.aliases.join(", ") : "")).join("\n"))}
      ${input("Segmentos", "segments", configuration.segments.join("\n"))}
      ${input("Domínios de conselhos permitidos", "council_domains", configuration.council_domains.join("\n"), "A equipe deve cadastrar somente domínios oficiais CAU/CREA. O Busqi não contorna login ou CAPTCHA.")}
      ${input("Domínios de e-mail pessoal bloqueados", "personal_email_domains", configuration.personal_email_domains.join("\n"))}</div>
      <label class="form-field">Responsável<input name="reviewer" required maxlength="100"></label><button class="button primary">Salvar padrão</button></form>
      <section class="panel source-card" style="margin-top:24px"><h2>Privacidade e eliminação</h2><p>Bloquear mantém o histórico. Eliminar remove os dados do contato deste Busqi e mantém somente identificadores protegidos para impedir novo cadastro. Esse bloqueio é dado pseudonimizado, não anonimização.</p><p>${privacy.erasure_count} eliminações registradas sem nome ou e-mail no recibo.</p><h3>Contatos bloqueados</h3>${privacy.suppressed.map((item) => `<div class="sync-row"><span>${esc(item.name)} · ${esc(item.company)}</span><button class="button small" data-action="open-lead" data-id="${item.id}">Abrir para eliminar</button></div>`).join("") || '<p class="muted">Nenhum contato bloqueado.</p>'}<p class="muted">A eliminação local não apaga automaticamente HubSpot, backups nem arquivos já baixados. Listas pendentes e exportações armazenadas também são removidas por precaução. Dispositivos offline verificam os bloqueios quando voltam a se conectar.</p></section>`
    );
  }
  function detail(lead) {
    const pending = Object.entries(lead.intake_data || {});
    return `<div class="detail-section"><div class="section-label">Conferência do padrão</div>${pending.length ? `<p>Recebidos, aguardando fonte permitida:</p><dl class="facts">${pending.map(([key, value]) => `<div class="fact"><dt>${esc(fields()[key] || key)}</dt><dd>${esc(value)}</dd></div>`).join("")}</dl>` : ""}${lead.unverified_fields?.length ? `<div class="notice warning">Dados anteriores ainda sem comprovação do padrão atual: ${lead.unverified_fields.map((key) => esc(fields()[key])).join(", ")}. Pesquise novamente antes de enviar.</div>` : ""}<p class="muted">${lead.identity_checks?.email?.status === "active_domain" ? "Domínio do e-mail conferido; existência da caixa não verificada." : lead.email ? "E-mail anterior sem conferência de domínio registrada." : "Sem e-mail informado."}</p><form id="source-evidence-form" data-id="${lead.id}"><label class="form-field">Página que comprova cargo ou telefone<input name="url" type="url" required placeholder="https://empresa.com.br/equipe"></label><p class="muted">Use o site informado ou uma página pública CAU/CREA da lista permitida. Cargo no conselho é título profissional; não comprova vínculo com a empresa.</p><button class="button small">Conferir página e gerar sugestões</button></form></div>`;
  }
  async function action(action, id) {
    if (action !== "erase-preview") return false;
    const plan = await api(`/leads/${id}/erasure-preview`);
    erasure = { ...plan, id };
    openModal(
      `${modalHead("Eliminar dados deste contato")}<p>Esta operação é definitiva no banco operacional. Remove contato, sugestões, histórico e cópias relacionadas nas pesquisas e na calibração.</p><div class="sync-rows">${Object.entries(
        plan.counts,
      )
        .map(
          ([key, value]) =>
            `<div class="sync-row"><span>${esc({ contatos: "Contatos", pesquisas_outbound: "Pesquisas outbound", recibos_de_captura: "Recibos de captura", eventos: "Eventos da auditoria", avaliacoes_historicas: "Avaliações históricas", suggestions: "Sugestões", jobs: "Pesquisas agendadas", syncs: "Registros de envio", calibracoes: "Exemplos de calibração", arquivos_de_listas: "Listas pendentes e arquivos armazenados" }[key] || key.replaceAll("_", " "))}</span><strong>${value}</strong></div>`,
        )
        .join(
          "",
        )}</div><div class="notice warning">${esc(plan.scope)} Permanecem bloqueios protegidos contra reimportação e um recibo sem os dados do contato.</div>${plan.configured ? `<form id="erasure-form"><label class="form-field">Digite ELIMINAR<input name="confirmation" required autocomplete="off" pattern="ELIMINAR"></label><button class="button danger">Eliminar definitivamente</button></form>` : "<p>Eliminação indisponível: configure a chave de proteção no servidor.</p>"}`,
    );
    return true;
  }
  async function submit(form) {
    const data = new FormData(form);
    if (form.id === "standard-form") {
      const lines = (key) =>
        String(data.get(key))
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean);
      const payload = Object.fromEntries(
        ["segments", "council_domains", "personal_email_domains"].map((key) => [
          key,
          lines(key),
        ]),
      );
      payload.roles = lines("roles").map((line) => {
        const [label, aliases = ""] = line.split("|");
        return {
          label: label.trim(),
          aliases: aliases
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean),
        };
      });
      await api("/standard", {
        method: "PUT",
        body: { configuration: payload, reviewer: data.get("reviewer") },
      });
      toast(
        "Padrão publicado. Novas entradas e aprovações já usam estas listas.",
      );
      await reload();
    } else if (form.id === "source-evidence-form") {
      const result = await api(`/leads/${form.dataset.id}/source`, {
        method: "POST",
        body: { url: data.get("url") },
      });
      toast(`${result.suggestions} sugestões geradas com fonte conferida.`);
      await reload();
      await openLead(form.dataset.id);
    } else if (form.id === "erasure-form") {
      const receipt = await api(`/leads/${erasure.id}/erase`, {
        method: "POST",
        body: {
          fingerprint: erasure.fingerprint,
          confirmation: data.get("confirmation"),
        },
      });
      document.querySelector("#detail-dialog").close();
      document.querySelector("#detail-content").replaceChildren();
      forgetLead();
      openModal(
        `${modalHead("Dados locais eliminados")}<p>O contato e suas cópias operacionais foram removidos. Novas entradas com os identificadores conhecidos serão bloqueadas.</p><p>Recibo: ${esc(receipt.receipt)}</p><div class="notice">${esc(receipt.scope)}</div>`,
      );
      await reload();
    } else return false;
    return true;
  }
  return { page, detail, action, submit };
}
