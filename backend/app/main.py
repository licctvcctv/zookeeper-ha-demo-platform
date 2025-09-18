from __future__ import annotations

import asyncio
import json
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Gauge, generate_latest
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import Response

from . import db, docker_control, storage, zookeeper_utils
from .config import Settings, get_settings
from .logging_service import search_logs
from .workload import DemoWorkload

logger = logging.getLogger("zk_demo")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")

app = FastAPI(title="ZooKeeper HA Demo", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

registry = CollectorRegistry()
settings: Settings = get_settings()

log_dir = settings.logs_directory
log_file = log_dir / "backend.log"
file_handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=5)
file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s"))
root_logger = logging.getLogger()
if not any(isinstance(h, RotatingFileHandler) for h in root_logger.handlers):
    root_logger.addHandler(file_handler)
root_logger.setLevel(logging.INFO)
node_up_gauge = Gauge(
    f"{settings.metrics_namespace}_node_up",
    "Whether ZooKeeper node is reachable (1) or not (0)",
    ["node"],
    registry=registry,
)
node_latency_gauge = Gauge(
    f"{settings.metrics_namespace}_avg_latency_ms",
    "Average request latency as reported by mntr",
    ["node"],
    registry=registry,
)
node_connections_gauge = Gauge(
    f"{settings.metrics_namespace}_connections",
    "Number of active client connections",
    ["node"],
    registry=registry,
)
files_per_node_gauge = Gauge(
    f"{settings.metrics_namespace}_files_per_node",
    "Demo file distribution across ZooKeeper nodes",
    ["node"],
    registry=registry,
)
task_status_gauge = Gauge(
    f"{settings.metrics_namespace}_tasks_total",
    "Synthetic task counts by status",
    ["status"],
    registry=registry,
)


class DemoAction(BaseModel):
    files: int = Field(0, ge=0, le=50)
    tasks: int = Field(0, ge=0, le=50)
    znodes: int = Field(0, ge=0, le=50)


@app.on_event("startup")
async def startup_event() -> None:
    logger.info("Initialising demo backend")
    db.init_db()
    zookeeper_utils.ensure_zk_paths()
    await refresh_metrics()
    if settings.auto_scheduler_enabled:
        asyncio.create_task(auto_scheduler_loop())
    demo = DemoWorkload()
    app.state.demo_workload = demo
    if settings.demo_workload_enabled:
        demo.start_auto()
    if settings.frontend_dir.exists():
        app.mount(
            "/ui",
            StaticFiles(directory=settings.frontend_dir, html=True),
            name="frontend",
        )


@app.on_event("shutdown")
def shutdown_event() -> None:
    worker = getattr(app.state, "demo_workload", None)
    if worker is not None:
        worker.stop_auto()
    zookeeper_utils.close_kazoo_client()


async def auto_scheduler_loop() -> None:
    logger.info("Starting auto scheduler loop (interval=%ss, threshold=%s)", settings.auto_scheduler_interval, settings.scheduler_threshold)
    while True:
        try:
            await maybe_rebalance_files()
        except Exception as exc:  # pragma: no cover - keep loop alive
            logger.exception("Scheduler loop error: %s", exc)
        await asyncio.sleep(settings.auto_scheduler_interval)


async def maybe_rebalance_files() -> None:
    files = db.get_files()
    if not files:
        return
    counts: Dict[str, int] = {}
    for node in settings.zk_nodes:
        host = node.split(":")[0]
        counts[host] = 0
    for record in files:
        counts[record["node"]] = counts.get(record["node"], 0) + 1
    if not counts:
        return
    max_node, max_count = max(counts.items(), key=lambda i: i[1])
    min_node, min_count = min(counts.items(), key=lambda i: i[1])
    if max_count - min_count < settings.scheduler_threshold:
        return
    candidate = next((item for item in files if item["node"] == max_node), None)
    if not candidate:
        return
    logger.info("Auto scheduler migrating file %s from %s to %s", candidate["filename"], max_node, min_node)
    new_path, new_node = storage.migrate_file(candidate, min_node)
    history = json.loads(candidate["history"])
    event = {
        "timestamp": datetime.utcnow().isoformat(),
        "action": "auto_migrate",
        "from": max_node,
        "to": min_node,
    }
    history.append(event)
    db.update_file_record(candidate["id"], node=new_node, path=new_path, history=history)
    zookeeper_utils.register_file_metadata(candidate["uuid"], {
        "filename": candidate["filename"],
        "size": candidate["size_bytes"],
        "node": new_node,
        "path": new_path,
        "history": history,
        "updated_at": datetime.utcnow().isoformat(),
    })
    db.record_operation(
        action="auto_migrate",
        status="success",
        node=new_node,
        details=f"Auto-migrated file {candidate['filename']} from {max_node} to {min_node}",
    )
    await refresh_metrics()


