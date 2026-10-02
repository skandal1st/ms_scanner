import { createHash } from 'node:crypto'
import type { Plugin } from 'vite'

/** Cache only public application files. API requests always use the network. */
export function tsdPwa(): Plugin {
  return {
    name: 'tsd-pwa',
    apply: 'build',
    generateBundle(_, bundle) {
      const assets = Object.keys(bundle).filter((name) => name.startsWith('assets/') && /\.(js|css|woff2?)$/.test(name))
      const version = createHash('sha256').update(assets.join('|')).digest('hex').slice(0, 16)
      const files = ['/tsd', '/tsd-icon-192.png', '/tsd-icon-512.png', ...assets.map((name) => '/' + name)]
      this.emitFile({ type: 'asset', fileName: 'tsd-sw.js', source: `
const CACHE = 'skandata-tsd-${version}';
const FILES = ${JSON.stringify(files)};
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(FILES)));
});
// Updates activate on the next launch, never halfway through scanning.
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('skandata-tsd-') && key !== CACHE).map(key => caches.delete(key)))));
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin) return;
  if (event.request.mode === 'navigate' && url.pathname === '/tsd') {
    event.respondWith(fetch('/tsd', { cache: 'no-cache' }).catch(() => caches.open(CACHE).then(cache => cache.match('/tsd'))));
    return;
  }
  if (FILES.includes(url.pathname) && url.pathname !== '/tsd') {
    event.respondWith(caches.open(CACHE).then(async cache => (await cache.match(url.pathname)) || fetch(event.request)));
  }
});
` })
    },
  }
}
