from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from datetime import datetime, timezone
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

CHATS_DIR = PROJECT_ROOT / "chats"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _chat_title(messages: list[dict[str, object]], title: str | None = None) -> str:
    if title and title.strip():
        return title.strip()[:80]

    for message in messages:
        if message.get("role") == "user":
            content = str(message.get("content", "")).strip()
            if content:
                first_line = content.splitlines()[0].strip()
                return first_line[:80] or "Neuer Chat"
    return "Neuer Chat"


def create_chat(
    title: str | None = None,
    messages: list[dict[str, object]] | None = None,
    sources: list[dict[str, object]] | None = None,
    is_temporary: bool = False,
) -> dict[str, object]:
    now = _utc_now()
    chat_messages = list(messages or [])
    chat_id = str(uuid4())
    return {
        "id": chat_id,
        "title": _chat_title(chat_messages, title),
        "created_at": now,
        "updated_at": now,
        "messages": chat_messages,
        "sources": list(sources or []),
        "is_temporary": is_temporary,
        "memory": _default_memory(),
    }


def _default_memory() -> dict[str, object]:
    return {
        "summary": "",
        "pinned_facts": [],
        "summarized_until_index": 0,
        "active_artifacts": [],
    }


def _migrate_chat(data: dict[str, object]) -> dict[str, object]:
    chat = dict(data)
    if not isinstance(chat.get("memory"), dict):
        chat["memory"] = _default_memory()
    else:
        memory = dict(chat["memory"])
        defaults = _default_memory()
        for key, value in defaults.items():
            memory.setdefault(key, value)
        chat["memory"] = memory
    chat.setdefault("is_temporary", False)
    messages = chat.get("messages")
    if isinstance(messages, list):
        migrated_messages = []
        for message in messages:
            if not isinstance(message, dict):
                migrated_messages.append(message)
                continue
            migrated = dict(message)
            for key, default in (
                ("sources", []),
                ("downloads", []),
                ("saved_notes", []),
                ("pending_saves", []),
                ("attachments_context", ""),
                ("attachment_names", []),
            ):
                migrated.setdefault(key, default)
            migrated_messages.append(migrated)
        chat["messages"] = migrated_messages
    return chat


def _resolve_chat_path(chat_id: str, chats_dir: str | Path | None) -> tuple[Path, Path]:
    if not isinstance(chat_id, str) or not chat_id.strip():
        raise ValueError("chat_id muss ein nicht-leerer String sein.")
    target_dir = Path(chats_dir or CHATS_DIR).expanduser().resolve()
    chat_path = (target_dir / f"{chat_id}.json").resolve()
    if chat_path.parent != target_dir:
        raise ValueError("Ungültiger Chat-Pfad.")
    return target_dir, chat_path


