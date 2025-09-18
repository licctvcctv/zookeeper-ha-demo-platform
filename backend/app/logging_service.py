from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from .config import get_settings

logger = logging.getLogger(__name__)
SETTINGS = get_settings()


def index_operation_log_sync(doc: Dict[str, Any]) -> None:
    if not SETTINGS.elasticsearch_url:
        return
    url = f"{SETTINGS.elasticsearch_url.rstrip('/')}/operations/_doc"
    try:
        with httpx.Client(timeout=2.0) as client:
            response = client.post(url, json=doc)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.debug("Failed to write operation log to Elasticsearch: %s", exc)


async def search_logs(query: Optional[str] = None, service: Optional[str] = None, size: int = 50) -> List[Dict[str, Any]]:
    if not SETTINGS.elasticsearch_url:
        return []
    payload: Dict[str, Any] = {
        "size": size,
        "sort": [{"@timestamp": {"order": "desc"}}],
        "query": {"bool": {"must": [], "filter": []}},
    }
    bool_clause = payload["query"]["bool"]
    must: List[Dict[str, Any]] = bool_clause["must"]  # type: ignore[assignment]
    filters: List[Dict[str, Any]] = bool_clause["filter"]  # type: ignore[assignment]
    if query:
        must.append({"query_string": {"query": query}})
    if service:
        filters.append({"term": {"service.name.keyword": service}})
    if not must and not filters:
        payload["query"] = {"match_all": {}}

    url = f"{SETTINGS.elasticsearch_url.rstrip('/')}/filebeat-*,operations/_search"

    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            response = await client.post(url, json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Elastic search failed: %s", exc)
            return []

    body = response.json()
    hits = body.get("hits", {}).get("hits", [])
    results: List[Dict[str, Any]] = []
    for hit in hits:
        source = hit.get("_source", {})
        host_info = source.get("host") or {}
        container_info = source.get("container") or {}
        service = source.get("service") or {}
        if isinstance(service, str):
            service = {"name": service}
        results.append(
            {
                "timestamp": source.get("@timestamp"),
                "message": source.get("message"),
                "service": service.get("name"),
                "host": host_info.get("name"),
                "container_id": container_info.get("id"),
            }
        )
    return results
