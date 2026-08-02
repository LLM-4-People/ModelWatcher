// IndexedDB-backed cache layer for providers, config, metrics, and VAPID key.
import { logError, logTag } from './utils.js';

const _DB_NAME = 'mw_cache';
const _DB_VERSION = 2;
const _STORE = 'items';
let _db = null;

function _open() {
  if (_db) return _db;
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(_DB_NAME, _DB_VERSION);
    req.onupgradeneeded = e => {
      const db = e.target.result;
      if (e.oldVersion < 2 && db.objectStoreNames.contains(_STORE)) {
        db.deleteObjectStore(_STORE);
      }
      if (!db.objectStoreNames.contains(_STORE)) {
        db.createObjectStore(_STORE, { keyPath: 'key' });
      }
    };
    req.onsuccess = e => { _db = e.target.result; resolve(_db); };
    req.onerror = () => reject(req.error);
  });
}

export async function cacheGet(key) {
  try {
    const db = await _open();
    return new Promise((resolve) => {
      const tx = db.transaction(_STORE, 'readonly');
      const req = tx.objectStore(_STORE).get(key);
      req.onsuccess = () => {
        const entry = req.result;
        if (!entry) return resolve(null);
        if (entry.expires && Date.now() > entry.expires) {
          const tx2 = db.transaction(_STORE, 'readwrite');
          tx2.objectStore(_STORE).delete(key);
          return resolve(null);
        }
        resolve(entry.data);
      };
      req.onerror = () => resolve(null);
    });
  } catch (e) { logError(logTag('Cache', '←', 'Error', 'Get'), e); return null; }
}

export async function cacheSet(key, data, ttlSeconds = 300) {
  try {
    const db = await _open();
    return new Promise((resolve) => {
      const tx = db.transaction(_STORE, 'readwrite');
      tx.objectStore(_STORE).put({
        key,
        data,
        expires: ttlSeconds > 0 ? Date.now() + ttlSeconds * 1000 : null,
      });
      tx.oncomplete = () => resolve();
      tx.onerror = () => resolve();
    });
  } catch (e) { logError(logTag('Cache', '←', 'Error', 'Set'), e); }
}

