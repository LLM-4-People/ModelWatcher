const CACHE_NAME = 'mw-shell-__CACHE_VERSION__';

// A classic service worker cannot import the page's ES-module loggers (utils.js); this is their
// stand-in, and every catch here goes through it (finding F35)
function swLogError(ctx, err) {
  console.error(`__APP_NAME__: SW ${ctx}`, err);
}

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
    }).catch(err => swLogError('activate failed', err))
  );
});

self.addEventListener('push', (e) => {
  let data = {};
  if (e.data) { try { data = e.data.json(); } catch (err) { swLogError('push payload is not JSON', err); } }
  e.waitUntil(
    self.registration.showNotification(data.title || '__APP_NAME__', {
      body: data.body || '',
      icon: '__STATIC_PREFIX__/icon-192.png',
      badge: '__STATIC_PREFIX__/badge-96.png',
      tag: data.tag || 'mw-event',
      data: { url: data.url || '/' },
    }).catch(err => swLogError('showNotification failed', err))
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
    }).catch(err => swLogError('notificationclick failed', err))
  );
});

self.addEventListener('pushsubscriptionchange', (e) => {
  const oldSub = e.oldSubscription;
  let vapidKey = oldSub?.options?.applicationServerKey;
  e.waitUntil(
    (vapidKey ? Promise.resolve(vapidKey) : fetch('/api/vapid-key').then(r => r.json()).then(d => d.public_key).catch(err => { swLogError('VAPID key fetch failed', err); return null; })).then(key => {
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
              }).catch(err => swLogError('push resubscribe failed', err)));
            };
            ch.port1.start();
            c.postMessage({ type: 'sw_needs_prefs' }, [ch.port2]);
            setTimeout(() => { if (!settled) { settled = true; resolve(); } }, 3000);
          });
        });
      });
    }).catch(err => swLogError('pushsubscriptionchange failed', err))
  );
});
