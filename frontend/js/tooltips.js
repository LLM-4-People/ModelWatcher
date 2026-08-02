// Tooltip system: hover (desktop), long-press (touch), and focus support.
// Single shared #help-tip div with content from three resolution paths:
// data-tip-id (registered HTML), data-tip (HELP dict + tier scale), or raw text.
import { HELP, isRecentTouch, wasTouchMove, isTouchDevice } from './utils.js';
import { tierScaleHTML } from './format.js';

const _tipHTML = {};

export function registerTip(id, html) { _tipHTML[id] = html; }
export function clearTips(prefix) { if (!prefix) { Object.keys(_tipHTML).forEach(k => delete _tipHTML[k]); return; } Object.keys(_tipHTML).forEach(k => { if (k.startsWith(prefix)) delete _tipHTML[k]; }); }

let _tip = null;
let _current = null;
let _shownByTap = false;

function _resolveContent(el) {
  const tipId = el.dataset.tipId;
  if (tipId && _tipHTML[tipId]) return _tipHTML[tipId];
  const key = el.dataset.tip;
  if (!key) return '';
  const raw = HELP[key] || key;
  return raw + tierScaleHTML(key);
}

function _setContent(html) {
  if (/<[a-zA-Z]/.test(html)) { _tip.innerHTML = html; } else { _tip.textContent = html; }
}

function _positionAtRect(r) {
  _tip.style.top = '0'; _tip.style.left = '0'; _tip.style.visibility = 'hidden';
  _tip.classList.add('visible');
  requestAnimationFrame(() => {
    if (_current && !_current.isConnected) { _hide(); return; }
    const tr = _tip.getBoundingClientRect();
    let top = r.bottom + 8;
    let left = r.left + r.width / 2 - tr.width / 2;
    if (top + tr.height > innerHeight - 8) top = r.top - tr.height - 8;
    if (top < 8) top = 8;
    left = Math.max(8, Math.min(left, innerWidth - tr.width - 8));
    _tip.style.top = top + 'px'; _tip.style.left = left + 'px'; _tip.style.visibility = '';
  });
}

function _show(el, byTap) {
  if (_current && !_current.isConnected) _hide();
  if (el === _current) return;
  const content = _resolveContent(el);
  if (!content) return;
  _current = el;
  _shownByTap = !!byTap;
  _setContent(content);
  _positionAtRect(el.getBoundingClientRect());
}

function _hide() {
  if (!_current) return;
  _current = null;
  _shownByTap = false;
  _tip.classList.remove('visible');
}

export function showTip(html, rect) {
  _current = null;
  _shownByTap = false;
  _setContent(html);
  _positionAtRect(rect);
}

export function hideTip() {
  _current = null;
  _shownByTap = false;
  _tip.classList.remove('visible');
}

let _copiedTimer = 0;

function copyTipText(el) {
  const content = _resolveContent(el);
  if (!content) return;
  const plain = new DOMParser().parseFromString(content, 'text/html').body.textContent.replace(/\s+/g, ' ').trim();
  if (!plain) return;
  navigator.clipboard.writeText(plain).then(() => {
    _tip.textContent = 'Copied!';
    clearTimeout(_copiedTimer);
    _copiedTimer = setTimeout(() => {
      if (_current) _setContent(_resolveContent(_current));
      else _hide();
    }, 800);
  }).catch(() => {});
}

export function initTooltips() {
  _tip = document.createElement('div');
  _tip.id = 'help-tip';
  _tip.setAttribute('aria-hidden', 'true');
  document.body.appendChild(_tip);

  function findTipTarget(el) {
    if (el.closest('.notif-toggle')) return null;
    const tip = el.closest('[data-tip], [data-tip-id]');
    if (!tip) return null;
    if ((tip.tagName === 'BUTTON' || tip.tagName === 'A' || tip.closest('button, a, [role="button"]')) && !tip.dataset.tip && !tip.dataset.tipId) return null;
    if (tip.id === 'theme-btn' && document.getElementById('help-panel')?.classList.contains('open')) return null;
    if (tip.id === 'notify-btn' && document.getElementById('notif-panel')?.classList.contains('open')) return null;
    if (tip.id === 'help-btn' && document.getElementById('help-panel')?.classList.contains('open')) return null;
    return tip;
  }

  const LONG_PRESS_MS = 500;
  let _longPressTimer = 0;
  let _longPressTarget = null;
  let _longPressFired = false;

  if (isTouchDevice) {
    document.addEventListener('touchstart', e => {
      const el = findTipTarget(e.target);
      if (!el) return;
      _longPressTarget = el;
      _longPressFired = false;
      _longPressTimer = setTimeout(() => {
        _longPressFired = true;
        _show(el, true);
      }, LONG_PRESS_MS);
    }, { passive: true });

    document.addEventListener('touchmove', () => {
      clearTimeout(_longPressTimer);
      _longPressTimer = 0;
      _longPressTarget = null;
    }, { passive: true });

    document.addEventListener('touchend', e => {
      clearTimeout(_longPressTimer);
      _longPressTimer = 0;
      if (_longPressFired) {
        _longPressFired = false;
        _longPressTarget = null;
        return;
      }
      const el = _longPressTarget;
      _longPressTarget = null;
      if (wasTouchMove()) return;
      if (!el) return;
      if (_current === el) { _hide(); }
    }, { passive: true });

    document.addEventListener('touchcancel', () => {
      clearTimeout(_longPressTimer);
      _longPressTimer = 0;
      _longPressTarget = null;
      _longPressFired = false;
    }, { passive: true });
  } else {
    document.addEventListener('touchend', e => {
      if (wasTouchMove()) return;
      const el = findTipTarget(e.target);
      if (el) { _current === el ? _hide() : _show(el, true); }
    }, { passive: true });
  }

  function showIfNotTouch(el) { if (!isRecentTouch() && el) _show(el, false); }
  function hideIfNotPinned(relatedTarget) {
    if (isRecentTouch() || _shownByTap) return;
    if (_current && !_current.contains(relatedTarget)) _hide();
  }
  document.addEventListener('mouseover', e => showIfNotTouch(findTipTarget(e.target)));
  document.addEventListener('mouseout', e => hideIfNotPinned(e.relatedTarget));
  document.addEventListener('focusin', e => showIfNotTouch(findTipTarget(e.target)));
  document.addEventListener('focusout', e => hideIfNotPinned(e.relatedTarget));

  document.addEventListener('click', e => {
    if (isTouchDevice && _shownByTap) return;
    const el = findTipTarget(e.target);
    if (el) {
      if (isTouchDevice) return;
      if (el.dataset.copyTip !== undefined && _current === el) {
        copyTipText(el);
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      if (_current !== el) _show(el, false);
    } else if (_current) {
      _hide();
    }
  }, true);

  document.addEventListener('touchmove', hideTip, { passive: true });
  document.addEventListener('scroll', hideTip, true);

  new MutationObserver(() => {
    if (_current && !_current.isConnected) _hide();
  }).observe(document.body, { childList: true, subtree: true });
}
