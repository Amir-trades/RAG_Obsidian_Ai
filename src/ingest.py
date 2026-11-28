from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Iterable, Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHROMA_DIR = PROJECT_ROOT / "chroma_db"
SYNC_STATE_PATH = PROJECT_ROOT / "sync_state.json"
BM25_CORPUS_PATH = CHROMA_DIR / "bm25_corpus.json"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


SKIPPED_DIRECTORY_NAMES = {
    ".obsidian",
    ".trash",
    ".copilot",
    ".smart-env",
    ".opencode",
    "anhaenge",
    "anhänge",
    "attachments",
}


@dataclass(frozen=True)
class MarkdownMetadata:
    path: Path
    category: str
    filename: str
    last_modified: float


@dataclass(frozen=True)
class MarkdownChunk:
    text: str
    path: Path
    category: str
    filename: str
    last_modified: float
    chunk_index: int
    heading_path: str = ""


@dataclass(frozen=True)
class SyncResult:
    total_files: int
    changed_files: int
    deleted_files: int
    chunks_embedded: int
    changed_paths: list[str]
    errors: list[str]


def _relative_vault_path(path: Path, vault: Path) -> str:
    try:
        relative = path.relative_to(vault)
    except ValueError:
        relative = path.resolve().relative_to(vault.resolve())
    return str(relative).replace(os.sep, "/")


def extract_markdown_metadata(
    markdown_path: str | Path,
    vault_path: str | Path,
) -> MarkdownMetadata:
    path = Path(markdown_path).expanduser()
    vault = Path(vault_path).expanduser()
    relative_path = Path(_relative_vault_path(path, vault))
    category = relative_path.parts[0] if len(relative_path.parts) > 1 else "root"

    return MarkdownMetadata(
        path=path,
        category=category,
        filename=path.name,
        last_modified=os.path.getmtime(path),
    )


def extract_metadata_for_vault(
    markdown_files: Iterable[Path],
    vault_path: str | Path,
) -> list[MarkdownMetadata]:
    vault = Path(vault_path).expanduser()
    return [extract_markdown_metadata(path, vault) for path in markdown_files]


def _window_split(text: str, max_chars: int = 800, overlap: int = 100) -> list[str]:
    text = text.strip()
    if len(text) <= max_chars:
        return [text] if text else []

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        chunks.append(text[start:end].strip())
        if end == len(text):
            break
        start = max(start + max_chars - overlap, start + 1)
    return [chunk for chunk in chunks if chunk]


def chunk_markdown_text(
    markdown_text: str,
    max_chars: int = 800,
    overlap: int = 100,
) -> list[str]:
    return [
        text
        for text, _heading_path in _chunk_markdown_text_with_headings(
            markdown_text,
            max_chars=max_chars,
            overlap=overlap,
        )
    ]


def _chunk_markdown_text_with_headings(
    markdown_text: str,
    max_chars: int = 800,
    overlap: int = 100,
) -> list[tuple[str, str]]:
    normalized = markdown_text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []

    chunks: list[tuple[str, str]] = []
    heading_stack: list[tuple[int, str]] = []
    lines: list[str] = []

    def flush_section() -> None:
        if not lines:
            return
        path = " > ".join(
            f"{'#' * level} {title}" for level, title in heading_stack
        )
        section_text = "\n".join(lines).strip()
        if not section_text:
            return
        paragraphs = [
            paragraph.strip()
            for paragraph in re.split(r"\n\s*\n", section_text)
            if paragraph.strip()
        ]
        if (
            len(paragraphs) > 1
            and re.match(r"^#{1,3}\s+\S", paragraphs[0])
        ):
            paragraphs[:2] = [f"{paragraphs[0]}\n\n{paragraphs[1]}"]
        for paragraph in paragraphs:
            block = paragraph.strip()
            for chunk in _window_split(block, max_chars, overlap):
                if chunks and chunks[-1][0] == chunk and chunks[-1][1] == path:
                    continue
                chunks.append((chunk, path))

    for line in normalized.split("\n"):
        match = re.match(r"^(#{1,3})\s+(.+?)\s*#*\s*$", line.strip())
        if match:
            flush_section()
            level = len(match.group(1))
            heading_stack = [
                (heading_level, heading)
                for heading_level, heading in heading_stack
                if heading_level < level
            ]
            heading_stack.append((level, match.group(2).strip()))
            lines.clear()
        lines.append(line)
    flush_section()

    return chunks


