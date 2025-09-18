from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


def _split_nodes(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass
class Settings:
    zk_nodes_raw: str = os.getenv("ZK_NODES", "zk1:2181,zk2:2181,zk3:2181")
    prometheus_url: str = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
    docker_control_enabled: bool = os.getenv("DOCKER_CONTROL_ENABLED", "true").lower() == "true"
    file_storage_path: Path = Path(os.getenv("FILE_STORAGE_PATH", "/data/uploads"))
    operations_db_path: Path = Path(os.getenv("OPERATIONS_DB_PATH", "/app/data/demo.db"))
    auto_scheduler_interval: int = int(os.getenv("AUTO_SCHEDULER_INTERVAL", "15"))
    scheduler_threshold: int = int(os.getenv("SCHEDULER_THRESHOLD", "5"))
    operations_log_path: Path = Path(os.getenv("OPERATIONS_LOG_PATH", "/app/data/operations.log"))
    frontend_dir: Path = Path(os.getenv("FRONTEND_DIRECTORY", "/app/frontend"))
    metrics_namespace: str = os.getenv("METRICS_NAMESPACE", "zk_demo")
    history_limit: int = int(os.getenv("OPERATIONS_HISTORY_LIMIT", "1000"))
    zk_root_path: str = os.getenv("ZK_FILE_ROOT", "/demo/files")
    zk_command_timeout: float = float(os.getenv("ZK_COMMAND_TIMEOUT", "2.5"))
    zk_command_retries: int = int(os.getenv("ZK_COMMAND_RETRIES", "2"))
    auto_scheduler_enabled: bool = os.getenv("AUTO_SCHEDULER_ENABLED", "true").lower() == "true"
    demo_workload_enabled: bool = os.getenv("DEMO_WORKLOAD_ENABLED", "false").lower() == "true"
    demo_workload_interval: int = int(os.getenv("DEMO_WORKLOAD_INTERVAL", "20"))
    demo_workload_jitter: int = int(os.getenv("DEMO_WORKLOAD_JITTER", "10"))
    demo_workload_max_files: int = int(os.getenv("DEMO_WORKLOAD_MAX_FILES", "18"))
    demo_workload_file_size_kb: int = int(os.getenv("DEMO_WORKLOAD_FILE_SIZE_KB", "128"))
    demo_workload_extra_clients: int = int(os.getenv("DEMO_WORKLOAD_EXTRA_CLIENTS", "2"))
    demo_workload_max_znodes: int = int(os.getenv("DEMO_WORKLOAD_MAX_ZNODES", "24"))
    demo_workload_max_tasks: int = int(os.getenv("DEMO_WORKLOAD_MAX_TASKS", "50"))
    elasticsearch_url: str = os.getenv("ELASTICSEARCH_URL", "")
    logs_directory: Path = Path(os.getenv("BACKEND_LOG_DIR", "/app/logs"))

    zk_nodes: List[str] = field(init=False)

    def __post_init__(self) -> None:
        self.zk_nodes = _split_nodes(self.zk_nodes_raw)
        self.file_storage_path.mkdir(parents=True, exist_ok=True)
        self.operations_db_path.parent.mkdir(parents=True, exist_ok=True)
        self.operations_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.logs_directory.mkdir(parents=True, exist_ok=True)
        # ensure per-node directories exist
        for node in self.zk_nodes:
            host = node.split(":")[0]
            (self.file_storage_path / host).mkdir(parents=True, exist_ok=True)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
