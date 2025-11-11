import { nextTick, markRaw } from 'https://cdn.jsdelivr.net/npm/vue@3.4.27/dist/vue.esm-browser.prod.js';
import { API_BASE, fetchJson, formatBytes, summarizeHistory, formatTimestamp } from '../common.js';

export const methodDefinitions = {
  formatBytes,
  summarizeHistory,
  formatTimestamp,
  statusLabel(status) {
    const labels = {
      queued: '排队中',
      running: '运行中',
      succeeded: '成功',
      failed: '失败',
      cancelled: '已取消'
    };
    return labels[status] || status;
  },
  actionLabel(action) {
    const labels = {
      upload: '文件上传',
      auto_migrate: '自动调度',
      demo_migrate: '演示迁移',
      bulk_upload_batch: '批量上传',
      stress_upload: '热点制造',
      stop: '停止节点',
      start: '启动节点',
      restart: '重启节点',
      drain: '摘除节点',
      undrain: '恢复节点',
      demo_task: '演示任务',
      demo_upload: '演示上传',
      demo_cleanup: '清理文件'
    };
    return labels[action] || action;
  },
  cloneCounts(source) {
    if (!source) return null;
    const result = {};
    for (const [key, value] of Object.entries(source)) {
      result[key] = value;
    }
    return result;
  },
  async refreshOverview() {
    const data = await fetchJson(`${API_BASE}/overview`);
    this.overview = data;
    this.files = (data.files || []).map(file => ({
      ...file,
      history: typeof file.history === 'string' ? JSON.parse(file.history) : (file.history || []),
    }));
    this.tasks = data.tasks || [];
    if (!this.stressForm.node && this.schedulerNodes.length) {
      this.stressForm.node = this.schedulerNodes[0];
    }
    if (!this.bulkForm.target_node && this.schedulerNodes.length) {
      this.bulkForm.target_node = this.schedulerNodes[0];
    }
  },
  async refreshOperations() {
    try {
      this.operations = await fetchJson(`${API_BASE}/operations?limit=50`);
    } catch (err) {
      console.warn('Failed to load operations:', err);
      this.operations = [];
    }
  },
  async refreshScheduler() {
    this.schedulerInfo = await fetchJson(`${API_BASE}/scheduler/diagnostics`);
  },
  async refreshAll() {
    await Promise.all([this.refreshOverview(), this.refreshScheduler(), this.refreshOperations()]);
    await nextTick();
    this.updateChart();
    this.updateNodeLoadStatus();
  },
  handleBulkFileSelect(event) {
    const files = Array.from(event.target.files || []);
    this.selectedFiles = files;
  },
  updateNodeLoadStatus() {
    const counts = this.schedulerInfo.counts || {};
    const nodes = Object.keys(counts);
    if (!nodes.length) return;

    const maxCount = Math.max(...Object.values(counts), 1);
    const avgCount = Object.values(counts).reduce((a, b) => a + b, 0) / nodes.length;

    this.nodeLoadStatus = nodes.map(node => {
      const fileCount = counts[node] || 0;
      const loadPercent = Math.round((fileCount / maxCount) * 100);
      let status = 'balanced';
      let statusText = '正常';

      if (fileCount > avgCount * 1.3) {
        status = 'overloaded';
        statusText = '负载较高';
      } else if (fileCount < avgCount * 0.7 && avgCount > 0) {
        status = 'underloaded';
        statusText = '负载较低';
      }

      return {
        name: node,
        fileCount,
        loadPercent,
        status,
        statusText
      };
    });
  },
  getChartCtor() {
    const chartGlobal = window.Chart ?? window.ChartJS;
    const ctor = chartGlobal && (chartGlobal.Chart ?? chartGlobal.ChartConstructor ?? chartGlobal);
    return typeof ctor === 'function' ? ctor : null;
  },
  initChart() {
    const ctx = document.getElementById('distributionChart');
    if (!ctx) return;
    const ChartCtor = this.getChartCtor();
    if (!ChartCtor) {
      console.error('Chart.js 未正确加载，无法绘制图表');
      this.chartError = '图表库加载失败，请刷新页面后重试。';
      return;
    }

    // 主文件分布图表 - 增强版
    // 使用 markRaw 防止 Chart 实例变成响应式对象，避免堆栈溢出
    this.chart = markRaw(new ChartCtor(ctx, {
      type: this.chartType,
      data: {
        labels: [],
        datasets: [{
          label: '文件数量',
          data: [],
          backgroundColor: [],
          borderColor: [],
          borderWidth: 2,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            display: this.chartType === 'pie' || this.chartType === 'doughnut',
            position: 'bottom',
          },
          tooltip: {
            callbacks: {
              label: (context) => {
                const label = context.label || '';
                const value = context.parsed.y || context.parsed || 0;
                const total = context.dataset.data.reduce((a, b) => a + b, 0);
                const percentage = total > 0 ? ((value / total) * 100).toFixed(1) : 0;
                return `${label}: ${value} 个文件 (${percentage}%)`;
              }
            }
          },
          annotation: {
            annotations: {
              thresholdLine: {
                type: 'line',
                yMin: this.schedulerInfo.threshold,
                yMax: this.schedulerInfo.threshold,
                borderColor: 'rgba(251, 191, 36, 0.8)',
                borderWidth: 2,
                borderDash: [5, 5],
                label: {
                  display: true,
                  content: `阈值: ${this.schedulerInfo.threshold}`,
                  position: 'end',
                  backgroundColor: 'rgba(251, 191, 36, 0.8)',
                }
              }
            }
          }
        },
        scales: this.chartType === 'bar' || this.chartType === 'horizontalBar' ? {
          y: {
            beginAtZero: true,
            ticks: {
              stepSize: 1,
              callback: (value) => value + ' 个'
            },
            grid: {
              color: 'rgba(0, 0, 0, 0.05)',
            }
          },
          x: {
            grid: {
              display: false,
            }
          }
        } : {},
        animation: {
          duration: 750,
          easing: 'easeInOutQuart'
        }
      },
    }));

    // 初始化任务状态图表
    const statusCtx = document.getElementById('taskStatusChart');
    if (statusCtx && !this.taskStatusChart) {
      this.taskStatusChart = markRaw(new ChartCtor(statusCtx, {
        type: 'pie',
        data: {
          labels: ['排队中', '运行中', '已成功', '已失败'],
          datasets: [{
            data: [0, 0, 0, 0],
            backgroundColor: [
              'rgba(79, 70, 229, 0.8)',
              'rgba(251, 191, 36, 0.8)',
              'rgba(16, 185, 129, 0.8)',
              'rgba(239, 68, 68, 0.8)',
            ],
            borderColor: 'white',
            borderWidth: 2,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { position: 'bottom' },
            tooltip: {
              callbacks: {
                label: function(context) {
                  return context.label + ': ' + context.parsed + ' 个';
                }
              }
            }
          },
        },
      }));
    }

    // 初始化节点任务图表
    const nodeCtx = document.getElementById('taskNodeChart');
    if (nodeCtx && !this.taskNodeChart) {
      this.taskNodeChart = markRaw(new ChartCtor(nodeCtx, {
        type: 'bar',
        data: {
          labels: [],
          datasets: [{
            label: '任务数量',
            data: [],
            backgroundColor: 'rgba(99, 102, 241, 0.8)',
            borderColor: 'rgba(99, 102, 241, 1)',
            borderWidth: 1,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            y: {
              beginAtZero: true,
              ticks: { stepSize: 1 },
            },
          },
          plugins: {
            legend: { display: false },
          },
        },
      }));
    }

    // 初始化负载对比图表
    const loadCompCtx = document.getElementById('loadComparisonChart');
    if (loadCompCtx && !this.loadComparisonChart) {
      this.loadComparisonChart = markRaw(new ChartCtor(loadCompCtx, {
        type: 'bar',
        data: {
          labels: [],
          datasets: [
            {
              label: '文件数量',
              data: [],
              backgroundColor: 'rgba(59, 130, 246, 0.6)',
              borderColor: 'rgba(59, 130, 246, 1)',
              borderWidth: 2,
              yAxisID: 'y',
            },
            {
              label: '文件总大小 (MB)',
              data: [],
              backgroundColor: 'rgba(16, 185, 129, 0.6)',
              borderColor: 'rgba(16, 185, 129, 1)',
              borderWidth: 2,
              yAxisID: 'y1',
            }
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: {
            mode: 'index',
            intersect: false,
          },
          plugins: {
            legend: {
              display: true,
              position: 'top',
            },
            tooltip: {
              callbacks: {
                label: (context) => {
                  let label = context.dataset.label || '';
                  if (label) {
                    label += ': ';
                  }
                  if (context.parsed.y !== null) {
                    if (context.datasetIndex === 0) {
                      label += context.parsed.y + ' 个';
                    } else {
                      label += context.parsed.y.toFixed(2) + ' MB';
                    }
                  }
                  return label;
                }
              }
            }
          },
          scales: {
            y: {
              type: 'linear',
              display: true,
              position: 'left',
              beginAtZero: true,
              title: {
                display: true,
                text: '文件数量'
              }
            },
            y1: {
              type: 'linear',
              display: true,
              position: 'right',
              beginAtZero: true,
              title: {
                display: true,
                text: '文件大小 (MB)'
              },
              grid: {
                drawOnChartArea: false,
              },
            },
          },
        },
      }));
    }

    // 初始化负载趋势图表
    const trendCtx = document.getElementById('loadTrendChart');
    if (trendCtx && !this.loadTrendChart) {
      this.loadTrendChart = markRaw(new ChartCtor(trendCtx, {
        type: 'line',
        data: {
          labels: [],
          datasets: [],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: {
            mode: 'index',
            intersect: false,
          },
          plugins: {
            legend: {
              display: true,
              position: 'top',
            },
            tooltip: {
              mode: 'index',
            }
          },
          scales: {
            y: {
              beginAtZero: true,
              title: {
                display: true,
                text: '文件数量'
              }
            },
            x: {
              title: {
                display: true,
                text: '时间'
              }
            }
          },
          elements: {
            line: {
              tension: 0.4
            },
            point: {
              radius: 3,
              hitRadius: 10,
              hoverRadius: 5
            }
          }
        },
      }));
    }
  },
  getNodeColor(value, maxValue, avgValue) {
    // 根据负载情况返回颜色
    if (value > avgValue * 1.3) {
      // 高负载 - 红色系
      return {
        bg: 'rgba(239, 68, 68, 0.7)',
        border: 'rgba(239, 68, 68, 1)'
      };
    } else if (value < avgValue * 0.7 && avgValue > 0) {
      // 低负载 - 蓝色系
      return {
        bg: 'rgba(59, 130, 246, 0.7)',
        border: 'rgba(59, 130, 246, 1)'
      };
    } else {
      // 正常负载 - 绿色系
      return {
        bg: 'rgba(16, 185, 129, 0.7)',
        border: 'rgba(16, 185, 129, 1)'
      };
    }
  },
  updateChart() {
    if (!this.chart) {
      this.initChart();
    }
    if (!this.chart) return;

    // 更新时间戳
    const now = new Date();
    this.lastUpdateTime = now.toLocaleTimeString('zh-CN');

    const counts = { ...(this.schedulerInfo.counts || {}) };
    if (!Object.keys(counts).length) {
      for (const file of this.files) {
        counts[file.node] = (counts[file.node] || 0) + 1;
      }
    }

    const labels = Object.keys(counts).sort();
    const data = labels.map(label => counts[label]);

    // 计算平均值和最大值用于颜色编码
    const maxValue = Math.max(...data, 1);
    const avgValue = data.reduce((a, b) => a + b, 0) / data.length || 0;

    // 为每个节点分配颜色
    const colors = data.map(value => this.getNodeColor(value, maxValue, avgValue));
    const backgroundColors = colors.map(c => c.bg);
    const borderColors = colors.map(c => c.border);

    this.chart.data.labels = labels;
    this.chart.data.datasets[0].data = data;
    this.chart.data.datasets[0].backgroundColor = backgroundColors;
    this.chart.data.datasets[0].borderColor = borderColors;

    // 更新阈值线（如果使用了 annotation 插件）
    if (this.chart.options.plugins?.annotation?.annotations?.thresholdLine) {
      this.chart.options.plugins.annotation.annotations.thresholdLine.yMin = this.schedulerInfo.threshold;
      this.chart.options.plugins.annotation.annotations.thresholdLine.yMax = this.schedulerInfo.threshold;
      this.chart.options.plugins.annotation.annotations.thresholdLine.label.content = `阈值: ${this.schedulerInfo.threshold}`;
    }

    this.chart.update('none'); // 使用 'none' 模式避免动画延迟
    this.chartEmpty = !labels.length || data.every(value => value === 0);
    this.chartError = '';

    // 更新负载对比图表
    this.updateLoadComparisonChart(labels, counts);

    // 更新趋势图表
    this.updateLoadTrendChart(labels, counts);

    // 更新任务状态图表
    if (this.taskStatusChart) {
      const queued = this.tasks.filter(t => t.status === 'queued').length;
      const running = this.tasks.filter(t => t.status === 'running').length;
      const succeeded = this.tasks.filter(t => t.status === 'succeeded').length;
      const failed = this.tasks.filter(t => t.status === 'failed').length;

      this.taskStatusChart.data.datasets[0].data = [queued, running, succeeded, failed];
      this.taskStatusChart.update('none');
    }

    // 更新节点任务图表
    if (this.taskNodeChart) {
      const tasksByNode = {};
      this.tasks.forEach(task => {
        const node = task.node || 'unknown';
        tasksByNode[node] = (tasksByNode[node] || 0) + 1;
      });

      const labels = Object.keys(tasksByNode).sort();
      const data = labels.map(label => tasksByNode[label]);

      this.taskNodeChart.data.labels = labels;
      this.taskNodeChart.data.datasets[0].data = data;
      this.taskNodeChart.update('none');
    }
  },
  updateLoadComparisonChart(labels, counts) {
    if (!this.loadComparisonChart) return;

    // 计算每个节点的文件总大小
    const sizeByNode = {};
    this.files.forEach(file => {
      const node = file.node;
      if (!sizeByNode[node]) {
        sizeByNode[node] = 0;
      }
      sizeByNode[node] += file.size_bytes || 0;
    });

    const fileCounts = labels.map(label => counts[label] || 0);
    const fileSizes = labels.map(label => (sizeByNode[label] || 0) / (1024 * 1024)); // 转换为 MB

    this.loadComparisonChart.data.labels = labels;
    this.loadComparisonChart.data.datasets[0].data = fileCounts;
    this.loadComparisonChart.data.datasets[1].data = fileSizes;
    this.loadComparisonChart.update('none');
  },
  updateLoadTrendChart(labels, counts) {
    if (!this.loadTrendChart) return;

    // 记录历史数据
    const timestamp = new Date().toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit'
    });

    // 添加新的数据点
    this.loadHistory.push({
      timestamp,
      counts: { ...counts }
    });

    // 限制历史记录数量
    if (this.loadHistory.length > this.maxHistoryPoints) {
      this.loadHistory.shift();
    }

    // 准备趋势图数据
    const timestamps = this.loadHistory.map(h => h.timestamp);
    const datasets = labels.map((node, index) => {
      const colors = [
        'rgba(59, 130, 246, 1)',
        'rgba(16, 185, 129, 1)',
        'rgba(239, 68, 68, 1)',
        'rgba(251, 191, 36, 1)',
        'rgba(139, 92, 246, 1)',
      ];
      const color = colors[index % colors.length];

      return {
        label: node,
        data: this.loadHistory.map(h => h.counts[node] || 0),
        borderColor: color,
        backgroundColor: color.replace('1)', '0.1)'),
        fill: false,
        tension: 0.4
      };
    });

    this.loadTrendChart.data.labels = timestamps;
    this.loadTrendChart.data.datasets = datasets;
    this.loadTrendChart.update('none');
  },
  switchChartType() {
    // 切换图表类型
    if (!this.chart) return;

    this.chart.config.type = this.chartType;

    // 根据图表类型调整配置
    if (this.chartType === 'pie' || this.chartType === 'doughnut') {
      this.chart.options.plugins.legend.display = true;
      this.chart.options.scales = {};
    } else if (this.chartType === 'horizontalBar') {
      this.chart.config.type = 'bar';
      this.chart.options.indexAxis = 'y';
      this.chart.options.plugins.legend.display = false;
      this.chart.options.scales = {
        x: {
          beginAtZero: true,
          ticks: {
            stepSize: 1,
            callback: (value) => value + ' 个'
          }
        }
      };
    } else {
      this.chart.options.indexAxis = 'x';
      this.chart.options.plugins.legend.display = false;
      this.chart.options.scales = {
        y: {
          beginAtZero: true,
          ticks: {
            stepSize: 1,
            callback: (value) => value + ' 个'
          }
        },
        x: {
          grid: {
            display: false,
          }
        }
      };
    }

    this.chart.update();
  },
  async runScheduler() {
    this.schedulerRunning = true;
    this.schedulerProgress = 0;
    this.schedulerProgressText = '准备执行调度...';

    try {
      this.schedulerProgress = 20;
      this.schedulerProgressText = '正在分析节点负载...';
      await new Promise(resolve => setTimeout(resolve, 200));

      this.schedulerProgress = 40;
      this.schedulerProgressText = '正在计算迁移策略...';

      const result = await fetchJson(`${API_BASE}/scheduler/run`, { method: 'POST' });

      this.schedulerProgress = 70;
      if (result.executed) {
        const migratedCount = result.migrations?.length || 0;
        const sourceNode = result.before?.sourceNode || '';
        const targetNode = result.before?.targetNode || '';
        this.schedulerProgressText = `正在迁移文件 (${sourceNode} → ${targetNode})...`;
      } else {
        this.schedulerProgressText = '检查完成，无需迁移';
      }
      await new Promise(resolve => setTimeout(resolve, 300));

      this.schedulerProgress = 100;
      this.schedulerProgressText = '✅ 调度完成！';

      const finalMessage = result?.after?.message || (result.executed ? '已执行一次自动调度。' : '未满足阈值条件，未执行迁移。');
      this.schedulerMessage = finalMessage;

      const beforeCounts = this.cloneCounts(result?.before?.counts);
      const afterCounts = this.cloneCounts(result?.after?.counts);
      if (beforeCounts || afterCounts) {
        this.lastSnapshots = {
          before: beforeCounts ? { label: '调度前', counts: beforeCounts } : null,
          afterScheduler: afterCounts ? { label: result.executed ? '调度后' : '调度后（未迁移）', counts: afterCounts } : null,
        };
      } else {
        this.lastSnapshots = null;
      }
      this.lastSchedulerNote = finalMessage;
      await this.refreshAll();
    } catch (err) {
      this.schedulerMessage = `调度执行失败：${err.message}`;
      this.schedulerProgressText = '❌ 调度失败';
    } finally {
      // 延迟隐藏进度条，让用户看到完成状态
      setTimeout(() => {
        this.schedulerRunning = false;
        this.schedulerProgress = 0;
        this.schedulerProgressText = '';
      }, 1000);
    }
  },
  async triggerStress() {
    if (!this.stressForm.node) {
      this.stressMessage = '请选择目标节点';
      return;
    }
    this.stressLoading = true;
    this.stressMessage = '';
    try {
      const res = await fetchJson(`${API_BASE}/demo/stress`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(this.stressForm),
      });
      const created = res?.result?.created?.length || 0;
      const trimmed = res?.result?.trimmed?.length || 0;
      const parts = [`新增 ${created} 个文件`];
      if (trimmed) parts.push(`回收 ${trimmed} 个过期文件`);
      this.stressMessage = `热点制造完成：${parts.join('，')}。`;
      const beforeCounts = this.cloneCounts(res?.scheduler?.before?.counts);
      const afterCounts = this.cloneCounts(res?.scheduler?.after?.counts);
      if (beforeCounts || afterCounts) {
        this.lastSnapshots = {
          before: beforeCounts ? { label: '操作前', counts: beforeCounts } : null,
          afterScheduler: afterCounts ? { label: res?.scheduler?.triggered ? '调度后' : '操作后', counts: afterCounts } : null,
        };
      } else {
        this.lastSnapshots = null;
      }
      const note = res?.scheduler?.after?.message || res?.scheduler?.before?.message || '';
      let finalMessage = note;
      if (!finalMessage) {
        finalMessage = res?.scheduler?.triggered ? '热点生成后已立即触发调度。' : '热点已生成，可手动触发调度。';
      }
      this.lastSchedulerNote = finalMessage;
      this.schedulerMessage = finalMessage;
      await this.refreshAll();
    } catch (err) {
      this.stressMessage = `执行失败：${err.message}`;
    } finally {
      this.stressLoading = false;
    }
  },
  async bulkUpload() {
    if (this.bulkForm.mode === 'pin' && !this.bulkForm.target_node) {
      this.bulkMessage = '请选择集中模式下的目标节点。';
      return;
    }

    // 真实文件上传
    if (this.bulkForm.uploadType === 'real') {
      if (!this.selectedFiles.length) {
        this.bulkMessage = '请先选择要上传的文件。';
        return;
      }
      await this.uploadRealFiles();
      return;
    }

    // 模拟文件生成
    this.bulkLoading = true;
    this.bulkMessage = '';
    try {
      const payload = { ...this.bulkForm };
      const res = await fetchJson(`${API_BASE}/files/bulk-generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const parts = [`新增 ${res.created_count || 0} 个文件`];
      if (res.per_node_created) {
        const dist = Object.entries(res.per_node_created).map(([node, value]) => `${node}:${value}`).join('，');
        parts.push(`分布 ${dist}`);
      }
      if (res?.scheduler?.triggered) {
        parts.push('已自动调度一次');
      }
      this.bulkMessage = parts.join('，');
      const beforeCounts = this.cloneCounts(res.counts?.before);
      const afterUploadCounts = this.cloneCounts(res.counts?.after_upload);
      const afterSchedulerCounts = this.cloneCounts(res.counts?.after_scheduler);
      if (beforeCounts || afterUploadCounts || afterSchedulerCounts) {
        this.lastSnapshots = {
          before: beforeCounts ? { label: '操作前', counts: beforeCounts } : null,
          afterUpload: afterUploadCounts ? { label: '上传后', counts: afterUploadCounts } : null,
          afterScheduler: afterSchedulerCounts ? { label: '调度后', counts: afterSchedulerCounts } : null,
        };
      } else {
        this.lastSnapshots = null;
      }
      const note = res?.scheduler?.after_scheduler?.message || res?.scheduler?.after_upload?.message || res?.scheduler?.message || '';
      let finalMessage = note;
      if (!finalMessage) {
        finalMessage = res?.scheduler?.triggered ? '已自动调度完成均衡。' : '未触发调度，批量上传完成。';
      }
      this.lastSchedulerNote = finalMessage;
      this.schedulerMessage = finalMessage;
      await this.refreshAll();
    } catch (err) {
      this.bulkMessage = `执行失败：${err.message}`;
    } finally {
      this.bulkLoading = false;
    }
  },
  async uploadRealFiles() {
    this.bulkLoading = true;
    this.bulkMessage = '';
    this.uploadProgress = [];

    try {
      const files = this.selectedFiles;
      let successCount = 0;
      let failCount = 0;

      // 初始化上传进度
      this.uploadProgress = files.map((file, index) => ({
        id: `upload-${Date.now()}-${index}`,
        filename: file.name,
        size: file.size,
        node: '分配中...',
        status: 'uploading'
      }));

      // 逐个上传文件
      for (let i = 0; i < files.length; i++) {
        const file = files[i];
        const progressItem = this.uploadProgress[i];

        try {
          const form = new FormData();
          form.append('file', file);

          const res = await fetchJson(`${API_BASE}/files/upload`, {
            method: 'POST',
            body: form,
          });

          progressItem.node = res.node;
          progressItem.status = 'success';
          successCount++;

          // 短暂延迟以显示动画效果
          await new Promise(resolve => setTimeout(resolve, 300));
        } catch (err) {
          progressItem.status = 'error';
          progressItem.node = '上传失败';
          failCount++;
        }
      }

      this.bulkMessage = `上传完成：成功 ${successCount} 个，失败 ${failCount} 个`;

      // 如果需要触发调度
      if (this.bulkForm.trigger_scheduler && successCount > 0) {
        await this.runScheduler();
      }

      await this.refreshAll();

      // 清空选择的文件
      this.selectedFiles = [];
      if (this.$refs.bulkFileInput) {
        this.$refs.bulkFileInput.value = '';
      }

      // 3秒后清除上传进度
      setTimeout(() => {
        this.uploadProgress = [];
      }, 3000);

    } catch (err) {
      this.bulkMessage = `批量上传失败：${err.message}`;
    } finally {
      this.bulkLoading = false;
    }
  },
  async quickGenerateLoad() {
    this.bulkLoading = true;
    this.bulkMessage = '';
    this.bulkProgress = 0;
    this.bulkProgressText = '准备生成文件...';

    try {
      // 模拟进度更新
      this.bulkProgress = 10;
      this.bulkProgressText = '正在连接服务器...';
      await new Promise(resolve => setTimeout(resolve, 200));

      this.bulkProgress = 30;
      this.bulkProgressText = '正在生成 50 个测试文件...';

      const payload = {
        count: 50,
        size_kb: Math.floor(Math.random() * 400) + 100, // 100-500KB 随机大小
        mode: 'auto',
        trigger_scheduler: false,
      };

      this.bulkProgress = 50;
      this.bulkProgressText = '正在上传文件到 ZooKeeper 集群...';

      const res = await fetchJson(`${API_BASE}/files/bulk-generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      this.bulkProgress = 80;
      this.bulkProgressText = '正在分配文件到各节点...';
      await new Promise(resolve => setTimeout(resolve, 300));

      this.bulkProgress = 100;
      this.bulkProgressText = '✅ 生成完成！';

      const parts = [`✅ 成功生成 ${res.created_count || 0} 个测试文件`];
      if (res.per_node_created) {
        const dist = Object.entries(res.per_node_created).map(([node, value]) => `${node}: ${value}个`).join('，');
        parts.push(`分布：${dist}`);
      }
      this.bulkMessage = parts.join(' | ');

      // 更新快照数据
      const beforeCounts = this.cloneCounts(res.counts?.before);
      const afterUploadCounts = this.cloneCounts(res.counts?.after_upload);
      if (beforeCounts || afterUploadCounts) {
        this.lastSnapshots = {
          before: beforeCounts ? { label: '操作前', counts: beforeCounts } : null,
          afterUpload: afterUploadCounts ? { label: '生成后', counts: afterUploadCounts } : null,
          afterScheduler: null,
        };
      }

      await this.refreshAll();
    } catch (err) {
      this.bulkMessage = `❌ 生成失败：${err.message}`;
      this.bulkProgressText = '❌ 生成失败';
    } finally {
      // 延迟隐藏进度条，让用户看到完成状态
      setTimeout(() => {
        this.bulkLoading = false;
        this.bulkProgress = 0;
        this.bulkProgressText = '';
      }, 1000);
    }
  },
  async uploadFile() {
    const input = this.$refs.uploadInput;
    if (!input || !input.files.length) return;
    const form = new FormData();
    form.append('file', input.files[0]);
    this.uploading = true;
    this.uploadMessage = '';
    try {
      const res = await fetchJson(`${API_BASE}/files/upload`, {
        method: 'POST',
        body: form,
      });
      this.uploadMessage = `上传成功，分配到 ${res.node}`;
      input.value = '';
      await this.refreshAll();
    } catch (err) {
      this.uploadMessage = `上传失败：${err.message}`;
    } finally {
      this.uploading = false;
    }
  },
};
