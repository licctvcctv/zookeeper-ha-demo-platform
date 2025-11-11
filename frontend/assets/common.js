export const API_BASE = '/api';

export async function fetchJson(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const data = await response.json();
      if (data?.detail) detail = data.detail;
    } catch (err) {
      // ignore JSON parse errors
    }
    throw new Error(detail);
  }
  return response.json();
}

export function formatBytes(bytes) {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = bytes;
  let idx = 0;
  while (value >= 1024 && idx < units.length - 1) {
    value /= 1024;
    idx += 1;
  }
  return `${value.toFixed(1)} ${units[idx]}`;
}

export function formatTimestamp(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

export function stateClass(state) {
  if (!state) return 'badge down';
  const lowered = state.toLowerCase();
  if (lowered.includes('leader')) return 'badge leader';
  if (lowered.includes('follower')) return 'badge follower';
  return 'badge down';
}

export function delay(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

export function withUiState(target, key) {
  return async function wrapped(asyncFn) {
    if (this[key]) return;
    this[key] = true;
    try {
      await asyncFn.call(this);
    } finally {
      this[key] = false;
    }
  };
}

export function logError(err) {
  console.error(err);
  return err instanceof Error ? err.message : String(err);
}

export function summarizeHistory(history = []) {
  if (!Array.isArray(history) || !history.length) return '—';
  const latest = history[history.length - 1];
  if (latest.action && latest.timestamp) {
    const parts = [latest.action];
    if (latest.from || latest.to || latest.node) {
      const origin = latest.from || latest.node || '未知';
      const target = latest.to || latest.node || '未知';
      parts.push(`(${origin} → ${target})`);
    }
    return `${formatTimestamp(latest.timestamp)} · ${parts.join(' ')}`;
  }
  return formatTimestamp(latest.timestamp) || '—';
}

export function applyLoadIndicator(value, max) {
  if (!max) return 0;
  const percent = Math.round((value / max) * 100);
  return Math.min(100, Math.max(5, percent));
}

const demoCommonBundle = {
  API_BASE,
  fetchJson,
  formatBytes,
  formatTimestamp,
  stateClass,
  delay,
  withUiState,
  logError,
  summarizeHistory,
  applyLoadIndicator,
};

if (typeof window !== 'undefined') {
  window.DemoCommon = demoCommonBundle;
}
