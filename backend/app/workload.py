from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from . import db, storage, zookeeper_utils
from .config import get_settings

logger = logging.getLogger(__name__)
SETTINGS = get_settings()


class DemoWorkload:
    """Generate synthetic activity so the demo dashboard always has data."""

    def __init__(self) -> None:
        self._running = False
        self._workload_nodes: List[str] = []
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def start_auto(self) -> None:
        if not SETTINGS.demo_workload_enabled:
            return
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="demo-workload-loop")
        logger.info("Demo workload loop started")

    async def _run_loop(self) -> None:
        await asyncio.sleep(5)
        while self._running:
            try:
                self._maybe_generate_file_activity()
                self._pulse_znode_activity()
                self._simulate_task_workflow()
            except Exception as exc:  # pragma: no cover - safety net
                logger.exception("Demo workload loop error: %s", exc)
            interval = SETTINGS.demo_workload_interval
            jitter = random.randint(0, max(SETTINGS.demo_workload_jitter, 0))
            await asyncio.sleep(max(1, interval + jitter))

    async def run_once(self, *, files: int = 0, tasks: int = 0, znodes: int = 0) -> Dict[str, Any]:
        async with self._lock:
            result: Dict[str, Any] = {"files": 0, "tasks": 0, "znodes": 0, "fileMessages": [], "taskMessages": [], "znodePaths": []}
            if files > 0:
                file_msgs = await asyncio.to_thread(self._generate_files, files)
                result["files"] = len(file_msgs)
                result["fileMessages"] = file_msgs
            if znodes > 0:
                paths = await asyncio.to_thread(self._create_znodes, znodes)
                result["znodes"] = len(paths)
                result["znodePaths"] = paths
            if tasks > 0:
                task_msgs = await asyncio.to_thread(self._run_task_cycles, tasks)
                result["tasks"] = len(task_msgs)
                result["taskMessages"] = task_msgs
            return result

    def _generate_files(self, count: int) -> List[str]:
        created: List[str] = []
        for _ in range(count):
            msg = self._maybe_generate_file_activity(force_create=True)
            if msg:
                created.append(str(msg))
        return created

    def _run_task_cycles(self, cycles: int) -> List[str]:
        events: List[str] = []
        for _ in range(cycles):
            msg = self._simulate_task_workflow(force_create=True)
            if msg:
                events.append(msg)
        return events

    def _create_znodes(self, count: int) -> List[str]:
        created: List[str] = []
        for _ in range(count):
            path = self._pulse_znode_activity()
            if path:
                created.append(path)
        return created

    def _maybe_generate_file_activity(self, force_create: bool = False) -> Optional[str]:
        files = db.get_files()
        max_files = SETTINGS.demo_workload_max_files
        if len(files) >= max_files and not force_create:
            victim = random.choice(files)
            other_nodes = [n.split(":")[0] for n in SETTINGS.zk_nodes if n.split(":")[0] != victim["node"]]
            target_node = random.choice(other_nodes) if other_nodes else victim["node"]
            if target_node != victim["node"]:
                new_path, new_node = storage.migrate_file(victim, target_node)
                history = self._get_history(victim)
                history.append({
                    "timestamp": datetime.utcnow().isoformat(),
                    "action": "demo_migrate",
                    "from": victim["node"],
                    "to": new_node,
                })
                db.update_file_record(victim["id"], node=new_node, path=new_path, history=history)
                zookeeper_utils.register_file_metadata(victim["uuid"], {
                    "filename": victim["filename"],
                    "size": victim["size_bytes"],
                    "node": new_node,
                    "path": new_path,
                    "history": history,
                    "updated_at": datetime.utcnow().isoformat(),
                })
                db.record_operation(
                    action="demo_migrate",
                    status="success",
                    node=new_node,
                    details=f"Synthetic migration {victim['filename']} -> {new_node}",
                )
                return f"{victim['filename']} -> {new_node}"
            else:
                oldest = files[-1]
                storage.remove_file(oldest["path"])
                db.delete_file_record(oldest["id"])
                zookeeper_utils.delete_file_metadata(oldest["uuid"])
                db.record_operation(
                    action="demo_cleanup",
                    status="success",
                    node=oldest["node"],
                    details=f"Removed stale demo file {oldest['filename']}",
                )
                return f"Removed {oldest['filename']}"

        if len(files) >= max_files:
            oldest = files[-1]
            storage.remove_file(oldest["path"])
            db.delete_file_record(oldest["id"])
            zookeeper_utils.delete_file_metadata(oldest["uuid"])
            db.record_operation(
                action="demo_cleanup",
                status="success",
                node=oldest["node"],
                details=f"Removed stale demo file {oldest['filename']} to create space",
            )

        node = storage.select_target_node()
        path, size, file_uuid, filename = storage.create_demo_file(node, SETTINGS.demo_workload_file_size_kb)
        history = [{
            "timestamp": datetime.utcnow().isoformat(),
            "action": "demo_upload",
            "node": node,
        }]
        file_id = db.create_file_record(
            uuid=file_uuid,
            filename=filename,
            size_bytes=size,
            node=node,
            path=path,
            history=history,
        )
        payload = {
            "id": file_id,
            "uuid": file_uuid,
            "filename": filename,
            "size": size,
            "node": node,
            "path": path,
            "history": history,
            "created_at": datetime.utcnow().isoformat(),
        }
        zookeeper_utils.register_file_metadata(file_uuid, payload)
        db.record_operation(
            action="demo_upload",
            status="success",
            node=node,
            details=f"Synthetic upload {filename} ({size} bytes) to {node}",
        )
        return filename

    def _pulse_znode_activity(self) -> Optional[str]:
        client = zookeeper_utils.get_kazoo_client()
        base = f"{SETTINGS.zk_root_path}/workload"
        client.ensure_path(base)
        znode_path = f"{base}/task-{uuid4().hex[:8]}"
        try:
            client.create(znode_path, b"demo", makepath=True)
            self._workload_nodes.append(znode_path)
        except Exception as exc:
            logger.debug("Unable to create workload znode %s: %s", znode_path, exc)
            return None
        while len(self._workload_nodes) > SETTINGS.demo_workload_max_znodes:
            old = self._workload_nodes.pop(0)
            try:
                client.delete(old)
            except Exception:
                pass
        return znode_path

    def _simulate_task_workflow(self, force_create: bool = False) -> Optional[str]:
        tasks = db.list_tasks(limit=SETTINGS.demo_workload_max_tasks + 20)
        if len(tasks) > SETTINGS.demo_workload_max_tasks:
            for record in tasks[SETTINGS.demo_workload_max_tasks :][::-1]:
                if record["status"] in {"succeeded", "failed", "cancelled"}:
                    db.delete_task(record["task_id"])
        running = next((t for t in tasks if t["status"] == "running"), None)
        queued = next((t for t in tasks if t["status"] == "queued"), None)

        def parse_payload(task: Dict[str, Any]) -> Any:
            payload = task.get("payload")
            if isinstance(payload, str):
                try:
                    return json.loads(payload)
                except json.JSONDecodeError:
                    return None
            return payload

        if running and (force_create or random.random() < 0.5):
            task_id = running["task_id"]
            node = running.get("node")
            payload = parse_payload(running)
            if random.random() < 0.8:
                next_status = "succeeded"
                details = "Task completed successfully"
            else:
                next_status = "failed"
                details = "Task failed with synthetic error"
            db.upsert_task_record(task_id=task_id, node=node, status=next_status, payload=payload, details=details)
            db.record_operation(action="demo_task", status=next_status, node=node, details=f"Task {task_id}: {details}")
            return f"Task {task_id}: {details}"

        if queued:
            task_id = queued["task_id"]
            node = queued.get("node")
            payload = parse_payload(queued)
            db.upsert_task_record(task_id=task_id, node=node, status="running", payload=payload, details="Task started")
            db.record_operation(action="demo_task", status="running", node=node, details=f"Task {task_id} is running")
            return f"Task {task_id} is running"

        if force_create or (len(tasks) < SETTINGS.demo_workload_max_tasks and random.random() < 0.6):
            node = random.choice([n.split(":")[0] for n in SETTINGS.zk_nodes])
            task_id = f"demo-task-{uuid4().hex[:8]}"
            payload = {
                "job": random.choice(["ETL", "Backup", "Report", "Sync"]),
                "size": random.randint(1, 10),
            }
            db.upsert_task_record(task_id=task_id, node=node, status="queued", payload=payload, details="Task queued")
            db.record_operation(
                action="demo_task",
                status="queued",
                node=node,
                details=f"Queued synthetic task {task_id} on {node}",
            )
            return f"Queued task {task_id} on {node}"

        return None

    def stop_auto(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        logger.info("Demo workload loop stopped")

    @staticmethod
    def _get_history(record: dict) -> List[dict]:
        history = record.get("history")
        if not history:
            return []
        if isinstance(history, list):
            return history
        try:
            return json.loads(history)
        except json.JSONDecodeError:
            return []
