export function createTransfersUI({
  api,
  esc,
  heading,
  openModal,
  modalHead,
  toast,
  refresh,
  config,
  navigate,
}) {
  let pageNumber = 1,
    selected = null,
    current = null,
    uploadFile = null,
    uploadKey = null;
  let requestKeys = {};
  const labels = {
    queued: "Na fila",
    running: "Em andamento",
    awaiting_mapping: "Aguardando mapeamento",
    completed: "Concluída",
    failed: "Falhou",
    cancelled: "Cancelada",
    expired: "Arquivo expirado",
    invalidated: "Arquivo invalidado",
  };
  const phases = {
    parsing: "Leitura do arquivo",
    importing: "Importação dos contatos",
    hubspot_fetch: "Consulta à HubSpot",
    exporting: "Preparação do CSV",
  };
  const kinds = {
    import: "Importação",
    hubspot_import: "Importação HubSpot",
    export: "Exportação de aprovados",
  };
  const number = (n) => Number(n || 0).toLocaleString("pt-BR");
  const time = (date) => new Date(date).toLocaleString("pt-BR");
  function importModal() {
    uploadFile = null;
    uploadKey = crypto.randomUUID();
    const limits = config().import_limits;
    openModal(
      `${modalHead("Importar uma lista")}<p>Envie o arquivo, aguarde a leitura e confira o mapeamento. O processamento continua mesmo com a página fechada.</p><label class="dropzone">CSV ou XLSX · até ${number(limits.rows)} linhas / ${number(limits.bytes / 1024 / 1024)} MB<input id="import-file" type="file" accept=".csv,.xlsx"></label><div id="import-preview" role="status"></div><div class="notice">Use valores, sem fórmulas. Dados existentes são preservados e diferenças seguem para revisão.</div>`,
    );
  }
  async function upload(file) {
    if (file && file !== uploadFile) {
      uploadFile = file;
      uploadKey = crypto.randomUUID();
    }
    if (!uploadFile) return;
    if (uploadFile.size > config().import_limits.bytes)
      throw new Error("Arquivo acima do limite permitido.");
    const panel = document.querySelector("#import-preview");
    const input = document.querySelector("#import-file");
    input.disabled = true;
    panel.textContent =
      "Enviando arquivo… Aguarde a confirmação antes de fechar esta janela.";
    const body = new FormData();
    body.append("file", uploadFile);
    try {
      const job = await api("/import/preview", {
        method: "POST",
        body,
        headers: { "Idempotency-Key": uploadKey },
      });
      selected = job.id;
      document.querySelector("#modal").close();
      await navigate();
      toast(
        "Arquivo recebido. Acompanhe a leitura e confirme o mapeamento abaixo.",
      );
    } catch (error) {
      panel.innerHTML = `<div class="notice warning">${esc(error.message)}</div><button class="button" data-action="transfer-upload-retry">Tentar novamente o mesmo envio</button>`;
    } finally {
      input.disabled = false;
    }
  }
  function mapping(job) {
    const fields = {
      name: "Nome",
      company: "Empresa",
      email: "E-mail",
      hubspot_id: "ID HubSpot",
      ...config().fields,
    };
    return `<div class="notice">${number(job.total)} linhas lidas. Nenhum contato é criado antes de confirmar.</div><div class="transfer-mapping">${job.headers
      .map(
        (header, i) =>
          `<div class="mapping-row"><strong>${esc(header)}</strong><span>→</span><select data-transfer-map="${i}" aria-label="Mapear ${esc(header)}"><option value="">Ignorar coluna</option>${Object.entries(
            fields,
          )
            .map(
              ([key, label]) =>
                `<option value="${key}" ${job.suggested_mapping[header] === key ? "selected" : ""}>${esc(label)}</option>`,
            )
            .join("")}</select></div>`,
      )
      .join(
        "",
      )}</div><div class="file-preview"><table><thead><tr>${job.headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${job.sample.map((row) => `<tr>${job.headers.map((h) => `<td>${esc(row[h])}</td>`).join("")}</tr>`).join("")}</tbody></table></div><button class="button primary" data-action="transfer-start" data-id="${job.id}">Confirmar e importar ${number(job.total)} linhas</button>`;
  }
  function details(job) {
    const active = ["queued", "running", "awaiting_mapping"].includes(
      job.status,
    );
    return `<section class="panel source-card" id="transfer-detail" style="margin-top:20px"><div class="panel-head"><div><h2>${esc(job.filename || kinds[job.kind])}</h2><p>${esc(labels[job.status])} · ${esc(phases[job.phase])}</p></div><button class="button small" data-action="transfer-close">Fechar detalhes</button></div>
      <p>${job.phase === "parsing" ? `${number(job.staged)} linhas lidas` : `${number(job.processed)} de ${job.total === null ? "total ainda em consulta" : number(job.total)} linhas processadas`}</p>
      ${job.total ? `<progress style="width:100%" value="${job.processed}" max="${job.total}" aria-label="Linhas processadas"></progress>` : ""}
      <p>${job.kind === "export" ? `${number(job.exported_count)} contatos com campos aprovados no arquivo` : `${number(job.created_count)} criados · ${number(job.duplicate_count)} localizados · ${number(job.error_count)} erros`}</p>
      ${job.error ? `<div class="notice warning">${esc(job.error)}</div>` : ""}
      ${job.status === "awaiting_mapping" ? mapping(job) : ""}
      ${job.error_count ? `<button class="button small" data-action="transfer-errors" data-id="${job.id}" data-page="1">Conferir ${number(job.error_count)} erros por linha</button>` : ""}
      <div class="modal-actions">${active ? `<button class="button" data-action="transfer-cancel-preview" data-id="${job.id}">Cancelar operação</button>` : ""}${job.can_retry ? `<button class="button" data-action="transfer-retry" data-id="${job.id}">Retomar do último lote</button>` : ""}${job.download_url ? `<button class="button primary" data-action="transfer-download" data-id="${job.id}">Baixar CSV aprovado</button><p class="muted">Disponível até ${time(job.expires_at)}, enquanto a base e as evidências permanecerem válidas.</p>` : ""}</div>
      <p class="muted">Recebida em ${time(job.created_at)}. Atualizada em ${time(job.updated_at)}.</p></section>`;
  }
  async function page() {
    const history = await api("/transfers?page=" + pageNumber);
    current = selected ? await api("/transfers/" + selected) : null;
    return (
      heading(
        "Listas e exportações",
        "O Busqi trabalha por lotes. Você acompanha o progresso e pode continuar usando a base.",
        `<button class="button" data-action="queue-export">Exportar aprovados</button><button class="button primary" data-action="import">Importar lista</button>`,
        "OPERAÇÕES EM SEGUNDO PLANO",
      ) +
      `<div class="notice">Arquivos de entrada ficam disponíveis por até 24 horas. Cancelar preserva os contatos já processados. Nenhuma destas operações grava na HubSpot.</div>` +
      (current ? details(current) : "") +
      `<section class="panel" style="margin-top:20px"><div class="panel-head"><h2>Histórico das operações</h2><button class="button small" data-action="transfer-refresh">Atualizar</button></div><div class="table-wrap"><table class="lead-table transfer-table"><thead><tr><th>OPERAÇÃO</th><th>ESTADO</th><th>PROGRESSO</th><th></th></tr></thead><tbody>${history.items.map((job) => `<tr><td><strong>${esc(job.filename || kinds[job.kind])}</strong><div class="muted">${time(job.created_at)}</div></td><td>${esc(labels[job.status])}${job.error_count ? ` · ${number(job.error_count)} erros` : ""}</td><td>${job.phase === "parsing" ? `${number(job.staged)} lidas` : `${number(job.processed)} / ${job.total === null ? "…" : number(job.total)}`}</td><td><button class="button small" data-action="transfer-open" data-id="${job.id}">${job.status === "awaiting_mapping" ? "Mapear colunas" : "Detalhes"}</button></td></tr>`).join("") || '<tr><td colspan="4" class="empty">Envie uma lista ou solicite uma exportação para começar.</td></tr>'}</tbody></table></div><div class="table-foot"><span>${number(history.total)} operações · página ${history.page} de ${history.pages}</span><div class="filters"><button class="button small" data-action="transfers-prev" ${history.page === 1 ? "disabled" : ""}>Anterior</button><button class="button small" data-action="transfers-next" ${history.page >= history.pages ? "disabled" : ""}>Próxima</button></div></div></section>`
    );
  }
  async function queue(kind) {
    requestKeys[kind] ||= crypto.randomUUID();
    const job = await api(kind === "export" ? "/exports" : "/hubspot/import", {
      method: "POST",
      body: {},
      headers: { "Idempotency-Key": requestKeys[kind] },
    });
    delete requestKeys[kind];
    selected = job.id;
    pageNumber = 1;
    await navigate();
    toast("Operação na fila. Você pode fechar esta página.");
  }
  async function action(action, button) {
    const id = button.dataset.id;
    if (action === "import") importModal();
    else if (action === "transfer-upload-retry") await upload(uploadFile);
    else if (action === "queue-export") await queue("export");
    else if (action === "transfer-download") {
      const ready = await api(`/transfers/${id}/download-ready`);
      const link = document.createElement("a");
      link.href = ready.url;
      link.download = "radar-aprovados.csv";
      link.click();
    } else if (action === "hubspot-import") await queue("hubspot_import");
    else if (action === "transfer-start") {
      const mapping = {};
      document
        .querySelectorAll("[data-transfer-map]")
        .forEach(
          (input) =>
            (mapping[current.headers[Number(input.dataset.transferMap)]] =
              input.value),
        );
      await api(`/transfers/${id}/start`, {
        method: "POST",
        body: { mapping },
      });
      await refresh();
    } else if (action === "transfer-cancel-preview")
      openModal(
        `${modalHead("Cancelar esta operação?")}<p>O trabalho pendente e os arquivos temporários serão descartados. Contatos já processados continuam na base.</p><div class="modal-actions"><button class="button" data-action="close-modal">Voltar</button><button class="button danger" data-action="transfer-cancel" data-id="${id}">Cancelar operação</button></div>`,
      );
    else if (action === "transfer-cancel" || action === "transfer-retry") {
      await api(
        `/transfers/${id}/${action === "transfer-cancel" ? "cancel" : "retry"}`,
        { method: "POST" },
      );
      document.querySelector("#modal").close();
      await refresh();
    } else if (action === "transfer-open") {
      selected = id;
      await refresh();
    } else if (action === "transfer-close") {
      selected = null;
      current = null;
      await refresh();
    } else if (action === "transfer-refresh") await refresh();
    else if (action === "transfers-prev" || action === "transfers-next") {
      pageNumber += action === "transfers-next" ? 1 : -1;
      await refresh();
    } else if (action === "transfer-errors") {
      const result = await api(
        `/transfers/${id}/errors?page=${button.dataset.page}`,
      );
      openModal(
        `${modalHead("Erros por linha")}<p>${number(result.total)} erros · página ${result.page} de ${result.pages}</p>${result.items.map((item) => `<p><strong>Linha ${item.line}:</strong> ${esc(item.error)}</p>`).join("")}<div class="modal-actions">${result.page > 1 ? `<button class="button" data-action="transfer-errors" data-id="${id}" data-page="${result.page - 1}">Anterior</button>` : ""}${result.page < result.pages ? `<button class="button" data-action="transfer-errors" data-id="${id}" data-page="${result.page + 1}">Próxima</button>` : ""}</div>`,
      );
    } else return false;
    return true;
  }
  async function poll() {
    // Don't replace selects while the operator is mapping a file.
    if (current?.status === "awaiting_mapping") return;
    await refresh();
  }
  return { page, action, upload, poll };
}
