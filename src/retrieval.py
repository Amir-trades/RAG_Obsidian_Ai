from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
import json
from pathlib import Path
import re
import logging

from rank_bm25 import BM25Okapi

from src.config import Config, PROJECT_ROOT


LOGGER = logging.getLogger(__name__)
BM25_CORPUS_PATH = PROJECT_ROOT / "chroma_db" / "bm25_corpus.json"
RERANKER_CACHE_DIR = PROJECT_ROOT / "chroma_db" / "reranker_cache"


@dataclass(frozen=True)
class RetrievalResult:
    chunk_id: str
    text: str
    filename: str
    path: str
    category: str
    heading_path: str
    score: float
    retrieval_method: str


def tokenize_for_bm25(text: str) -> list[str]:
    tokens: list[str] = []
    for token in re.findall(
        r"[^\W_]+(?:[-_.][^\W_]+)*",
        text.casefold(),
        flags=re.UNICODE,
    ):
        tokens.append(token)
        tokens.extend(part for part in re.split(r"[-_]", token) if part != token)
    return tokens


def _corpus_signature(path: Path) -> tuple[str, int, int]:
    try:
        stat = path.stat()
    except OSError:
        return str(path), 0, 0
    return str(path), stat.st_mtime_ns, stat.st_size


@lru_cache(maxsize=32)
def _cached_bm25(
    path_string: str,
    modified_ns: int,
    file_size: int,
    folders: tuple[str, ...] | None,
) -> tuple[tuple[dict[str, object], ...], BM25Okapi | None]:
    del modified_ns, file_size
    path = Path(path_string)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return (), None
    if not isinstance(payload, list):
        return (), None

    documents = tuple(
        entry
        for entry in payload
        if isinstance(entry, dict)
        and (folders is None or str(entry.get("category", "")) in folders)
    )
    if not documents:
        return (), None
    tokenized = [tokenize_for_bm25(str(entry.get("text", ""))) for entry in documents]
    return documents, BM25Okapi(tokenized)


def _load_bm25_index(
    folder_filter: list[str] | None = None,
    corpus_path: Path | None = None,
) -> tuple[tuple[dict[str, object], ...], BM25Okapi | None]:
    path = corpus_path or BM25_CORPUS_PATH
    signature = _corpus_signature(path)
    folders = tuple(folder_filter) if folder_filter else None
    return _cached_bm25(*signature, folders)


def keyword_search(
    query: str,
    folder_filter: list[str] | None = None,
    top_k: int = 20,
    corpus_path: Path | None = None,
) -> list[RetrievalResult]:
    query_tokens = tokenize_for_bm25(query)
    if not query_tokens or top_k <= 0:
        return []
    documents, index = _load_bm25_index(folder_filter, corpus_path)
    if index is None:
        return []

    scores = index.get_scores(query_tokens)
    ranked = sorted(
        (
            (float(score), item)
            for item, score in zip(documents, scores)
            if score > 0
        ),
        key=lambda pair: (-pair[0], str(pair[1].get("id", ""))),
    )[:top_k]
    results: list[RetrievalResult] = []
    for score, item in ranked:
        relative_path = str(item.get("path", ""))
        results.append(
            RetrievalResult(
                chunk_id=str(item.get("id") or f"{relative_path}:{item.get('chunk_index', 0)}"),
                text=str(item.get("text", "")),
                filename=str(item.get("filename", Path(relative_path).name)),
                path=relative_path,
                category=str(item.get("category", "root")),
                heading_path=str(item.get("heading_path", "")),
                score=score,
                retrieval_method="bm25_only",
            )
        )
    return results


def _vector_search(
    query: str,
    folder_filter: list[str] | None = None,
    config: Config | None = None,
) -> list[RetrievalResult]:
    from src.config import load_config
    from src.ingest import embed_texts

    config = config or load_config()
    if not config.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY ist nicht gesetzt.")
    import chromadb

    collection = chromadb.PersistentClient(
        path=str(PROJECT_ROOT / "chroma_db")
    ).get_or_create_collection("obsidian_markdown")
    count = collection.count()
    if count == 0:
        return []

    where = None
    if folder_filter:
        where = (
            {"category": folder_filter[0]}
            if len(folder_filter) == 1
            else {"category": {"$in": folder_filter}}
        )
    query_vector = embed_texts([query], config.gemini_api_key)[0]
    response = collection.query(
        query_embeddings=[query_vector],
        n_results=min(20, count),
        where=where,
        include=["documents", "metadatas", "distances"],
    )
    documents = (response.get("documents") or [[]])[0]
    metadatas = (response.get("metadatas") or [[]])[0]
    distances = (response.get("distances") or [[]])[0]
    results: list[RetrievalResult] = []
    for index, text in enumerate(documents):
        metadata = metadatas[index] if index < len(metadatas) else {}
        path = str(metadata.get("path", ""))
        chunk_index = metadata.get("chunk_index", index)
        distance = distances[index] if index < len(distances) else 0.0
        results.append(
            RetrievalResult(
                chunk_id=f"{path}:{chunk_index}",
                text=str(text),
                filename=str(metadata.get("filename", Path(path).name)),
                path=path,
                category=str(metadata.get("category", "root")),
                heading_path=str(metadata.get("heading_path", "")),
                score=float(distance or 0.0),
                retrieval_method="vector",
            )
        )
    return results


