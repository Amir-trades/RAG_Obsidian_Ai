import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import ingest
from src.config import load_config
from src import retrieval


def test_chunk_markdown_file_tracks_heading_path(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    note = vault / "Projects" / "design.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        "# Roadmap\n\nOpening text.\n\n## Architecture\n\n"
        "### Retrieval\n\nBM25 retrieval details.",
        encoding="utf-8",
    )

    chunks = ingest.chunk_markdown_file(note, vault)

    assert [chunk.heading_path for chunk in chunks] == [
        "# Roadmap",
        "# Roadmap > ## Architecture",
        "# Roadmap > ## Architecture > ### Retrieval",
    ]


def test_embed_texts_splits_calls_into_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    from google import genai

    batch_sizes: list[int] = []

    class FakeModels:
        def embed_content(self, *, model: str, contents: list[str]) -> object:
            batch_sizes.append(len(contents))
            return SimpleNamespace(
                embeddings=[
                    SimpleNamespace(values=[float(index)])
                    for index, _ in enumerate(contents)
                ]
            )

    class FakeClient:
        def __init__(self, *, api_key: str) -> None:
            self.models = FakeModels()

    monkeypatch.setattr(genai, "Client", FakeClient)

    vectors = ingest.embed_texts([str(index) for index in range(101)], "test-key")

    assert batch_sizes == [50, 50, 1]
    assert len(vectors) == 101


