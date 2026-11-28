from __future__ import annotations

from dataclasses import dataclass, field
import io
import logging
import re
from typing import Any, Callable
from uuid import uuid4

from google import genai
from google.genai import types

from src.config import Config
from src.memory import (
    build_gemini_contents,
    build_system_instruction,
    maybe_compress_memory,
    summarize_attachments_for_memory,
)
from src import tools


LOGGER = logging.getLogger(__name__)
MAX_TOOL_ITERATIONS = 8


@dataclass
class TurnToolContext:
    obsidian_sources: list[dict[str, Any]] = field(default_factory=list)
    web_sources: list[dict[str, Any]] = field(default_factory=list)
    download_files: list[dict[str, Any]] = field(default_factory=list)
    pending_saves: list[dict[str, Any]] = field(default_factory=list)
    saved_notes: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class AgentTurnResult:
    reply_text: str
    obsidian_sources: list[dict[str, Any]]
    web_sources: list[dict[str, Any]]
    download_files: list[dict[str, Any]]
    pending_saves: list[dict[str, Any]]
    saved_notes: list[dict[str, Any]]
    attachments_context: str


def _explicit_write_request(prompt: str) -> bool:
    normalized = prompt.casefold()
    if re.search(
        r"\b(nicht|kein(?:e|en|er|es)?)\b.{0,30}\b"
        r"(speicher|schreib|notier|überschreib)",
        normalized,
    ):
        return False
    return bool(
        re.search(
            r"\b(speichere|speicher|schreibe|schreib|notiere|notier|"
            r"save|write|überschreib|ersetze|aktualisiere)\b",
            normalized,
        )
    )


def _explicit_update_request(prompt: str) -> bool:
    normalized = prompt.casefold()
    return _explicit_write_request(prompt) and bool(
        re.search(r"\b(überschreib|ersetze|aktualisier|update|overwrite)\w*\b", normalized)
    )


def _extract_binary_upload_text(
    client: Any,
    model_name: str,
    filename: str,
    file_part: Any,
) -> str:
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=[
                types.Part.from_text(
                    text=(
                        f"Extrahiere aus der Datei '{filename}' den vollständigen "
                        "lesbaren Inhalt als kompakten Klartext. Bewahre alle "
                        "konkreten Fakten wortgetreu (Namen, vollständige Adresse, "
                        "Telefon, E-Mail, Daten, Abschlüsse, Berufserfahrung, "
                        "Projekte, Zahlen)."
                    )
                ),
                file_part,
            ],
        )
        return str(getattr(response, "text", None) or "").strip()
    except Exception as exc:
        LOGGER.warning(
            "Binary attachment extraction failed for %s (%s).",
            filename,
            type(exc).__name__,
        )
        return ""


def _prepare_uploads(
    uploads: list[Any],
    client: Any | None = None,
    model_name: str | None = None,
    mock: bool = False,
) -> tuple[list[Any], list[str]]:
    parts: list[Any] = []
    extracted_texts: list[str] = []
    for upload in uploads:
        name = str(getattr(upload, "name", "Anhang"))
        suffix = name.rsplit(".", maxsplit=1)[-1].casefold() if "." in name else ""
        data = upload.getvalue() if hasattr(upload, "getvalue") else b""
        if suffix in {"png", "jpg", "jpeg", "webp", "pdf"}:
            mime_type = getattr(upload, "type", None) or {
                "png": "image/png",
                "jpg": "image/jpeg",
                "jpeg": "image/jpeg",
                "webp": "image/webp",
                "pdf": "application/pdf",
            }[suffix]
            file_part = types.Part.from_bytes(
                data=data,
                mime_type=mime_type,
            )
            parts.append(file_part)
            extracted = ""
            if not mock and client is not None and model_name:
                extracted = _extract_binary_upload_text(
                    client,
                    model_name,
                    name,
                    file_part,
                )
            extracted_texts.append(f"[{name}]\n{extracted}" if extracted else "")
            continue
        if suffix in {"txt", "md", "csv"}:
            text = data.decode("utf-8", errors="replace")
            extracted_texts.append(f"[{name}]\n{text}")
            parts.append(types.Part.from_text(text=f"[Anhang: {name}]\n{text}"))
            continue
        if suffix == "docx":
            from docx import Document

            document = Document(io.BytesIO(data))
            text = "\n".join(paragraph.text for paragraph in document.paragraphs)
            extracted_texts.append(f"[{name}]\n{text}")
            parts.append(types.Part.from_text(text=f"[Anhang: {name}]\n{text}"))
            continue
        extracted_texts.append("")
    return parts, extracted_texts


def _response_parts(response: Any) -> tuple[Any | None, list[Any]]:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return None, []
    content = getattr(candidates[0], "content", None)
    return content, list(getattr(content, "parts", None) or [])


