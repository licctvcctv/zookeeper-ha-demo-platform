from __future__ import annotations

import asyncio
import json
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

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


class DemoStressRequest(BaseModel):
    node: str = Field(..., description="目标节点 (如 zk1)")
    files: int = Field(6, ge=1, le=100, description="要生成的示例文件数")
    size_kb: int = Field(settings.demo_workload_file_size_kb, ge=1, le=4096, description="每个文件的大约大小 (KB)")
    trigger_scheduler: bool = Field(False, description="生成后立即尝试执行一次调度")


class BulkUploadMode(str, Enum):
    AUTO = "auto"
    PIN = "pin"


class BulkUploadRequest(BaseModel):
    count: int = Field(12, ge=1, le=200, description="批量生成的文件数量")
    size_kb: int = Field(settings.demo_workload_file_size_kb, ge=1, le=4096, description="每个文件的目标大小 (KB)")
    mode: BulkUploadMode = Field(BulkUploadMode.AUTO, description="auto=按负载均衡分配，pin=集中到指定节点")
    target_node: Optional[str] = Field(None, description="集中模式下的目标节点")
    trigger_scheduler: bool = Field(True, description="生成后立即尝试执行一次调度")


class NodeDrainRequest(BaseModel):
    reason: Optional[str] = Field(None, max_length=200)


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
        # 挂载 /ui 路径用于完整的前端访问
        app.mount(
            "/ui",
            StaticFiles(directory=settings.frontend_dir, html=True),
            name="frontend",
        )
        # 挂载 /assets 路径用于静态资源（CSS、JS 等）
        assets_dir = settings.frontend_dir / "assets"
        if assets_dir.exists():
            app.mount(
                "/assets",
                StaticFiles(directory=assets_dir),
                name="assets",
            )
        # 挂载 /partials 路径用于 HTML 片段
        partials_dir = settings.frontend_dir / "partials"
        if partials_dir.exists():
            app.mount(
                "/partials",
                StaticFiles(directory=partials_dir),
                name="partials",
            )


@app.on_event("shutdown")
def shutdown_event() -> None:
    worker = getattr(app.state, "demo_workload", None)
    if worker is not None:
        worker.stop_auto()
    zookeeper_utils.close_kazoo_client()


def build_scheduler_plan() -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    files = db.get_files()
    node_states = db.get_node_states()
    drained_nodes: Set[str] = {node for node, info in node_states.items() if info.get("drained")}
    counts: Dict[str, int] = {}
    for node in settings.zk_nodes:
        host = node.split(":")[0]
        counts[host] = 0
    for record in files:
        counts[record["node"]] = counts.get(record["node"], 0) + 1

    plan: Dict[str, Any] = {
        "counts": counts,
        "totalFiles": len(files),
        "drainedNodes": sorted(drained_nodes),
        "threshold": settings.scheduler_threshold,
        "delta": 0,
        "sourceNode": None,
        "targetNode": None,
        "shouldMigrate": False,
        "reason": "no_files" if not files else "pending",
        "message": "集群中暂无文件，调度器保持空闲。" if not files else "",
        "candidate": None,
        "nodeStates": {
            host: {
                "drained": node_states.get(host, {}).get("drained", False),
                "reason": node_states.get(host, {}).get("reason"),
                "updated_at": node_states.get(host, {}).get("updated_at"),
            }
            for host in counts.keys()
        },
    }

    if not files:
        return plan, None

    if not counts:
        plan["reason"] = "no_nodes"
        plan["message"] = "未找到可用节点，无法计算调度计划。"
        return plan, None

    drained_with_files = [(node, counts.get(node, 0)) for node in drained_nodes if counts.get(node, 0) > 0]
    if drained_with_files:
        source_node, source_count = max(drained_with_files, key=lambda item: item[1])
    else:
        source_node, source_count = max(counts.items(), key=lambda item: item[1])

    ready_targets = [(node, count) for node, count in counts.items() if node not in drained_nodes]
    if ready_targets:
        target_node, target_count = min(ready_targets, key=lambda item: item[1])
    else:
        target_node, target_count = min(counts.items(), key=lambda item: item[1])

    plan["sourceNode"] = source_node
    plan["targetNode"] = target_node
    delta = source_count - target_count
    plan["delta"] = delta

    candidate_record = next((item for item in files if item["node"] == source_node), None)
    if candidate_record is None:
        plan["reason"] = "no_candidate"
        plan["message"] = "源节点未找到可迁移文件。"
        return plan, None

    history = candidate_record.get("history")
    if isinstance(history, str):
        try:
            history = json.loads(history)
        except json.JSONDecodeError:
            history = []
    if history is None:
        history = []
    recent_event = history[-1] if history else None
    plan["candidate"] = {
        "id": candidate_record["id"],
        "filename": candidate_record["filename"],
        "node": candidate_record["node"],
        "size_bytes": candidate_record["size_bytes"],
        "created_at": candidate_record["created_at"],
        "last_action": recent_event.get("action") if isinstance(recent_event, dict) else None,
        "history_length": len(history),
    }

    if source_node == target_node:
        plan["reason"] = "single_target"
        plan["message"] = "只有一个可用节点，调度器无需迁移。"
        return plan, candidate_record

    if not ready_targets and drained_nodes:
        plan["reason"] = "no_target"
        plan["message"] = "所有节点均被手动摘除，无法执行迁移。"
        return plan, candidate_record

    if delta < settings.scheduler_threshold:
        plan["reason"] = "below_threshold"
        plan["message"] = f"最大差异 {delta} 低于阈值 {settings.scheduler_threshold}，暂不迁移。"
        return plan, candidate_record

    plan["shouldMigrate"] = True
    plan["reason"] = "ready"
    plan["message"] = f"节点 {source_node} 比 {target_node} 多 {delta} 个文件，准备迁移 {candidate_record['filename']}。"
    return plan, candidate_record


