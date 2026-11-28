from pathlib import Path
from types import SimpleNamespace

from src import ingest
from src.agent import run_agent_turn
from src.config import Config
from src.retrieval import keyword_search


def _function_call(name: str, arguments: dict[str, object]) -> object:
    call = SimpleNamespace(name=name, args=arguments)
    part = SimpleNamespace(function_call=call, text=None)
    return SimpleNamespace(
        text=None,
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))],
    )


class FakeModels:
    def __init__(self) -> None:
        self.responses = [
            _function_call("search_vault", {"query": "RAG architecture"}),
            _function_call(
                "write_vault_note",
                {
                    "content": "# Architecture\n\nRAG design notes.",
                    "filename": "Architecture",
                    "target_folder": "Inbox",
                    "auto_save": True,
                },
            ),
            SimpleNamespace(text="Die Notiz wurde in der Inbox gespeichert.", candidates=[]),
        ]
        self.calls: list[list[object]] = []

    def generate_content(self, *, model: str, contents: list[object], config: object) -> object:
        self.calls.append(list(contents))
        return self.responses.pop(0)


class FakeClient:
    def __init__(self) -> None:
        self.models = FakeModels()


def test_agent_keeps_tool_history_and_honors_explicit_write_intent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    corpus = tmp_path / "bm25_corpus.json"
    sync_state = tmp_path / "sync_state.json"
    monkeypatch.setattr(ingest, "BM25_CORPUS_PATH", corpus)
    monkeypatch.setattr(ingest, "SYNC_STATE_PATH", sync_state)
    (vault / "Inbox").mkdir()
    (vault / "Inbox" / "existing-one.md").write_text("Notes about projects.", encoding="utf-8")
    (vault / "Inbox" / "existing-two.md").write_text("General ideas.", encoding="utf-8")
    ingest.sync_vault(vault_path=vault, mock=True)
    client = FakeClient()
    config = Config(
        gemini_api_key="",
        gemini_model="gemini-2.5-flash",
        obsidian_vault_path=vault,
        default_save_folder="System_Engineering",
        auto_save_folders=("Inbox",),
        export_dir=tmp_path / "exports",
    )
    statuses: list[str] = []
    chat: dict[str, object] = {
        "messages": [],
        "memory": {
            "summary": "",
            "pinned_facts": [],
            "summarized_until_index": 0,
            "active_artifacts": [],
        },
    }

    result = run_agent_turn(
        prompt="Durchsuche mein Vault und speichere das in meiner Inbox.",
        chat=chat,
        focus="Allrounder",
        model_name="gemini-2.5-flash",
        uploads=[],
        config=config,
        allow_auto_inbox_write=True,
        on_status=statuses.append,
        mock=True,
        client=client,
    )

    saved_file = vault / "Inbox" / "Architecture.md"
    assert saved_file.is_file()
    assert result.reply_text == "Die Notiz wurde in der Inbox gespeichert."
    assert result.saved_notes == [
        {"filename": "Architecture.md", "path": "Inbox/Architecture.md"}
    ]
    assert result.pending_saves == []
    assert result.obsidian_sources
    assert "text" not in result.obsidian_sources[0]
    assert len(client.models.calls) == 3
    assert len(client.models.calls[1]) > len(client.models.calls[0])
    assert any("Schreibe in die Inbox" in status for status in statuses)
    assert any(
        match.path == "Inbox/Architecture.md"
        for match in keyword_search(
            "RAG design",
            folder_filter=["Inbox"],
            corpus_path=corpus,
        )
    )


def test_agent_does_not_auto_save_without_explicit_user_write_request(
    tmp_path: Path,
    monkeypatch,
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(ingest, "BM25_CORPUS_PATH", tmp_path / "corpus.json")
    monkeypatch.setattr(ingest, "SYNC_STATE_PATH", tmp_path / "sync.json")
    client = FakeClient()
    config = Config(
        gemini_api_key="",
        gemini_model="gemini-2.5-flash",
        obsidian_vault_path=vault,
        default_save_folder="System_Engineering",
        auto_save_folders=("Inbox",),
        export_dir=tmp_path / "exports",
    )

    result = run_agent_turn(
        prompt="Kannst du mir vorschlagen, was ich speichern soll?",
        chat={"messages": [], "memory": {"summarized_until_index": 0}},
        focus="Allrounder",
        model_name="gemini-2.5-flash",
        uploads=[],
        config=config,
        allow_auto_inbox_write=True,
        mock=True,
        client=client,
    )

    assert not list(vault.rglob("*.md"))
    assert result.pending_saves


def test_agent_synthesizes_answer_instead_of_aborting_at_iteration_limit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(ingest, "BM25_CORPUS_PATH", tmp_path / "corpus.json")
    monkeypatch.setattr(ingest, "SYNC_STATE_PATH", tmp_path / "sync.json")
    (vault / "Inbox").mkdir()
    (vault / "Inbox" / "ideas.md").write_text("Vergessene Projektidee.", encoding="utf-8")
    ingest.sync_vault(vault_path=vault, mock=True)

    class LoopModels:
        def __init__(self) -> None:
            self.calls = 0

        def generate_content(self, *, model: str, contents: list[object], config: object) -> object:
            self.calls += 1
            if getattr(config, "tools", None):
                return _function_call("search_vault", {"query": "vergessen"})
            return SimpleNamespace(
                text="Hier ist die Zusammenfassung deiner gefundenen Notizen.",
                candidates=[],
            )

    client = SimpleNamespace(models=LoopModels())
    config = Config(
        gemini_api_key="",
        gemini_model="gemini-2.5-flash",
        obsidian_vault_path=vault,
        default_save_folder="System_Engineering",
        auto_save_folders=("Inbox",),
        export_dir=tmp_path / "exports",
    )

    result = run_agent_turn(
        prompt="Was sind Infos im Vault, die ich vergessen habe?",
        chat={"messages": [], "memory": {"summarized_until_index": 0}},
        focus="Allrounder",
        model_name="gemini-2.5-flash",
        uploads=[],
        config=config,
        mock=True,
        client=client,
    )

    assert "Zusammenfassung" in result.reply_text
    assert "maximale Anzahl an Schritten" not in result.reply_text
    assert client.models.calls == 9


def test_pdf_upload_extracts_text_into_attachments_context(tmp_path: Path) -> None:
    class PdfModels:
        def generate_content(self, *, model: str, contents: list[object], config: object = None) -> object:
            if config is None:
                return SimpleNamespace(text="Name: Max Mustermann\nAdresse: Musterweg 12, 12345 Musterstadt")
            return SimpleNamespace(text="PDF gelesen.", candidates=[])

    upload = SimpleNamespace(
        name="cv.pdf",
        type="application/pdf",
        getvalue=lambda: b"%PDF-1.4 fake",
    )
    config = Config(
        gemini_api_key="test-key",
        gemini_model="gemini-2.5-flash",
        obsidian_vault_path=tmp_path,
        default_save_folder="Inbox",
        export_dir=tmp_path / "exports",
    )

    result = run_agent_turn(
        prompt="Hier ist mein Lebenslauf.",
        chat={"messages": [], "memory": {"summarized_until_index": 0}},
        focus="Allrounder",
        model_name="gemini-2.5-flash",
        uploads=[upload],
        config=config,
        mock=False,
        client=SimpleNamespace(models=PdfModels()),
    )

    assert "Musterweg 12, 12345 Musterstadt" in result.attachments_context
    assert "kein Textauszug verfügbar" not in result.attachments_context
