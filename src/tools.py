from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
import sys
import unicodedata
from typing import Any

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
        for field in ("heading_path", "score", "retrieval_method"):
            if field in result:
                source[field] = result[field]
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
    mode: str = "create"


def _resolve_vault_path(vault_path: str | Path | None = None) -> Path:
    if vault_path is not None:
        return Path(vault_path).expanduser()

    from src.config import load_config

    return load_config().obsidian_vault_path


def _safe_relative_folder(target_folder: str) -> str:
    if not isinstance(target_folder, str) or not target_folder.strip():
        raise ValueError("target_folder muss ein nicht-leerer String sein.")

    normalized = target_folder.strip().replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise ValueError("Absolute Vault-Pfade sind nicht erlaubt.")
    raw_parts = normalized.split("/")
    if any(part in {".", ".."} for part in raw_parts):
        raise ValueError("Relative Pfadsegmente sind nicht erlaubt.")
    parts = [sanitize_filename(part) for part in raw_parts if part]
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


def _validate_vault_destination(vault: Path, destination: Path) -> Path:
    resolved = destination.expanduser().resolve()
    try:
        resolved.relative_to(vault)
    except ValueError as exc:
        raise ValueError("Der Zielpfad liegt außerhalb des Obsidian-Vault.") from exc
    return resolved


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
        mode = proposal.mode
    else:
        content = proposal.get("content")
        full_path_value = proposal.get("full_path")
        if not isinstance(content, str) or not isinstance(full_path_value, str):
            raise ValueError("Der Speicher-Vorschlag ist unvollständig.")
        full_path = Path(full_path_value)
        mode = str(proposal.get("mode", "create"))
    if mode not in {"create", "append", "overwrite"}:
        raise ValueError("Der Speicher-Vorschlag enthält einen ungültigen Modus.")

    vault = _resolve_vault_path(vault_path).resolve()
    resolved_path = _validate_vault_destination(vault, full_path)

    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_path = _validate_vault_destination(vault, resolved_path)
    if mode == "create":
        with resolved_path.open("x", encoding="utf-8") as file:
            file.write(content)
    elif mode == "append" and resolved_path.is_file():
        with resolved_path.open("a", encoding="utf-8") as file:
            file.write(f"\n\n---\n\n{content}")
    else:
        resolved_path.write_text(content, encoding="utf-8")
    return str(resolved_path)


def _proposal_dict(proposal: NoteSaveProposal) -> dict[str, str]:
    return {
        "content": proposal.content,
        "target_folder": proposal.target_folder,
        "filename": proposal.filename,
        "full_path": proposal.full_path,
        "mode": proposal.mode,
    }


def write_vault_note(
    content: str,
    filename: str,
    target_folder: str = "Inbox",
    mode: str = "create",
    auto_save: bool = False,
    allow_auto_save: bool = True,
    config: Any | None = None,
    vault_path: str | Path | None = None,
    api_key: str | None = None,
    mock: bool = False,
    allow_overwrite: bool = False,
) -> dict[str, object]:
    if not isinstance(content, str) or not content.strip():
        raise ValueError("content muss ein nicht-leerer String sein.")
    if mode not in {"create", "append", "overwrite"}:
        raise ValueError("mode muss create, append oder overwrite sein.")

    if config is None and vault_path is None:
        from src.config import load_config

        config = load_config()
    vault = Path(
        vault_path
        if vault_path is not None
        else config.obsidian_vault_path
    ).expanduser().resolve()
    if config is None:
        auto_save_folders = ("Inbox",)
        api_key = api_key or ""
    else:
        auto_save_folders = tuple(config.auto_save_folders)
        api_key = api_key if api_key is not None else config.gemini_api_key

    relative_folder = _safe_relative_folder(target_folder)
    top_level_folder = relative_folder.split("/", maxsplit=1)[0].casefold()
    allowed_folders = {folder.casefold() for folder in auto_save_folders}
    can_auto_save = (
        auto_save
        and allow_auto_save
        and top_level_folder in allowed_folders
        and (mode != "overwrite" or allow_overwrite)
    )
    safe_name = sanitize_filename(filename)
    if not safe_name.lower().endswith(".md"):
        safe_name += ".md"
    destination = _validate_vault_destination(
        vault,
        vault / relative_folder / safe_name,
    )
    if mode == "create" and destination.exists():
        stem = destination.stem
        suffix = 2
        while destination.exists():
            candidate = destination.with_name(f"{stem}_{suffix}.md")
            destination = _validate_vault_destination(vault, candidate)
            suffix += 1
        safe_name = destination.name
    proposal = NoteSaveProposal(
        content=content,
        target_folder=relative_folder,
        filename=safe_name,
        full_path=str(destination),
        mode=mode,
    )
    if not can_auto_save:
        return {
            "status": "pending_confirmation",
            "message": "Vorschlag erstellt; der Nutzer muss in der UI bestätigen.",
            "proposal": _proposal_dict(proposal),
        }

    destination.parent.mkdir(parents=True, exist_ok=True)
    _validate_vault_destination(vault, destination.parent)
    if mode == "create":
        stem = destination.stem
        suffix = 1
        while True:
            candidate = destination if suffix == 1 else destination.with_name(
                f"{stem}_{suffix}.md"
            )
            try:
                safe_candidate = _validate_vault_destination(vault, candidate)
                with safe_candidate.open("x", encoding="utf-8") as file:
                    file.write(content)
                destination = safe_candidate
                break
            except FileExistsError:
                suffix += 1
    else:
        destination = _validate_vault_destination(vault, destination)
        if mode == "append" and destination.is_file():
            with destination.open("a", encoding="utf-8") as file:
                file.write(f"\n\n---\n\n{content}")
        else:
            destination.write_text(content, encoding="utf-8")

    from src.ingest import reindex_single_file

    reindex_single_file(
        destination,
        vault,
        api_key=api_key,
        mock=mock,
    )
    relative_path = str(destination.relative_to(vault)).replace("\\", "/")
    return {
        "status": "saved",
        "filename": destination.name,
        "path": str(destination),
        "relative_path": relative_path,
        "message": "Notiz gespeichert und für die Vault-Suche reindexiert.",
    }