def _snapshot_node_counts() -> Dict[str, int]:
    counts = storage.get_node_counts()
    for node in settings.zk_nodes:
        host = node.split(":")[0]
        counts.setdefault(host, 0)
    return counts


def _bulk_generate_files(*, count: int, size_kb: int, mode: BulkUploadMode, target_node: Optional[str]) -> Dict[str, Any]:
    """Generate many demo files in either balanced or pinned mode."""
    size_kb = max(int(size_kb), 1)
    created: List[Dict[str, Any]] = []
    per_node: Dict[str, int] = {}
    history_action = "bulk_upload_pinned" if mode == BulkUploadMode.PIN else "bulk_upload_auto"
    for _ in range(count):
        if mode == BulkUploadMode.PIN and target_node:
            node = target_node
        else:
            node = storage.select_target_node()
        path, size_bytes, file_uuid, filename = storage.create_demo_file(node, size_kb)
        history = [{
            "timestamp": datetime.utcnow().isoformat(),
            "action": history_action,
            "node": node,
        }]
        file_id = db.create_file_record(
            uuid=file_uuid,
            filename=filename,
            size_bytes=size_bytes,
            node=node,
            path=path,
            history=history,
        )
        metadata = {
            "id": file_id,
            "uuid": file_uuid,
            "filename": filename,
            "size": size_bytes,
            "node": node,
            "path": path,
            "history": history,
            "created_at": datetime.utcnow().isoformat(),
        }
        try:
            zookeeper_utils.register_file_metadata(file_uuid, metadata)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("Failed to register bulk upload metadata: %s", exc)
        created.append({
            "id": file_id,
            "filename": filename,
            "node": node,
            "size_bytes": size_bytes,
        })
        per_node[node] = per_node.get(node, 0) + 1
    return {
        "created": created,
        "per_node": per_node,
    }


async def auto_scheduler_loop() -> None:
    logger.info("Starting auto scheduler loop (interval=%ss, threshold=%s)", settings.auto_scheduler_interval, settings.scheduler_threshold)
    while True:
        try:
            await maybe_rebalance_files()
        except Exception as exc:  # pragma: no cover - keep loop alive
            logger.exception("Scheduler loop error: %s", exc)
        await asyncio.sleep(settings.auto_scheduler_interval)


