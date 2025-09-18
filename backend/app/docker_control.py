from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

import docker
from docker.models.containers import Container

from .config import get_settings

logger = logging.getLogger(__name__)
SETTINGS = get_settings()


@lru_cache(maxsize=1)
def _get_client() -> docker.DockerClient:
    if not SETTINGS.docker_control_enabled:
        raise RuntimeError("Docker control is disabled by configuration")
    try:
        return docker.from_env()
    except PermissionError as exc:
        raise RuntimeError(
            "Docker socket permission denied. 请确保挂载 /var/run/docker.sock 且服务以 root 或具有访问权限的用户运行。"
        ) from exc
    except docker.errors.DockerException as exc:
        raise RuntimeError(f"无法连接 Docker 后端: {exc}") from exc


def _get_container(container_name: str) -> Container:
    client = _get_client()
    return client.containers.get(container_name)


def stop_container(container_name: str, timeout: int = 10) -> None:
    container = _get_container(container_name)
    logger.info("Stopping container %s", container_name)
    container.stop(timeout=timeout)


def start_container(container_name: str) -> None:
    container = _get_container(container_name)
    logger.info("Starting container %s", container_name)
    container.start()


def restart_container(container_name: str) -> None:
    container = _get_container(container_name)
    logger.info("Restarting container %s", container_name)
    container.restart()


def get_logs(container_name: str, tail: int = 200) -> str:
    container = _get_container(container_name)
    output = container.logs(tail=tail)
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="ignore")
    return str(output)


def container_status(container_name: str) -> Optional[str]:
    try:
        container = _get_container(container_name)
        container.reload()
        return container.status
    except Exception as exc:
        logger.warning("Unable to fetch container status for %s: %s", container_name, exc)
        return None