def _status(callback: Callable[[str], None] | None, message: str) -> None:
    if callback is not None:
        callback(message)


def _tool_status(name: str) -> str:
    return {
        "search_vault": "Durchsuche Obsidian-Vault (Hybrid + Rerank)…",
        "read_vault_note": "Lese Obsidian-Notiz…",
        "list_vault_folders": "Prüfe Vault-Ordner…",
        "write_vault_note": "Schreibe in die Inbox…",
        "web_search": "Suche im Web…",
        "create_downloadable_file": "Erstelle Dokument…",
    }.get(name, f"Führe Tool {name} aus…")


def _dispatch_tool(
    name: str,
    arguments: dict[str, Any],
    prompt: str,
    config: Config,
    allow_auto_inbox_write: bool,
    context: TurnToolContext,
    mock: bool,
    chat: dict[str, Any],
) -> dict[str, Any]:
    if name == "search_vault":
        results = tools.search_vault(
            str(arguments.get("query", "")),
            folder_filter=arguments.get("folder_filter"),
            mock=mock,
            top_n=int(arguments.get("top_n", 5)),
            turn_context=context,
        )
        return {"results": results}
    if name == "read_vault_note":
        return tools.read_vault_note(
            str(arguments.get("relative_path", "")),
            vault_path=config.obsidian_vault_path,
        )
    if name == "list_vault_folders":
        return {"folders": tools.list_vault_folders(config.obsidian_vault_path)}
    if name == "write_vault_note":
        result = tools.write_vault_note(
            content=str(arguments.get("content", "")),
            filename=str(arguments.get("filename", "Neue Notiz.md")),
            target_folder=str(arguments.get("target_folder", config.inbox_folder)),
            mode=str(arguments.get("mode", "create")),
            auto_save=bool(arguments.get("auto_save", False)),
            allow_auto_save=(
                allow_auto_inbox_write and _explicit_write_request(prompt)
            ),
            config=config,
            vault_path=config.obsidian_vault_path,
            mock=mock,
            allow_overwrite=_explicit_update_request(prompt),
        )
        if result.get("status") == "pending_confirmation":
            proposal = result.get("proposal")
            if isinstance(proposal, dict):
                context.pending_saves.append(
                    {"proposal_id": str(uuid4()), **proposal}
                )
        elif result.get("status") == "saved":
            saved_note = {
                "filename": result.get("filename"),
                "path": result.get("relative_path"),
            }
            context.saved_notes.append(saved_note)
            memory = chat.setdefault("memory", {})
            if isinstance(memory, dict):
                artifacts = memory.setdefault("active_artifacts", [])
                if isinstance(artifacts, list) and saved_note not in artifacts:
                    artifacts.append(saved_note)
        return result
    if name == "web_search":
        if mock:
            return {"text": "Mock-Websuche: keine Live-Anfrage ausgeführt.", "sources": []}
        return tools.web_search(
            str(arguments.get("query", "")),
            api_key=config.gemini_api_key,
            model=config.gemini_model,
            turn_context=context,
        )
    if name == "create_downloadable_file":
        path = tools.create_downloadable_file(
            content=str(arguments.get("content", "")),
            filename=str(arguments.get("filename", "Aqua Export")),
            file_type=str(arguments.get("file_type", "docx")),
            doc_style=str(arguments.get("doc_style", "standard")),
            export_dir=config.export_dir,
        )
        download = {
            "filename": path.rsplit("/", maxsplit=1)[-1],
            "path": path,
            "file_type": str(arguments.get("file_type", "docx")),
        }
        context.download_files.append(download)
        memory = chat.setdefault("memory", {})
        if isinstance(memory, dict):
            artifacts = memory.setdefault("active_artifacts", [])
            if isinstance(artifacts, list) and download not in artifacts:
                artifacts.append(download)
        return {"status": "created", **download}
    raise ValueError(f"Unbekanntes Tool: {name}")


def _gemini_error(exc: Exception) -> RuntimeError:
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    text = str(exc).casefold()
    if code == 429 or "429" in text or "resource_exhausted" in text:
        return RuntimeError(
            "Das Gemini-Kontingent ist ausgeschöpft. Bitte Quota oder Billing prüfen."
        )
    return RuntimeError(
        f"Die Gemini-Anfrage ist fehlgeschlagen ({type(exc).__name__})."
    )


def _call_model(
    client: Any,
    model_name: str,
    contents: list[Any],
    config: Any,
) -> Any:
    try:
        return client.models.generate_content(
            model=model_name,
            contents=contents,
            config=config,
        )
    except Exception as exc:
        raise _gemini_error(exc) from exc


