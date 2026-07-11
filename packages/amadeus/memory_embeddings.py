from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from amadeus.embeddings import (
    BGE_M3_DIMENSIONS,
    BGE_M3_MODEL_ID,
    BGE_M3_PROVIDER_ID,
    LocalBGEM3EmbeddingProvider,
    LocalEmbeddingConfig,
    default_bge_m3_model_dir,
    normalize_embedding_local_dir,
)
from amadeus.model import parse_bool_value, parse_positive_int_value, parse_providers_config


class TextEmbeddingProvider(Protocol):
    provider: str
    model_id: str
    dimensions: int

    def available(self) -> bool:
        ...

    def encode_texts(self, texts: list[str] | tuple[str, ...]) -> list[list[float]]:
        ...


@dataclass(frozen=True)
class MemoryEmbeddingBackfillResult:
    provider: str
    model: str
    dimensions: int
    scanned: int = 0
    embedded: int = 0
    skipped: int = 0
    error: str = ""
    coverage: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "dimensions": self.dimensions,
            "scanned": self.scanned,
            "embedded": self.embedded,
            "skipped": self.skipped,
            "error": self.error,
            "coverage": self.coverage,
        }


class MemoryEmbeddingBackfillService:
    def __init__(self, memory_store: Any, embedding_provider: TextEmbeddingProvider) -> None:
        self.memory_store = memory_store
        self.embedding_provider = embedding_provider

    def coverage(self) -> dict[str, Any]:
        return self.memory_store.memory_item_embedding_coverage(
            provider=self.embedding_provider.provider,
            model=self.embedding_provider.model_id,
            dimensions=self.embedding_provider.dimensions,
        )

    def backfill(self, *, limit: int = 50, batch_size: int = 8) -> MemoryEmbeddingBackfillResult:
        bounded_limit = max(1, min(500, int(limit)))
        bounded_batch_size = max(1, min(64, int(batch_size)))
        provider = self.embedding_provider
        if not provider.available():
            return MemoryEmbeddingBackfillResult(
                provider=provider.provider,
                model=provider.model_id,
                dimensions=provider.dimensions,
                error="embedding_provider_not_available",
                coverage=self.coverage(),
            )

        items = self.memory_store.list_memory_items_needing_embeddings(
            provider=provider.provider,
            model=provider.model_id,
            dimensions=provider.dimensions,
            limit=bounded_limit,
        )
        embedded = 0
        skipped = 0
        for start in range(0, len(items), bounded_batch_size):
            batch = items[start:start + bounded_batch_size]
            texts = [memory_item_embedding_text(item) for item in batch]
            vectors = provider.encode_texts(texts)
            for item, vector in zip(batch, vectors):
                self.memory_store.upsert_memory_item_embedding(
                    int(item["memoryItemId"]),
                    provider=provider.provider,
                    model=provider.model_id,
                    dimensions=provider.dimensions,
                    vector=vector,
                )
                embedded += 1
            skipped += max(0, len(batch) - len(vectors))

        return MemoryEmbeddingBackfillResult(
            provider=provider.provider,
            model=provider.model_id,
            dimensions=provider.dimensions,
            scanned=len(items),
            embedded=embedded,
            skipped=skipped,
            coverage=self.coverage(),
        )


class MemoryEmbeddingBackfillRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._state: dict[str, Any] = {
            "status": "idle",
            "active": False,
            "startedAt": "",
            "finishedAt": "",
            "message": "",
            "error": "",
            "result": None,
        }

    def start(self, service: MemoryEmbeddingBackfillService, *, limit: int = 50, batch_size: int = 8) -> dict[str, Any]:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self.status(service.coverage())
            self._state = {
                "status": "running",
                "active": True,
                "startedAt": now_iso(),
                "finishedAt": "",
                "message": "Memory embedding backfill is running.",
                "error": "",
                "result": None,
            }
            self._thread = threading.Thread(
                target=self._run,
                args=(service, int(limit), int(batch_size)),
                name="memory-embedding-backfill",
                daemon=True,
            )
            self._thread.start()
            return self.status(service.coverage())

    def status(self, coverage: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            payload = dict(self._state)
        if coverage is not None:
            payload["coverage"] = coverage
        return payload

    def _run(self, service: MemoryEmbeddingBackfillService, limit: int, batch_size: int) -> None:
        try:
            result = service.backfill(limit=limit, batch_size=batch_size)
            with self._lock:
                self._state = {
                    "status": "completed" if not result.error else "failed",
                    "active": False,
                    "startedAt": str(self._state.get("startedAt") or ""),
                    "finishedAt": now_iso(),
                    "message": "Memory embedding backfill completed." if not result.error else "Memory embedding backfill failed.",
                    "error": result.error,
                    "result": result.to_payload(),
                }
        except Exception as error:
            with self._lock:
                self._state = {
                    "status": "failed",
                    "active": False,
                    "startedAt": str(self._state.get("startedAt") or ""),
                    "finishedAt": now_iso(),
                    "message": "Memory embedding backfill failed.",
                    "error": str(error),
                    "result": None,
                }


def memory_item_embedding_text(item: dict[str, Any]) -> str:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    parts = [
        f"scope: {item.get('scope', '')}",
        f"type: {item.get('memoryType', '')}",
        f"content: {item.get('content', '')}",
    ]
    if metadata:
        parts.append(f"metadata: {metadata}")
    return "\n".join(str(part).strip() for part in parts if str(part).strip())


def current_local_bge_m3_embedding_config(*, providers_config_path: Path, repo_root: Path) -> LocalEmbeddingConfig:
    config = parse_providers_config(providers_config_path)
    embedding = config.get("embedding") if isinstance(config.get("embedding"), dict) else {}
    providers = embedding.get("providers") if isinstance(embedding.get("providers"), dict) else {}
    default_provider = str(os.environ.get("AMADEUS_EMBEDDING_PROVIDER") or embedding.get("default") or BGE_M3_PROVIDER_ID)
    provider_entry = providers.get(default_provider) if isinstance(providers.get(default_provider), dict) else {}
    model_id = str(provider_entry.get("model") or os.environ.get("AMADEUS_BGE_M3_MODEL_ID") or BGE_M3_MODEL_ID)
    local_dir_value = (
        provider_entry.get("localPath")
        or os.environ.get("AMADEUS_BGE_M3_MODEL_DIR")
        or str(default_bge_m3_model_dir(repo_root))
    )
    dimensions = parse_positive_int_value(provider_entry.get("dimensions")) or BGE_M3_DIMENSIONS
    batch_size = parse_positive_int_value(provider_entry.get("batchSize")) or 8
    return LocalEmbeddingConfig(
        provider=default_provider,
        model_id=model_id,
        local_dir=normalize_embedding_local_dir(str(local_dir_value), repo_root=repo_root),
        dimensions=dimensions,
        normalize_embeddings=parse_bool_value(provider_entry.get("normalizeEmbeddings"), True),
        batch_size=batch_size,
        device=str(provider_entry.get("device") or "auto"),
    )


def local_bge_m3_embedding_is_configured(*, providers_config_path: Path) -> bool:
    config = parse_providers_config(providers_config_path)
    embedding = config.get("embedding") if isinstance(config.get("embedding"), dict) else {}
    providers = embedding.get("providers") if isinstance(embedding.get("providers"), dict) else {}
    env_provider = os.environ.get("AMADEUS_EMBEDDING_PROVIDER", "").strip()
    return (
        env_provider == BGE_M3_PROVIDER_ID
        or str(embedding.get("default") or "") == BGE_M3_PROVIDER_ID
        or isinstance(providers.get(BGE_M3_PROVIDER_ID), dict)
    )


def create_local_bge_m3_embedding_provider(*, providers_config_path: Path, repo_root: Path) -> LocalBGEM3EmbeddingProvider | None:
    if not local_bge_m3_embedding_is_configured(providers_config_path=providers_config_path):
        return None
    config = current_local_bge_m3_embedding_config(providers_config_path=providers_config_path, repo_root=repo_root)
    if config.provider != BGE_M3_PROVIDER_ID:
        return None
    return LocalBGEM3EmbeddingProvider(config)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
