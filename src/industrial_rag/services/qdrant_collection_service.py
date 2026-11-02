"""Lifecycle-safe Qdrant collection operations for KB shadow generations."""

from __future__ import annotations

from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient

from industrial_rag.config import Settings
from industrial_rag.vector_collections import CollectionNameResolver, VectorBackend


@dataclass(frozen=True, slots=True)
class QdrantCollectionService:
    """Operate only on the exact collections resolved for one KB generation."""

    settings: Settings

    def _require_qdrant(self) -> None:
        if self.settings.vector_backend is not VectorBackend.qdrant:
            raise RuntimeError("Qdrant collection operation requires the Qdrant backend")
        if not self.settings.qdrant_url or not self.settings.qdrant_kb_id:
            raise RuntimeError("Qdrant KB settings are incomplete")

    def names(self) -> dict[str, str]:
        self._require_qdrant()
        if self.settings.qdrant_generation is None:
            raise RuntimeError("Qdrant generation is required")
        return CollectionNameResolver(self.settings.qdrant_collection_prefix).names_for(
            kb_id=self.settings.qdrant_kb_id,
            generation=self.settings.qdrant_generation,
        )

    def _client(self) -> AsyncQdrantClient:
        self._require_qdrant()
        return AsyncQdrantClient(
            url=self.settings.qdrant_url,
            api_key=self.settings.qdrant_api_key,
            timeout=10,
        )

    async def delete_generation(self) -> None:
        """Delete only exact, generated collection names and verify they are gone."""
        client = self._client()
        try:
            for name in self.names().values():
                if await client.collection_exists(name):
                    await client.delete_collection(name)
                if await client.collection_exists(name):
                    raise RuntimeError(f"Qdrant collection cleanup was not durable: {name}")
        finally:
            await client.close()

    async def verify_generation(
        self,
        *,
        expected_chunks: int | None = None,
        require_chunks: bool = True,
    ) -> int:
        """Verify exact namespace collections and the expected chunks point count."""
        client = self._client()
        try:
            names = self.names()
            for name in names.values():
                if not await client.collection_exists(name):
                    raise RuntimeError(f"Qdrant collection is missing: {name}")
            count = (await client.count(names["chunks"], exact=True)).count
            minimum = expected_chunks if expected_chunks is not None else int(require_chunks)
            if count < minimum:
                raise RuntimeError(
                    f"Qdrant chunks collection has {count} points; expected at least {minimum}"
                )
            return count
        finally:
            await client.close()

    async def verify_graph(self, *, minimum_entities: int = 1) -> tuple[int, int]:
        """Verify the entity and relationship namespaces were materialised.

        Chunk-level checks pass even when entity extraction wrote nothing, which
        is how a knowledge base ends up ``ready`` while its graph is empty.  A
        generation in that state answers from vectors only, so it must not be
        activated as if the graph worked.
        """
        client = self._client()
        try:
            names = self.names()
            entities = (await client.count(names["entities"], exact=True)).count
            relationships = (await client.count(names["relationships"], exact=True)).count
            if entities < minimum_entities or relationships < minimum_entities:
                raise RuntimeError(
                    "Index health: knowledge graph is empty "
                    f"(entities={entities}, relationships={relationships}); "
                    "re-index the documents so entity extraction can write the graph"
                )
            return entities, relationships
        finally:
            await client.close()
