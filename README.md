# ZooKeeper HA Demo Stack

一个基于 Docker 的 ZooKeeper 高可用演示项目，集成了三节点 ZooKeeper 集群、Prometheus + Grafana 监控、现代化可视化控制台（FastAPI + Vue 3）以及智能负载调度能力。适用于课堂演示、学生竞赛、技术分享等场景。

## ✨ 核心特性

### 🎯 **一键式操作体验**
- **简化的负载演示**：点击"一键生成测试负载"按钮，自动生成 50 个测试文件并智能分配到各节点
- **实时进度反馈**：操作过程中显示进度条和状态文本，清晰展示当前执行阶段
- **智能负载均衡**：自动检测节点负载差异，超过阈值时触发文件迁移，保持集群平衡

### 📊 **可视化监控**
- **实时负载图表**：支持柱状图、横向柱状图、饼图、环形图等多种图表类型
- **智能颜色编码**：根据负载水平自动调整颜色（绿色=正常，黄色=中等，红色=高负载）
- **历史趋势分析**：记录并展示节点负载的历史变化趋势
- **Grafana 集成**：嵌入式 Grafana 仪表板，展示 ZooKeeper 集群的核心指标

### �� **集群监控与管理**
- **主从角色识别**：实时显示 Leader/Follower 角色、连接数、请求延迟等关键指标
- **节点健康检查**：通过 ZooKeeper 四字命令（mntr、stat、ruok）获取节点状态
- **操作审计留痕**：所有操作自动记录到 SQLite 数据库和 Elasticsearch，支持历史查询

### 🚀 **负载调度演示**
- **自动负载均衡**：后台调度器定期检测节点负载差异，自动迁移文件以保持平衡
- **调度快照回放**：展示调度前后的节点文件分布变化，直观理解调度效果
- **可配置阈值**：支持自定义调度阈值和执行间隔

> **注意**：为便于本地演示，节点启停通过控制 Docker 容器实现；若需对真实虚机执行启停，可在 `backend/app/docker_control.py` 中改为调用 SSH / systemd 指令。

## 组件结构