async def refresh_metrics() -> Dict[str, Any]:
    status = zookeeper_utils.get_cluster_status()
    files = db.get_files()
    tasks = db.list_tasks(limit=settings.demo_workload_max_tasks)
    for node in settings.zk_nodes:
        host = node.split(":")[0]
        files_per_node_gauge.labels(node=host).set(0)
    node_map: Dict[str, Dict[str, Any]] = {}
    for node_info in status["nodes"]:
        node_name = node_info.get("node") or node_info.get("endpoint", "unknown")
        node_map[node_name] = node_info
        state = node_info.get("state", "down")
        node_up_gauge.labels(node=node_name).set(1 if state and str(state).lower() != "down" else 0)
        latency = _extract_numeric(node_info, ["zk_avg_latency", "zk_avg_latency_ms", "zk_avg_request_latency_ms"])
        if latency is not None:
            node_latency_gauge.labels(node=node_name).set(latency)
        connections = _extract_numeric(node_info, ["zk_num_alive_connections"])
        if connections is not None:
            node_connections_gauge.labels(node=node_name).set(connections)
    for file_record in files:
        files_per_node_gauge.labels(node=file_record["node"]).inc()
    for status_name in ["queued", "running", "succeeded", "failed", "cancelled"]:
        task_status_gauge.labels(status=status_name).set(0)
    for task in tasks:
        task_status_gauge.labels(status=task["status"]).inc()
    status["tasks"] = tasks
    return status


def _extract_numeric(data: Dict[str, Any], keys: List[str]) -> Optional[float]:
    for key in keys:
        value = data.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


@app.get("/metrics")
def metrics_endpoint() -> Response:
    return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)
@app.get("/api/overview")
async def api_overview() -> Dict[str, Any]:
    status = await refresh_metrics()
    try:
        zk_files = zookeeper_utils.list_registered_files()
    except Exception as exc:
        logger.warning("Unable to read registered ZooKeeper files: %s", exc)
        zk_files = []
    return {
        "cluster": status,
        "files": db.get_files(),
        "tasks": status.get("tasks", []),
        "zk_registered_files": zk_files,
    }


@app.get("/api/operations")
def api_operations(limit: int = 100) -> List[Dict[str, Any]]:
    return db.list_operations(limit=limit)


@app.get("/api/files")
def api_files() -> List[Dict[str, Any]]:
    records = db.get_files()
    for record in records:
        history = record.get("history")
        if isinstance(history, str):
            try:
                record["history"] = json.loads(history)
            except json.JSONDecodeError:
                record["history"] = []
    return records


@app.post("/api/demo/actions")
async def api_demo_actions(action: DemoAction) -> Dict[str, Any]:
    worker: DemoWorkload = getattr(app.state, "demo_workload", DemoWorkload())
    app.state.demo_workload = worker
    result = await worker.run_once(files=action.files, tasks=action.tasks, znodes=action.znodes)
    await refresh_metrics()
    return result


