"""异议追踪结果存储：JSON 文件 + 异步锁，独立于公告采集 store.json。

再次追踪成功（含 completed=False 的部分采集）时以完整新结果整体替换；
请求级异常（如未进入采集流程）时本模块不被触碰，旧结果自然保留。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from . import config
from .objection_models import ObjectionCollectResult, ObjectionTrackSummary


class StoredObjectionTrack:
    def __init__(
        self,
        collected: ObjectionCollectResult,
        summary: ObjectionTrackSummary,
    ) -> None:
        self.collected = collected
        self.summary = summary

    def model_dump(self) -> dict:
        return {
            "collected": self.collected.model_dump(),
            "summary": self.summary.model_dump(),
        }

    @classmethod
    def model_validate(cls, data: dict) -> "StoredObjectionTrack":
        return cls(
            collected=ObjectionCollectResult.model_validate(data["collected"]),
            summary=ObjectionTrackSummary.model_validate(data["summary"]),
        )


class ObjectionStore:
    def __init__(self, path: Path = config.OBJECTION_STORE_PATH) -> None:
        self.path = path
        self._lock = asyncio.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    async def replace(self, track: StoredObjectionTrack) -> None:
        async with self._lock:
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(track.model_dump(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self.path)

    async def load(self) -> StoredObjectionTrack | None:
        async with self._lock:
            if not self.path.exists():
                return None
            return StoredObjectionTrack.model_validate(
                json.loads(self.path.read_text(encoding="utf-8"))
            )


objection_store = ObjectionStore()