def reciprocal_rank_fusion(
    dense_results: list[RetrievalResult],
    sparse_results: list[RetrievalResult],
    k: int = 60,
    pool_size: int = 15,
    w_dense: float = 1.0,
    w_sparse: float = 1.0,
) -> list[RetrievalResult]:
    fused: dict[str, tuple[RetrievalResult, float]] = {}
    for weight, results in (
        (w_dense, dense_results),
        (w_sparse, sparse_results),
    ):
        for rank, result in enumerate(results, start=1):
            previous = fused.get(result.chunk_id)
            total_score = (previous[1] if previous else 0.0) + weight / (k + rank)
            selected = (
                result
                if previous is None or result.retrieval_method == "vector"
                else previous[0]
            )
            fused[result.chunk_id] = (selected, total_score)

    ranked = sorted(
        fused.values(),
        key=lambda pair: (-pair[1], pair[0].chunk_id),
    )[: max(0, pool_size)]
    return [
        replace(result, score=score, retrieval_method="hybrid_rrf")
        for result, score in ranked
    ]


@lru_cache(maxsize=4)
def _get_ranker(model_name: str, cache_dir: str) -> object:
    from flashrank import Ranker

    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    return Ranker(model_name=model_name, cache_dir=cache_dir)


def rerank_candidates(
    query: str,
    candidates: list[RetrievalResult],
    top_n: int = 5,
    model_name: str = "ms-marco-MultiBERT-L-12",
    enabled: bool = True,
    mock: bool = False,
) -> list[RetrievalResult]:
    if top_n <= 0:
        return []
    fallback = [
        replace(candidate, retrieval_method="hybrid_rrf")
        for candidate in candidates[: max(0, top_n)]
    ]
    if not candidates or not enabled or mock:
        return fallback

    try:
        from flashrank import RerankRequest

        ranker = _get_ranker(model_name, str(RERANKER_CACHE_DIR))
        request = RerankRequest(
            query=query,
            passages=[
                {"id": candidate.chunk_id, "text": candidate.text}
                for candidate in candidates
            ],
        )
        ranked = ranker.rerank(request)  # type: ignore[attr-defined]
        by_id = {candidate.chunk_id: candidate for candidate in candidates}
        results: list[RetrievalResult] = []
        for item in ranked:
            candidate = by_id.get(str(item.get("id", "")))
            if candidate is None:
                continue
            score = max(0.0, min(1.0, float(item.get("score", 0.0))))
            results.append(
                replace(
                    candidate,
                    score=score,
                    retrieval_method="hybrid+rerank",
                )
            )
            if len(results) >= top_n:
                break
        return results or fallback
    except Exception as exc:
        LOGGER.warning(
            "FlashRank unavailable; using RRF ranking (%s).",
            type(exc).__name__,
        )
        return fallback


def hybrid_search_and_rerank(
    query: str,
    folder_filter: list[str] | None = None,
    top_n: int = 5,
    pool_size: int = 15,
    mock: bool = False,
) -> list[RetrievalResult]:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query muss ein nicht-leerer String sein.")
    if top_n <= 0 or pool_size <= 0:
        return []

    sparse_results = keyword_search(
        query,
        folder_filter=folder_filter,
        top_k=pool_size,
    )
    if mock:
        dense_results = []
        reranker_enabled = False
        model_name = "ms-marco-MultiBERT-L-12"
    else:
        from src.config import load_config

        config = load_config()
        dense_results = _vector_search(query, folder_filter, config)
        reranker_enabled = config.enable_reranker
        model_name = config.reranker_model
    candidates = reciprocal_rank_fusion(
        dense_results,
        sparse_results,
        pool_size=pool_size,
    )
    return rerank_candidates(
        query,
        candidates,
        top_n=top_n,
        model_name=model_name,
        enabled=reranker_enabled,
        mock=mock,
    )
