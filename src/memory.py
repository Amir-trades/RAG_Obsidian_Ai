from __future__ import annotations

import json
import logging
import re
from typing import Any

from google.genai import types

from src.config import Config


LOGGER = logging.getLogger(__name__)
FOCUS_FOLDERS = {
    "Allrounder": None,
    "System-Engineering & Projekt-Architekt": [
        "System_Engineering",
        "Studium",
        "Projekte",
        "Prompts",
    ],
    "Interview- & Reflexions-Coach": ["Inbox", "Projekte"],
}


def build_system_instruction(
    focus: str,
    chat: dict[str, Any],
    config: Config,
    allow_auto_inbox_write: bool,
) -> str:
    memory = chat.get("memory")
    if not isinstance(memory, dict):
        memory = {}
    summary = str(memory.get("summary", "")).strip()
    pinned_facts = memory.get("pinned_facts", [])
    active_artifacts = memory.get("active_artifacts", [])
    folders = FOCUS_FOLDERS.get(focus)
    if folders is None:
        focus_instruction = (
            "Allrounder: Nutze das gesamte Vault, Websuche und dein eigenes "
            "Wissen passend zur Anfrage."
        )
    else:
        focus_instruction = (
            f"{focus}: Priorisiere bei der Vault-Suche diese Ordner: "
            f"{', '.join(folders)}."
        )
    if focus == "Interview- & Reflexions-Coach":
        focus_instruction += " Gib ehrliches Feedback und strukturiere Beispiele bei Bedarf nach STAR."

    instruction = [
        "Du bist Aqua, ein präziser Second-Brain-Assistent.",
        "Antworte standardmäßig auf Deutsch, ehrlich und klar. Erfinde keine "
        "Vault-Inhalte und kennzeichne Unsicherheit.",
        focus_instruction,
        "Vault-Schreibregeln: Nutze write_vault_note nur auf ausdrücklichen "
        "Schreib-/Speicherwunsch. Ohne expliziten Auftrag erstelle einen "
        "Bestätigungsvorschlag; niemals selbst eine Bestätigung umgehen.",
        (
            "Direktes Inbox-Speichern auf ausdrücklichen Befehl ist aktiviert."
            if allow_auto_inbox_write
            else "Direktes Speichern ist deaktiviert; jeder Vault-Schreibvorgang "
            "benötigt eine UI-Bestätigung."
        ),
        "Word-Export: create_downloadable_file unterstützt doc_style "
        "'standard', 'cover_letter' und 'cv'. Formatiere Anschreiben mit "
        "Absenderblock, Empfängerblock, Datumszeile (z. B. 'Ort, DD. Monat YYYY'), "
        "'**Betreff: ...**', Anrede, sauberem Fließtext und Grußformel; formatiere "
        "Lebensläufe mit '# Name', Kontaktdaten, '## Rubrik' und 2-spaltigen "
        "Markdown-Tabellen ('| Zeitraum | Station & Schwerpunkte |').",
        f"Standard-Inbox: {config.inbox_folder}. Erlaubte Auto-Save-Ordner: "
        f"{', '.join(config.auto_save_folders) or 'keine'}.",
    ]
    if summary:
        instruction.append(f"Bisherige Gesprächszusammenfassung: {summary}")
    if pinned_facts:
        instruction.append(f"Wichtige Fakten / Rahmenbedingungen: {pinned_facts}")
    if active_artifacts:
        instruction.append(f"Zuletzt erstellte Dateien/Notizen: {active_artifacts}")
    return "\n\n".join(instruction)


def _deterministic_summary(
    messages: list[dict[str, Any]],
    previous_summary: str,
) -> str:
    excerpts: list[str] = []
    if previous_summary.strip():
        excerpts.append(previous_summary.strip())
    for message in messages:
        role = str(message.get("role", ""))
        content = str(message.get("content", "")).strip()
        if not content:
            continue
        if role == "user":
            excerpt = content.splitlines()[0].strip()
            excerpts.append(f"Nutzer: {excerpt[:300]}")
        elif role == "assistant":
            sentence = re.split(r"(?<=[.!?])\s+", content, maxsplit=1)[0]
            excerpts.append(f"Aqua: {sentence[:300]}")
    return "\n".join(excerpts[-20:])