def chunk_markdown_file(
    markdown_path: str | Path,
    vault_path: str | Path,
    max_chars: int = 800,
    overlap: int = 100,
) -> list[MarkdownChunk]:
    path = Path(markdown_path).expanduser()
    vault = Path(vault_path).expanduser()
    metadata = extract_markdown_metadata(path, vault)
    text = path.read_text(encoding="utf-8")

    return [
        MarkdownChunk(
            text=text_chunk,
            path=path,
            category=metadata.category,
            filename=metadata.filename,
            last_modified=metadata.last_modified,
            chunk_index=index,
            heading_path=heading_path,
        )
        for index, (text_chunk, heading_path) in enumerate(
            _chunk_markdown_text_with_headings(
                text,
                max_chars=max_chars,
                overlap=overlap,
            )
        )
    ]


def load_sync_state(state_path: str | Path) -> dict[str, float]:
    path = Path(state_path)
    if not path.is_file():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

    if not isinstance(data, dict):
        return {}
    return {
        str(file_path): float(last_modified)
        for file_path, last_modified in data.items()
        if isinstance(file_path, str)
    }


def save_sync_state(
    state_path: str | Path,
    markdown_metadata: Iterable[MarkdownMetadata],
    vault_path: str | Path,
) -> None:
    path = Path(state_path)
    vault = Path(vault_path).expanduser()
    state = {
        _relative_vault_path(item.path, vault): item.last_modified
        for item in markdown_metadata
    }
    _atomic_write_json(path, state)


def _atomic_write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary_path, path)


def detect_vault_changes(
    vault_path: str | Path,
    markdown_metadata: Iterable[MarkdownMetadata],
    state: dict[str, float],
) -> tuple[list[MarkdownMetadata], list[str]]:
    vault = Path(vault_path).expanduser()
    metadata = list(markdown_metadata)
    current_state = {
        _relative_vault_path(item.path, vault): item.last_modified
        for item in metadata
    }
    changed = [
        item
        for item in metadata
        if state.get(_relative_vault_path(item.path, vault))
        != item.last_modified
    ]
    deleted = sorted(set(state) - set(current_state))
    return changed, deleted


def delete_markdown_chunks(collection: object, relative_path: str) -> None:
    collection.delete(where={"path": relative_path})  # type: ignore[attr-defined]


def embed_texts(texts: list[str], api_key: str) -> list[list[float]]:
    if not texts:
        return []
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY ist nicht gesetzt. Verwende --mock oder hinterlege "
            "den Key in .env."
        )

    from google import genai

    embeddings: list[list[float]] = []
    client = genai.Client(api_key=api_key)
    backoff_delays = (1.0, 3.0, 8.0)
    for start in range(0, len(texts), 50):
        batch = texts[start : start + 50]
        for attempt in range(len(backoff_delays) + 1):
            try:
                response = client.models.embed_content(
                    model="gemini-embedding-001",
                    contents=batch,
                )
                break
            except Exception as exc:
                status_code = getattr(exc, "status_code", None)
                code = getattr(exc, "code", None)
                error_text = str(exc).casefold()
                transient = (
                    status_code in {429, 503}
                    or code in {429, 503}
                    or "429" in error_text
                    or "503" in error_text
                    or "resource_exhausted" in error_text
                    or "service unavailable" in error_text
                    or "temporarily unavailable" in error_text
                )
                if not transient or attempt >= len(backoff_delays):
                    raise
                time.sleep(backoff_delays[attempt])
        else:
            raise RuntimeError("Embedding-Batch konnte nicht verarbeitet werden.")

        if not response.embeddings:
            raise RuntimeError("Die Embedding-API hat keine Vektoren zurückgegeben.")
        if len(response.embeddings) != len(batch):
            raise RuntimeError("Die Embedding-API lieferte eine unerwartete Vektoranzahl.")
        for item in response.embeddings:
            if item.values is None:
                raise RuntimeError("Die Embedding-API hat einen leeren Vektor zurückgegeben.")
            embeddings.append(list(item.values))
    return embeddings