@app.post("/api/files/upload")
async def api_upload(file: UploadFile = File(...)) -> Dict[str, Any]:
    target_node = storage.select_target_node()
    path, size, file_uuid = storage.save_upload(file, target_node)
    history = [{
        "timestamp": datetime.utcnow().isoformat(),
        "action": "upload",
        "node": target_node,
    }]
    file_id = db.create_file_record(
        uuid=file_uuid,
        filename=file.filename or "uploaded.bin",
        size_bytes=size,
        node=target_node,
        path=path,
        history=history,
    )
    payload = {
        "id": file_id,
        "uuid": file_uuid,
        "filename": file.filename,
        "size": size,
        "node": target_node,
        "path": path,
        "history": history,
        "created_at": datetime.utcnow().isoformat(),
    }
    try:
        zookeeper_utils.register_file_metadata(file_uuid, payload)
    except Exception as exc:
        logger.warning("Failed to replicate metadata to ZooKeeper: %s", exc)
    db.record_operation(
        action="upload",
        status="success",
        node=target_node,
        details=f"Uploaded {file.filename} ({size} bytes) to {target_node}",
    )
    await refresh_metrics()
    return payload


@app.post("/api/nodes/{node_id}/{action}")
async def api_node_action(node_id: str, action: str, request: Request) -> Dict[str, Any]:
    action = action.lower()
    if action not in {"stop", "start", "restart"}:
        raise HTTPException(status_code=400, detail="Unsupported action")
    container_name = node_id
    if node_id not in {node.split(":")[0] for node in settings.zk_nodes}:
        raise HTTPException(status_code=404, detail=f"Unknown node {node_id}")
    try:
        before = zookeeper_utils.get_node_metrics(f"{node_id}:2181")
    except Exception:
        before = None
    actor = request.headers.get("X-Demo-User", "web")
    status = "success"
    details = ""
    after: Optional[Dict[str, Any]] = None
    try:
        if action == "stop":
            docker_control.stop_container(container_name)
            await asyncio.sleep(2)
            try:
                after = zookeeper_utils.get_node_metrics(f"{node_id}:2181")
            except Exception:
                after = {"state": "down"}
        elif action == "start":
            docker_control.start_container(container_name)
            await asyncio.sleep(4)
            after = zookeeper_utils.get_node_metrics(f"{node_id}:2181")
        else:
            docker_control.restart_container(container_name)
            await asyncio.sleep(4)
            after = zookeeper_utils.get_node_metrics(f"{node_id}:2181")
        details = f"{action} executed on {node_id}"
    except Exception as exc:
        status = "error"
        details = str(exc)
        logger.error("Failed to execute %s on %s: %s", action, node_id, exc)
        raise HTTPException(status_code=500, detail=details)
    finally:
        db.record_operation(
            action=action,
            status=status,
            node=node_id,
            actor=actor,
            before_metrics=before,
            after_metrics=after,
            details=details,
        )
        await refresh_metrics()
    return {"status": status, "details": details, "after_metrics": after}


@app.get("/api/tasks")
def api_tasks(limit: int = 100) -> List[Dict[str, Any]]:
    return db.list_tasks(limit=limit)


@app.get("/api/logs/search")
async def api_search_logs(query: Optional[str] = None, service: Optional[str] = None, size: int = 50) -> List[Dict[str, Any]]:
    return await search_logs(query=query, service=service, size=size)


@app.get("/api/logs/zookeeper/{node_id}")
def api_zk_logs(node_id: str, tail: int = 200) -> Dict[str, Any]:
    if node_id not in {node.split(":")[0] for node in settings.zk_nodes}:
        raise HTTPException(status_code=404, detail="Unknown node")
    try:
        logs = docker_control.get_logs(node_id, tail=tail)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"node": node_id, "tail": tail, "logs": logs}


@app.get("/api/files/{file_id}/download")
def api_download_file(file_id: int) -> FileResponse:
    record = db.get_file(file_id)
    if not record:
        raise HTTPException(status_code=404, detail="File not found")
    path = Path(record["path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="File missing on disk")
    return FileResponse(path, filename=record["filename"])


@app.get("/api/cluster/metrics")
async def api_cluster_metrics() -> Dict[str, Any]:
    status = await refresh_metrics()
    return status


@app.get("/api/ping")
def api_ping() -> Dict[str, str]:
    return {"status": "ok", "time": datetime.utcnow().isoformat()}


@app.get("/healthz")
def healthz() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def serve_index() -> HTMLResponse:
    index_path = settings.frontend_dir / "index.html"
    if index_path.exists():
        return HTMLResponse(index_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>ZooKeeper Demo Backend</h1>")
