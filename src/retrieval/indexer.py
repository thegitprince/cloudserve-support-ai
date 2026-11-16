"""Chroma-backed semantic retrieval over CloudServe's 29-article knowledge
base — implements the retrieval half of FR-05.

Chunking decision (recorded here because the Build Spec and Setup Guide both
require the choice to be justified, not left at a default without comment):

    Each of the 29 articles in documentation.json is indexed as ONE chunk —
    the full article content, not split into paragraphs or sections.

    Why: article lengths in this corpus range from 860 to 1336 characters
    (mean 1094), which is already smaller than a typical single embedding
    chunk. The Dataset Guide warns explicitly that "splitting inside a
    resolution sequence tends to produce passages that retrieve well but
    read as incomplete" — these articles are short, numbered-step documents
    (symptoms, causes, resolution), so sub-splitting would only risk cutting
    a resolution sequence in half for no retrieval benefit, since nothing in
    this corpus is long enough to need splitting in the first place. A
    second configuration (sub-splitting at a smaller chunk size, e.g. by
    `##` section header) is left as an explicit open comparison in
    docs/Stage_4_Sprint_Plan.md (B-03/B-04) rather than implemented here
    without being measured against this baseline.

Embeddings: sentence-transformers/all-MiniLM-L6-v2, per the pinned stack in
_context_md/01_Project_Brief.md §09 and _context_md/Setup_Guide.md §03.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_DOCS_PATH = Path("Capstone_Pack/05_Datasets/documentation.json")
DEFAULT_CHROMA_PATH = Path("storage/chroma")
DEFAULT_COLLECTION_NAME = "cloudserve_kb"
EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


def load_documentation(path: str | Path = DEFAULT_DOCS_PATH) -> list[dict[str, Any]]:
    """Load the 29 KB articles. Raises FileNotFoundError / JSONDecodeError
    rather than returning a silently empty corpus, because an empty
    retrieval corpus behaving as if it were intentional is exactly the
    failure mode the Build Spec warns against (Retrieve: "Retrieving
    something plausible but irrelevant... is worse than retrieving
    nothing" — an empty corpus that nobody notices is worse still)."""
    docs = json.loads(Path(path).read_text(encoding="utf-8"))
    if not docs:
        raise ValueError(f"{path} loaded but contains zero documents")
    return docs


class KnowledgeBaseIndexer:
    """Builds and queries a local Chroma collection over the documentation
    corpus. Each article is one chunk (see module docstring); doc_id,
    title, and category are stored as metadata so citations (FR-06) can be
    resolved back to a real document, per Build Spec criterion A4."""

    def __init__(
        self,
        chroma_path: str | Path = DEFAULT_CHROMA_PATH,
        collection_name: str = DEFAULT_COLLECTION_NAME,
        embedding_model_name: str = EMBEDDING_MODEL_NAME,
    ):
        import chromadb
        from chromadb.utils import embedding_functions

        self.chroma_path = Path(chroma_path)
        self.chroma_path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(self.chroma_path))
        self._embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=embedding_model_name.removeprefix("sentence-transformers/")
        )
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            embedding_function=self._embedding_fn,
            metadata={"hnsw:space": "cosine"},
        )

    def index_documents(self, docs: list[dict[str, Any]], *, reset: bool = True) -> int:
        """Embed and store every article. Returns the number of chunks
        stored. With reset=True (default), any prior contents of this
        collection are cleared first, so re-running the indexer is
        idempotent rather than accumulating duplicate chunks on every run."""
        if reset and self._collection.count() > 0:
            existing_ids = self._collection.get()["ids"]
            if existing_ids:
                self._collection.delete(ids=existing_ids)

        ids, texts, metadatas = [], [], []
        for doc in docs:
            required = {"doc_id", "title", "category", "content"}
            missing = required - doc.keys()
            if missing:
                raise ValueError(f"document missing required fields {missing}: {doc.get('doc_id', '?')}")
            ids.append(doc["doc_id"])
            texts.append(doc["content"])
            metadatas.append(
                {
                    "doc_id": doc["doc_id"],
                    "title": doc["title"],
                    "category": doc["category"],
                }
            )

        self._collection.add(ids=ids, documents=texts, metadatas=metadatas)
        return len(ids)

    def count(self) -> int:
        return self._collection.count()

    def retrieve(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        """Semantic search over the indexed corpus. Returns up to top_k
        passages, each with doc_id, title, category, content, and a
        relevance_score in [0, 1] (higher is more relevant — Chroma's raw
        cosine distance is inverted here so callers never have to remember
        which direction is "better").

        Returns an empty list rather than padding with low-relevance
        results, per the Build Spec's "Retrieve" requirement: "Applies a
        relevance threshold and returns nothing rather than something
        irrelevant." The threshold itself is applied by the caller (the
        router, FR-04/FR-05), not baked in here, so this function always
        returns its raw top_k and lets routing decide what counts as
        "relevant enough."
        """
        if not query or not query.strip():
            return []
        n_results = min(top_k, max(self._collection.count(), 1))
        results = self._collection.query(query_texts=[query], n_results=n_results)
        if not results["ids"] or not results["ids"][0]:
            return []

        passages = []
        for doc_id, document, metadata, distance in zip(
            results["ids"][0],
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            # Chroma's cosine distance is in [0, 2]; convert to a
            # relevance score in [0, 1] where 1.0 is a perfect match.
            relevance_score = max(0.0, 1.0 - (distance / 2.0))
            passages.append(
                {
                    "doc_id": metadata["doc_id"],
                    "title": metadata["title"],
                    "category": metadata["category"],
                    "content": document,
                    "relevance_score": round(relevance_score, 4),
                }
            )
        return passages


def build_index(
    docs_path: str | Path = DEFAULT_DOCS_PATH,
    chroma_path: str | Path = DEFAULT_CHROMA_PATH,
) -> KnowledgeBaseIndexer:
    """Convenience entry point: load the corpus and index it in one call."""
    docs = load_documentation(docs_path)
    indexer = KnowledgeBaseIndexer(chroma_path=chroma_path)
    stored = indexer.index_documents(docs)
    print(f"Indexed {stored} articles from {docs_path} into {chroma_path}")
    return indexer


# Module-level convenience matching the requested signature
# `retrieve(query: str, top_k: int = 3)`. Lazily builds/loads the index on
# first call and reuses it afterwards within the process.
_default_indexer: KnowledgeBaseIndexer | None = None


def retrieve(query: str, top_k: int = 3) -> list[dict[str, Any]]:
    global _default_indexer
    if _default_indexer is None:
        _default_indexer = KnowledgeBaseIndexer()
        if _default_indexer.count() == 0:
            docs = load_documentation()
            _default_indexer.index_documents(docs)
    return _default_indexer.retrieve(query, top_k=top_k)


if __name__ == "__main__":
    indexer = build_index()
    # Smoke check using Sofia's own named wording mismatch (Interview 2):
    # the article is titled around "container health check failures" but
    # customers say "my deployment keeps dying" — this is the exact
    # findability gap discovery evidence identifies (Stage 1 Section 6,
    # Contradiction 3), so it is the right sanity check for retrieval.
    sample_query = "my deployment keeps dying and rolling back, what's going on"
    results = indexer.retrieve(sample_query, top_k=3)
    print(f"\nQuery: {sample_query!r}")
    for r in results:
        print(f"  [{r['relevance_score']:.3f}] {r['doc_id']} — {r['title']}")