def read_vault_note(
    relative_path: str,
    vault_path: str | Path | None = None,
    max_chars: int = 20_000,
) -> dict[str, object]:
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ValueError("relative_path muss ein nicht-leerer String sein.")
    vault = _resolve_vault_path(vault_path).resolve()
    normalized = relative_path.replace("\\", "/")
    if (
        Path(normalized).is_absolute()
        or re.match(r"^[A-Za-z]:", normalized)
        or ".." in normalized.split("/")
    ):
        raise ValueError("Der Notizpfad enthält unzulässige Pfadsegmente.")
    note_path = _validate_vault_destination(vault, vault / normalized)
    if note_path.suffix.casefold() != ".md" or not note_path.is_file():
        raise ValueError("Die angegebene Markdown-Notiz existiert nicht.")
    text = note_path.read_text(encoding="utf-8")
    truncated = len(text) > max_chars
    return {
        "path": normalized,
        "content": text[:max_chars],
        "truncated": truncated,
    }


def list_vault_folders(
    vault_path: str | Path | None = None,
) -> list[str]:
    from src.ingest import SKIPPED_DIRECTORY_NAMES

    vault = _resolve_vault_path(vault_path).resolve()
    folders: list[str] = []
    for first_level in sorted(vault.iterdir(), key=lambda path: path.name.casefold()):
        if (
            not first_level.is_dir()
            or first_level.is_symlink()
            or first_level.name.startswith(".")
            or first_level.name.casefold() in SKIPPED_DIRECTORY_NAMES
        ):
            continue
        folders.append(first_level.name)
        for second_level in sorted(
            first_level.iterdir(),
            key=lambda path: path.name.casefold(),
        ):
            if (
                second_level.is_dir()
                and not second_level.is_symlink()
                and not second_level.name.startswith(".")
                and second_level.name.casefold() not in SKIPPED_DIRECTORY_NAMES
            ):
                folders.append(f"{first_level.name}/{second_level.name}")
    return folders


def create_downloadable_file(
    content: str,
    filename: str,
    file_type: str = "md",
    doc_style: str = "standard",
    export_dir: str | Path | None = None,
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

        return markdown_to_docx(content, safe_name, doc_style, export_dir=export_dir)

    target_dir = Path(export_dir).expanduser() if export_dir else PROJECT_ROOT / "exports"
    target_dir = target_dir.resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    output_path = (target_dir / safe_name).resolve()
    if output_path.parent != target_dir:
        raise ValueError("Der Dateiname enthält einen ungültigen Pfad.")
    output_path.write_text(content, encoding="utf-8")
    return str(output_path)


def search_vault(
    query: str,
    folder_filter: list[str] | tuple[str, ...] | None = None,
    session_state: object = None,
    mock: bool = False,
    top_n: int = 5,
    turn_context: object | None = None,
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
                "heading_path": "# Mock",
                "score": 1.0,
                "retrieval_method": "mock",
                "distance": 0.0,
            }
        ]
        _log_used_sources(session_state, results)
        if turn_context is not None and hasattr(turn_context, "obsidian_sources"):
            turn_context.obsidian_sources.extend(
                {key: value for key, value in result.items() if key != "text"}
                for result in results
            )
        return results

    try:
        from src.retrieval import hybrid_search_and_rerank

        retrieval_results = hybrid_search_and_rerank(
            query.strip(),
            folder_filter=normalized_filter,
            top_n=top_n,
        )
    except ImportError as exc:
        raise RuntimeError("Hybrid Retrieval-Abhängigkeiten sind nicht verfügbar.") from exc

    results = [
        {
            "text": result.text,
            "filename": result.filename,
            "path": result.path,
            "category": result.category,
            "heading_path": result.heading_path,
            "score": result.score,
            "retrieval_method": result.retrieval_method,
            "distance": result.score,
        }
        for result in retrieval_results
    ]

    _log_used_sources(session_state, results)
    if turn_context is not None and hasattr(turn_context, "obsidian_sources"):
        for result in results:
            source = {key: value for key, value in result.items() if key != "text"}
            if source not in turn_context.obsidian_sources:
                turn_context.obsidian_sources.append(source)
    return results