def store_chunks(
    collection: object,
    chunks: Iterable[MarkdownChunk],
    api_key: str,
    vault_path: str | Path,
) -> int:
    vault = Path(vault_path).expanduser()
    chunk_list = list(chunks)
    if not chunk_list:
        return 0

    contextual_texts = []
    relative_paths: list[str] = []
    for chunk in chunk_list:
        relative_path = _relative_vault_path(chunk.path, vault)
        relative_paths.append(relative_path)
        contextual_texts.append(
            f"[Datei: {relative_path} | Abschnitt: {chunk.heading_path}]\n\n{chunk.text}"
        )
    vectors = embed_texts(contextual_texts, api_key)
    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict[str, object]] = []

    for chunk, vector, relative_path, contextual_text in zip(
        chunk_list, vectors, relative_paths, contextual_texts
    ):
        stable_id = hashlib.sha256(
            f"{relative_path}:{chunk.chunk_index}".encode("utf-8")
        ).hexdigest()
        ids.append(stable_id)
        documents.append(contextual_text)
        metadatas.append(
            {
                "path": relative_path,
                "category": chunk.category,
                "filename": chunk.filename,
                "last_modified": chunk.last_modified,
                "chunk_index": chunk.chunk_index,
                "heading_path": chunk.heading_path,
            }
        )

    collection.upsert(  # type: ignore[attr-defined]
        ids=ids,
        embeddings=vectors,
        documents=documents,
        metadatas=metadatas,
    )
    return len(chunk_list)


def _load_bm25_corpus(path: Path | None = None) -> list[dict[str, object]]:
    path = path or BM25_CORPUS_PATH
    if not path.is_file():
        return []
    try:
        corpus = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(corpus, list):
        return []
    return [entry for entry in corpus if isinstance(entry, dict)]


def _corpus_entries(
    chunks: Iterable[MarkdownChunk],
    vault_path: str | Path,
) -> list[dict[str, object]]:
    vault = Path(vault_path).expanduser()
    entries: list[dict[str, object]] = []
    for chunk in chunks:
        relative_path = _relative_vault_path(chunk.path, vault)
        entries.append(
            {
                "id": f"{relative_path}:{chunk.chunk_index}",
                "text": (
                    f"[Datei: {relative_path} | Abschnitt: {chunk.heading_path}]\n\n"
                    f"{chunk.text}"
                ),
                "path": relative_path,
                "category": chunk.category,
                "filename": chunk.filename,
                "heading_path": chunk.heading_path,
                "chunk_index": chunk.chunk_index,
                "last_modified": chunk.last_modified,
            }
        )
    return entries


def _update_bm25_corpus(
    changed_paths: set[str],
    deleted_paths: set[str],
    new_entries: Iterable[dict[str, object]],
    corpus_path: Path | None = None,
    allowed_paths: set[str] | None = None,
) -> None:
    corpus_path = corpus_path or BM25_CORPUS_PATH
    replaced_paths = changed_paths | deleted_paths
    corpus = [
        entry
        for entry in _load_bm25_corpus(corpus_path)
        if entry.get("path") not in replaced_paths
        and (allowed_paths is None or entry.get("path") in allowed_paths)
    ]
    corpus.extend(new_entries)
    corpus.sort(
        key=lambda entry: (
            str(entry.get("path", "")),
            int(entry.get("chunk_index", 0)),
        )
    )
    _atomic_write_json(corpus_path, corpus)


def _is_fatal_api_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    code = getattr(exc, "code", None)
    text = str(exc).casefold()
    return (
        status_code in {401, 403, 429}
        or code in {401, 403, 429}
        or any(
            marker in text
            for marker in (
                "resource_exhausted",
                "api_key",
                "permission_denied",
                "unauthenticated",
                "429",
                "401",
                "403",
            )
        )
    )


