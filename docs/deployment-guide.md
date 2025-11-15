# ZooKeeper HA Demo 部署指南

本文档详述如何部署本仓库的 ZooKeeper 高可用演示环境，并补充日志采集与自动调度的相关配置要点。部署环境假定已具备 Docker 与 Docker Compose v2。

## 1. 环境准备

- **系统要求**：Linux / macOS / WSL 均可，只需支持 Docker Desktop 或原生 Docker。建议准备至少 4 核 CPU、8 GB 内存，避免 Elasticsearch 启动因资源不足报错。
- **Docker 依赖**：确保已安装 Docker Engine 与 Compose 插件，可执行 `docker --version` 与 `docker compose version` 验证；若提示命令不存在，请先升级 Docker Desktop 或安装 Compose v2。
- **基础镜像下载**：网络带宽较慢时，建议先运行 `docker compose pull` 预拉取所需镜像，以缩短正式启动的等待时间。
- **端口规划**：确认宿主机未占用以下端口：
  - ZooKeeper 集群客户端端口 `2181-2183`
  - ZooKeeper 指标端口 `7001-7003`
  - FastAPI 后端 `8080`
  - Prometheus `9090`
  - Grafana `3000`
  - Elasticsearch `9200`
- **目录结构**：首次运行会在项目下生成 `logs/`、`data/uploads/` 等目录作为数据、日志挂载点。也可以提前创建并检查权限：

  ```bash
  mkdir -p logs/zk1 logs/zk2 logs/zk3 logs/backend data/uploads
  ls -R logs data | head
  ```

  如需自定义路径，可在 `docker-compose.yml` 中调整对应卷映射。

## 2. 首次启动

```bash
cd zk-ha-demo
docker compose up -d
```

- Compose 将自动构建 `backend/` 镜像，并拉取 ZooKeeper / Prometheus / Grafana / Elasticsearch / Filebeat 等镜像。
- 所有容器默认加入 `zk_net` 桥接网络，内部通过服务名互联，外部通过上述端口访问。
- 初次拉取镜像或编译依赖可能耗时，请耐心等待。
- 若修改过后端代码或依赖，可执行 `docker compose up -d --build backend` 强制重新构建镜像。

### 2.1 容器状态验证

```bash
docker compose ps
docker compose logs backend --tail=100
```

- `zk1`、`zk2`、`zk3` 三个节点应均显示为 `healthy` 或 `Up`。
- 若后端日志出现 ZooKeeper 连接失败，请确认 ZooKeeper 容器已启动完毕（约需数秒）。

## 3. 服务访问入口

- **控制台 UI**：<http://localhost:8080/ui/>（由 FastAPI 提供 Vue 静态资源）
- **Grafana**：<http://localhost:3000>（默认账号密码 `admin/admin`，已启用匿名只读）
- **Prometheus**：<http://localhost:9090>
- **Elasticsearch API**：<http://localhost:9200>

启动完成数秒后，Prometheus 会开始采集 ZooKeeper 与后端指标；Grafana 预设仪表盘会自动刷新显示连接数、延迟、Znode 数量等图表。

## 4. 日志采集与排查

### 4.1 Filebeat → Elasticsearch

- 容器 `filebeat` 使用 `filebeat/filebeat.yml` 将宿主机挂载的 `logs/zk*`、`logs/backend` 目录以及 Docker 容器日志采集进 Elasticsearch。
- 可通过以下 API 验证索引：

```bash
curl http://localhost:9200/_cat/indices?v
curl http://localhost:9200/operations/_search?pretty
```

- 若需要 Kibana 或其他可视化工具，可另行在 Compose 中添加。

### 4.2 快速查看节点日志

- 使用 `docker compose logs zk1 --tail=200` 等命令快速调试。
- FastAPI 前端“集中日志”页也会展示 Filebeat 入库后的日志片段，便于在 UI 中检索。
- 若希望直接查阅宿主机上的原始日志，可在 `logs/zk*`、`logs/backend` 目录下查看，每个目录按服务划分并与容器同步。

### 4.3 清理日志与数据

- 停止并删除容器及卷：

```bash
docker compose down -v
```

- 如需额外清理上传文件或 SQLite 数据，可手动执行：

```bash
rm -rf data/uploads/* backend/app/data/
```

## 5. 自动调度机制

### 5.1 配置项