def _python_function_declarations() -> list[object]:
    from google.genai import types

    return [
        types.FunctionDeclaration(
            name="search_vault",
            description="Hybrid-Suche im lokalen Obsidian-Vault.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "folder_filter": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "top_n": {"type": "integer"},
                },
                "required": ["query"],
            },
        ),
        types.FunctionDeclaration(
            name="read_vault_note",
            description="Liest eine Markdown-Notiz innerhalb des Vaults.",
            parameters={
                "type": "object",
                "properties": {"relative_path": {"type": "string"}},
                "required": ["relative_path"],
            },
        ),
        types.FunctionDeclaration(
            name="list_vault_folders",
            description="Listet sichtbare Vault-Ordner bis zur zweiten Ebene.",
            parameters={"type": "object", "properties": {}},
        ),
        types.FunctionDeclaration(
            name="write_vault_note",
            description=(
                "Schreibt eine Notiz oder erzeugt einen UI-Bestätigungsvorschlag. "
                "Auto-Speichern ist nur auf ausdrücklichen Nutzerbefehl zulässig."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "filename": {"type": "string"},
                    "target_folder": {"type": "string"},
                    "mode": {
                        "type": "string",
                        "enum": ["create", "append", "overwrite"],
                    },
                    "auto_save": {"type": "boolean"},
                },
                "required": ["content", "filename"],
            },
        ),
        types.FunctionDeclaration(
            name="web_search",
            description="Sucht mit Gemini Google Search im Web.",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
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
                    "doc_style": {
                        "type": "string",
                        "enum": ["standard", "cover_letter", "cv"],
                    },
                },
                "required": ["content", "filename", "file_type"],
            },
        ),
    ]


def web_search(
    query: str,
    api_key: str | None = None,
    model: str | None = None,
    turn_context: object | None = None,
) -> dict[str, object]:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query muss ein nicht-leerer String sein.")
    if not api_key or not model:
        from src.config import load_config

        config = load_config()
        api_key = api_key or config.gemini_api_key
        model = model or config.gemini_model
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY ist nicht gesetzt.")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=query,
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())]
        ),
    )
    text = getattr(response, "text", None) or ""
    sources: list[dict[str, str]] = []
    for candidate in getattr(response, "candidates", []) or []:
        metadata = getattr(candidate, "grounding_metadata", None)
        for chunk in getattr(metadata, "grounding_chunks", []) or []:
            web = getattr(chunk, "web", None)
            uri = getattr(web, "uri", None)
            if not uri:
                continue
            source = {
                "title": str(getattr(web, "title", "") or uri),
                "url": str(uri),
            }
            if source not in sources:
                sources.append(source)
    if turn_context is not None and hasattr(turn_context, "web_sources"):
        turn_context.web_sources.extend(
            source for source in sources if source not in turn_context.web_sources
        )
    return {"text": text, "sources": sources}


def build_generate_content_config(
    enable_google_search: bool = True,
    combined_tools: bool = True,
) -> object:
    from google.genai import types

    del enable_google_search, combined_tools
    return types.GenerateContentConfig(
        tools=[
            types.Tool(
                function_declarations=_python_function_declarations()
            )
        ]
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

    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as export_dir:
        for file_type in ("docx", "md", "txt"):
            created = create_downloadable_file(
                "# Mock-Datei\n\n**Lokaler Export**",
                f"Mock Download:{file_type}",
                file_type,
                export_dir=export_dir,
            )
            if not Path(created).is_file():
                raise AssertionError(f"{file_type}-Download wurde nicht erstellt.")
            print(Path(created).name)

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
