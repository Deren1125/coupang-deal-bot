/* Sns-board 서비스 워커 (범위 /dashboard/): 앱 파일은 캐시, 화면·상태는 네트워크 먼저(3초) 그다음 마지막 캐시, 웹 푸시 알림. */
'use strict';
const V = 'sb-v14', STATIC = 'sb-static-' + V, PAGE = 'sb-page-' + V;
const PRE = ['/dashboard/static/icon-192.png', '/dashboard/static/badge-72.png'];
const STATE = '/dashboard/api/state', HOME = '/dashboard/';

self.addEventListener('install', e => {
  e.waitUntil(caches.open(STATIC).then(c => c.addAll(PRE)).catch(() => {}));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== STATIC && k !== PAGE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('message', e => {
  const t = e.data && e.data.type;
  if (t === 'skip') self.skipWaiting();
  if (t === 'logout') e.waitUntil(caches.keys().then(ks => Promise.all(ks.map(k => caches.delete(k)))));
});

function timeout(p, ms) {
  return new Promise((ok, bad) => { const t = setTimeout(() => bad(new Error('timeout')), ms); p.then(r => { clearTimeout(t); ok(r); }, x => { clearTimeout(t); bad(x); }); });
}
// 네트워크 먼저 (3초) → 받은 200 은 저장 → 안 되면 마지막 저장본 (상태에는 x-sw-cache 표시)
async function fresh(req, key, mark) {
  const c = await caches.open(PAGE);
  try {
    const r = await timeout(fetch(req), 3000);
    if (r.status === 200 && !r.redirected && r.type === 'basic') c.put(key, r.clone());
    return r;
  } catch (x) {
    return cached(key, mark, x);
  }
}
async function cached(key, mark, x) {
  const old = await (await caches.open(PAGE)).match(key);
  if (!old) throw x || new Error('offline');
  if (!mark) return old;
  const h = new Headers(old.headers);
  h.set('x-sw-cache', '1');
  return new Response(await old.blob(), {status: 200, headers: h});
}
// 앱 파일: 주소(?v= 포함)가 같으면 캐시, 새 주소면 받아서 같은 파일의 옛것은 지움
async function asset(req) {
  const c = await caches.open(STATIC);
  const hit = await c.match(req);
  if (hit) return hit;
  const r = await fetch(req);
  if (r.status === 200) {
    const path = new URL(req.url).pathname;
    for (const k of await c.keys()) if (new URL(k.url).pathname === path) await c.delete(k);
    await c.put(req, r.clone());
  }
  return r;
}
self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const u = new URL(req.url);
  if (u.origin !== location.origin) return;
  if (req.mode === 'navigate' && u.pathname === HOME) { e.respondWith(fresh(req, HOME, false)); return; }
  if (u.pathname === STATE && !req.headers.has('if-none-match')) { e.respondWith(fresh(req, STATE, true)); return; }
  if (u.pathname === STATE) { e.respondWith(fetch(req).catch(x => cached(STATE, true, x))); return; }
  if (u.pathname.startsWith('/dashboard/static/')) e.respondWith(asset(req));
});

self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (x) { d = {title: 'Sns-board', body: e.data ? e.data.text() : ''}; }
  // 사진 부탁 (type item 만): 버튼 하나 '상품 열기'·'사진 찾기' → 그 주소 바로 (안드로이드 크롬 — iOS 는 버튼 없이 알림 누르면 할 일)
  const o = d.type === 'item' && d.open && /^https?:\/\//.test(d.open.url || '') ? d.open : null;
  const show = self.registration.showNotification(d.title || 'Sns-board', {
    body: d.body || '', tag: d.tag || 'todo', renotify: !!d.urgent, icon: '/dashboard/static/icon-192.png',
    badge: '/dashboard/static/badge-72.png', data: {url: d.url || HOME, open: o ? o.url : null}, timestamp: (d.ts || Date.now() / 1000) * 1000,
    actions: o ? [{action: 'open', title: String(o.title || '열기').slice(0, 12)}] : [],
  });
  const badge = self.navigator && self.navigator.setAppBadge && d.badge != null ? self.navigator.setAppBadge(d.badge).catch(() => {}) : null;
  e.waitUntil(Promise.all([show, badge]));
});
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const ext = e.action === 'open' && e.notification.data && e.notification.data.open;
  if (ext) { e.waitUntil(self.clients.openWindow(ext)); return; }  // 상품·사진 출처 페이지 (새 창)
  const url = new URL((e.notification.data && e.notification.data.url) || HOME, location.origin).href;
  e.waitUntil((async () => {
    const all = await self.clients.matchAll({type: 'window', includeUncontrolled: true});
    const win = all.find(c => new URL(c.url).pathname.startsWith('/dashboard'));
    if (win) {
      await win.focus();
      if (new URL(win.url).pathname === HOME) { win.postMessage({go: url}); return; }  // 앱이 열려 있으면 그 자리에서 그 일로
      try { if (win.navigate) { await win.navigate(url); return; } } catch (x) { /* 새 창으로 */ }
    }
    await self.clients.openWindow(url);
  })());
});
