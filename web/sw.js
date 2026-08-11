/* Service worker: makes the app installable and keeps the shell instant.

   Strategy:
     - audio            -> NOT intercepted at all (see below)
     - episodes.json    -> network first, cache as fallback
     - transcripts      -> cache on first read, served from cache after
     - shell (html/css/icons) -> cache first, revalidated in the background

   Why audio is deliberately left alone:
   A service worker must not sit in front of streamed media. Media elements
   fetch with byte ranges and read lazily -- an <audio> tag with
   preload="metadata" pulls the first chunk and then stops reading. If the
   worker calls response.clone() to stash a copy in the cache, that second
   branch is never drained, backpressure stalls both streams, and the element
   hangs at readyState 0 forever with no error fired. Ordinary fetch() of the
   same URL still works, which makes it a nasty one to spot.

   The cost is that episodes are not available offline. That is the right
   trade: a brief that reliably plays beats one that occasionally plays
   without signal. Doing it properly means serving 206 responses out of the
   cache by hand, which is a lot of machinery for a seven-minute file.
*/

// Bump this whenever you change anything in web/. The old cache is dropped on
// activate, so a version bump is what makes a UI change actually reach the
// phone instead of sitting behind a cached index.html.
const VERSION = 'brief-v3';
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

  // Hands off: media, and anything asking for a byte range.
  if (url.pathname.includes('/audio/') || req.headers.has('range')) return;

  // Network first for the episode index, so a new brief appears the moment
  // it is published.
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

  // Everything else (shell, icons, transcripts): cache first, revalidate
  // quietly. These are small text and image files that get read to
  // completion, so cloning them is safe.
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
