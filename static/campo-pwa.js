"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const standalone = window.matchMedia("(display-mode: standalone)");
  let installPrompt = null;
  let installed = false;
  function showInstallation() {
    const inApp =
      installed || standalone.matches || navigator.standalone === true;
    const pending = Number($("queue-count").textContent) > 0;
    $("install-campo").hidden = inApp || !installPrompt;
    $("install-campo").disabled = pending;
    $("install-status").textContent = inApp
      ? "Busqi instalado. As conversas ficam salvas neste aplicativo até o envio."
      : pending
        ? "Há conversas aguardando envio. Sincronize antes de instalar ou mudar de navegador."
        : "Use no navegador ou instale sem passar pela loja de aplicativos.";
    $("secure-install-note").hidden = window.isSecureContext;
  }
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    installPrompt = event;
    showInstallation();
  });
  window.addEventListener("appinstalled", () => {
    installed = true;
    installPrompt = null;
    showInstallation();
  });
  standalone.addEventListener("change", showInstallation);
  new MutationObserver(showInstallation).observe($("queue-count"), {
    childList: true,
    subtree: true,
    characterData: true,
  });
  $("install-campo").onclick = async () => {
    if (!installPrompt) return;
    const prompt = installPrompt;
    installPrompt = null;
    $("install-campo").hidden = true;
    try {
      await prompt.prompt();
      await prompt.userChoice;
    } catch {
      $("installation-help").open = true;
    }
    showInstallation();
  };
  async function storageStatus() {
    if (!navigator.storage?.persisted) return;
    try {
      const persistent = await navigator.storage.persisted();
      $("persist-storage").hidden = persistent || !navigator.storage.persist;
      $("storage-status").textContent = persistent
        ? "Armazenamento persistente autorizado. Limpar os dados do navegador ainda apaga os registros que não foram enviados."
        : "Conversas salvas neste navegador. Não limpe os dados do site enquanto houver envios pendentes.";
    } catch {
      $("storage-status").textContent =
        "Não foi possível conferir a proteção do armazenamento. Preserve os dados do navegador até confirmar os envios.";
    }
  }
  $("persist-storage").onclick = async () => {
    $("persist-storage").disabled = true;
    try {
      const allowed = await navigator.storage.persist();
      await storageStatus();
      if (!allowed)
        $("storage-status").textContent =
          "O navegador não concedeu armazenamento persistente. A fila continua funcionando; preserve os dados do site até enviar.";
    } catch {
      $("storage-status").textContent =
        "A proteção não pôde ser solicitada. A fila continua funcionando neste navegador.";
    } finally {
      $("persist-storage").disabled = false;
    }
  };
  showInstallation();
  storageStatus();
})();