def test_config_accepts_only_original_environment_values(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    env_file = tmp_path / ".env"
    env_file.write_text(
        "GEMINI_API_KEY=\n"
        "GEMINI_MODEL=gemini-2.5-flash\n"
        f"OBSIDIAN_VAULT_PATH={vault}\n"
        "DEFAULT_SAVE_FOLDER=System_Engineering\n",
        encoding="utf-8",
    )

    config = load_config(env_file)

    assert config.inbox_folder == "Inbox"
    assert config.auto_save_folders == ("Inbox",)
    assert config.export_dir == ingest.PROJECT_ROOT / "exports"
    assert config.enable_reranker is True
    assert config.memory_recent_turns == 8


def test_mock_sync_builds_and_updates_bm25_corpus_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "Inbox.md"
    note.write_text("# Notes\n\nFirst version.", encoding="utf-8")
    corpus_path = tmp_path / "bm25_corpus.json"
    state_path = tmp_path / "sync_state.json"
    monkeypatch.setattr(ingest, "BM25_CORPUS_PATH", corpus_path)
    monkeypatch.setattr(ingest, "SYNC_STATE_PATH", state_path)

    first = ingest.sync_vault(vault_path=vault, mock=True)
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    assert first.changed_files == 1
    assert corpus[0]["heading_path"] == "# Notes"
    assert "[Datei: Inbox.md | Abschnitt: # Notes]" in corpus[0]["text"]
    assert state_path.is_file()
    assert list(tmp_path.glob(".*.tmp")) == []

    note.write_text("# Notes\n\nUpdated version.", encoding="utf-8")
    second = ingest.sync_vault(vault_path=vault, mock=True)
    assert second.changed_files == 1
    updated = json.loads(corpus_path.read_text(encoding="utf-8"))
    assert len(updated) == 1
    assert "Updated version" in updated[0]["text"]


def test_single_file_reindex_updates_only_target_corpus_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    first_note = vault / "first.md"
    second_note = vault / "second.md"
    first_note.write_text("# First\n\nOriginal.", encoding="utf-8")
    second_note.write_text("# Second\n\nKeep this.", encoding="utf-8")
    corpus_path = tmp_path / "bm25_corpus.json"
    state_path = tmp_path / "sync_state.json"
    ingest._atomic_write_json(
        corpus_path,
        [
            {"id": "first.md:0", "path": "first.md", "chunk_index": 0, "text": "old"},
            {"id": "second.md:0", "path": "second.md", "chunk_index": 0, "text": "keep"},
        ],
    )
    ingest._atomic_write_json(state_path, {"second.md": second_note.stat().st_mtime})
    monkeypatch.setattr(ingest, "BM25_CORPUS_PATH", corpus_path)
    monkeypatch.setattr(ingest, "SYNC_STATE_PATH", state_path)
    first_note.write_text("# First\n\nReindexed.", encoding="utf-8")

    chunk_count = ingest.reindex_single_file(first_note, vault, mock=True)

    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    assert chunk_count == 1
    assert {entry["path"] for entry in corpus} == {"first.md", "second.md"}
    assert any("Reindexed" in entry["text"] for entry in corpus)
    assert json.loads(state_path.read_text(encoding="utf-8"))["second.md"] == (
        second_note.stat().st_mtime
    )


def test_single_file_reindex_rejects_path_outside_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")

    with pytest.raises(ValueError, match="außerhalb"):
        ingest.reindex_single_file(outside, vault, mock=True)


def test_tokenizer_preserves_and_splits_specialist_terms() -> None:
    tokens = retrieval.tokenize_for_bm25(
        "MOD-104 System_Engineering gemini-2.5-flash"
    )

    assert {"mod-104", "mod", "104", "system_engineering", "system",
            "engineering", "gemini-2.5-flash", "gemini", "2.5", "flash"} <= set(tokens)


def test_bm25_finds_exact_identifiers_and_filters_folders(tmp_path: Path) -> None:
    corpus = tmp_path / "bm25_corpus.json"
    corpus.write_text(
        json.dumps(
            [
                {
                    "id": "System_Engineering/spec.md:0",
                    "text": "MOD-104 defines the System_Engineering interface.",
                    "path": "System_Engineering/spec.md",
                    "category": "System_Engineering",
                    "filename": "spec.md",
                    "heading_path": "# MOD-104",
                    "chunk_index": 0,
                },
                {
                    "id": "System_Engineering/overview.md:0",
                    "text": "The general architecture has several components.",
                    "path": "System_Engineering/overview.md",
                    "category": "System_Engineering",
                    "filename": "overview.md",
                    "heading_path": "",
                    "chunk_index": 0,
                },
                {
                    "id": "System_Engineering/operations.md:0",
                    "text": "Operations follow the common engineering process.",
                    "path": "System_Engineering/operations.md",
                    "category": "System_Engineering",
                    "filename": "operations.md",
                    "heading_path": "",
                    "chunk_index": 0,
                },
                {
                    "id": "Inbox/other.md:0",
                    "text": "General project notes without this identifier.",
                    "path": "Inbox/other.md",
                    "category": "Inbox",
                    "filename": "other.md",
                    "heading_path": "",
                    "chunk_index": 0,
                },
            ]
        ),
        encoding="utf-8",
    )

    results = retrieval.keyword_search(
        "MOD-104",
        folder_filter=["System_Engineering"],
        corpus_path=corpus,
    )

    assert len(results) == 1
    assert results[0].chunk_id == "System_Engineering/spec.md:0"
    assert results[0].category == "System_Engineering"


def test_reciprocal_rank_fusion_combines_and_deduplicates_results() -> None:
    dense = retrieval.RetrievalResult(
        chunk_id="Inbox/a.md:0",
        text="Dense",
        filename="a.md",
        path="Inbox/a.md",
        category="Inbox",
        heading_path="# A",
        score=0.1,
        retrieval_method="vector",
    )
    sparse = retrieval.RetrievalResult(
        chunk_id="Inbox/a.md:0",
        text="Sparse",
        filename="a.md",
        path="Inbox/a.md",
        category="Inbox",
        heading_path="# A",
        score=2.0,
        retrieval_method="bm25_only",
    )
    sparse_only = retrieval.RetrievalResult(
        chunk_id="Inbox/b.md:0",
        text="Keyword-only",
        filename="b.md",
        path="Inbox/b.md",
        category="Inbox",
        heading_path="# B",
        score=1.0,
        retrieval_method="bm25_only",
    )

    fused = retrieval.reciprocal_rank_fusion(
        [dense],
        [sparse, sparse_only],
        pool_size=10,
    )

    assert [result.chunk_id for result in fused] == [
        "Inbox/a.md:0",
        "Inbox/b.md:0",
    ]
    assert fused[0].text == "Dense"
    assert fused[0].score == pytest.approx((1 / 61) + (1 / 61))
    assert all(result.retrieval_method == "hybrid_rrf" for result in fused)


def test_reranker_falls_back_to_rrf_when_disabled_or_mock() -> None:
    candidate = retrieval.RetrievalResult(
        chunk_id="Inbox/a.md:0",
        text="Fallback",
        filename="a.md",
        path="Inbox/a.md",
        category="Inbox",
        heading_path="",
        score=0.03,
        retrieval_method="hybrid_rrf",
    )

    result = retrieval.rerank_candidates(
        "query",
        [candidate],
        enabled=True,
        mock=True,
    )

    assert result == [candidate]


def test_sync_vault_batches_across_files_and_checkpoints_on_partial_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import chromadb

    vault = tmp_path / "vault"
    vault.mkdir()
    for index in range(3):
        (vault / f"note_{index}.md").write_text(
            f"# Note {index}\n\n" + ("word " * 190),
            encoding="utf-8",
        )

    chroma_dir = tmp_path / "chroma"
    corpus_path = tmp_path / "bm25_corpus.json"
    state_path = tmp_path / "sync_state.json"
    ingest._atomic_write_json(
        corpus_path,
        [{"id": "stale.md:0", "path": "stale.md", "chunk_index": 0, "text": "stale"}],
    )
    monkeypatch.setattr(ingest, "CHROMA_DIR", chroma_dir)
    monkeypatch.setattr(ingest, "BM25_CORPUS_PATH", corpus_path)
    monkeypatch.setattr(ingest, "SYNC_STATE_PATH", state_path)

    class FakeCollection:
        def __init__(self) -> None:
            self.upserted: list[str] = []

        def delete(self, *, where: dict[str, object]) -> None:
            return None

        def upsert(self, *, ids, embeddings, documents, metadatas) -> None:
            self.upserted.extend(ids)

    fake_collection = FakeCollection()

    class FakeClient:
        def __init__(self, *, path: str) -> None:
            pass

        def get_or_create_collection(self, name: str) -> FakeCollection:
            return fake_collection

    monkeypatch.setattr(chromadb, "PersistentClient", FakeClient)

    embed_calls: list[int] = []

    def fake_embed_texts(texts: list[str], api_key: str) -> list[list[float]]:
        embed_calls.append(len(texts))
        return [[0.1, 0.2] for _ in texts]

    monkeypatch.setattr(ingest, "embed_texts", fake_embed_texts)

    result = ingest.sync_vault(vault_path=vault, api_key="test-key", mock=False)
    assert result.errors == []
    assert len(embed_calls) == 1
    assert result.chunks_embedded == sum(embed_calls)
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    assert all(entry["path"] != "stale.md" for entry in corpus)
    assert len(json.loads(state_path.read_text(encoding="utf-8"))) == 3

    # Now test partial failure checkpointing with 60 chunks across 2 batches
    big_vault = tmp_path / "big_vault"
    big_vault.mkdir()
    for index in range(30):
        (big_vault / f"big_{index:02d}.md").write_text(
            f"# Big {index}\n\n" + ("alpha " * 190) + "\n\n" + ("beta " * 190),
            encoding="utf-8",
        )

    state_path.unlink()
    corpus_path.unlink()
    call_count = 0

    class QuotaError(Exception):
        status_code = 429

    def failing_second_batch(texts: list[str], api_key: str) -> list[list[float]]:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise QuotaError("429 RESOURCE_EXHAUSTED")
        return [[0.1] for _ in texts]

    monkeypatch.setattr(ingest, "embed_texts", failing_second_batch)
    partial = ingest.sync_vault(vault_path=big_vault, api_key="test-key", mock=False)

    assert len(partial.errors) == 1
    assert "QuotaError" in partial.errors[0]
    saved_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert 0 < len(saved_state) < 30
    assert corpus_path.is_file()
