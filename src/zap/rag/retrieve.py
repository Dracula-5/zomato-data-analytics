"""Four ways to find chunks, so the evaluation can show what each step really adds.

Dense search finds meaning, BM25 finds exact terms such as `ORD_AMOUNT_MISMATCH`, and reciprocal
rank fusion combines them using ranks alone, so their incomparable scores never mix. A
cross-encoder then rereads the best candidates next to the question. The models run locally
on CPU: search costs nothing and sends nothing anywhere.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

import bm25s

from zap.rag.corpus import Chunk

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
RERANK_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"
COLLECTION = "documents"
RRF_K = 60  # the usual constant: it stops one list's top result from dominating
CANDIDATES = 30
MODES = ("dense", "bm25", "hybrid", "hybrid_rerank")


class Embedder(Protocol):
    def passages(self, texts: list[str]) -> list[list[float]]: ...

    def query(self, text: str) -> list[float]: ...


class Reranker(Protocol):
    def scores(self, query: str, texts: list[str]) -> list[float]: ...


class LocalEmbedder:
    def __init__(self, cache_dir: Path) -> None:
        from fastembed import TextEmbedding

        self.model = TextEmbedding(EMBEDDING_MODEL, cache_dir=str(cache_dir))

    def passages(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self.model.passage_embed(texts)]

    def query(self, text: str) -> list[float]:
        vector: list[float] = next(iter(self.model.query_embed(text))).tolist()
        return vector


class LocalReranker:
    def __init__(self, cache_dir: Path) -> None:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self.model = TextCrossEncoder(RERANK_MODEL, cache_dir=str(cache_dir))

    def scores(self, query: str, texts: list[str]) -> list[float]:
        return [float(score) for score in self.model.rerank(query, texts)]


def reciprocal_rank_fusion(rankings: Sequence[Sequence[str]], k: int = RRF_K) -> list[str]:
    """Score each id by 1 / (k + rank) in every list that has it. Ties keep id order."""
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] += 1 / (k + rank)
    return sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))


def _tokenize(texts: list[str]) -> Any:
    return bm25s.tokenize(texts, stopwords="en", show_progress=False)


class Retriever:
    def __init__(
        self,
        chunks: list[Chunk],
        embedder: Embedder,
        reranker: Reranker,
        store: Path | None = None,
    ) -> None:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        self.chunks = {chunk.id: chunk for chunk in chunks}
        self.ids = [chunk.id for chunk in chunks]
        self.embedder = embedder
        self.reranker = reranker
        texts = [chunk.searchable for chunk in chunks]
        self.lexical = bm25s.BM25()
        self.lexical.index(_tokenize(texts), show_progress=False)
        options = ChromaSettings(anonymized_telemetry=False)
        client = (
            chromadb.PersistentClient(path=str(store), settings=options)
            if store
            else chromadb.EphemeralClient(settings=options)
        )
        # Rebuilt each time: the corpus is small, and a stale index would be a silent bug.
        if COLLECTION in [c.name for c in client.list_collections()]:
            client.delete_collection(COLLECTION)
        collection: Any = client.create_collection(
            COLLECTION, metadata={"hnsw:space": "cosine"}, embedding_function=None
        )
        collection.add(ids=self.ids, embeddings=embedder.passages(texts), documents=texts)
        self.dense_index: Any = collection

    def dense(self, question: str, n: int = CANDIDATES) -> list[str]:
        found = self.dense_index.query(
            query_embeddings=[self.embedder.query(question)],
            n_results=min(n, len(self.ids)),
            include=[],
        )
        return list(found["ids"][0])

    def bm25(self, question: str, n: int = CANDIDATES) -> list[str]:
        k = min(n, len(self.ids))
        order, scores = self.lexical.retrieve(_tokenize([question]), k=k, show_progress=False)
        # A score of zero means no shared term: it isn't a match, just the next in line.
        return [self.ids[i] for i, s in zip(order[0], scores[0], strict=True) if s > 0]

    def hybrid(self, question: str, n: int = CANDIDATES) -> list[str]:
        return reciprocal_rank_fusion([self.dense(question), self.bm25(question)])[:n]

    def hybrid_rerank(self, question: str, n: int = CANDIDATES) -> list[str]:
        candidates = self.hybrid(question)
        texts = [self.chunks[chunk_id].searchable for chunk_id in candidates]
        scores = self.reranker.scores(question, texts)
        ranked = sorted(zip(scores, candidates, strict=True), key=lambda pair: (-pair[0], pair[1]))
        return [chunk_id for _, chunk_id in ranked[:n]]

    def search(self, question: str, mode: str = "hybrid", n: int = 5) -> list[str]:
        if mode not in MODES:
            raise ValueError(f"unknown search mode {mode}")
        results: list[str] = getattr(self, mode)(question, n)
        return results[:n]