\`\`\`
zk-ha-demo/
├── docker-compose.yml           # 主编排，包含 3 个 ZK、Prometheus、Grafana、FastAPI 后端
├── backend/                     # FastAPI + Prometheus-client + Kazoo + Docker SDK
├── frontend/                    # Vue 3 + Chart.js 单页应用，通过 FastAPI 提供静态文件
├── prometheus/prometheus.yml    # 采集 ZooKeeper 与后台指标的配置
└── grafana/                     # 数据源、仪表盘自动化配置
\`\`\`

## 环境要求

- 已安装 Docker 与 Docker Compose（v2）
- 主机未占用端口：2181-2183、7001-7003、8080、9090、3000

## 快速开始

\`\`\`bash
cd zk-ha-demo
# 首次执行会构建 backend 镜像并拉取依赖镜像
docker compose up -d
\`\`\`

启动成功后访问：

- 控制台 UI：<http://localhost:8080/ui/>
- Grafana：<http://localhost:3000> （默认账号密码 \`admin/admin\`）
- Prometheus：<http://localhost:9090>
- Elasticsearch API：<http://localhost:9200>

停止演示：

\`\`\`bash
docker compose down -v
\`\`\`

## 控制台功能速览

### 1. 运行概览页面 (\`/ui/overview.html\`)
- 集群状态总览：实时显示 3 个 ZooKeeper 节点的运行状态
- 节点详细信息：展示每个节点的角色（Leader/Follower）、连接数、平均延迟、未处理请求等
- 负载平衡状态：显示节点间文件分布是否平衡，以及当前负载差异
- Grafana 监控面板：嵌入式仪表板展示活跃连接数、Znode 数量、平均请求延迟等核心指标

### 2. 负载调度演示页面 (\`/ui/workload.html\`) ⭐ 最新优化
**一键生成负载**：
- 点击"一键生成测试负载"按钮，自动生成 50 个测试文件（100-500KB）
- 实时显示进度条和操作状态（准备 → 连接 → 生成 → 上传 → 完成）
- 智能分配文件到各节点，保持负载均衡

**手动执行调度**：
- 点击"手动执行调度"按钮，触发负载均衡调度器
- 实时显示调度进度（分析负载 → 计算策略 → 迁移文件 → 完成）
- 展示调度前后的节点文件分布变化

**实时负载可视化**：
- 负载卡片：直观显示各节点的文件数量和负载百分比
- 智能颜色编码：根据负载水平自动调整颜色
- 调度快照回放：展示操作前后的文件分布对比

**节点负载监控**：
- 支持 4 种图表类型切换（柱状图、横向柱状图、饼图、环形图）
- 节点文件分布图：显示各节点的文件数量和阈值参考线
- 节点负载对比图：双 Y 轴对比文件数量和负载百分比
- 负载趋势图：展示历史负载变化趋势
- 每 8 秒自动刷新数据

### 3. 日志与审计页面 (\`/ui/logs.html\`)
- 操作历史记录：查看所有文件上传、迁移、调度等操作的详细日志
- 节点日志查看：通过 Docker API 获取各节点的最新日志（最近 200 行）
- 集中式日志查询：通过 Elasticsearch 查询和分析历史日志

### 4. 自动调度机制
- 后台调度器每 \`AUTO_SCHEDULER_INTERVAL\` 秒（默认 15 秒）检测节点负载
- 当节点间文件数量差异超过 \`SCHEDULER_THRESHOLD\`（默认 5 个文件）时自动触发迁移
- 迁移结果写入 SQLite 数据库和 Prometheus 指标
- 前端实时展示从节点 A 到节点 B 的迁移过程

## 重要环境变量

在 \`docker-compose.yml\` 中可调整：

| 变量 | 说明 |
| ---- | ---- |
| \`AUTO_SCHEDULER_INTERVAL\` | 后台调度器执行间隔（秒），默认 15 |
| \`SCHEDULER_THRESHOLD\`     | 触发迁移的负载差阈值（默认 5 个文件） |
| \`DOCKER_CONTROL_ENABLED\`  | 是否允许通过 Docker API 控制节点（true/false） |
| \`FILE_STORAGE_PATH\`       | 文件存储根目录（已通过卷映射至宿主机 \`./data/uploads\`） |
| \`ZK_NODES\`                | ZooKeeper 集群节点列表，用于四字命令、Kazoo 连接 |
| \`DEMO_WORKLOAD_ENABLED\`   | 是否启用内置示例流量（默认 \`true\`）。设为 \`false\` 可完全手动演示 |
| \`DEMO_WORKLOAD_INTERVAL\`  | Demo workload 周期秒数（默认 20，叠加随机抖动） |
| \`DEMO_WORKLOAD_MAX_FILES\` | Demo workload 保留的示例文件上限（默认 18） |
| \`DEMO_WORKLOAD_MAX_TASKS\` | Demo workload 保留的任务流水上限（默认 50） |
| \`DEMO_WORKLOAD_EXTRA_CLIENTS\` | Demo workload 额外保持的 ZooKeeper 客户端连接数（默认 2） |
| \`ELASTICSEARCH_URL\`       | 集中式日志的 Elasticsearch 地址（默认 \`http://elasticsearch:9200\`） |

## 常见扩展方向

- 将 \`docker_control.py\` 替换为调用 Rundeck/AWX/AHV API，实现真实虚机启停与审批流。
- 如果要自定义示例流量，可修改 \`backend/app/workload.py\` 或通过上述环境变量调整节奏/规模。
- 已内置 Filebeat → Elasticsearch。如需可视化界面，可额外引入 Kibana 或 Grafana Loki。
- 在 \`backend/app/main.py\` 中新增 WebSocket 推送，增强前端实时性。
- 为上传文件实现副本校验、SHA 校验和或对象存储同步，更贴近生产场景。

## 清理数据

演示结束后若要清空上传文件与 SQLite 数据，可执行：

\`\`\`bash
rm -rf data/uploads/* backend/app/data/
\`\`\`

## 故障排查

- **节点无法启动**：\`docker compose logs zk1\` 查看原因，确认宿主机端口未被占用。
- **Grafana 无法展示数据**：等待数秒保证 Prometheus 完成首次抓取，或进入 Grafana 检查数据源状态。
- **Elasticsearch 拒绝写入**：首次启动前请确保宿主机 \`vm.max_map_count\` 足够（Linux: \`sysctl -w vm.max_map_count=262144\`）。
- **后台报错 "Docker control is disabled"**：若宿主机不希望暴露 \`/var/run/docker.sock\`，可将 \`DOCKER_CONTROL_ENABLED=false\`，同时将前端启动/停用按钮隐藏或替换为 SSH 脚本。

祝演示顺利！
