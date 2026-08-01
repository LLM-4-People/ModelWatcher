// Batched UI scheduling - coalesces DOM update requests into a single RAF tick.
// Multiple WS messages for different models within one frame collapse into one flush.
import { state } from './state.js';
import { slug } from './utils.js';
import { updateProviderCounts, updateCardDOM, updateTimeAgoLabels } from './dom.js';
import { updateStatusLegend } from './help.js';
import { updateChartForModel } from './chart.js';
import { updateModalIfNeeded } from './modal-loader.js';
import { applyFilter, filterActive } from './filter.js';

const _d = {
  models: new Set(),
  summary: false,
  providers: false,
  checkLines: false,
  modal: null,
};

let _rafId = 0;

export function scheduleUI({ models, summary, providers, checkLines, modal } = {}) {
  if (models) for (const id of models) _d.models.add(id);
  if (summary) _d.summary = true;
  if (providers) _d.providers = true;
  if (checkLines) _d.checkLines = true;
  if (modal) _d.modal = modal;
  if (!_rafId) _rafId = requestAnimationFrame(_flush);
}

function _flush() {
  _rafId = 0;
  const models = new Set(_d.models);
  const summary = _d.summary;
  const providers = _d.providers;
  const checkLines = _d.checkLines;
  const modal = _d.modal;
  _d.models.clear();
  _d.summary = false;
  _d.providers = false;
  _d.checkLines = false;
  _d.modal = null;

  if (summary) updateStatusLegend();
  if (providers) updateProviderCounts();
  for (const id of models) {
    if (document.getElementById(`card-${slug(id)}`)) updateCardDOM(id);
  }
  // Re-apply the active filter so WS-driven status changes keep visibility and
  // the "X of Y" count live. Skipped when no filter is active (all cards visible).
  if (filterActive()) applyFilter();
  if (checkLines) updateTimeAgoLabels();
  for (const id of models) {
    const canvasId = `chart-${slug(id)}`;
    if (state.charts[canvasId]) updateChartForModel(id);
  }
  const openModel = modal?.modelId;
  for (const id of models) {
    if (id !== openModel && !document.getElementById(`card-${slug(id)}`)) continue;
    const opts = (id === openModel)
      ? { record: modal.record, testType: modal.testType }
      : {};
    updateModalIfNeeded(id, opts);
  }
  if (modal && !models.has(modal.modelId)) {
    updateModalIfNeeded(modal.modelId, { record: modal.record, testType: modal.testType });
  }
}