def sync_vault(
    vault_path: Path | None = None,
    api_key: str | None = None,
    mock: bool = False,
) -> SyncResult:
    if vault_path is None:
        from src.config import load_config

        config = load_config()
        vault = config.obsidian_vault_path
        api_key = api_key if api_key is not None else config.gemini_api_key
    else:
        vault = Path(vault_path).expanduser().resolve()
    if not vault.is_dir():
        raise RuntimeError(f"Obsidian-Vault ist kein lesbarer Ordner: {vault}")

    files = scan_markdown_files(vault)
    metadata = extract_metadata_for_vault(files, vault)
    state = load_sync_state(SYNC_STATE_PATH)
    changed, deleted = detect_vault_changes(vault, metadata, state)
    all_current_paths = {_relative_vault_path(item.path, vault) for item in metadata}
    changed_paths = {_relative_vault_path(item.path, vault) for item in changed}

    chunks_embedded = 0
    errors: list[str] = []
    completed_paths: list[str] = []
    collection: Any | None = None
    if not mock:
        try:
            import chromadb

            collection = chromadb.PersistentClient(path=str(CHROMA_DIR)).get_or_create_collection(
                "obsidian_markdown"
            )
            for relative_path in deleted:
                delete_markdown_chunks(collection, relative_path)
        except Exception as exc:
            raise RuntimeError(f"ChromaDB konnte nicht initialisiert werden: {exc}") from exc

    working_state = {
        path: mtime
        for path, mtime in state.items()
        if path not in set(deleted) and path in all_current_paths
    }
    if deleted or any(path not in all_current_paths for path in state):
        _update_bm25_corpus(
            set(),
            set(deleted),
            [],
            allowed_paths=all_current_paths,
        )
        _atomic_write_json(SYNC_STATE_PATH, working_state)

    prepared_files: list[tuple[MarkdownMetadata, str, list[MarkdownChunk]]] = []
    for item in changed:
        rel_path = _relative_vault_path(item.path, vault)
        try:
            chunks = chunk_markdown_file(item.path, vault)
        except (OSError, UnicodeDecodeError, ValueError, RuntimeError) as exc:
            errors.append(f"{rel_path}: {type(exc).__name__}: {str(exc).splitlines()[0][:120]}")
            continue
        prepared_files.append((item, rel_path, chunks))

    batch_size = 50
    batch_items: list[tuple[MarkdownMetadata, str, list[MarkdownChunk]]] = []
    batch_chunks: list[MarkdownChunk] = []

    def flush_batch() -> bool:
        nonlocal chunks_embedded
        if not batch_items:
            return True

        batch_paths = {rel_path for _item, rel_path, _chunks in batch_items}
        batch_entries: list[dict[str, object]] = []
        for _item, _rel_path, file_chunks in batch_items:
            batch_entries.extend(_corpus_entries(file_chunks, vault))

        if not mock and collection is not None:
            try:
                for rel_path in sorted(batch_paths):
                    delete_markdown_chunks(collection, rel_path)
                if batch_chunks:
                    chunks_embedded += store_chunks(
                        collection,
                        batch_chunks,
                        api_key or "",
                        vault,
                    )
            except Exception as exc:
                short = str(exc).splitlines()[0][:140] if str(exc) else type(exc).__name__
                sample_path = next(iter(sorted(batch_paths)), "batch")
                errors.append(f"{sample_path}: {type(exc).__name__}: {short}")
                return not _is_fatal_api_error(exc)

        _update_bm25_corpus(
            batch_paths,
            set(deleted),
            batch_entries,
            allowed_paths=all_current_paths,
        )
        for item, rel_path, _file_chunks in batch_items:
            working_state[rel_path] = item.last_modified
            completed_paths.append(rel_path)
        _atomic_write_json(SYNC_STATE_PATH, working_state)
        return True

    for item, rel_path, file_chunks in prepared_files:
        if batch_chunks and len(batch_chunks) + len(file_chunks) > batch_size:
            should_continue = flush_batch()
            batch_items = []
            batch_chunks = []
            if not should_continue:
                break
        batch_items.append((item, rel_path, file_chunks))
        batch_chunks.extend(file_chunks)
    else:
        flush_batch()

    if not changed and not deleted and not SYNC_STATE_PATH.is_file():
        save_sync_state(SYNC_STATE_PATH, metadata, vault)
    elif not changed and not deleted:
        _update_bm25_corpus(set(), set(), [], allowed_paths=all_current_paths)

    return SyncResult(
        total_files=len(metadata),
        changed_files=len(changed),
        deleted_files=len(deleted),
        chunks_embedded=chunks_embedded,
        changed_paths=sorted(set(completed_paths) | set(deleted))
        if errors
        else sorted(changed_paths | set(deleted)),
        errors=errors,
    )


