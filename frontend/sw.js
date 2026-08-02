const CACHE_NAME = 'mw-shell-__CACHE_VERSION__';

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then(ks => {
      const old = ks.filter(k => k !== CACHE_NAME);
      return Promise.all(old.map(k => caches.delete(k))).then(() =>
        clients.claim().then(() => {
          if (old.length === 0) return;
          return clients.matchAll({ type: 'window' }).then(cs =>
            Promise.allSettled(cs.map(c => c.navigate(c.url)))
          );
        })
      );
    })
  );
});

self.addEventListener('push', (e) => {
  let data = {};
  if (e.data) { try { data = e.data.json(); } catch {} }
  e.waitUntil(
    self.registration.showNotification(data.title || '__APP_NAME__', {
      body: data.body || '',
      icon: '__STATIC_PREFIX__/icon-192.png',
      badge: '__STATIC_PREFIX__/badge-96.png',
      tag: data.tag || 'mw-event',
      data: { url: data.url || '/' },
    })
  );
});

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const url = e.notification.data?.url || '/';
  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(wins => {
      for (const w of wins) {
        if ('focus' in w) { w.focus(); w.navigate(url); return; }
      }
      return clients.openWindow(url);
    })
  );
});

self.addEventListener('pushsubscriptionchange', (e) => {
  const oldSub = e.oldSubscription;
  let vapidKey = oldSub?.options?.applicationServerKey;
  e.waitUntil(
    (vapidKey ? Promise.resolve(vapidKey) : fetch('/api/vapid-key').then(r => r.json()).then(d => d.public_key).catch(() => null)).then(key => {
      if (!key) return;
      return self.registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: key,
      }).then(newSub => {
        if (!newSub) return;
        return clients.matchAll({ type: 'window', includeUncontrolled: true }).then(cs => {
          const c = cs[0];
          if (!c) return;
          return new Promise(resolve => {
            let settled = false;
            const ch = new MessageChannel();
            ch.port1.onmessage = (ev) => {
              if (settled) return;
              settled = true;
              const { client_id, prefs } = ev.data || {};
              resolve(fetch('/api/push/subscribe', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ...newSub.toJSON(), client_id, prefs }),
              }).catch(() => {}));
            };
            ch.port1.start();
            c.postMessage({ type: 'sw_needs_prefs' }, [ch.port2]);
            setTimeout(() => { if (!settled) { settled = true; resolve(); } }, 3000);
          });
        });
      });
    }).catch((err) => { console.error('[SW] pushsubscriptionchange error:', err); })
  );
});
