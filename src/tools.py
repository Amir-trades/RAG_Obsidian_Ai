from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
import sys
import unicodedata

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def sanitize_filename(filename: str) -> str:
    if not isinstance(filename, str):
        raise TypeError("filename muss ein String sein.")

    name = unicodedata.normalize("NFC", filename)
    name = re.sub(r"[/\\:]", "-", name)
    name = re.sub(r'[<>\"|?*\x00-\x1f]', "", name)
    name = re.sub(r"\s+", " ", name).strip(" .")

    if not name:
        return "untitled"
    return name


def _log_used_sources(
    session_state: object,
    results: list[dict[str, object]],
) -> None:
    if session_state is None:
        return

    sources: list[dict[str, object]] = []
    for result in results:
        source = {
            "filename": result["filename"],
            "path": result["path"],
            "category": result["category"],
        }
        if source not in sources:
            sources.append(source)

    try:
        session_state["used_sources"] = sources  # type: ignore[index]
    except (AttributeError, KeyError, TypeError):
        pass


@dataclass(frozen=True)
class NoteSaveProposal:
    content: str
    target_folder: str
    filename: str
    full_path: str


def _resolve_vault_path(vault_path: str | Path | None = None) -> Path:
    if vault_path is not None:
        return Path(vault_path).expanduser()

    from src.config import load_config

    return load_config().obsidian_vault_path


def _safe_relative_folder(target_folder: str) -> str:
    if not isinstance(target_folder, str) or not target_folder.strip():
        raise ValueError("target_folder muss ein nicht-leerer String sein.")

    normalized = target_folder.strip().replace("\\", "/")
    parts = [
        sanitize_filename(part)
        for part in normalized.split("/")
        if part and part not in {".", ".."}
    ]
    if not parts:
        raise ValueError("target_folder enthält keinen gültigen Ordnernamen.")
    return "/".join(parts)


def propose_note_save(
    content: str,
    target_folder: str = "System_Engineering",
    filename: str = "Neue Notiz.md",
    vault_path: str | Path | None = None,
) -> NoteSaveProposal:
    if not isinstance(content, str) or not content.strip():
        raise ValueError("content muss ein nicht-leerer String sein.")

    vault = _resolve_vault_path(vault_path).resolve()
    relative_folder = _safe_relative_folder(target_folder)
    safe_name = sanitize_filename(filename)
    if not safe_name.lower().endswith(".md"):
        safe_name += ".md"

    full_path = (vault / relative_folder / safe_name).resolve()
    try:
        full_path.relative_to(vault)
    except ValueError as exc:
        raise ValueError("Der Zielpfad liegt außerhalb des Obsidian-Vault.") from exc

    return NoteSaveProposal(
        content=content,
        target_folder=relative_folder,
        filename=safe_name,
        full_path=str(full_path),
    )


def execute_note_save(
    proposal: NoteSaveProposal | dict[str, object],
    confirmed: bool = False,
    vault_path: str | Path | None = None,
) -> str:
    if not confirmed:
        raise PermissionError(
            "Speichern nicht erlaubt: Es ist keine explizite Benutzer-Bestätigung vorhanden."
        )

    if isinstance(proposal, NoteSaveProposal):
        content = proposal.content
        full_path = Path(proposal.full_path)
    else:
        content = proposal.get("content")
        full_path_value = proposal.get("full_path")
        if not isinstance(content, str) or not isinstance(full_path_value, str):
            raise ValueError("Der Speicher-Vorschlag ist unvollständig.")
        content = content
        full_path = Path(full_path_value)

    vault = _resolve_vault_path(vault_path).resolve()
    resolved_path = full_path.expanduser().resolve()
    try:
        resolved_path.relative_to(vault)
    except ValueError as exc:
        raise ValueError("Der Zielpfad liegt außerhalb des Obsidian-Vault.") from exc

    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_path.write_text(content, encoding="utf-8")
    return str(resolved_path)


