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
    if title:
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
    }


def save_chat(
    chat: dict[str, object],
    chats_dir: str | Path | None = None,
) -> str:
    chat_id = str(chat.get("id", "")).strip()
    if not chat_id:
        raise ValueError("Chat besitzt keine id.")

    target_dir = Path(chats_dir or CHATS_DIR)
    target_dir.mkdir(parents=True, exist_ok=True)
    chat_path = (target_dir / f"{chat_id}.json").resolve()
    if chat_path.parent != target_dir.resolve():
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
    target_dir = Path(chats_dir or CHATS_DIR)
    chat_path = (target_dir / f"{chat_id}.json").resolve()
    if chat_path.parent != target_dir.resolve():
        raise ValueError("Ungültiger Chat-Pfad.")
    if not chat_path.is_file():
        raise FileNotFoundError(f"Chat nicht gefunden: {chat_id}")

    data = json.loads(chat_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Chat-Datei enthält kein gültiges JSON-Objekt.")
    return data


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
            chats.append(chat)

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
) -> dict[str, object]:
    if role not in {"user", "assistant", "system"}:
        raise ValueError("role muss user, assistant oder system sein.")
    if not isinstance(content, str):
        raise TypeError("content muss ein String sein.")

    messages = chat.setdefault("messages", [])
    if not isinstance(messages, list):
        raise ValueError("messages muss eine Liste sein.")
    messages.append(
        {
            "role": role,
            "content": content,
            "timestamp": _utc_now(),
        }
    )

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
        print("Chat-Speicher Mock erfolgreich")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
