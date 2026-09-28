// The model modal loads on first use. Its calls log their own failure here, so no caller leaves a
// rejected promise unhandled (a failed import surfaced as an uncaught rejection, finding F100).
import { logError, logTag } from './utils.js';

let _promise = null;

function _load() {
  if (!_promise) {
    _promise = import('./modal.js').catch(e => {
      _promise = null;
      throw e;
    });
  }
  return _promise;
}

function _call(name, ...args) {
  return _load().then(m => m[name](...args)).catch(e => logError(logTag('Modal', '←', 'Error', name), e));
}

export function openModal(key) { return _call('openModal', key); }

export function closeModal() { return _call('closeModal'); }

export function updateModalIfNeeded(id, opts) {
  const el = document.getElementById('modal');
  if (!el || el.classList.contains('hidden')) return;
  return _call('updateModalIfNeeded', id, opts);
}
