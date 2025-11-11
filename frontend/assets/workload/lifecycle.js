export function mountedHook() {
  this.initChart();
  this.refreshAll();
  this._autoRefreshTimer = setInterval(() => this.refreshAll(), 8000);
}

export function beforeUnmountHook() {
  if (this._autoRefreshTimer) {
    clearInterval(this._autoRefreshTimer);
    this._autoRefreshTimer = null;
  }
}