def save_chat(
    chat: dict[str, object],
    chats_dir: str | Path | None = None,
) -> str | None:
    if chat.get("is_temporary"):
        return None

    chat_id = str(chat.get("id", "")).strip()
    if not chat_id:
        raise ValueError("Chat besitzt keine id.")

    target_dir = Path(chats_dir or CHATS_DIR).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    resolved_dir, chat_path = _resolve_chat_path(chat_id, target_dir)
    if resolved_dir != target_dir:
        raise ValueError("Ungültiger Chat-Pfad.")

    chat["updated_at"] = _utc_now()
    chat_path.write_text(
        json.dumps(chat, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(chat_path)


def load_chat(
    chat_id: str,
    chats_dir: str | Path | None = None,
) -> dict[str, object]:
    _target_dir, chat_path = _resolve_chat_path(chat_id, chats_dir)
    if not chat_path.is_file():
        raise FileNotFoundError(f"Chat nicht gefunden: {chat_id}")

    data = json.loads(chat_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Chat-Datei enthält kein gültiges JSON-Objekt.")
    return _migrate_chat(data)


def list_chats(chats_dir: str | Path | None = None) -> list[dict[str, object]]:
    target_dir = Path(chats_dir or CHATS_DIR)
    if not target_dir.is_dir():
        return []

    chats: list[dict[str, object]] = []
    for chat_path in target_dir.glob("*.json"):
        try:
            chat = json.loads(chat_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(chat, dict) and chat.get("id"):
            chats.append(_migrate_chat(chat))

    chats.sort(
        key=lambda chat: str(chat.get("updated_at", "")),
        reverse=True,
    )
    return chats


def add_message(
    chat: dict[str, object],
    role: str,
    content: str,
    sources: list[dict[str, object]] | None = None,
    downloads: list[dict[str, object]] | None = None,
    saved_notes: list[dict[str, object]] | None = None,
    pending_saves: list[dict[str, object]] | None = None,
    attachments_context: str = "",
    attachment_names: list[str] | None = None,
) -> dict[str, object]:
    if role not in {"user", "assistant", "system"}:
        raise ValueError("role muss user, assistant oder system sein.")
    if not isinstance(content, str):
        raise TypeError("content muss ein String sein.")

    messages = chat.setdefault("messages", [])
    if not isinstance(messages, list):
        raise ValueError("messages muss eine Liste sein.")
    message: dict[str, object] = {
        "role": role,
        "content": content,
        "timestamp": _utc_now(),
        "sources": list(sources or []),
        "downloads": list(downloads or []),
        "saved_notes": list(saved_notes or []),
        "pending_saves": list(pending_saves or []),
        "attachments_context": attachments_context,
        "attachment_names": list(attachment_names or []),
    }
    messages.append(message)

    if sources:
        existing = chat.setdefault("sources", [])
        if not isinstance(existing, list):
            raise ValueError("sources muss eine Liste sein.")
        for source in sources:
            if source not in existing:
                existing.append(source)

    chat["updated_at"] = _utc_now()
    if chat.get("title") == "Neuer Chat":
        chat["title"] = _chat_title(messages)
    return chat


def delete_chat(
    chat_id: str,
    chats_dir: str | Path | None = None,
) -> bool:
    _target_dir, chat_path = _resolve_chat_path(chat_id, chats_dir)
    if not chat_path.is_file():
        return False
    chat_path.unlink()
    return True


def delete_all_chats(chats_dir: str | Path | None = None) -> int:
    target_dir = Path(chats_dir or CHATS_DIR).expanduser().resolve()
    if not target_dir.is_dir():
        return 0
    deleted = 0
    for chat_path in target_dir.glob("*.json"):
        if chat_path.is_file() or chat_path.is_symlink():
            chat_path.unlink()
            deleted += 1
    return deleted


def rename_chat(
    chat_id: str,
    new_title: str,
    chats_dir: str | Path | None = None,
) -> dict[str, object]:
    if not isinstance(new_title, str) or not new_title.strip():
        raise ValueError("Der Chat-Titel darf nicht leer sein.")
    chat = load_chat(chat_id, chats_dir)
    if chat.get("is_temporary"):
        raise ValueError("Temporäre Chats können nicht umbenannt werden.")
    chat["title"] = new_title.strip()[:80]
    chat["updated_at"] = _utc_now()
    save_chat(chat, chats_dir)
    return chat


def main() -> int:
    parser = argparse.ArgumentParser(description="Dauerhafte Chat-Historie verwalten")
    parser.add_argument("--mock", action="store_true", help="Lokalen Chat-Speicher testen")
    args = parser.parse_args()

    if not args.mock:
        print("Hinweis: Verwende --mock für einen lokalen Chat-Speichertest.")
        return 0

    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as directory:
        chat = create_chat()
        add_message(chat, "user", "Mock-Frage zu RAG")
        add_message(
            chat,
            "assistant",
            "Mock-Antwort",
            sources=[{"filename": "Mock.md", "path": "Inbox/Mock.md"}],
        )
        saved_path = save_chat(chat, directory)
        loaded = load_chat(chat["id"], directory)
        listed = list_chats(directory)

        assert Path(saved_path).is_file()
        assert loaded["title"] == "Mock-Frage zu RAG"
        assert len(loaded["messages"]) == 2
        assert listed and listed[0]["id"] == chat["id"]
        renamed = rename_chat(chat["id"], "Umbenannt", directory)
        assert renamed["title"] == "Umbenannt"
        assert delete_chat(chat["id"], directory)
        assert not delete_chat(chat["id"], directory)
        temporary = create_chat(is_temporary=True)
        assert save_chat(temporary, Path(directory) / "must-not-exist") is None
        assert not (Path(directory) / "must-not-exist").exists()
        print("Chat-Speicher Mock erfolgreich")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
