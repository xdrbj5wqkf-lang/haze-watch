// Haze Watch service worker: app shell cached, forecast fetched fresh with the last copy as offline fallback.
const SHELL = 'haze-shell-v1', DATA = 'haze-data';
const FILES = ['./', 'index.html', 'geo.json', 'manifest.webmanifest', 'icons/apple-touch-icon.png', 'icons/icon-192.png'];
self.addEventListener('install', e => { e.waitUntil(caches.open(SHELL).then(c => c.addAll(FILES))); self.skipWaiting(); });
self.addEventListener('activate', e => { e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== SHELL && k !== DATA).map(k => caches.delete(k))))); self.clients.claim(); });
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;
  if (url.pathname.endsWith('/data/latest.json')) {
    e.respondWith(fetch(e.request).then(r => { const copy = r.clone(); caches.open(DATA).then(c => c.put('latest', copy)); return r; })
      .catch(() => caches.open(DATA).then(c => c.match('latest'))));
    return;
  }
  e.respondWith(fetch(e.request).then(r => { if (r.ok) { const copy = r.clone(); caches.open(SHELL).then(c => c.put(e.request, copy)); } return r; })
    .catch(() => caches.match(e.request).then(m => m || caches.match('index.html'))));
});
