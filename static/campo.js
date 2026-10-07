/* Captura em campo: o recibo do servidor é a única confirmação de recebimento. */
"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const form = $("capture-form");
  const DB_NAME = "radar-campo-v1";
  let database;
  let step = 1;
  let draftTimer;
  let retryTimer;
  let running = false;
  let saving = false;
  let activeReceiptKey = null;
  let lastReceipt = null;
  let receiptReturnStep = 1;
  let ready = false;
  const businessLabels = {
    engineering_office: "Escritório de projetos",
    builder: "Construtora",
    developer: "Incorporadora",
    management_service: "Escritório de gestão",
    industrial_owner: "Indústria",
    manufacturer: "Fabricante",
    public_owner: "Setor público",
  };
  const maturityFields = [
    "technology",
    "processes",
    "collaboration",
    "management",
    "adoption",
    "scalability",
    "automation",
  ].map((dimension) => `maturity_${dimension}`);

  function notify(message, error = false) {
    $("notice").textContent = message;
    $("notice").classList.toggle("error", error);
    $("notice").hidden = false;
  }
  function openDatabase() {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, 1);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains("queue"))
          db.createObjectStore("queue", { keyPath: "key" });
        if (!db.objectStoreNames.contains("state"))
          db.createObjectStore("state", { keyPath: "key" });
      };
      request.onsuccess = () => {
        request.result.onversionchange = () => request.result.close();
        resolve(request.result);
      };
      request.onerror = () => reject(request.error);
      request.onblocked = () =>
        reject(new Error("Feche outras abas de captura e tente novamente."));
    });
  }
  async function read(store, key) {
    const db = await database;
    return new Promise((resolve, reject) => {
      const request =
        key === undefined
          ? db.transaction(store).objectStore(store).getAll()
          : db.transaction(store).objectStore(store).get(key);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }
  async function write(stores, operation) {
    const db = await database;
    return new Promise((resolve, reject) => {
      const transaction = db.transaction(stores, "readwrite");
      transaction.oncomplete = resolve;
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () =>
        reject(transaction.error || new Error("Gravação interrompida"));
      try {
        operation(transaction);
      } catch (error) {
        transaction.abort();
        reject(error);
      }
    });
  }
  function draftValues() {
    const values = {};
    for (const element of form.elements) {
      if (!element.name) continue;
      if (element.type === "checkbox") {
        if (!values[element.name]) values[element.name] = [];
        if (element.checked) values[element.name].push(element.value);
      } else values[element.name] = element.value;
    }
    return { values, step, savedAt: new Date().toISOString() };
  }
  function hasMeaningfulDraft(values = {}) {
    return Object.entries(values).some(
      ([key, value]) =>
        key !== "consultant" &&
        (Array.isArray(value)
          ? value.length > 0
          : typeof value === "string" && value.trim() !== ""),
    );
  }
  function updateReceiptShortcut() {
    $("view-receipt").hidden = !lastReceipt || !$("receipt").hidden;
  }
  async function saveDraft() {
    if (!ready || saving || $("capture-panel").hidden) return;
    const value = draftValues();
    try {
      await write(["state"], (tx) =>
        tx.objectStore("state").put({ key: "draft", value }),
      );
      $("draft-status").textContent = "Rascunho salvo neste celular";
    } catch {
      $("draft-status").textContent = "Não foi possível salvar o rascunho";
      notify(
        "O navegador não conseguiu salvar o rascunho. Mantenha esta página aberta e confira o espaço disponível.",
        true,
      );
    }
  }
  function scheduleDraft() {
    if (!ready) return;
    $("draft-status").textContent = "Salvando rascunho…";
    clearTimeout(draftTimer);
    draftTimer = setTimeout(saveDraft, 250);
  }
  function updateContext() {
    const model = $("business_model").value;
    const context =
      model === "engineering_office"
        ? "office"
        : ["builder", "developer", "industrial_owner"].includes(model)
          ? "construction"
          : model === "management_service"
            ? "service"
            : "";
    for (const element of document.querySelectorAll("[data-context]")) {
      const visible = element.dataset.context === context;
      element.hidden = !visible;
      element.querySelectorAll("input,select").forEach((input) => {
        input.disabled = !visible;
      });
    }
    const previous = $("pain").value;
    const pains =
      context === "office"
        ? [
            ["engineering", "Projeto técnico e produtividade"],
            ["management", "Processos, prazos e gestão"],
            ["both", "Projeto técnico e gestão"],
          ]
        : context === "construction"
          ? [
              ["quantities", "Quantidades que não são confiáveis"],
              ["coordination", "Coordenação e compatibilização"],
              ["budget", "Orçamento e controle de custos"],
            ]
          : [];
    $("pain").replaceChildren(
      new Option("Ainda não sei", ""),
      ...pains.map(([value, label]) => new Option(label, value)),
    );
    if (pains.some(([value]) => value === previous)) $("pain").value = previous;
    $("pain-wrap").hidden = !pains.length;
    $("pain").disabled = !pains.length;
    const notes = {
      manufacturer:
        "Fabricantes têm uma iniciativa própria. O Busqi ajuda a encaminhar a conversa para o time adequado.",
      public_owner:
        "Órgãos donos de obras seguem a esteira do setor público. O Busqi preserva esse contexto no encaminhamento.",
    };
    $("routing-note").textContent = notes[model] || "";
    $("routing-note").hidden = !notes[model];
    updateDiscovery();
  }
  function updateDiscovery() {
    const model = $("business_model").value;
    const construction = ["builder", "developer", "industrial_owner"].includes(
      model,
    );
    const visibleBlocks = {
      "developer-discovery": model === "developer",
      "budget-discovery": construction && $("pain").value === "budget",
      "custom-discovery": construction,
      "maturity-discovery":
        model === "engineering_office" && $("pain").value === "both",
    };
    for (const [id, visible] of Object.entries(visibleBlocks)) {
      $(id).hidden = !visible;
      $(id)
        .querySelectorAll("select")
        .forEach((input) => {
          input.disabled = !visible;
        });
    }
    const values = maturityFields
      .map((key) => $(key).value)
      .filter((value) => value !== "");
    $("maturity-progress").textContent =
      values.length === 7
        ? `7 de 7 dimensões · ${values.reduce((sum, value) => sum + Number(value), 0)}/21 pontos declarados`
        : `${values.length} de 7 dimensões preenchidas · avaliação ainda incompleta`;
  }
  function setStep(value, focus = false) {
    step = value;
    $("contact-fields").hidden = value !== 1;
    $("context-fields").hidden = value !== 2;
    $("previous-step").hidden = value === 1;
    $("continue-step").hidden = value !== 1;
    $("save-capture").hidden = value !== 2;
    for (const [id, number] of [
      ["step-contact", 1],
      ["step-context", 2],
    ]) {
      $(id).classList.toggle("active", value === number);
      if (value === number) $(id).setAttribute("aria-current", "step");
      else $(id).removeAttribute("aria-current");
    }
    $("form-error").hidden = true;
    if (focus) {
      $("capture-panel").scrollIntoView({ block: "start" });
      (value === 1 ? $("company") : $("business_model")).focus({
        preventScroll: true,
      });
    }
  }
  function validateContact() {
    for (const input of $("contact-fields").querySelectorAll("input,select")) {
      if (
        !input.checkValidity() ||
        ((input.id === "company" || input.id === "consultant") &&
          !input.value.trim())
      ) {
        setStep(1);
        const details = input.closest("details");
        if (details) details.open = true;
        $("form-error").textContent = input.validity.typeMismatch
          ? "Confira o formato do e-mail profissional."
          : `Confira o campo “${input.labels?.[0]?.textContent.replace(/\s+/g, " ").trim() || "informação"}”.`;
        $("form-error").hidden = false;
        input.focus();
        return false;
      }
    }
    return true;
  }
  function payloadFromForm() {
    const data = {};
    const qualification = {};
    for (const key of [
      "website",
      "phone",
      "role",
      "decision_role",
      "cnpj",
      "employee_count",
    ]) {
      const value = $(key).value.trim();
      if (value !== "")
        data[key] = key === "employee_count" ? Number(value) : value;
    }
    const model = $("business_model").value;
    if (model) {
      qualification.business_model = model;
      data.segment = businessLabels[model];
    }
    for (const key of [
      "building_design",
      "building_work",
      "sponsor",
      "assisted_capacity",
      "absorbs_method",
      "uses_bim",
      "sector_autonomy",
      "internal_project_management",
      "internal_budgeting",
      "model_ready",
      "eap_ready",
      "quantities_ready",
      "large_scale",
      "needs_customization",
      "adaptation_budget",
    ]) {
      const input = $(key);
      if (!input.disabled && input.value !== "")
        qualification[key] = input.value === "true";
    }
    for (const key of ["designers", "pain", "service"]) {
      const input = $(key);
      if (!input.disabled && input.value !== "")
        qualification[key] =
          key === "designers" ? Number(input.value) : input.value;
    }
    const disciplines = [
      ...form.querySelectorAll(
        'input[name="disciplines"]:checked:not(:disabled)',
      ),
    ].map((input) => input.value);
    if (disciplines.length) qualification.disciplines = disciplines;
    const maturityInputs = maturityFields.map((key) => $(key));
    if (
      maturityInputs.every(
        (input) => !input.disabled && ["1", "2", "3"].includes(input.value),
      )
    ) {
      qualification.maturity = maturityInputs.map((input) =>
        Number(input.value),
      );
    }
    const payload = {
      company: $("company").value.trim(),
      consultant: $("consultant").value.trim(),
      data,
      qualification,
    };
    if ($("name").value.trim()) payload.name = $("name").value.trim();
    if ($("email").value.trim())
      payload.email = $("email").value.trim().toLowerCase();
    return payload;
  }
  function uuid() {
    if (crypto.randomUUID) return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    const hex = [...bytes]
      .map((byte) => byte.toString(16).padStart(2, "0"))
      .join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }
  function appendList(element, values) {
    element.replaceChildren();
    for (const value of Array.isArray(values) ? values : []) {
      const item = document.createElement("li");
      item.textContent = String(value);
      element.append(item);
    }
  }
  function showReceipt(item, result = null) {
    if (!$("capture-panel").hidden) receiptReturnStep = step;
    $("new-capture").textContent = hasMeaningfulDraft(draftValues().values)
      ? "Voltar ao rascunho"
      : "Registrar outra conversa ＋";
    activeReceiptKey = item.key;
    $("capture-panel").hidden = true;
    $("receipt").hidden = false;
    $("receipt-title").textContent = result
      ? "Recebido pelo Busqi"
      : "Salvo neste celular";
    $("receipt-description").textContent = result
      ? `${item.payload.company}. ${result.message || "Registro recebido com sucesso."}`
      : `${item.payload.company}. ${navigator.onLine ? "Aguardando a confirmação de recebimento." : "Será enviado quando a conexão voltar."}`;
    const fit = result?.fit;
    $("fit-result").hidden = !fit;
    if (fit) {
      $("fit-group").textContent = fit.group || "A qualificar";
      $("fit-route").textContent = fit.route || "";
      $("fit-offers").replaceChildren();
      for (const offer of Array.isArray(fit.offers) ? fit.offers : []) {
        const tag = document.createElement("span");
        tag.textContent = String(offer);
        $("fit-offers").append(tag);
      }
      appendList($("fit-reasons"), fit.reasons);
      appendList($("fit-questions").querySelector("ul"), fit.questions);
      $("fit-questions").hidden =
        !Array.isArray(fit.questions) || !fit.questions.length;
    }
    updateReceiptShortcut();
  }
  async function renderQueue() {
    const queue = (await read("queue")).sort((a, b) =>
      a.createdAt.localeCompare(b.createdAt),
    );
    $("queue-count").textContent = queue.length;
    $("sync-now").disabled = running || !queue.length || !navigator.onLine;
    $("sync-now").textContent = running ? "Enviando…" : "Sincronizar";
    $("queue-list").replaceChildren();
    if (!queue.length) {
      const empty = document.createElement("div");
      empty.className = "empty-queue";
      const check = document.createElement("span");
      check.textContent = "✓";
      empty.append(
        check,
        document.createTextNode("Nenhum registro aguardando envio."),
      );
      $("queue-list").append(empty);
    }
    for (const item of queue) {
      const card = document.createElement("article");
      card.className = `queue-item ${item.status === "blocked" ? "blocked" : ""}`;
      const title = document.createElement("h3");
      title.textContent = item.payload.company;
      const detail = document.createElement("p");
      detail.textContent = `${item.payload.name || item.payload.email || "Contato sem nome"} · ${new Date(item.createdAt).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}`;
      const state = document.createElement("span");
      state.className = "queue-state";
      state.textContent =
        {
          blocked: "Precisa de atenção",
          auth: "Acesso necessário",
          sending: "Aguardando confirmação",
          pending: "Salvo · aguardando envio",
          retry: "Salvo · nova tentativa pendente",
        }[item.status] || "Salvo · aguardando envio";
      card.append(title, detail, state);
      if (item.error) {
        const error = document.createElement("p");
        error.className = "queue-error";
        error.textContent = item.error;
        card.append(error);
      }
      const actions = document.createElement("div");
      actions.className = "queue-actions";
      if (item.status === "auth") {
        const login = document.createElement("button");
        login.className = "text-button";
        login.textContent = "Acessar";
        login.type = "button";
        login.onclick = () => openLogin(true);
        actions.append(login);
      }
      const discard = document.createElement("button");
      discard.type = "button";
      discard.className = "text-button discard";
      discard.textContent = "Descartar";
      discard.disabled = item.status === "sending" && running;
      discard.onclick = async () => {
        if (
          !window.confirm(
            `Descartar o registro de ${item.payload.company} deste celular? Isso não remove um registro já recebido pelo Busqi.`,
          )
        )
          return;
        try {
          await write(["queue"], (tx) =>
            tx.objectStore("queue").delete(item.key),
          );
          if (activeReceiptKey === item.key) newCapture();
          await renderQueue();
          notify("Registro removido da fila deste celular.");
        } catch {
          notify(
            "Não foi possível descartar o registro. Tente novamente.",
            true,
          );
        }
      };
      actions.append(discard);
      card.append(actions);
      $("queue-list").append(card);
    }
    updateReceiptShortcut();
    return queue;
  }
  function apiError(body, fallback) {
    if (typeof body?.detail === "string") return body.detail;
    if (Array.isArray(body?.detail))
      return body.detail
        .map((error) => error.msg || "Campo inválido")
        .join("; ");
    return typeof body?.message === "string" ? body.message : fallback;
  }
  async function request(path, options) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      return await fetch(path, {
        ...options,
        credentials: "same-origin",
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timeout);
    }
  }
  function openLogin(focus = false) {
    $("login-panel").hidden = false;
    if (focus) {
      $("login-panel").scrollIntoView({ block: "start" });
      $("access-token").focus();
    }
  }
  function clearReceiptView() {
    for (const id of [
      "receipt-description",
      "fit-group",
      "fit-route",
      "fit-offers",
      "fit-reasons",
    ])
      $(id).replaceChildren();
    $("fit-questions").querySelector("ul").replaceChildren();
  }
  async function reconcilePrivacy() {
    const [queue, draft, receipt] = await Promise.all([
      read("queue"),
      read("state", "draft"),
      read("state", "receipt"),
    ]);
    const records = queue.map((item) => ({
      store: "queue",
      key: item.key,
      payload: { ...item.payload, request_key: item.key },
    }));
    if (draft?.value?.values)
      records.push({
        store: "state",
        key: "draft",
        payload: draft.value.values,
      });
    if (receipt?.value?.item)
      records.push({
        store: "state",
        key: "receipt",
        payload: {
          ...receipt.value.item.payload,
          request_key: receipt.value.item.key,
        },
      });
    for (let start = 0; start < records.length; start += 200) {
      const group = records.slice(start, start + 200);
      const response = await request("/api/privacy/check", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          items: group.map(({ payload }) => ({
            name: payload.name || "",
            company: payload.company || "",
            email: payload.email || "",
            data: { linkedin_person: payload.data?.linkedin_person || "" },
            request_key: payload.request_key || "",
          })),
        }),
      });
      if (!response.ok) continue;
      const result = await response.json();
      const removed = result.blocked
        .filter((i) => Number.isInteger(i) && group[i])
        .map((i) => group[i]);
      if (!removed.length) continue;
      clearTimeout(draftTimer);
      await write(["queue", "state"], (tx) => {
        for (const item of removed) tx.objectStore(item.store).delete(item.key);
      });
      if (removed.some((item) => item.key === "receipt")) {
        lastReceipt = null;
        clearReceiptView();
      }
      if (removed.some((item) => item.key === "draft")) {
        $("capture-form").reset();
        updateContext();
      }
      if (
        removed.some(
          (item) => item.key === activeReceiptKey || item.key === "receipt",
        )
      )
        newCapture();
      notify(
        "Dados de contato eliminado foram removidos deste aparelho. O bloqueio impede reenvio.",
      );
    }
  }
  async function drainQueue(manual) {
    const queue = (await read("queue")).sort((a, b) =>
      a.createdAt.localeCompare(b.createdAt),
    );
    for (const original of queue) {
      if (!navigator.onLine) break;
      const item = await read("queue", original.key);
      if (
        !item ||
        item.status === "blocked" ||
        (!manual && (item.status === "auth" || item.nextRetry > Date.now()))
      )
        continue;
      item.status = "sending";
      item.attempts += 1;
      item.error = "";
      await write(["queue"], (tx) => tx.objectStore("queue").put(item));
      await renderQueue();
      try {
        const response = await request("/api/captures", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": item.key,
          },
          body: JSON.stringify(item.payload),
        });
        const body = await response.json().catch(() => ({}));
        if (response.ok && typeof body.id === "string") {
          await write(["queue", "state"], (tx) => {
            tx.objectStore("state").put({
              key: "receipt",
              value: { item, result: body },
            });
            tx.objectStore("queue").delete(item.key);
          });
          lastReceipt = { item, result: body };
          if (activeReceiptKey === item.key) showReceipt(item, body);
          else notify(`${item.payload.company}: recebido pelo Busqi.`);
          continue;
        }
        if (response.status === 410 && body.erased) {
          await write(["queue"], (tx) =>
            tx.objectStore("queue").delete(item.key),
          );
          if (activeReceiptKey === item.key) {
            clearReceiptView();
            newCapture();
          }
          await reconcilePrivacy();
          notify(
            "Contato eliminado: captura removida deste aparelho e reenvio bloqueado.",
          );
          continue;
        }
        if (response.status === 401) {
          item.status = "auth";
          item.error = "Entre com a chave da equipe para enviar este registro.";
          openLogin(false);
        } else if ([400, 403, 404, 409, 413, 422].includes(response.status)) {
          item.status = "blocked";
          item.error = `${apiError(body, "O Busqi não aceitou este registro.")} Ele continua neste celular. Confira os dados; se precisar refazer, descarte esta captura explicitamente.`;
        } else {
          item.status = "retry";
          item.error = apiError(
            body,
            "Recebimento ainda não confirmado. O registro permanece salvo para uma nova tentativa.",
          );
        }
      } catch {
        item.status = "retry";
        item.error =
          "Recebimento ainda não confirmado. A próxima tentativa usa o mesmo registro para evitar duplicação.";
      }
      item.nextRetry =
        Date.now() + Math.min(60000, 3000 * 2 ** Math.min(item.attempts, 5));
      await write(["queue"], (tx) => tx.objectStore("queue").put(item));
      if (item.status === "auth") break;
    }
  }
  async function syncQueue(manual = false) {
    if (!ready || running || !navigator.onLine) return;
    running = true;
    clearTimeout(retryTimer);
    try {
      if (navigator.locks)
        await navigator.locks.request(
          "radar-campo-sync",
          { ifAvailable: true },
          async (lock) => {
            if (lock) {
              await reconcilePrivacy();
              await drainQueue(manual);
            }
          },
        );
      else {
        await reconcilePrivacy();
        await drainQueue(manual);
      }
    } catch {
      notify(
        "A fila não pôde ser atualizada. Os registros continuam neste celular; tente sincronizar novamente.",
        true,
      );
    } finally {
      running = false;
      try {
        const queue = await renderQueue();
        const pending = queue.filter(
          (item) => !["blocked", "auth"].includes(item.status),
        );
        if (pending.length && navigator.onLine)
          retryTimer = setTimeout(
            () => syncQueue(),
            Math.max(
              1500,
              Math.min(
                ...pending.map((item) => (item.nextRetry || 0) - Date.now()),
              ),
            ),
          );
      } catch {
        notify(
          "Não foi possível ler a fila salva. Mantenha os dados deste navegador e tente reabrir a página.",
          true,
        );
      }
    }
  }
  function connectionChanged() {
    $("connection").replaceChildren();
    $("connection").append(
      document.createElement("i"),
      document.createTextNode(
        navigator.onLine ? "Rede disponível" : "Sem rede · pode registrar",
      ),
    );
    $("connection").classList.toggle("offline", !navigator.onLine);
    if (ready) {
      renderQueue().catch(() =>
        notify("Não foi possível ler a fila local.", true),
      );
      if (navigator.onLine) syncQueue();
    }
  }
  function newCapture() {
    activeReceiptKey = null;
    $("receipt").hidden = true;
    $("capture-panel").hidden = false;
    $("notice").hidden = true;
    setStep(
      hasMeaningfulDraft(draftValues().values) ? receiptReturnStep : 1,
      true,
    );
    updateReceiptShortcut();
    scheduleDraft();
  }

  form.addEventListener("input", scheduleDraft);
  form.addEventListener("change", (event) => {
    if (event.target.id === "business_model") updateContext();
    else if (
      event.target.id === "pain" ||
      event.target.id.startsWith("maturity_")
    )
      updateDiscovery();
    scheduleDraft();
  });
  $("continue-step").onclick = $("step-context").onclick = () => {
    if (validateContact()) {
      setStep(2, true);
      scheduleDraft();
    }
  };
  $("previous-step").onclick = $("step-contact").onclick = () => {
    setStep(1, true);
    scheduleDraft();
  };
  $("new-capture").onclick = newCapture;
  $("view-receipt").onclick = async () => {
    if (!lastReceipt) return;
    clearTimeout(draftTimer);
    await saveDraft();
    showReceipt(lastReceipt.item, lastReceipt.result);
    $("receipt").scrollIntoView({ block: "start" });
  };
  $("sync-now").onclick = () => syncQueue(true);
  $("open-login").onclick = () => openLogin(true);
  $("close-login").onclick = () => {
    $("login-panel").hidden = true;
    $("access-token").value = "";
  };
  $("clear-draft").onclick = async () => {
    if (
      !window.confirm(
        "Limpar este rascunho? Os registros que já estão na fila serão mantidos.",
      )
    )
      return;
    clearTimeout(draftTimer);
    const consultant = $("consultant").value;
    form.reset();
    $("consultant").value = consultant;
    updateContext();
    setStep(1);
    await saveDraft();
  };
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (saving || !ready || !validateContact()) return;
    if (step === 1) {
      setStep(2, true);
      scheduleDraft();
      return;
    }
    if (!form.checkValidity()) {
      const invalid = form.querySelector(":invalid");
      invalid?.focus();
      invalid?.reportValidity();
      return;
    }
    saving = true;
    clearTimeout(draftTimer);
    $("save-capture").disabled = true;
    let stored = false;
    try {
      const payload = payloadFromForm();
      const item = {
        key: uuid(),
        payload,
        createdAt: new Date().toISOString(),
        status: "pending",
        attempts: 0,
        nextRetry: 0,
        error: "",
      };
      await write(["queue", "state"], (tx) => {
        tx.objectStore("queue").add(item);
        tx.objectStore("state").delete("draft");
        tx.objectStore("state").put({
          key: "consultant",
          value: payload.consultant,
        });
      });
      stored = true;
      form.reset();
      $("consultant").value = payload.consultant;
      updateContext();
      $("notice").hidden = true;
      showReceipt(item);
      $("receipt").scrollIntoView({ block: "start" });
      await renderQueue();
      syncQueue();
    } catch {
      notify(
        stored
          ? "O registro foi salvo neste celular, mas a tela não pôde ser atualizada. Reabra a página para consultar a fila."
          : "Não foi possível salvar neste celular. O formulário foi preservado; confira o armazenamento e tente novamente.",
        true,
      );
    } finally {
      saving = false;
      $("save-capture").disabled = false;
    }
  });
  $("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.target.querySelector("button");
    button.disabled = true;
    $("login-error").hidden = true;
    try {
      const response = await request("/api/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: $("access-token").value }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok)
        throw new Error(
          apiError(body, "A chave não foi aceita. Confira com sua equipe."),
        );
      $("access-token").value = "";
      $("login-panel").hidden = true;
      notify("Acesso confirmado. Enviando os registros pendentes.");
      syncQueue(true);
    } catch (error) {
      $("login-error").textContent =
        error.message === "Failed to fetch"
          ? "Sem conexão com o Busqi. Seus registros continuam salvos."
          : error.message;
      $("login-error").hidden = false;
    } finally {
      button.disabled = false;
    }
  });
  window.addEventListener("online", connectionChanged);
  window.addEventListener("offline", connectionChanged);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      clearTimeout(draftTimer);
      saveDraft();
    } else syncQueue();
  });

  async function initialize() {
    connectionChanged();
    try {
      database = openDatabase();
      await database;
      const savedStandard = await read("state", "standard");
      if (savedStandard?.value?.roles)
        $("role-options").replaceChildren(
          ...savedStandard.value.roles.map((label) => new Option(label, label)),
        );
      const [draft, consultant, storedReceipt, pending] = await Promise.all([
        read("state", "draft"),
        read("state", "consultant"),
        read("state", "receipt"),
        read("queue"),
      ]);
      if (storedReceipt?.value?.item?.key && storedReceipt.value.result?.id)
        lastReceipt = storedReceipt.value;
      const meaningfulDraft = hasMeaningfulDraft(draft?.value?.values);
      if (consultant) $("consultant").value = consultant.value;
      if (draft?.value?.values) {
        const values = draft.value.values;
        $("business_model").value = values.business_model || "";
        updateContext();
        for (const input of form.elements) {
          if (!input.name || values[input.name] === undefined) continue;
          if (input.type === "checkbox")
            input.checked =
              Array.isArray(values[input.name]) &&
              values[input.name].includes(input.value);
          else input.value = values[input.name];
        }
        updateContext();
        setStep(draft.value.step === 2 ? 2 : 1);
        $("draft-status").textContent = "Rascunho recuperado deste celular";
      } else updateContext();
      const params = new URLSearchParams(location.search);
      const hasDeepLink = ["company", "name", "email"].some((key) =>
        params.has(key),
      );
      if (hasDeepLink) {
        if (!meaningfulDraft) {
          for (const key of ["company", "name", "email"])
            if (params.has(key))
              $(key).value = params.get(key).slice(0, $(key).maxLength);
          setStep(1);
        } else
          notify(
            "Seu rascunho anterior foi preservado. Conclua ou limpe o rascunho antes de abrir outro contato.",
          );
        history.replaceState(null, "", location.pathname);
      }
      ready = true;
      if (!meaningfulDraft && !hasDeepLink) {
        const latestPending = pending.sort((a, b) =>
          b.createdAt.localeCompare(a.createdAt),
        )[0];
        if (latestPending) showReceipt(latestPending);
        else if (lastReceipt) showReceipt(lastReceipt.item, lastReceipt.result);
      }
      await renderQueue();
      syncQueue();
    } catch {
      $("save-capture").disabled = true;
      $("continue-step").disabled = true;
      notify(
        "O armazenamento deste navegador está indisponível. Ative os dados de sites ou use outro navegador para salvar com segurança.",
        true,
      );
    }
    request("/api/config")
      .then((response) => (response.ok ? response.json() : null))
      .then((config) => {
        if (!config) return;
        if (config.roles) {
          $("role-options").replaceChildren(
            ...config.roles.map((label) => new Option(label, label)),
          );
          write(["state"], (tx) =>
            tx
              .objectStore("state")
              .put({ key: "standard", value: { roles: config.roles } }),
          ).catch(() => {});
        }
        $("mode").hidden = !config.demo;
        $("mode").textContent = "DEMONSTRAÇÃO";
        $("open-login").hidden = !config.auth_required;
      })
      .catch(() => {
        /* A captura offline não depende da configuração remota. */
      });
    if ("serviceWorker" in navigator && window.isSecureContext) {
      navigator.serviceWorker
        .register("/sw.js", { scope: "/" })
        .then(async (registration) => {
          const worker =
            registration.installing ||
            registration.waiting ||
            registration.active;
          if (worker && worker.state !== "activated") {
            await new Promise((resolve, reject) => {
              const changed = () => {
                if (worker.state === "activated") {
                  worker.removeEventListener("statechange", changed);
                  resolve();
                } else if (worker.state === "redundant") {
                  worker.removeEventListener("statechange", changed);
                  reject(new Error("A preparação offline não foi concluída."));
                }
              };
              worker.addEventListener("statechange", changed);
              changed();
            });
          }
          await navigator.serviceWorker.ready;
        })
        .then(() => {
          $("offline-readiness").textContent =
            "Acesso offline preparado neste navegador. Reabra esta mesma página quando precisar.";
        })
        .catch(() => {
          $("offline-readiness").textContent =
            "A fila local funciona nesta página. O acesso offline ao reabrir ainda não está disponível.";
        });
    } else
      $("offline-readiness").textContent =
        "Para reabrir sem conexão, acesse por HTTPS. Nesta página, seus registros ficam salvos localmente.";
  }
  initialize();
})();
