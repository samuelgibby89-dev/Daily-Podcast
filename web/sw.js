/* Service worker: makes the app installable and keeps the last brief
   listenable when the phone has no signal.

   Strategy:
     - shell (html/css/icons)  -> cache first, refreshed in the background
     - episodes.json           -> network first, cache as fallback
     - audio + transcripts     -> cache on first play, served from cache after
*/

const VERSION = 'brief-v1';
const SHELL = ['./', './index.html', './manifest.webmanifest',
               './icons/icon-192.png', './icons/icon-512.png'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(VERSION)
      .then((c) => c.addAll(SHELL))
      .then(() => self.skipWaiting())
      .catch(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Always try the network first for the episode index, so a new morning
  // brief shows up the moment it is published.
  if (url.pathname.endsWith('episodes.json')) {
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(VERSION).then((c) => c.put(req, copy));
          return res;
        })
        .catch(() => caches.match(req))
    );
    return;
  }

  // Media and transcripts: cheap to keep, expensive to re-download.
  if (url.pathname.includes('/audio/') || url.pathname.includes('/transcripts/')) {
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        if (res.ok && !res.headers.get('content-range')) {
          const copy = res.clone();
          caches.open(VERSION).then((c) => c.put(req, copy));
        }
        return res;
      }))
    );
    return;
  }

  // Everything else: cache first, revalidate quietly.
  event.respondWith(
    caches.match(req).then((hit) => {
      const network = fetch(req).then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(VERSION).then((c) => c.put(req, copy));
        }
        return res;
      }).catch(() => hit);
      return hit || network;
    })
  );
});
