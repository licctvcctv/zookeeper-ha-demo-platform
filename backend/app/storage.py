from __future__ import annotations

import os
import random
import shutil
from pathlib import Path
from typing import Dict, Tuple
from uuid import uuid4

from fastapi import UploadFile

from .config import get_settings
from . import db

SETTINGS = get_settings()


def _node_counts() -> Dict[str, int]:
    counts: Dict[str, int] = {node.split(":")[0]: 0 for node in SETTINGS.zk_nodes}
    for record in db.get_files():
        counts[record["node"]] = counts.get(record["node"], 0) + 1
    return counts


def select_target_node() -> str:
    counts = _node_counts()
    # fallback: ensure every node key exists
    for node in SETTINGS.zk_nodes:
        host = node.split(":")[0]
        counts.setdefault(host, 0)
    sorted_nodes = sorted(counts.items(), key=lambda item: item[1])
    return sorted_nodes[0][0]


def save_upload(upload_file: UploadFile, node: str) -> Tuple[str, int, str]:
    node_dir = SETTINGS.file_storage_path / node
    node_dir.mkdir(parents=True, exist_ok=True)
    file_uuid = uuid4().hex
    safe_name = upload_file.filename or "uploaded.bin"
    destination = node_dir / f"{file_uuid}_{safe_name}"
    size = 0
    with destination.open("wb") as out_f:
        while chunk := upload_file.file.read(1024 * 1024):
            size += len(chunk)
            out_f.write(chunk)
    upload_file.file.close()
    return str(destination), size, file_uuid


def create_demo_file(node: str, size_kb: int) -> Tuple[str, int, str, str]:
    node_dir = SETTINGS.file_storage_path / node
    node_dir.mkdir(parents=True, exist_ok=True)
    file_uuid = uuid4().hex
    filename = f"demo-{file_uuid[:6]}.bin"
    destination = node_dir / filename
    size_bytes = max(size_kb, 1) * 1024
    remaining = size_bytes
    with destination.open("wb") as out_f:
        while remaining > 0:
            chunk_size = min(remaining, 32 * 1024)
            out_f.write(os.urandom(chunk_size))
            remaining -= chunk_size
    # add small variability so files differ
    if random.random() < 0.3:
        extra = 512
        with destination.open("ab") as out_f:
            out_f.write(os.urandom(extra))
        size_bytes += extra
    return str(destination), size_bytes, file_uuid, filename


def migrate_file(file_record: Dict[str, any], new_node: str) -> Tuple[str, str]:
    current_path = Path(file_record["path"])
    if not current_path.exists():
        raise FileNotFoundError(f"File path not found on disk: {current_path}")
    new_dir = SETTINGS.file_storage_path / new_node
    new_dir.mkdir(parents=True, exist_ok=True)
    new_path = new_dir / current_path.name
    shutil.move(str(current_path), new_path)
    return str(new_path), new_node


def remove_file(path: str) -> None:
    file_path = Path(path)
    try:
        if file_path.exists():
            file_path.unlink()
    except OSError:
        pass