def create_downloadable_file(
    content: str,
    filename: str,
    file_type: str = "md",
) -> str:
    if not isinstance(content, str):
        raise TypeError("content muss ein String sein.")

    normalized_type = file_type.lower().lstrip(".")
    if normalized_type not in {"docx", "md", "txt"}:
        raise ValueError("file_type muss docx, md oder txt sein.")

    safe_name = sanitize_filename(filename)
    if safe_name.lower().endswith((".docx", ".md", ".txt")):
        safe_name = re.sub(
            r"\.(docx|md|txt)$",
            "",
            safe_name,
            flags=re.IGNORECASE,
        )
    safe_name = f"{safe_name}.{normalized_type}"

    if normalized_type == "docx":
        from src.exporter import markdown_to_docx

        return markdown_to_docx(content, safe_name)

    export_dir = PROJECT_ROOT / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    output_path = (export_dir / safe_name).resolve()
    if output_path.parent != export_dir.resolve():
        raise ValueError("Der Dateiname enthält einen ungültigen Pfad.")
    output_path.write_text(content, encoding="utf-8")
    return str(output_path)


def search_vault(
    query: str,
    folder_filter: list[str] | tuple[str, ...] | None = None,
    session_state: object = None,
    mock: bool = False,
) -> list[dict[str, object]]:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query muss ein nicht-leerer String sein.")

    normalized_filter = (
        [name for name in folder_filter if name]
        if folder_filter is not None
        else None
    )

    if mock:
        results: list[dict[str, object]] = [
            {
                "text": "Mock-Text: Das Obsidian-Vault enthält einen passenden Wissenschunk.",
                "filename": "Mock-Notiz.md",
                "path": "System_Engineering/Mock-Notiz.md",
                "category": "System_Engineering",
                "distance": 0.0,
            }
        ]
        _log_used_sources(session_state, results)
        return results

    try:
        import chromadb

        from src.config import ConfigError, load_config
        from src.ingest import embed_texts

        config = load_config()
        if not config.gemini_api_key:
            raise RuntimeError(
                "GEMINI_API_KEY ist nicht gesetzt. Verwende mock=True für einen lokalen Test."
            )

        client = chromadb.PersistentClient(path=str(PROJECT_ROOT / "chroma_db"))
        collection = client.get_or_create_collection("obsidian_markdown")
        query_vector = embed_texts([query.strip()], config.gemini_api_key)[0]
        where = None
        if normalized_filter:
            where = (
                {"category": normalized_filter[0]}
                if len(normalized_filter) == 1
                else {"category": {"$in": normalized_filter}}
            )

        response = collection.query(
            query_embeddings=[query_vector],
            n_results=5,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
    except ConfigError as exc:
        raise RuntimeError(str(exc)) from exc
    except ImportError as exc:
        raise RuntimeError(
            "ChromaDB oder google-genai ist nicht verfügbar."
        ) from exc

    documents = (response.get("documents") or [[]])[0]
    metadatas = (response.get("metadatas") or [[]])[0]
    distances = (response.get("distances") or [[]])[0]
    results = []
    for index, document in enumerate(documents):
        metadata = metadatas[index] if index < len(metadatas) else {}
        distance = distances[index] if index < len(distances) else None
        results.append(
            {
                "text": document,
                "filename": metadata.get("filename", "unbekannt"),
                "path": metadata.get("path", ""),
                "category": metadata.get("category", "root"),
                "distance": distance,
            }
        )

    _log_used_sources(session_state, results)
    return results


def _python_function_declarations() -> list[object]:
    from google.genai import types

    return [
            types.FunctionDeclaration(
                name="search_vault",
                description="Semantische Suche im lokalen Obsidian-Vault.",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "folder_filter": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["query"],
                },
            ),
            types.FunctionDeclaration(
                name="propose_note_save",
                description="Schlägt eine Markdown-Notiz zum Speichern vor.",
                parameters={
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "target_folder": {"type": "string"},
                        "filename": {"type": "string"},
                    },
                    "required": ["content", "filename"],
                },
            ),
            types.FunctionDeclaration(
                name="execute_note_save",
                description="Speichert einen zuvor bestätigten NoteSaveProposal.",
                parameters={
                    "type": "object",
                    "properties": {
                        "proposal": {"type": "object"},
                    },
                    "required": ["proposal"],
                },
            ),
            types.FunctionDeclaration(
                name="create_downloadable_file",
                description="Erstellt eine DOCX-, Markdown- oder TXT-Datei.",
                parameters={
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "filename": {"type": "string"},
                        "file_type": {
                            "type": "string",
                            "enum": ["docx", "md", "txt"],
                        },
                    },
                    "required": ["content", "filename", "file_type"],
                },
            ),
    ]


