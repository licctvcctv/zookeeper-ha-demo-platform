from __future__ import annotations

import json
import socket
import time
from contextlib import closing
from typing import Any, Dict, List, Optional

from kazoo.client import KazooClient

from .config import get_settings

SETTINGS = get_settings()
_KAZOO_CLIENT: Optional[KazooClient] = None


def get_kazoo_client() -> KazooClient:
    global _KAZOO_CLIENT
    if _KAZOO_CLIENT is None:
        _KAZOO_CLIENT = KazooClient(hosts=",".join(SETTINGS.zk_nodes), timeout=SETTINGS.zk_command_timeout)
        _KAZOO_CLIENT.start()
    return _KAZOO_CLIENT


def close_kazoo_client() -> None:
    global _KAZOO_CLIENT
    if _KAZOO_CLIENT is not None:
        _KAZOO_CLIENT.stop()
        _KAZOO_CLIENT.close()
        _KAZOO_CLIENT = None


def ensure_zk_paths() -> None:
    client = get_kazoo_client()
    if not client.exists(SETTINGS.zk_root_path):
        client.ensure_path(SETTINGS.zk_root_path)


def send_four_letter_cmd(host: str, port: int, command: str, *, timeout: float | None = None) -> str:
    timeout = timeout or SETTINGS.zk_command_timeout
    with closing(socket.create_connection((host, port), timeout=timeout)) as sock:
        sock.sendall(command.encode("utf-8"))
        sock.sendall(b"\n")
        sock.shutdown(socket.SHUT_WR)
        data = sock.recv(4096)
        chunks = [data]
        while data:
            data = sock.recv(4096)
            if not data:
                break
            chunks.append(data)
    return b"".join(chunks).decode("utf-8", errors="ignore")


def parse_mntr_output(output: str) -> Dict[str, Any]:
    metrics: Dict[str, Any] = {}
    for line in output.splitlines():
        if "\t" not in line:
            continue
        key, value = line.split("\t", 1)
        value = value.strip()
        metrics[key] = _coerce_value(value)
    return metrics


def _coerce_value(value: str) -> Any:
    if value.isdigit():
        try:
            return int(value)
        except ValueError:
            return value
    try:
        return float(value)
    except ValueError:
        return value


def get_node_metrics(node: str) -> Dict[str, Any]:
    host, port_str = node.split(":", 1)
    port = int(port_str)
    attempt = 0
    last_error: Optional[Exception] = None
    while attempt <= SETTINGS.zk_command_retries:
        try:
            output = send_four_letter_cmd(host, port, "mntr")
            metrics = parse_mntr_output(output)
            metrics["node"] = host
            metrics["endpoint"] = node
            metrics.setdefault("timestamp", time.time())
            return metrics
        except Exception as exc:  # broad for retries
            attempt += 1
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(f"Failed to fetch mntr metrics from {node}: {last_error}")


def get_cluster_status(nodes: Optional[List[str]] = None) -> Dict[str, Any]:
    nodes = nodes or SETTINGS.zk_nodes
    node_metrics: List[Dict[str, Any]] = []
    leader: Optional[str] = None
    for node in nodes:
        try:
            metrics = get_node_metrics(node)
            state = str(metrics.get("zk_server_state") or metrics.get("zk_server_state", "unknown"))
            metrics["state"] = state
            if str(state).lower() == "leader":
                leader = metrics.get("node")
            node_metrics.append(metrics)
        except Exception as exc:
            node_metrics.append(
                {
                    "node": node.split(":", 1)[0],
                    "endpoint": node,
                    "state": "down",
                    "error": str(exc),
                }
            )
    response = {
        "leader": leader,
        "nodes": node_metrics,
        "timestamp": time.time(),
    }
    return response


def register_file_metadata(znode: str, payload: Dict[str, Any]) -> None:
    client = get_kazoo_client()
    encoded = json.dumps(payload).encode("utf-8")
    path = f"{SETTINGS.zk_root_path}/{znode}"
    if client.exists(path):
        client.set(path, encoded)
    else:
        client.create(path, encoded, makepath=True)


def delete_file_metadata(znode: str) -> None:
    client = get_kazoo_client()
    path = f"{SETTINGS.zk_root_path}/{znode}"
    if client.exists(path):
        client.delete(path)


def list_registered_files() -> List[Dict[str, Any]]:
    client = get_kazoo_client()
    if not client.exists(SETTINGS.zk_root_path):
        return []
    entries: List[Dict[str, Any]] = []
    for child in client.get_children(SETTINGS.zk_root_path):
        path = f"{SETTINGS.zk_root_path}/{child}"
        data, stat = client.get(path)
        try:
            payload = json.loads(data.decode("utf-8")) if data else {}
        except json.JSONDecodeError:
            payload = {"raw": data.decode("utf-8", errors="ignore")}
        payload["znode"] = path
        payload["mtime"] = stat.mtime / 1000.0
        entries.append(payload)
    return entries