def run_agent_turn(
    prompt: str,
    chat: dict[str, Any],
    focus: str,
    model_name: str,
    uploads: list[Any],
    config: Config,
    allow_auto_inbox_write: bool = True,
    on_status: Callable[[str], None] | None = None,
    mock: bool = False,
    client: Any | None = None,
) -> AgentTurnResult:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt muss ein nicht-leerer String sein.")
    if mock and client is None:
        return AgentTurnResult(
            reply_text=(
                f"[Mock-Modus] Fokus: {focus} | Modell: {model_name}\n\n"
                f"Ich habe deine Anfrage erhalten: {prompt}"
            ),
            obsidian_sources=[],
            web_sources=[],
            download_files=[],
            pending_saves=[],
            saved_notes=[],
            attachments_context="",
        )
    if client is None:
        if not config.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY ist nicht gesetzt.")
        client = genai.Client(api_key=config.gemini_api_key)

    upload_parts, extracted_texts = _prepare_uploads(
        uploads,
        client=client,
        model_name=model_name,
        mock=mock,
    )
    attachments_context = summarize_attachments_for_memory(
        uploads,
        extracted_texts,
        max_chars=8000,
    )
    maybe_compress_memory(chat, config, client=client, mock=mock)
    contents = build_gemini_contents(
        chat,
        prompt,
        upload_parts,
        recent_limit=config.memory_recent_turns,
    )
    system_instruction = build_system_instruction(
        focus,
        chat,
        config,
        allow_auto_inbox_write,
    )
    from src.tools import _python_function_declarations

    agent_config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=[
            types.Tool(
                function_declarations=_python_function_declarations()
            )
        ],
    )
    context = TurnToolContext()
    final_text = ""
    hit_iteration_limit = False
    for _iteration in range(MAX_TOOL_ITERATIONS):
        _status(on_status, "Aqua denkt nach…")
        response = _call_model(client, model_name, contents, agent_config)
        model_content, response_parts = _response_parts(response)
        function_calls = [
            part.function_call
            for part in response_parts
            if getattr(part, "function_call", None) is not None
        ]
        if not function_calls:
            final_text = str(getattr(response, "text", None) or "").strip()
            if not final_text:
                final_text = "\n".join(
                    str(getattr(part, "text", "") or "")
                    for part in response_parts
                    if getattr(part, "text", None)
                ).strip()
            break

        if model_content is not None:
            contents.append(model_content)
        for function_call in function_calls:
            name = str(getattr(function_call, "name", ""))
            raw_arguments = getattr(function_call, "args", {}) or {}
            arguments = dict(raw_arguments)
            _status(on_status, _tool_status(name))
            try:
                result = _dispatch_tool(
                    name,
                    arguments,
                    prompt,
                    config,
                    allow_auto_inbox_write,
                    context,
                    mock,
                    chat,
                )
            except Exception as exc:
                LOGGER.warning("Agent tool failed (%s).", type(exc).__name__)
                result = {
                    "error": f"Tool konnte nicht ausgeführt werden ({type(exc).__name__})."
                }
            contents.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_function_response(
                            name=name,
                            response=result,
                        )
                    ],
                )
            )
    else:
        hit_iteration_limit = True

    if not final_text:
        _status(on_status, "Fasse Ergebnisse zusammen…")
        synthesis_prompt = (
            "Du hast alle verfügbaren Suchschritte für diesen Zug ausgeschöpft. "
            "Fasse jetzt alle bisherigen Erkenntnisse aus den Tool-Ergebnissen "
            "strukturiert und hilfreich für den Nutzer zusammen, ohne weitere "
            "Tools aufzurufen. Weise kurz darauf hin, falls du für noch tiefere "
            "Details eine gezieltere Rückfrage brauchst."
            if hit_iteration_limit
            else "Gib jetzt eine klare, hilfreiche Textantwort auf die Anfrage, "
            "ohne weitere Tools aufzurufen."
        )
        retry_contents = list(contents)
        retry_contents.append(
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=synthesis_prompt)],
            )
        )
        retry_config = types.GenerateContentConfig(
            system_instruction=system_instruction,
        )
        response = _call_model(client, model_name, retry_contents, retry_config)
        final_text = str(getattr(response, "text", None) or "").strip()
        if not final_text:
            final_text = (
                "Das Modell hat keine Textantwort erzeugt. Bitte frage erneut "
                "oder wähle ein anderes Gemini-Modell."
            )

    return AgentTurnResult(
        reply_text=final_text,
        obsidian_sources=context.obsidian_sources,
        web_sources=context.web_sources,
        download_files=context.download_files,
        pending_saves=context.pending_saves,
        saved_notes=context.saved_notes,
        attachments_context=attachments_context,
    )


def stream_final_reply(text: str):
    yield from re.findall(r"\S+\s*", text)