def build_generate_content_config(
    enable_google_search: bool = True,
    combined_tools: bool = True,
) -> object:
    from google.genai import types

    own_tools = types.Tool(
        function_declarations=_python_function_declarations()
    )
    if not enable_google_search:
        return types.GenerateContentConfig(tools=[own_tools])

    if combined_tools:
        return types.GenerateContentConfig(
            tools=[
                types.Tool(
                    function_declarations=_python_function_declarations(),
                    google_search=types.GoogleSearch(),
                )
            ]
        )

    return types.GenerateContentConfig(
        tools=[types.Tool(function_declarations=_python_function_declarations())]
    )


def build_two_step_configs() -> tuple[object, object]:
    from google.genai import types

    own_config = types.GenerateContentConfig(
        tools=[
            types.Tool(function_declarations=_python_function_declarations())
        ]
    )
    web_config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())]
    )
    return own_config, web_config


def main() -> int:
    parser = argparse.ArgumentParser(description="Dateinamen sicher bereinigen")
    parser.add_argument("--mock", action="store_true", help="Beispielnamen lokal prüfen")
    args = parser.parse_args()

    if not args.mock:
        print("Hinweis: Verwende --mock für einen lokalen Dateinamentest.")
        return 0

    for example in ("Projekt: PLAN / Draft", "Meine\\Notiz:2", ""):
        print(sanitize_filename(example))

    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as mock_vault:
        proposal = propose_note_save(
            "# Mock-Learning\n\nErfolg.",
            target_folder="System_Engineering",
            filename="Lernen: Test",
            vault_path=mock_vault,
        )
        try:
            execute_note_save(proposal, confirmed=False, vault_path=mock_vault)
        except PermissionError:
            pass
        else:
            raise AssertionError("Speichern ohne Bestätigung muss fehlschlagen.")
        saved_path = execute_note_save(
            proposal,
            confirmed=True,
            vault_path=mock_vault,
        )
        if not Path(saved_path).is_file():
            raise AssertionError("Bestätigte Notiz wurde nicht gespeichert.")
        print("Write-Back Mock erfolgreich")

    for file_type in ("docx", "md", "txt"):
        created = create_downloadable_file(
            "# Mock-Datei\n\n**Lokaler Export**",
            f"Mock Download:{file_type}",
            file_type,
        )
        if not Path(created).is_file():
            raise AssertionError(f"{file_type}-Download wurde nicht erstellt.")
        print(created)

    session_state: dict[str, object] = {}
    results = search_vault(
        "Projektarchitektur",
        folder_filter=["System_Engineering", "Studium"],
        session_state=session_state,
        mock=True,
    )
    print(f"{len(results)} Mock-Suchergebnisse")
    print(session_state["used_sources"])

    combined = build_generate_content_config(combined_tools=True)
    own_config, web_config = build_two_step_configs()
    if not combined.tools or not own_config.tools or not web_config.tools:
        raise AssertionError("Tool-Konfigurationen sind unvollständig.")
    print("Combined/Fallback Tool-Konfigurationen erfolgreich")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
