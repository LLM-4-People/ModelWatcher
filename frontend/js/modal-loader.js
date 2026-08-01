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

export function openModal(key) { return _load().then(m => m.openModal(key)); }

export function closeModal() { return _load().then(m => m.closeModal()); }

export function updateModalIfNeeded(id, opts) {
  const el = document.getElementById('modal');
  if (!el || el.classList.contains('hidden')) return;
  return _load().then(m => m.updateModalIfNeeded(id, opts));
}