def reindex_single_file(
    file_path: Path,
    vault_path: Path,
    api_key: str | None = None,
    mock: bool = False,
) -> int:
    vault = Path(vault_path).expanduser().resolve()
    resolved_file = Path(file_path).expanduser().resolve()
    try:
        relative_path = str(resolved_file.relative_to(vault)).replace(os.sep, "/")
    except ValueError as exc:
        raise ValueError("Die Datei liegt außerhalb des Obsidian-Vault.") from exc
    if not resolved_file.is_file() or resolved_file.suffix.casefold() != ".md":
        raise ValueError("Es kann nur eine vorhandene Markdown-Datei reindexiert werden.")

    chunks = chunk_markdown_file(resolved_file, vault)
    collection: Any | None = None
    if not mock:
        import chromadb

        collection = chromadb.PersistentClient(path=str(CHROMA_DIR)).get_or_create_collection(
            "obsidian_markdown"
        )
        delete_markdown_chunks(collection, relative_path)
        chunk_count = store_chunks(collection, chunks, api_key or "", vault)
    else:
        chunk_count = len(chunks)

    _update_bm25_corpus(
        {relative_path},
        set(),
        _corpus_entries(chunks, vault),
    )
    state = load_sync_state(SYNC_STATE_PATH)
    state[relative_path] = resolved_file.stat().st_mtime
    _atomic_write_json(SYNC_STATE_PATH, state)
    return chunk_count


def scan_markdown_files(vault_path: str | Path) -> list[Path]:
    vault = Path(vault_path).expanduser()
    if not vault.is_dir():
        raise RuntimeError(f"Obsidian-Vault ist kein lesbarer Ordner: {vault}")

    markdown_files: list[Path] = []
    for root, directory_names, file_names in os.walk(vault):
        root_path = Path(root)
        directory_names[:] = [
            name
            for name in directory_names
            if name.casefold() not in SKIPPED_DIRECTORY_NAMES
            and not name.startswith(".")
        ]

        for file_name in file_names:
            if file_name.casefold().endswith(".md"):
                markdown_files.append(root_path / file_name)

    return sorted(markdown_files, key=lambda path: str(path).casefold())


def format_scan_result(files: Iterable[Path], vault_path: Path) -> str:
    count = 0
    lines = []
    for path in files:
        count += 1
        lines.append(str(path.relative_to(vault_path)))
    return f"{count} Markdown-Dateien gefunden:\n" + "\n".join(lines)


def read_mock_vault_path() -> Path:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.is_file():
        raise RuntimeError("Mock-Modus benötigt eine vorhandene .env-Datei.")

    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")

    raw_path = values.get("OBSIDIAN_VAULT_PATH")
    if not raw_path:
        raise RuntimeError("OBSIDIAN_VAULT_PATH ist in der .env-Datei nicht gesetzt.")
    return Path(raw_path).expanduser()


def main() -> int:
    parser = argparse.ArgumentParser(description="Obsidian Markdown-Dateien scannen")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Lokalen Scan ausführen, ohne Gemini- oder Embedding-API zu verwenden",
    )
    args = parser.parse_args()

    try:
        if args.mock:
            vault_path = read_mock_vault_path()
            api_key = ""
        else:
            from src.config import ConfigError, load_config

            try:
                config = load_config()
                vault_path = config.obsidian_vault_path
                api_key = config.gemini_api_key
            except ConfigError as exc:
                print(exc)
                return 1
    except RuntimeError as exc:
        print(exc)
        return 1

    if args.mock:
        files = scan_markdown_files(vault_path)
        metadata = extract_metadata_for_vault(files, vault_path)
        categories = Counter(item.category for item in metadata)
        changed, deleted = detect_vault_changes(
            vault_path,
            metadata,
            load_sync_state(SYNC_STATE_PATH),
        )
        print(f"{len(metadata)} Markdown-Dateien mit Metadaten gefunden")
        for category in sorted(categories):
            print(f"{category}: {categories[category]}")
        print(f"{len(changed)} neue oder geänderte Dateien")
        print(f"{len(deleted)} gelöschte Dateien")
        mock_chunk_count = sum(
            len(chunk_markdown_file(item.path, vault_path))
            for item in metadata
        )
        print(f"{mock_chunk_count} Mock-Chunks erzeugt")
        print("Mock-Modus: keine ChromaDB-Änderung und kein sync_state.json-Schreiben")
        return 0

    try:
        result = sync_vault(vault_path=vault_path, api_key=api_key)
    except Exception as exc:
        print(f"Ingestion fehlgeschlagen: {type(exc).__name__}: {exc}")
        return 1

    print(f"{result.total_files} Markdown-Dateien mit Metadaten gefunden")
    print(f"{result.changed_files} neue oder geänderte Dateien")
    print(f"{result.deleted_files} gelöschte Dateien")
    print(f"{result.chunks_embedded} Chunks eingebettet")
    for error in result.errors:
        print(f"Datei konnte nicht verarbeitet werden: {error}")
    print("sync_state.json aktualisiert")
    return 1 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
