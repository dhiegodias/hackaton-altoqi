"use strict";
const CACHE = "radar-campo-shell-v5";
const SHELL = [
  "/campo",
  "/static/campo.html",
  "/static/campo.css",
  "/static/campo.js",
  "/static/campo-pwa.js",
  "/static/icon.svg",
  "/static/brand.css",
  "/static/brand/busqi-logo.svg",
  "/static/fonts/nunito-variable.ttf",
  "/campo.webmanifest",
  "/static/campo-icon-180.png",
  "/static/campo-icon-192.png",
  "/static/campo-icon-512.png",
];
const ALLOWED = new Set(SHELL);

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE);
      // A new shell must bypass older HTTP caches without touching IndexedDB.
      await cache.addAll(
        SHELL.map((url) => new Request(url, { cache: "reload" })),
      );
      await self.skipWaiting();
    })(),
  );
});
self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      await Promise.all(
        names
          .filter(
            (name) => name.startsWith("radar-campo-shell-") && name !== CACHE,
          )
          .map((name) => caches.delete(name)),
      );
      await self.clients.claim();
    })(),
  );
});
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (
    event.request.method !== "GET" ||
    url.origin !== self.location.origin ||
    !ALLOWED.has(url.pathname)
  )
    return;
  // A lista explícita exclui todas as APIs, sessões, recibos e dados pessoais.
  event.respondWith(
    (async () => {
      const cache = await caches.open(CACHE);
      if (url.pathname === "/campo" || url.pathname === "/static/campo.html") {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 4000);
        try {
          const response = await fetch(event.request, {
            signal: controller.signal,
          });
          if (response.ok) await cache.put(url.pathname, response.clone());
          else if (response.status >= 500)
            return (await cache.match(url.pathname)) || response;
          return response;
        } catch {
          return (
            (await cache.match(url.pathname)) ||
            (await cache.match("/campo")) ||
            Response.error()
          );
        } finally {
          clearTimeout(timeout);
        }
      }
      const cached = await cache.match(url.pathname);
      const update = fetch(event.request).then(async (response) => {
        if (response.ok) await cache.put(url.pathname, response.clone());
        return response;
      });
      if (cached) {
        event.waitUntil(update.catch(() => undefined));
        return cached;
      }
      return update;
    })(),
  );
});
