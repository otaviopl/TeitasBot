/* sw.js — Service Worker for PWA (cache-first static assets, network-first API) */
var CACHE_NAME = 'pa-pwa-v4';
var STATIC_ASSETS = [
    '/',
    '/chat',
    '/static/css/style.css',
    '/static/js/app.js',
    '/static/js/audio.js',
    '/static/icons/icon-192.png',
    '/static/icons/icon-512.png',
    '/static/icons/bot-icon.png',
];

self.addEventListener('install', function (event) {
    event.waitUntil(
        caches.open(CACHE_NAME).then(function (cache) {
            return cache.addAll(STATIC_ASSETS);
        })
    );
    self.skipWaiting();
});

self.addEventListener('activate', function (event) {
    event.waitUntil(
        caches.keys().then(function (names) {
            return Promise.all(
                names
                    .filter(function (name) { return name !== CACHE_NAME; })
                    .map(function (name) { return caches.delete(name); })
            );
        })
    );
    self.clients.claim();
});

self.addEventListener('fetch', function (event) {
    var url = new URL(event.request.url);

    // Network-first for API calls
    if (url.pathname.startsWith('/api/')) {
        event.respondWith(
            fetch(event.request).catch(function () {
                return new Response(
                    JSON.stringify({ detail: 'Offline — sem conexão com o servidor' }),
                    { status: 503, headers: { 'Content-Type': 'application/json' } }
                );
            })
        );
        return;
    }

    // Cache-first for static assets
    event.respondWith(
        caches.match(event.request).then(function (cached) {
            if (cached) {
                // Update cache in the background
                fetch(event.request).then(function (response) {
                    if (response && response.status === 200) {
                        caches.open(CACHE_NAME).then(function (cache) {
                            cache.put(event.request, response);
                        });
                    }
                }).catch(function () {});
                return cached;
            }
            return fetch(event.request);
        })
    );
});
