// Service worker: cache-first stale-while-revalidate for /register shell
// and its static assets. Form submissions go to /api/* which we never cache.

const CACHE_VERSION = "v2";
const CACHE_NAME = `debate-shell-${CACHE_VERSION}`;
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

  const shellPath =
    url.pathname === "/register" ||
    url.pathname === "/static/js/register.js" ||
    url.pathname.startsWith("/static/assets/") ||
    url.pathname.startsWith("/static/fonts/");
  if (!shellPath) return;

  event.respondWith(
    caches.open(CACHE_NAME).then((cache) =>
      cache.match(event.request, { ignoreSearch: true }).then((cached) => {
        const network = fetch(event.request)
          .then((response) => {
            if (response.ok) cache.put(event.request, response.clone());
            return response;
          })
          .catch(() => null);
        return cached || network || Response.error();
      })
    )
  );
});
