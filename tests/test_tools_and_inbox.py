from pathlib import Path

import pytest

from src import ingest, retrieval, tools
from src.config import Config


def _config(vault: Path) -> Config:
    return Config(
        gemini_api_key="",
        gemini_model="gemini-2.5-flash",
        obsidian_vault_path=vault,
        default_save_folder="System_Engineering",
        auto_save_folders=("Inbox",),
        inbox_folder="Inbox",
        export_dir=vault.parent / "exports",
        enable_reranker=False,
    )


def test_write_proposal_requires_approval_and_never_writes_early(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()

    result = tools.write_vault_note(
        "# Idea",
        "idea",
        config=_config(vault),
        vault_path=vault,
        auto_save=False,
        mock=True,
    )

    assert result["status"] == "pending_confirmation"
    assert result["proposal"]["filename"] == "idea.md"
    assert not list(vault.rglob("*.md"))


def test_auto_save_creates_collision_safe_file_and_reindexes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = tmp_path / "vault"
    inbox = vault / "Inbox"
    inbox.mkdir(parents=True)
    (inbox / "idea.md").write_text("Existing note.", encoding="utf-8")
    corpus = tmp_path / "bm25_corpus.json"
    state = tmp_path / "sync_state.json"
    monkeypatch.setattr(ingest, "BM25_CORPUS_PATH", corpus)
    monkeypatch.setattr(ingest, "SYNC_STATE_PATH", state)
    for path, content in (
        (inbox / "one.md", "General inbox note."),
        (inbox / "two.md", "Another inbox note."),
        (vault / "Projects" / "project.md", "Project context."),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    ingest.sync_vault(vault_path=vault, mock=True)

    result = tools.write_vault_note(
        "# MOD-104\n\nIdentifier belongs to this project.",
        "idea",
        target_folder="Inbox",
        auto_save=True,
        allow_auto_save=True,
        config=_config(vault),
        vault_path=vault,
        mock=True,
    )

    saved_file = Path(result["path"])
    assert result["status"] == "saved"
    assert saved_file.name == "idea_2.md"
    assert state.is_file()
    matches = retrieval.keyword_search(
        "MOD-104",
        folder_filter=["Inbox"],
        corpus_path=corpus,
    )
    assert any(match.path == "Inbox/idea_2.md" for match in matches)


def test_write_rejects_traversal_and_auto_save_outside_allowed_folder(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(ValueError, match="Relative Pfadsegmente"):
        tools.write_vault_note(
            "Unsafe",
            "unsafe",
            target_folder="../outside",
            auto_save=True,
            allow_auto_save=True,
            config=_config(vault),
            vault_path=vault,
            mock=True,
        )

    result = tools.write_vault_note(
        "Needs confirmation",
        "note",
        target_folder="Projects",
        auto_save=True,
        allow_auto_save=True,
        config=_config(vault),
        vault_path=vault,
        mock=True,
    )
    assert result["status"] == "pending_confirmation"


def test_read_note_traversal_and_visible_folder_listing(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "Projects" / "Design").mkdir(parents=True)
    (vault / ".obsidian").mkdir()
    (vault / "Projects" / ".hidden").mkdir()
    note = vault / "Projects" / "Design" / "architecture.md"
    note.write_text("A safe note.", encoding="utf-8")

    assert tools.read_vault_note(
        "Projects/Design/architecture.md",
        vault_path=vault,
    ) == {
        "path": "Projects/Design/architecture.md",
        "content": "A safe note.",
        "truncated": False,
    }
    assert tools.list_vault_folders(vault) == ["Projects", "Projects/Design"]
    with pytest.raises(ValueError, match="unzulässige Pfadsegmente"):
        tools.read_vault_note("../outside.md", vault_path=vault)


def test_only_six_safe_tools_are_exposed_to_the_model() -> None:
    declarations = tools._python_function_declarations()

    assert {
        declaration.name
        for declaration in declarations
    } == {
        "search_vault",
        "read_vault_note",
        "list_vault_folders",
        "write_vault_note",
        "web_search",
        "create_downloadable_file",
    }
    assert "execute_note_save" not in {
        declaration.name for declaration in declarations
    }
