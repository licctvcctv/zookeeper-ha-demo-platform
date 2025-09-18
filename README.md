# ZooKeeper HA Demo Stack

一个基于 Docker 的 ZooKeeper 高可用演示项目，集成了三节点 ZooKeeper 集群、Prometheus + Grafana 监控、可视化控制台（FastAPI + Vue）以及模拟的业务负载调度能力。适用于课堂 / 学生竞赛场景，展示以下能力：

- **主从角色与实时负载**：调用四字命令与 Prometheus 指标，标识 Leader/Follower、连接数、请求延迟等关键指标。
- **网页端启停控制 + 审计留痕**：通过 Docker API 控制容器化的 ZooKeeper 节点，自动记录操作前/后的指标快照与审计日志。
- **文件 / 图片上传分布**：上传示例文件时根据节点负载自动分配，元数据写入 ZooKeeper `/demo/files` 目录，前端可查看历史迁移轨迹。
- **负载均衡演示**：后台调度器按阈值监控节点负载差异并自动迁移文件，实时更新操作日志与前端图表。
- **内置示例流量**：默认开启的 demo workload 会周期性写入 / 迁移文件、增删 znode 并保持额外连接，Grafana 和前端无需手工操作即可看到实时数据（可通过环境变量关闭）。
- **集中式日志**：Filebeat 将 ZooKeeper / 后端日志及操作流水推送到 Elasticsearch，并在页面提供查询界面；也可直接通过 `http://localhost:9200/operations/_search` 等索引验证。
- **运行日志与监控面板**：支持查看节点最新日志片段，并嵌入 Grafana 预设仪表盘。

> **注意**：为便于本地演示，节点启停通过控制 Docker 容器实现；若需对真实虚机执行启停，可在 `backend/app/docker_control.py` 中改为调用 SSH / systemd 指令。

## 组件结构

```
zk-ha-demo/
├── docker-compose.yml           # 主编排，包含 3 个 ZK、Prometheus、Grafana、FastAPI 后端
├── backend/                     # FastAPI + Prometheus-client + Kazoo + Docker SDK
├── frontend/                    # Vue + Chart.js 单页应用，通过 FastAPI 提供静态文件
├── prometheus/prometheus.yml    # 采集 ZooKeeper 与后台指标的配置
└── grafana/                     # 数据源、仪表盘自动化配置
```

## 环境要求

- 已安装 Docker 与 Docker Compose（v2）
- 主机未占用端口：2181-2183、7001-7003、8080、9090、3000

## 快速开始

```bash
cd zk-ha-demo
# 首次执行会构建 backend 镜像并拉取依赖镜像
docker compose up -d
```

启动成功后访问：

- 控制台 UI：<http://localhost:8080/ui/>
- Grafana：<http://localhost:3000> （默认账号密码 `admin/admin`）
- Prometheus：<http://localhost:9090>
- Elasticsearch API：<http://localhost:9200>

停止演示：

```bash
docker compose down -v
```

## 控制台功能速览

1. **集群概览**：
   - 展示节点状态、实时连接数、平均延迟、未处理请求等核心指标。
   - 一键执行 `启动 / 停用 / 重启 / 查看日志`，操作会被写入 SQLite + Prometheus 指标。

2. **Grafana 面板**：
   - 自动挂载 Prometheus 数据源和示例仪表盘（连接数 / Znode 数 / 延迟）。
   - 如需外链嵌入，记得在 Grafana 内开启匿名只读或调整 CORS 设置。

3. **文件分布模拟**：
   - 上传任意文件（图片、文档等）将被保存到最空闲的节点目录（通过共享卷写入），并创建 ZooKeeper 元数据。
   - 表格展示文件历史迁移记录，Chart.js 图表对比各节点负载。

4. **应用任务流水**：
   - “负载与任务”页提供“手动触发示例流量”面板，可按需生成示例文件、推进任务、制造 znode 事件。
   - 若要接入真实业务任务，可复用 `db.upsert_task_record` 等接口；保留的手动按钮也可用于压测或演示。

5. **自动调度**：
   - 后台每 `AUTO_SCHEDULER_INTERVAL` 秒检测节点间文件数量差异，超过 `SCHEDULER_THRESHOLD` 即触发迁移（可在 `docker-compose.yml` 中调整）。
   - 迁移结果写入 `operations` 表及 Prometheus 指标，前端可实时看到“从节点 A → 节点 B”变化。

6. **日志中心**：
   - 通过 Docker API 获取 `docker logs` 最新 200 行，快速定位节点异常。
   - Filebeat → Elasticsearch 的集中式日志通过“集中日志”模块或 REST API 查询；可自定义查询语句或按服务筛选。

## 重要环境变量

在 `docker-compose.yml` 中可调整：

| 变量 | 说明 |
| ---- | ---- |
| `AUTO_SCHEDULER_INTERVAL` | 后台调度器执行间隔（秒），默认 15 |
| `SCHEDULER_THRESHOLD`     | 触发迁移的负载差阈值（默认 5 个文件） |
| `DOCKER_CONTROL_ENABLED`  | 是否允许通过 Docker API 控制节点（true/false） |
| `FILE_STORAGE_PATH`       | 文件存储根目录（已通过卷映射至宿主机 `./data/uploads`） |
| `ZK_NODES`                | ZooKeeper 集群节点列表，用于四字命令、Kazoo 连接 |
| `DEMO_WORKLOAD_ENABLED`   | 是否启用内置示例流量（默认 `true`）。设为 `false` 可完全手动演示 |
| `DEMO_WORKLOAD_INTERVAL`  | Demo workload 周期秒数（默认 20，叠加随机抖动） |
| `DEMO_WORKLOAD_MAX_FILES` | Demo workload 保留的示例文件上限（默认 18） |
| `DEMO_WORKLOAD_MAX_TASKS` | Demo workload 保留的任务流水上限（默认 50） |
| `DEMO_WORKLOAD_EXTRA_CLIENTS` | Demo workload 额外保持的 ZooKeeper 客户端连接数（默认 2） |
| `ELASTICSEARCH_URL`       | 集中式日志的 Elasticsearch 地址（默认 `http://elasticsearch:9200`） |

## 常见扩展方向

- 将 `docker_control.py` 替换为调用 Rundeck/AWX/AHV API，实现真实虚机启停与审批流。
- 如果要自定义示例流量，可修改 `backend/app/workload.py` 或通过上述环境变量调整节奏/规模。
- 已内置 Filebeat → Elasticsearch。如需可视化界面，可额外引入 Kibana 或 Grafana Loki。
- 在 `backend/app/main.py` 中新增 WebSocket 推送，增强前端实时性。
- 为上传文件实现副本校验、SHA 校验和或对象存储同步，更贴近生产场景。

## 清理数据

演示结束后若要清空上传文件与 SQLite 数据，可执行：

```bash
rm -rf data/uploads/* backend/app/data/
```

## 故障排查

- **节点无法启动**：`docker compose logs zk1` 查看原因，确认宿主机端口未被占用。
- **Grafana 无法展示数据**：等待数秒保证 Prometheus 完成首次抓取，或进入 Grafana 检查数据源状态。
- **Elasticsearch 拒绝写入**：首次启动前请确保宿主机 `vm.max_map_count` 足够（Linux: `sysctl -w vm.max_map_count=262144`）。
- **后台报错 "Docker control is disabled"**：若宿主机不希望暴露 `/var/run/docker.sock`，可将 `DOCKER_CONTROL_ENABLED=false`，同时将前端启动/停用按钮隐藏或替换为 SSH 脚本。

祝演示顺利！
