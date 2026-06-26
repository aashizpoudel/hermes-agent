// Hermes service worker. Bump CACHE_VERSION on every change to force refresh.
const CACHE_VERSION = 'hermes-v10-bundled';
// `/` is intentionally excluded — it carries a per-process auth token in
// <meta name="hermes-token">, so caching the page would lock the browser
// to a stale token after every server restart.
const SHELL = ['/static/dist/app.js', '/static/dist/style.css', '/manifest.json', '/static/favicon.svg'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_VERSION).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

function isShell(url) {
  return SHELL.some((s) => url.pathname === s);
}

function isBypass(url) {
  return url.pathname.startsWith('/api/') || url.pathname.startsWith('/files/');
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (isBypass(url)) return; // never intercept api/files/stream
  if (isShell(url)) {
    // cache-first for shell
    event.respondWith(
      caches.match(req).then((cached) => cached || fetch(req).then((res) => {
        const copy = res.clone();
        caches.open(CACHE_VERSION).then((c) => c.put(req, copy)).catch(() => {});
        return res;
      }))
    );
    return;
  }
  // network-first for everything else
  event.respondWith(
    fetch(req).catch(() => caches.match(req))
  );
});