在 `docker-compose.yml` 的 `backend` 服务中，以下环境变量控制调度逻辑：

| 变量 | 说明 |
| --- | --- |
| `AUTO_SCHEDULER_INTERVAL` | 调度器轮询间隔（秒），默认 15 |
| `SCHEDULER_THRESHOLD` | 节点文件数差异达到该阈值时触发自动迁移，默认 5 |
| `DEMO_WORKLOAD_ENABLED` | 是否启用内置示例流量（默认 `true`），关闭后所有写入需手动触发 |
| `DEMO_WORKLOAD_INTERVAL` | 示例流量执行周期（秒），默认 20，加随机抖动 |
| `DEMO_WORKLOAD_EXTRA_CLIENTS` | 示例流量额外保持的 ZooKeeper 连接数 |

调整上述变量后，需要 `docker compose up -d backend` 以重新加载配置。

### 5.2 调度流程

1. 后端调度器定期读取 ZooKeeper 各节点的文件数量与负载指标。
2. 一旦节点间差异超过 `SCHEDULER_THRESHOLD`，自动触发文件迁移，将最空闲节点作为目标。
3. 迁移过程会：
   - 更新 ZooKeeper 元数据（`/demo/files`）以记录文件所属节点。
   - 将操作写入 SQLite `operations` 表，并通过 Prometheus 指标对外暴露。
   - Filebeat 同步采集迁移日志，便于追踪。
4. 前端“负载与任务”页会显示最新迁移记录与节点状态图表。

### 5.3 手动干预

- UI 中提供“手动触发示例流量”“迁移文件”“暂停/启动节点”等操作，均通过 FastAPI 后端调用 Docker API 或调度器接口实现。
- 若不希望暴露 `/var/run/docker.sock`，可将 `DOCKER_CONTROL_ENABLED=false` 并在前端隐藏相关按钮。

### 5.4 智能调度演示工具

- 在控制台 “负载与任务” 标签页中新增了“智能调度演示工具”面板，可选择目标节点、文件数量与大小，一键在单节点堆积热点数据。
- 勾选“生成后立即尝试执行一次调度”即可在写入完成后立即触发一次调度循环；旁边按钮也可以随时手动执行调度。
- 面板下方实时展示调度器返回的诊断信息（各节点文件数、阈值、最大差异、节点是否被 Drain），方便观察算法行为。
- 对于自动化脚本或命令行演示，可以直接调用新的 REST 接口：

  ```bash
  # 在 zk1 上生成 8 个示例文件并触发调度
  curl -X POST http://localhost:8080/api/demo/stress \
       -H 'Content-Type: application/json' \
       -d '{"node":"zk1","files":8,"size_kb":128,"trigger_scheduler":true}'

  # 获取当前调度诊断信息
  curl http://localhost:8080/api/scheduler/diagnostics | jq

  # 手动执行一次调度循环
  curl -X POST http://localhost:8080/api/scheduler/run | jq
  ```

- 若需要调整演示效果，可结合 `SCHEDULER_THRESHOLD` 与 `AUTO_SCHEDULER_INTERVAL` 配置，或配合 Drain 节点来观察不同策略下的迁移决策。

## 6. 故障排查技巧

- **ZooKeeper 节点不健康**：检查 `logs/zk*/` 目录与 `docker compose logs zk*`；确认宿主机端口无冲突。
- **Grafana 无数据**：等待 Prometheus 完成首次抓取或登录 Grafana 检查数据源状态（`Configuration -> Data Sources`）。
- **Elasticsearch 写入失败**：在 Linux 环境需保证 `vm.max_map_count >= 262144`，可执行 `sudo sysctl -w vm.max_map_count=262144`。
- **后端警告 “Docker control is disabled”**：确认环境变量 `DOCKER_CONTROL_ENABLED` 是否为 `true`，或根据实际需求关闭并调整前端。

## 7. 关闭与重启

- **重启单个服务**：`docker compose restart grafana`
- **平滑更新后端**（例如修改代码或配置）：

```bash
docker compose up -d --build backend
```

- **完整停机**：`docker compose down -v`（会删除命名卷，慎用；若仅需停止服务，可省略 `-v`）。

---

部署完成后，可围绕 Grafana 仪表盘、Filebeat 日志、自动调度迁移等功能进行演示或二次开发。祝实验顺利！
