export function createAuditUI({
  api,
  esc,
  icon,
  time,
  heading,
  actions,
  fields,
  refresh,
}) {
  let state = { page: 1, pageSize: 25, throughId: null };
  let busy = false;
  let sequence = 0;

  async function page() {
    const version = ++sequence;
    const params = new URLSearchParams({
      page: state.page,
      page_size: state.pageSize,
    });
    if (state.throughId !== null) params.set("through_id", state.throughId);
    const result = await api("/audit/page?" + params);
    if (version !== sequence) return "";
    state.page = result.page;
    state.throughId = result.through_id;
    const first = (result.page - 1) * result.page_size + 1;
    const range = result.total
      ? `${first}–${first + result.items.length - 1} de ${result.total} eventos`
      : "Nenhum evento registrado";
    return (
      heading(
        "A história de cada dado.",
        "Registro de pesquisas, decisões da equipe e envios ao CRM.",
        '<button class="button" data-action="audit-refresh">Atualizar eventos</button>',
        "AUDITORIA",
      ) +
      `<section class="panel" aria-labelledby="audit-title">
      <div class="panel-head audit-controls"><div><h2 id="audit-title">Histórico de eventos</h2><p>Eventos novos aparecem ao atualizar.</p></div>
      <label class="audit-size">Eventos por página<select id="audit-page-size">${[25, 50, 100].map((size) => `<option value="${size}" ${state.pageSize === size ? "selected" : ""}>${size}</option>`).join("")}</select></label></div>
      <div class="audit-list">${result.items.map((event) => `<div class="audit-item" data-event-id="${event.id}">${icon(event.action === "approved" ? "check" : "clock")}<div><strong>${esc(actions[event.action] || event.action)}</strong><p>${esc(event.company || "Configuração do ambiente")} · ${esc(event.actor)}${event.detail.field ? " · " + esc(fields()[event.detail.field]) : ""}</p></div><time>${time(event.created_at)}</time></div>`).join("") || '<div class="empty">Os eventos aparecerão aqui conforme a equipe usar o Busqi.</div>'}</div>
      <nav class="audit-pagination" aria-label="Paginação da auditoria"><p id="audit-range" role="status">${range}</p><div><button class="button small" data-action="audit-previous" ${result.page === 1 ? "disabled" : ""}>← Anterior</button><span id="audit-page-label">Página ${result.page} de ${result.pages}</span><button class="button small" data-action="audit-next" ${result.page >= result.pages ? "disabled" : ""}>Próxima →</button></div></nav>
    </section>`
    );
  }
  async function move(changes) {
    if (busy) return;
    const previous = { ...state };
    busy = true;
    Object.assign(state, changes);
    try {
      await refresh();
    } catch (error) {
      state = previous;
      throw error;
    } finally {
      busy = false;
    }
  }
  async function action(name) {
    if (name === "audit-next") await move({ page: state.page + 1 });
    else if (name === "audit-previous")
      await move({ page: Math.max(1, state.page - 1) });
    else if (name === "audit-refresh") await move({ page: 1, throughId: null });
    else return false;
    return true;
  }
  async function change(target) {
    if (target.id === "audit-page-size") {
      const size = Number(target.value);
      if ([25, 50, 100].includes(size)) await move({ page: 1, pageSize: size });
    }
  }
  return { page, action, change };
}