async def maybe_rebalance_files() -> bool:
    plan, candidate = build_scheduler_plan()
    if not plan.get("shouldMigrate") or not candidate:
        return False

    source_node = plan.get("sourceNode") or candidate.get("node")
    target_node = plan.get("targetNode")
    if not target_node or source_node == target_node:
        return False

    logger.info(
        "Auto scheduler migrating file %s from %s to %s",
        candidate["filename"],
        source_node,
        target_node,
    )
    new_path, new_node = storage.migrate_file(candidate, target_node)
    history_raw = candidate.get("history")
    if isinstance(history_raw, str):
        try:
            history = json.loads(history_raw)
        except json.JSONDecodeError:
            history = []
    else:
        history = history_raw or []
    event = {
        "timestamp": datetime.utcnow().isoformat(),
        "action": "auto_migrate",
        "from": source_node,
        "to": target_node,
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
        details=f"Auto-migrated file {candidate['filename']} from {source_node} to {target_node}",
    )
    await refresh_metrics()
    return True


async def refresh_metrics() -> Dict[str, Any]:
    status = zookeeper_utils.get_cluster_status()
    files = db.get_files()
    tasks = db.list_tasks(limit=settings.demo_workload_max_tasks)
    node_states = db.get_node_states()
    drained_nodes: Set[str] = {node for node, info in node_states.items() if info.get("drained")}
    for node in settings.zk_nodes:
        host = node.split(":")[0]
        files_per_node_gauge.labels(node=host).set(0)
    node_map: Dict[str, Dict[str, Any]] = {}
    for node_info in status["nodes"]:
        node_name = node_info.get("node") or node_info.get("endpoint", "unknown")
        node_map[node_name] = node_info
        state = node_info.get("state", "down")
        node_info["drained"] = node_name in drained_nodes
        state_info = node_states.get(node_name)
        if state_info:
            node_info["drain_reason"] = state_info.get("reason")
            node_info["drain_updated_at"] = state_info.get("updated_at")
        else:
            node_info["drain_reason"] = None
            node_info["drain_updated_at"] = None
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
    status["node_states"] = node_states
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


@app.get("/api/scheduler/diagnostics")
async def api_scheduler_diagnostics() -> Dict[str, Any]:
    plan, _ = build_scheduler_plan()
    return plan


@app.post("/api/scheduler/run")
async def api_scheduler_run() -> Dict[str, Any]:
    before_plan, _ = build_scheduler_plan()
    executed = await maybe_rebalance_files()
    await refresh_metrics()
    after_plan, _ = build_scheduler_plan()
    return {
        "executed": executed,
        "before": before_plan,
        "after": after_plan,
    }


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


