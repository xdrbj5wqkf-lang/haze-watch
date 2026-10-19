// Haze Watch service worker: app shell cached, forecast fetched fresh with the last copy as offline fallback.
const SHELL = 'haze-shell-v6', DATA = 'haze-data', FONTS = 'haze-fonts';
const FILES = ['./', 'index.html', 'geo.json', 'manifest.webmanifest', 'fonts/climate-crisis.woff2', 'fonts/figtree.woff2', 'icons/apple-touch-icon.png', 'icons/icon-192.png'];
self.addEventListener('install', e => { e.waitUntil(caches.open(SHELL).then(c => c.addAll(FILES))); self.skipWaiting(); });
self.addEventListener('activate', e => { e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => ![SHELL, DATA, FONTS].includes(k)).map(k => caches.delete(k))))); self.clients.claim(); });
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') return;
  // Google Fonts: cache first, so the app keeps its look offline
  if (url.hostname === 'fonts.googleapis.com' || url.hostname === 'fonts.gstatic.com') {
    e.respondWith(caches.open(FONTS).then(c => c.match(e.request).then(m => m || fetch(e.request).then(r => { if (r.ok || r.type === 'opaque') c.put(e.request, r.clone()); return r; }))));
    return;
  }
  if (url.origin !== location.origin) return;
  if (url.pathname.endsWith('/data/latest.json')) {
    e.respondWith(fetch(e.request).then(r => { const copy = r.clone(); caches.open(DATA).then(c => c.put('latest', copy)); return r; })
      .catch(() => caches.open(DATA).then(c => c.match('latest'))));
    return;
  }
  e.respondWith(fetch(e.request).then(r => { if (r.ok) { const copy = r.clone(); caches.open(SHELL).then(c => c.put(e.request, copy)); } return r; })
    .catch(() => caches.match(e.request).then(m => m || caches.match('index.html'))));
});
