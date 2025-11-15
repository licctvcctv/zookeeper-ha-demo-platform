# ZooKeeper Demo 监控面板说明

## 三个节点的安装内容
- **zk1 / zk2 / zk3**：每个节点都是基于 `bitnami/zookeeper:3.9` 的容器，内置一套完整的 ZooKeeper 服务进程。
  - 对外暴露的客户端接口端口分别是 `2181`、`2182`、`2183`（分别映射到宿主机）。
  - 同时开放 Prometheus 采集端口 `7000`，用于上报 `num_alive_connections`、`znode_count`、`avg_latency` 等指标。
  - 持久化数据目录（`/bitnami/zookeeper`）和日志目录挂载到宿主机的 `./logs/zk*` 便于演示和排查。
- 三个节点安装的内容一致，仅实例 ID 不同（`ZOO_SERVER_ID=1/2/3`），共同组成一个 ZooKeeper 仲裁集群。

## 网页代码的运行位置
- Web 控制台采用 **Vue 单页前端 + FastAPI 后端** 的形式。
- 打包后的静态文件位于仓库根目录的 `frontend/`，在容器内被挂载到 `/app/frontend`。
- `backend/app/main.py` 在启动时通过 `FastAPI` 将该目录挂载到 `/ui` 路径，因此访问 `http://localhost:8080/ui/` 会直接由后端容器（`zk-demo-backend`）提供前端页面和 API。

## 中间件如何组织协同
- **Docker Compose**：`docker-compose.yml` 在单一 `bridge` 网络 `zk_net` 上编排所有服务，同时把宿主机的日志、数据目录通过卷共享给容器，保证节点状态可持久化、监控日志可访问。
- **ZooKeeper 集群**：`zk1`、`zk2`、`zk3` 组成仲裁集群，对外提供 2181/2182/2183 端口给业务或演示程序使用，并在 7000 端口暴露 Prometheus 指标。
- **FastAPI 后端（zk-demo-backend）**：既是控制面 API，也是前端静态资源服务器；它通过 Kazoo 连接 ZooKeeper、调用 Docker SDK 管理容器、把操作写入 SQLite/日志，并暴露 `/metrics` 给 Prometheus 抓取调度指标。
- **Vue 前端**：被挂载在后端容器的 `/app/frontend`，通过 REST API 与后端交互，展示节点状态、操作历史等内容。
- **Prometheus**：周期性抓取 ZooKeeper 节点和后端的 `/metrics`，存入时间序列库，供 Grafana 和后端调度逻辑查询。
- **Grafana**：使用预配置的数据源读取 Prometheus 中的时间序列，渲染成截图中的仪表盘，支持匿名只读嵌入。
- **Filebeat + Elasticsearch**：Filebeat 读取挂载进容器的 `./logs/*` 目录以及 Docker 容器日志，写入 Elasticsearch；后端的日志查询接口可基于该索引向前端返回结果。

## Grafana 面板解读
Grafana 通过预配置的 Prometheus 数据源，展示 ZooKeeper 节点的三类核心指标。截图中的面板含义如下：

### 1. Active Connections（活跃连接数）
- **数据来源**：Prometheus 指标 `num_alive_connections`。
- **横坐标**：时间（默认最近 15 分钟）。
- **纵坐标**：各节点当前的客户端连接数，单位为“个”。
- **图例**：蓝线、黄线、绿色分别代表 `zk3:7000`、`zk2:7000`、`zk1:7000` 的指标走势。
- **用途**：观察客户端流量是否集中在某个节点，判断是否存在连接倾斜或断连。

### 2. Znode Count（Znode 数量）
- **数据来源**：Prometheus 指标 `znode_count`。
- **显示方式**：横向柱状仪表，每个条目对应一个节点（如 `zk1:7000`）。
- **数值**：右侧的红色数字为当前节点 Znode 总数，单位为“个”。
- **用途**：快速比较各节点的元数据数量，辅助判断文件/任务在集群中的分布是否均衡。

### 3. Average Request Latency（平均请求延迟）
- **数据来源**：Prometheus 指标 `avg_latency`（单位毫秒）。
- **横坐标**：时间。
- **纵坐标**：处理请求的平均延迟，单位为毫秒（ms）。
- **图例**：同 Active Connections，颜色对应各节点。
- **用途**：监控节点性能，若曲线突然抬升说明该节点响应缓慢或存在抖动。

> 以上指标每 10 秒由 Prometheus 从三个 ZooKeeper 节点的 7000 端口拉取，Grafana 则以 10 秒刷新一次的节奏更新面板。