def _parse_summary_response(text: str) -> dict[str, Any] | None:
    candidate = text.strip()
    candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate, flags=re.IGNORECASE)
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def maybe_compress_memory(
    chat: dict[str, Any],
    config: Config,
    client: Any | None = None,
    mock: bool = False,
) -> bool:
    messages = chat.get("messages", [])
    if not isinstance(messages, list):
        raise ValueError("chat.messages muss eine Liste sein.")
    memory = chat.get("memory")
    if not isinstance(memory, dict):
        memory = {
            "summary": "",
            "pinned_facts": [],
            "summarized_until_index": 0,
            "active_artifacts": [],
        }
        chat["memory"] = memory
    summarized_until = max(0, int(memory.get("summarized_until_index", 0)))
    recent_count = max(0, config.memory_recent_turns)
    threshold = max(0, config.memory_summary_threshold)
    older_end = max(summarized_until, len(messages) - recent_count)
    older_messages = messages[summarized_until:older_end]
    older_characters = sum(
        len(str(message.get("content", "")))
        for message in older_messages
        if isinstance(message, dict)
    )
    should_compress = (
        len(messages) - summarized_until > threshold
        or older_characters > 18_000
    )
    if not should_compress or not older_messages:
        return False

    previous_summary = str(memory.get("summary", ""))
    parsed: dict[str, Any] | None = None
    if not mock and client is not None:
        prompt = (
            "Fasse den bisherigen Chat knapp zusammen. Bewahre wichtige "
            "Entscheidungen, offene Aufgaben, Nutzervorgaben und zuletzt "
            "erstellte Dateien/Notizen. Antworte ausschließlich als JSON mit "
            "summary (String), pinned_facts (String-Liste) und "
            "active_artifacts (String-Liste).\n\n"
            f"Bisherige Zusammenfassung:\n{previous_summary}\n\n"
            f"Zu komprimierende Nachrichten:\n"
            f"{json.dumps(older_messages, ensure_ascii=False, default=str)}"
        )
        try:
            response = client.models.generate_content(
                model=config.gemini_model,
                contents=prompt,
            )
            response_text = getattr(response, "text", None)
            if isinstance(response_text, str):
                parsed = _parse_summary_response(response_text)
        except Exception as exc:
            LOGGER.warning(
                "Memory summary request failed; using deterministic summary (%s).",
                type(exc).__name__,
            )

    if parsed is None:
        memory["summary"] = _deterministic_summary(older_messages, previous_summary)
    else:
        summary = parsed.get("summary")
        memory["summary"] = (
            summary.strip()
            if isinstance(summary, str) and summary.strip()
            else _deterministic_summary(older_messages, previous_summary)
        )
        for field in ("pinned_facts", "active_artifacts"):
            new_values = parsed.get(field, [])
            if not isinstance(new_values, list):
                continue
            existing = memory.get(field, [])
            if not isinstance(existing, list):
                existing = []
            combined = list(existing)
            for value in new_values:
                if isinstance(value, str) and value.strip() and value not in combined:
                    combined.append(value.strip())
            memory[field] = combined

    memory["summarized_until_index"] = older_end
    return True


def build_gemini_contents(
    chat: dict[str, Any],
    current_prompt: str,
    current_parts: list[Any],
    recent_limit: int = 8,
) -> list[types.Content]:
    messages = chat.get("messages", [])
    if not isinstance(messages, list):
        raise ValueError("chat.messages muss eine Liste sein.")
    memory = chat.get("memory")
    summarized_until = (
        int(memory.get("summarized_until_index", 0))
        if isinstance(memory, dict)
        else 0
    )
    recent = messages[max(summarized_until, len(messages) - max(0, recent_limit)) :]
    contents: list[types.Content] = []
    for message in recent:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue
        text = str(message.get("content", "")).strip()
        parts: list[types.Part] = []
        if text:
            parts.append(types.Part.from_text(text=text))
        if role == "user":
            attachment_context = str(message.get("attachments_context", "")).strip()
            if attachment_context:
                parts.append(
                    types.Part.from_text(
                        text=f"Kontext zu früherem Anhang:\n{attachment_context}"
                    )
                )
        if parts:
            contents.append(
                types.Content(
                    role="model" if role == "assistant" else "user",
                    parts=parts,
                )
            )

    current_content_parts: list[Any] = list(current_parts)
    if current_prompt.strip():
        current_content_parts.insert(0, types.Part.from_text(text=current_prompt))
    if current_content_parts:
        contents.append(types.Content(role="user", parts=current_content_parts))
    return contents


def summarize_attachments_for_memory(
    uploads: list[Any],
    extracted_texts: list[str],
    max_chars: int = 2500,
) -> str:
    sections: list[str] = []
    represented: set[str] = set()
    for index, text in enumerate(extracted_texts):
        excerpt = text.strip()
        if not excerpt:
            continue
        upload_name = (
            str(getattr(uploads[index], "name", f"Anhang {index + 1}"))
            if index < len(uploads)
            else f"Anhang {index + 1}"
        )
        header = f"[{upload_name}]"
        if excerpt.startswith(header):
            sections.append(excerpt)
        else:
            sections.append(f"{header}\n{excerpt}")
        represented.add(upload_name)
    for upload in uploads:
        name = str(getattr(upload, "name", "Datei"))
        if name not in represented:
            sections.append(f"[{name}]\nDateianhang; kein Textauszug verfügbar.")
    return "\n\n".join(sections)[:max_chars]


build_conversation_contents = build_gemini_contents