@app.post("/api/files/bulk-generate")
async def api_bulk_generate(payload: BulkUploadRequest) -> Dict[str, Any]:
    allowed_nodes = {node.split(":")[0] for node in settings.zk_nodes}
    if payload.mode == BulkUploadMode.PIN:
        if not payload.target_node:
            raise HTTPException(status_code=400, detail="集中模式需要提供 target_node")
        if payload.target_node not in allowed_nodes:
            raise HTTPException(status_code=400, detail=f"未知节点 {payload.target_node}")
    elif payload.target_node and payload.target_node not in allowed_nodes:
        raise HTTPException(status_code=400, detail=f"未知节点 {payload.target_node}")

    before_counts = _snapshot_node_counts()
    before_plan, _ = build_scheduler_plan()

    try:
        summary = await asyncio.to_thread(
            _bulk_generate_files,
            count=payload.count,
            size_kb=payload.size_kb,
            mode=payload.mode,
            target_node=payload.target_node,
        )
    except Exception as exc:
        logger.exception("Bulk upload batch failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"批量上传失败: {exc}") from exc

    await refresh_metrics()
    after_counts = _snapshot_node_counts()
    after_plan, _ = build_scheduler_plan()

    triggered = False
    final_counts = after_counts
    final_plan = after_plan
    if payload.trigger_scheduler:
        triggered = await maybe_rebalance_files()
        await refresh_metrics()
        final_plan, _ = build_scheduler_plan()
        final_counts = _snapshot_node_counts()

    db.record_operation(
        action="bulk_upload_batch",
        status="success",
        node=payload.target_node if payload.mode == BulkUploadMode.PIN else None,
        before_metrics={"counts": before_counts},
        after_metrics={
            "counts": final_counts,
            "triggered": triggered,
            "mode": payload.mode,
        },
        details=f"Bulk generated {payload.count} files (mode={payload.mode}, size_kb={payload.size_kb})",
    )

    return {
        "created": summary["created"],
        "created_count": len(summary["created"]),
        "per_node_created": summary["per_node"],
        "counts": {
            "before": before_counts,
            "after_upload": after_counts,
            "after_scheduler": final_counts if payload.trigger_scheduler else None,
        },
        "mode": payload.mode,
        "target_node": payload.target_node,
        "scheduler": {
            "triggered": triggered,
            "before": before_plan,
            "after_upload": after_plan,
            "after_scheduler": final_plan if payload.trigger_scheduler else None,
            "message": final_plan.get("message") if isinstance(final_plan, dict) else None,
        },
    }


@app.post("/api/demo/stress")
async def api_demo_stress(payload: DemoStressRequest) -> Dict[str, Any]:
    allowed_nodes = {node.split(":")[0] for node in settings.zk_nodes}
    if payload.node not in allowed_nodes:
        raise HTTPException(status_code=400, detail=f"未知节点 {payload.node}")
    before_plan, _ = build_scheduler_plan()
    worker: DemoWorkload = getattr(app.state, "demo_workload", DemoWorkload())
    app.state.demo_workload = worker
    try:
        result = await worker.skew_files(payload.node, payload.files, size_kb=payload.size_kb)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await refresh_metrics()
    after_plan, _ = build_scheduler_plan()
    triggered = False
    if payload.trigger_scheduler:
        triggered = await maybe_rebalance_files()
        await refresh_metrics()
        after_plan, _ = build_scheduler_plan()
    return {
        "result": result,
        "scheduler": {
            "before": before_plan,
            "after": after_plan,
            "triggered": triggered,
        },
    }


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


@app.post("/api/nodes/{node_id}/drain")
async def api_node_drain(node_id: str, request: Request, payload: Optional[NodeDrainRequest] = None) -> Dict[str, Any]:
    allowed_nodes = {node.split(":")[0] for node in settings.zk_nodes}
    if node_id not in allowed_nodes:
        raise HTTPException(status_code=404, detail=f"Unknown node {node_id}")
    reason = (payload.reason.strip() if (payload and payload.reason) else None)
    db.set_node_state(node_id, drained=True, reason=reason)
    actor = request.headers.get("X-Demo-User", "web")
    details = f"Node {node_id} marked as drained"
    if reason:
        details = f"{details} ({reason})"
    db.record_operation(
        action="drain",
        status="success",
        node=node_id,
        actor=actor,
        details=details,
    )
    await refresh_metrics()
    return {"node": node_id, "drained": True, "reason": reason}


@app.post("/api/nodes/{node_id}/undrain")
async def api_node_undrain(node_id: str, request: Request) -> Dict[str, Any]:
    allowed_nodes = {node.split(":")[0] for node in settings.zk_nodes}
    if node_id not in allowed_nodes:
        raise HTTPException(status_code=404, detail=f"Unknown node {node_id}")
    db.set_node_state(node_id, drained=False, reason=None)
    actor = request.headers.get("X-Demo-User", "web")
    details = f"Node {node_id} restored to scheduling pool"
    db.record_operation(
        action="undrain",
        status="success",
        node=node_id,
        actor=actor,
        details=details,
    )
    await refresh_metrics()
    return {"node": node_id, "drained": False}


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


@app.get("/{filename:path}.html", include_in_schema=False)
def serve_html(filename: str) -> HTMLResponse:
    """提供前端 HTML 文件（overview.html, workload.html, logs.html 等）"""
    html_path = settings.frontend_dir / f"{filename}.html"
    if html_path.exists() and html_path.is_file():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    raise HTTPException(status_code=404, detail="Page not found")
