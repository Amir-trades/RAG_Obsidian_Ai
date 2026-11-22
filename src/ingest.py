from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
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


def extract_markdown_metadata(
    markdown_path: str | Path,
    vault_path: str | Path,
) -> MarkdownMetadata:
    path = Path(markdown_path).expanduser()
    vault = Path(vault_path).expanduser()
    relative_path = path.relative_to(vault)
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
    normalized = markdown_text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []

    heading_matches = list(re.finditer(r"(?m)^#{1,3}\s+\S.*$", normalized))
    sections: list[str] = []
    if not heading_matches:
        sections = [normalized]
    else:
        if heading_matches[0].start() > 0:
            sections.append(normalized[: heading_matches[0].start()].strip())
        for index, match in enumerate(heading_matches):
            end = (
                heading_matches[index + 1].start()
                if index + 1 < len(heading_matches)
                else len(normalized)
            )
            sections.append(normalized[match.start():end].strip())

    chunks: list[str] = []
    for section in sections:
        if not section:
            continue

        lines = section.split("\n")
        first_line = lines[0].strip()
        if re.match(r"^#{1,3}\s+\S", first_line):
            blocks = [first_line]
            body = "\n".join(lines[1:]).strip()
        else:
            blocks = []
            body = section

        blocks.extend(
            paragraph.strip()
            for paragraph in re.split(r"\n\s*\n", body)
            if paragraph.strip()
        )

        buffer = ""
        for block in blocks:
            if len(block) > max_chars:
                if buffer:
                    chunks.append(buffer.strip())
                    buffer = buffer[-overlap:].strip()

                window_chunks = _window_split(block, max_chars, overlap)
                chunks.extend(window_chunks)
                buffer = window_chunks[-1][-overlap:].strip() if window_chunks else ""
                continue

            candidate = f"{buffer}\n\n{block}".strip() if buffer else block
            if len(candidate) <= max_chars:
                buffer = candidate
            else:
                if buffer:
                    chunks.append(buffer.strip())
                    tail = buffer[-overlap:].strip()
                    buffer = f"{tail}\n\n{block}".strip()
                else:
                    buffer = block

        if buffer:
            chunks.append(buffer.strip())

    unique_chunks: list[str] = []
    for chunk in chunks:
        if chunk and (not unique_chunks or chunk != unique_chunks[-1]):
            unique_chunks.append(chunk)
    return unique_chunks


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
        )
        for index, text_chunk in enumerate(
            chunk_markdown_text(text, max_chars=max_chars, overlap=overlap)
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
        str(item.path.relative_to(vault)).replace(os.sep, "/"): item.last_modified
        for item in markdown_metadata
    }
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def detect_vault_changes(
    vault_path: str | Path,
    markdown_metadata: Iterable[MarkdownMetadata],
    state: dict[str, float],
) -> tuple[list[MarkdownMetadata], list[str]]:
    vault = Path(vault_path).expanduser()
    metadata = list(markdown_metadata)
    current_state = {
        str(item.path.relative_to(vault)).replace(os.sep, "/"): item.last_modified
        for item in metadata
    }
    changed = [
        item
        for item in metadata
        if state.get(str(item.path.relative_to(vault)).replace(os.sep, "/"))
        != item.last_modified
    ]
    deleted = sorted(set(state) - set(current_state))
    return changed, deleted


def delete_markdown_chunks(collection: object, relative_path: str) -> None:
    collection.delete(where={"path": relative_path})  # type: ignore[attr-defined]


def embed_texts(texts: list[str], api_key: str) -> list[list[float]]:
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY ist nicht gesetzt. Verwende --mock oder hinterlege "
            "den Key in .env."
        )

    from google import genai

    client = genai.Client(api_key=api_key)
    response = client.models.embed_content(
        model="gemini-embedding-001",
        contents=texts,
    )
    if not response.embeddings:
        raise RuntimeError("Die Embedding-API hat keine Vektoren zurückgegeben.")
    embeddings: list[list[float]] = []
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

    vectors = embed_texts([chunk.text for chunk in chunk_list], api_key)
    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict[str, object]] = []

    for chunk, vector in zip(chunk_list, vectors):
        relative_path = str(chunk.path.relative_to(vault)).replace(os.sep, "/")
        stable_id = hashlib.sha256(
            f"{relative_path}:{chunk.chunk_index}".encode("utf-8")
        ).hexdigest()
        ids.append(stable_id)
        documents.append(chunk.text)
        metadatas.append(
            {
                "path": relative_path,
                "category": chunk.category,
                "filename": chunk.filename,
                "last_modified": chunk.last_modified,
                "chunk_index": chunk.chunk_index,
            }
        )

    collection.upsert(  # type: ignore[attr-defined]
        ids=ids,
        embeddings=vectors,
        documents=documents,
        metadatas=metadatas,
    )
    return len(chunk_list)


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

    files = scan_markdown_files(vault_path)
    metadata = extract_metadata_for_vault(files, vault_path)
    categories = Counter(item.category for item in metadata)
    state_path = PROJECT_ROOT / "sync_state.json"
    previous_state = load_sync_state(state_path)
    changed, deleted = detect_vault_changes(vault_path, metadata, previous_state)

    print(f"{len(metadata)} Markdown-Dateien mit Metadaten gefunden")
    for category in sorted(categories):
        print(f"{category}: {categories[category]}")
    print(f"{len(changed)} neue oder geänderte Dateien")
    print(f"{len(deleted)} gelöschte Dateien")

    if args.mock:
        mock_chunk_count = sum(
            len(chunk_markdown_file(item.path, vault_path))
            for item in metadata
        )
        print(f"{mock_chunk_count} Mock-Chunks erzeugt")
        print("Mock-Modus: keine ChromaDB-Änderung und kein sync_state.json-Schreiben")
        return 0

    try:
        import chromadb

        client = chromadb.PersistentClient(path=str(PROJECT_ROOT / "chroma_db"))
        collection = client.get_or_create_collection("obsidian_markdown")
    except Exception as exc:
        print(f"ChromaDB konnte nicht initialisiert werden: {exc}")
        return 1

    changed_paths = [
        str(item.path.relative_to(vault_path)).replace(os.sep, "/")
        for item in changed
    ]
    for relative_path in sorted(set(changed_paths) | set(deleted)):
        delete_markdown_chunks(collection, relative_path)

    try:
        stored_chunk_count = 0
        for item in changed:
            chunks = chunk_markdown_file(item.path, vault_path)
            stored_chunk_count += store_chunks(
                collection,
                chunks,
                api_key,
                vault_path,
            )
    except RuntimeError as exc:
        print(f"Ingestion fehlgeschlagen: {exc}")
        return 1

    print(f"{stored_chunk_count} Chunks eingebettet")
    save_sync_state(state_path, metadata, vault_path)
    print("sync_state.json aktualisiert")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
