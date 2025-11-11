export const computedDefinitions = {
  schedulerNodes() {
    const counts = this.schedulerInfo.counts || {};
    const keys = Object.keys(counts);
    if (keys.length) return keys;
    return (this.overview.cluster?.nodes || [])
      .map(node => node.node || (node.endpoint ? node.endpoint.split(':')[0] : null))
      .filter(Boolean);
  },
  taskStats() {
    const total = this.tasks.length;
    const succeeded = this.tasks.filter(t => t.status === 'succeeded').length;
    const running = this.tasks.filter(t => t.status === 'running').length;
    const failed = this.tasks.filter(t => t.status === 'failed').length;
    const successRate = total > 0 ? Math.round((succeeded / (succeeded + failed || 1)) * 100) : 0;
    return { total, succeeded, running, failed, successRate };
  },
  schedulingStrategy() {
    if (this.bulkForm.mode === 'pin') {
      return '集中分配模式 - 所有文件将被分配到指定节点';
    }
    return '智能负载均衡 - 根据节点当前负载自动选择最优节点';
  },
  totalFileSize() {
    return this.selectedFiles.reduce((sum, file) => sum + file.size, 0);
  },
  snapshotCards() {
    if (!this.lastSnapshots) return [];
    const segments = [];
    const { before, afterUpload, afterScheduler } = this.lastSnapshots;
    if (before?.counts) segments.push({ label: before.label || '操作前', counts: before.counts });
    if (afterUpload?.counts) segments.push({ label: afterUpload.label || '操作后', counts: afterUpload.counts });
    if (afterScheduler?.counts) segments.push({ label: afterScheduler.label || '调度后', counts: afterScheduler.counts });
    if (!segments.length) return [];

    const nodeSet = new Set();
    segments.forEach(item => {
      Object.keys(item.counts || {}).forEach(key => nodeSet.add(key));
    });
    const nodes = Array.from(nodeSet).sort();
    const cards = [];
    let previousCounts = null;
    segments.forEach((item, index) => {
      const counts = item.counts || {};
      const rows = nodes.map(node => {
        const value = counts[node] ?? 0;
        let delta = 0;
        if (index > 0 && previousCounts) {
          const base = previousCounts[node] ?? 0;
          delta = value - base;
        }
        return { node, value, delta };
      });
      cards.push({ label: item.label, rows, showDelta: index > 0 });
      previousCounts = counts;
    });
    return cards;
  },
};
