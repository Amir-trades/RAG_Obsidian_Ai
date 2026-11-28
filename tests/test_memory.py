from pathlib import Path
from types import SimpleNamespace

from google.genai import types

from src.config import Config
from src.memory import (
    build_gemini_contents,
    build_system_instruction,
    maybe_compress_memory,
    summarize_attachments_for_memory,
)


def _config(
    *,
    summary_threshold: int = 10,
    recent_turns: int = 8,
) -> Config:
    return Config(
        gemini_api_key="",
        gemini_model="gemini-2.5-flash",
        obsidian_vault_path=Path("/tmp/vault"),
        default_save_folder="System_Engineering",
        memory_summary_threshold=summary_threshold,
        memory_recent_turns=recent_turns,
    )


def test_mock_compression_summarizes_old_messages_and_keeps_recent_text() -> None:
    messages = [
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"Nachricht {index}. Vollständiger Inhalt bleibt erhalten.",
        }
        for index in range(14)
    ]
    chat = {
        "messages": messages,
        "memory": {
            "summary": "",
            "pinned_facts": [],
            "summarized_until_index": 0,
            "active_artifacts": [],
        },
    }

    changed = maybe_compress_memory(chat, _config(), mock=True)

    assert changed
    assert chat["memory"]["summarized_until_index"] == 6
    assert "Nachricht 0" in chat["memory"]["summary"]
    assert chat["messages"][-8:] == messages[-8:]
    contents = build_gemini_contents(chat, "Neue Frage", [], recent_limit=8)
    assert len(contents) == 9
    assert all(isinstance(item, types.Content) for item in contents)
    assert "Nachricht 6" in contents[0].parts[0].text
    assert contents[-1].parts[0].text == "Neue Frage"


def test_compression_waits_until_threshold_is_exceeded() -> None:
    chat = {
        "messages": [
            {"role": "user", "content": f"Turn {index}"}
            for index in range(10)
        ],
        "memory": {"summary": "", "summarized_until_index": 0},
    }

    assert not maybe_compress_memory(chat, _config(), mock=True)
    assert chat["memory"]["summarized_until_index"] == 0


def test_attachment_context_is_carried_into_follow_up_contents() -> None:
    chat = {
        "messages": [
            {
                "role": "user",
                "content": "Fasse das Dokument zusammen.",
                "attachments_context": "[brief.docx]\nProjekt startet im März.",
            },
            {"role": "assistant", "content": "Der Start ist im März."},
        ],
        "memory": {"summarized_until_index": 0},
    }

    contents = build_gemini_contents(chat, "Wann startet es?", [], recent_limit=8)

    assert [content.role for content in contents] == ["user", "model", "user"]
    assert "Projekt startet im März" in contents[0].parts[1].text
    assert contents[-1].parts[0].text == "Wann startet es?"


def test_system_instruction_includes_focus_memory_and_write_controls() -> None:
    chat = {
        "memory": {
            "summary": "Der User baut ein RAG-System.",
            "pinned_facts": ["Antworten auf Deutsch."],
            "active_artifacts": ["Inbox/Projekt.md"],
        }
    }

    instruction = build_system_instruction(
        "System-Engineering & Projekt-Architekt",
        chat,
        _config(),
        allow_auto_inbox_write=False,
    )

    assert "System_Engineering, Studium, Projekte, Prompts" in instruction
    assert "Der User baut ein RAG-System" in instruction
    assert "Antworten auf Deutsch" in instruction
    assert "Inbox/Projekt.md" in instruction
    assert "deaktiviert" in instruction


def test_attachment_summaries_are_bounded_and_cover_unreadable_files() -> None:
    uploads = [
        SimpleNamespace(name="notes.txt"),
        SimpleNamespace(name="diagram.png"),
    ]

    summary = summarize_attachments_for_memory(
        uploads,
        ["X" * 3000],
    )

    assert len(summary) == 2500
    assert "[notes.txt]" in summary
    image_summary = summarize_attachments_for_memory([uploads[1]], [])
    assert "[diagram.png]" in image_summary
    assert "kein Textauszug verfügbar" in image_summary
