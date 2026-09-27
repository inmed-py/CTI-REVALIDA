/* CTi – service worker (servido em /sw.js para controlar o app inteiro)
   - instala o "esqueleto" do app (HTML, fonte, logo, ícones) → o app abre mesmo sem internet;
   - arquivos estáticos: cache primeiro, atualizando em segundo plano;
   - API (GET): rede primeiro; sem internet usa a última resposta guardada (questões já abertas, índice, trilhas);
   - correção (POST /api/responder, /api/missao) exige internet de propósito: o gabarito não fica no aparelho. */
const VERSAO = 'cti-v10-estacoes-completas';
const SHELL = `${VERSAO}-shell`, API = `${VERSAO}-api`;
const ARQUIVOS = [
  '/', '/static/manifest.json', '/static/fonts/inter-latin.woff2',
  '/static/img/logo-mark.svg', '/static/img/icon.svg', '/static/img/favicon-32.png',
  '/static/img/icon-192.png', '/static/img/apple-touch-icon.png',
  '/static/img/hero_m.jpg', '/static/img/essencia_m.jpg',
];
const API_OFFLINE = /^\/api\/(stats|indice|questoes|reforco|ia-explicar|ia-dica|ia-status|atualizacoes|segunda-fase|estacoes)/;
const MAX_API = 400;

self.addEventListener('install', e => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(ARQUIVOS)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil((async () => {
    for (const k of await caches.keys()) if (!k.startsWith(VERSAO)) await caches.delete(k);
    await self.clients.claim();
  })());
});

async function limitar(nome, max){
  const c = await caches.open(nome), ks = await c.keys();
  for (let i = 0; i < ks.length - max; i++) await c.delete(ks[i]);
}

self.addEventListener('fetch', e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== location.origin) return;   // POST e terceiros: direto na rede

  if (req.mode === 'navigate') {                       // páginas: rede primeiro, senão o app guardado
    e.respondWith(fetch(req).then(r => { const cp = r.clone(); caches.open(SHELL).then(c => c.put('/', cp)); return r; })
      .catch(() => caches.match('/')));
    return;
  }
  if (url.pathname.startsWith('/static/')) {           // estáticos: cache primeiro + atualização em segundo plano
    e.respondWith(caches.open(SHELL).then(async c => {
      const hit = await c.match(req);
      const rede = fetch(req).then(r => { if (r.ok) c.put(req, r.clone()); return r; }).catch(() => hit);
      return hit || rede;
    }));
    return;
  }
  if (API_OFFLINE.test(url.pathname)) {                // API de leitura: rede primeiro, cópia para uso offline
    e.respondWith(fetch(req).then(r => {
      if (r.ok) { const cp = r.clone(); caches.open(API).then(c => c.put(req, cp)).then(() => limitar(API, MAX_API)); }
      return r;
    }).catch(async () => (await caches.match(req)) ||
      new Response(JSON.stringify({detail: 'Sem conexão com a internet.'}), {status: 503, headers: {'Content-Type': 'application/json'}})));
  }
});
