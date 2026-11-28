import json
from pathlib import Path

import pytest

from src import chat_storage


def test_temporary_chat_never_creates_directory_or_file(tmp_path: Path) -> None:
    chats_dir = tmp_path / "not-created"
    chat = chat_storage.create_chat(is_temporary=True)

    assert chat_storage.save_chat(chat, chats_dir) is None
    assert not chats_dir.exists()


def test_v1_chat_migrates_in_memory_and_remains_readable(tmp_path: Path) -> None:
    chats_dir = tmp_path / "chats"
    chats_dir.mkdir()
    payload = {
        "id": "legacy",
        "title": "Alter Chat",
        "messages": [
            {"role": "user", "content": "Frage", "timestamp": "2024-01-01"}
        ],
        "sources": [],
    }
    chat_path = chats_dir / "legacy.json"
    chat_path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = chat_storage.load_chat("legacy", chats_dir)
    listed = chat_storage.list_chats(chats_dir)

    assert loaded["is_temporary"] is False
    assert loaded["memory"] == {
        "summary": "",
        "pinned_facts": [],
        "summarized_until_index": 0,
        "active_artifacts": [],
    }
    assert loaded["messages"][0]["downloads"] == []
    assert loaded["messages"][0]["attachments_context"] == ""
    assert listed[0]["memory"] == loaded["memory"]
    assert json.loads(chat_path.read_text(encoding="utf-8")) == payload


def test_delete_and_rename_enforce_chat_path_and_title_rules(
    tmp_path: Path,
) -> None:
    chats_dir = tmp_path / "chats"
    chat = chat_storage.create_chat(title="Original")
    chat_storage.save_chat(chat, chats_dir)

    renamed = chat_storage.rename_chat(chat["id"], "  Neuer Titel  ", chats_dir)

    assert renamed["title"] == "Neuer Titel"
    assert len(renamed["title"]) <= 80
    with pytest.raises(ValueError, match="ungültig|Ungültig"):
        chat_storage.delete_chat("../outside", chats_dir)
    with pytest.raises(ValueError, match="ungültig|Ungültig"):
        chat_storage.load_chat("../outside", chats_dir)
    with pytest.raises(ValueError, match="nicht leer"):
        chat_storage.rename_chat(chat["id"], "  ", chats_dir)
    assert chat_storage.delete_chat(chat["id"], chats_dir)
    assert not chat_storage.delete_chat(chat["id"], chats_dir)


def test_add_message_persists_per_message_metadata() -> None:
    chat = chat_storage.create_chat()
    source = {"path": "Inbox/note.md"}
    download = {"filename": "report.docx"}
    saved_note = {"path": "Inbox/note.md"}
    pending = {"proposal_id": "proposal-1"}

    chat_storage.add_message(
        chat,
        "assistant",
        "Antwort",
        sources=[source],
        downloads=[download],
        saved_notes=[saved_note],
        pending_saves=[pending],
        attachments_context="Dokumentauszug",
    )

    message = chat["messages"][0]
    assert message["sources"] == [source]
    assert message["downloads"] == [download]
    assert message["saved_notes"] == [saved_note]
    assert message["pending_saves"] == [pending]
    assert message["attachments_context"] == "Dokumentauszug"


def test_delete_all_chats_only_removes_json_files(tmp_path: Path) -> None:
    chats_dir = tmp_path / "chats"
    chats_dir.mkdir()
    (chats_dir / "one.json").write_text("{}", encoding="utf-8")
    (chats_dir / "two.json").write_text("{}", encoding="utf-8")
    (chats_dir / "keep.txt").write_text("keep", encoding="utf-8")

    assert chat_storage.delete_all_chats(chats_dir) == 2
    assert (chats_dir / "keep.txt").is_file()
    assert not list(chats_dir.glob("*.json"))


def test_rename_rejects_temporary_chat(tmp_path: Path) -> None:
    chats_dir = tmp_path / "chats"
    chats_dir.mkdir()
    chat = chat_storage.create_chat(is_temporary=True)
    (chats_dir / f"{chat['id']}.json").write_text(
        json.dumps(chat),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Temporäre"):
        chat_storage.rename_chat(chat["id"], "Titel", chats_dir)
