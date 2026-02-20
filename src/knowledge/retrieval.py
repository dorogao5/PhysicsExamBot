from __future__ import annotations

import json
from collections import deque
from typing import Any

from src.storage.db import Database, SearchChunk


class KnowledgeRetrieval:
    def __init__(self, db: Database):
        self._db = db

    async def lookup_theory(self, *, course_id: int, query: str, top_k: int) -> list[SearchChunk]:
        return await self._db.search_theory_chunks(course_id=course_id, query=query, limit=top_k)

    async def get_related_topics(self, *, course_id: int, topic_key: str, depth: int = 1) -> list[str]:
        nodes = await self._db.list_topic_nodes(course_id)
        graph: dict[str, set[str]] = {}

        for node in nodes:
            key = node["topic_key"]
            related = set(json.loads(node["related_json"]))
            prerequisites = set(json.loads(node["prerequisites_json"]))
            graph[key] = related | prerequisites

        visited: set[str] = {topic_key}
        queue = deque([(topic_key, 0)])

        while queue:
            current, level = queue.popleft()
            if level >= depth:
                continue
            for nxt in graph.get(current, set()):
                if nxt in visited:
                    continue
                if nxt not in graph:
                    continue
                visited.add(nxt)
                queue.append((nxt, level + 1))

        return list(visited)

    async def sample_questions(
        self,
        *,
        course_id: int,
        topic_keys: list[str],
        limit: int,
        mix: dict[str, float],
        exclude_ids: set[int] | None = None,
    ) -> list[dict[str, Any]]:
        return await self._db.sample_questions(
            course_id=course_id,
            topic_keys=topic_keys,
            limit=limit,
            scope_mix=mix,
            exclude_ids=exclude_ids,
        )
