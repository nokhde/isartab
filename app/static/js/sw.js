// Service worker: network-first for the /register shell and its JS (so a
// deploy shows up on the next reload), cache-first stale-while-revalidate for
// immutable assets. Form submissions go to /api/* which we never cache.

const CACHE_VERSION = "v3";
const CACHE_NAME = `debate-shell-${CACHE_VERSION}`;

// How long a shell request waits on the network before falling back to the
// cached copy. Short enough not to be felt as a hang on bad venue wifi.
const SHELL_NETWORK_TIMEOUT_MS = 2000;

// The shells are event-agnostic (JS reads ?event=… at runtime), so every
// ?event=CODE variant is byte-identical. Key them on the path alone — keying
// on the full URL grows the cache by one full copy per event code.
const shellKey = (url) => url.origin + url.pathname;
const PRECACHE = [
  "/register",
  "/static/js/register.js",
  "/static/assets/bg.png",
  "/static/assets/isartab_logo.png",
  "/static/fonts/geist/Geist-Medium.woff2",
  "/static/fonts/geist/Geist-SemiBold.woff2",
  "/static/fonts/geist/GeistMono-Medium.woff2",
  "/static/fonts/geist/GeistMono-SemiBold.woff2",
  "/static/fonts/geist/GeistMono-Regular.woff2",
  "/static/fonts/geist/GeistMono-Bold.woff2",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;

  // The shell (HTML + its JS) changes on every deploy, so it must never be
  // served from cache while the network is available — the cache is only an
  // offline fallback. Assets are content-stable and stay cache-first.
  const shellPath =
    url.pathname === "/register" || url.pathname === "/static/js/register.js";
  const assetPath =
    url.pathname.startsWith("/static/assets/") ||
    url.pathname.startsWith("/static/fonts/");
  if (!shellPath && !assetPath) return;

  if (shellPath) {
    event.respondWith(
      caches.open(CACHE_NAME).then(async (cache) => {
        // `cache: "no-store"` keeps the browser's own HTTP cache out of the
        // loop; otherwise a still-fresh max-age response would be replayed
        // here and written back as if it were current.
        const network = fetch(
          new Request(event.request, { cache: "no-store" })
        ).then((response) => {
          if (response.ok) cache.put(shellKey(url), response.clone());
          return response;
        });
        // Keep the worker alive for the cache write even when we end up
        // answering from cache below.
        event.waitUntil(network.catch(() => {}));

        const cached = await cache.match(shellKey(url));
        if (!cached) return network.catch(() => Response.error());

        // Prefer the network, but don't let a stalled connection hold up the
        // paint — venue wifi is the normal case here. Falling back still
        // leaves the write above running, so the next load is fresh.
        const winner = await Promise.race([
          network.catch(() => null),
          new Promise((resolve) =>
            setTimeout(() => resolve(null), SHELL_NETWORK_TIMEOUT_MS)
          ),
        ]);
        return winner || cached;
      })
    );
    return;
  }

  event.respondWith(
    caches.open(CACHE_NAME).then((cache) =>
      cache.match(event.request, { ignoreSearch: true }).then((cached) => {
        const network = fetch(event.request)
          .then((response) => {
            if (response.ok) cache.put(event.request, response.clone());
            return response;
          })
          .catch(() => null);
        return cached || network.then((r) => r || Response.error());
      })
    )
  );
});
