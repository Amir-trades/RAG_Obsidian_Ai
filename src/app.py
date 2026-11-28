from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
import os
import sys
from typing import Any

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

st.set_page_config(page_title="Aqua", page_icon="🌊", layout="wide")

from src.agent import run_agent_turn, stream_final_reply
from src.chat_storage import (
    add_message,
    create_chat,
    delete_chat,
    list_chats,
    load_chat,
    rename_chat,
    save_chat,
)
from src.config import Config, ConfigError, load_config
from src.ingest import reindex_single_file, sync_vault
from src.tools import execute_note_save
from src.ui_styles import apply_styles


FOCUS_OPTIONS = [
    "Allrounder",
    "System-Engineering & Projekt-Architekt",
    "Interview- & Reflexions-Coach",
]
FALLBACK_MODEL = "gemini-2.5-flash"
UPLOAD_TYPES = [
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".pdf",
    ".txt",
    ".md",
    ".csv",
    ".docx",
]


def _read_config() -> Config | None:
    try:
        return load_config()
    except ConfigError:
        return None


def _agent_fallback_config() -> Config:
    return Config(
        gemini_api_key="",
        gemini_model=FALLBACK_MODEL,
        obsidian_vault_path=PROJECT_ROOT,
        default_save_folder="Inbox",
    )


def _initialize_state(config: Config | None) -> None:
    if "chat" not in st.session_state:
        st.session_state.chat = create_chat()
    st.session_state.chat.setdefault("memory", {
        "summary": "",
        "pinned_facts": [],
        "summarized_until_index": 0,
        "active_artifacts": [],
    })
    st.session_state.setdefault("focus", FOCUS_OPTIONS[0])
    model_default = config.gemini_model if config else FALLBACK_MODEL
    st.session_state.setdefault("model", model_default)
    st.session_state.setdefault("allow_auto_inbox_write", True)
    st.session_state.setdefault("used_sources", [])
    st.session_state.setdefault("auto_synced", False)
    st.session_state.setdefault("sync_message", "")


def _date_group(chat: dict[str, Any]) -> str:
    raw_timestamp = str(chat.get("updated_at", chat.get("created_at", "")))
    try:
        timestamp = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
        chat_date = timestamp.astimezone().date() if timestamp.tzinfo else timestamp.date()
    except ValueError:
        return "Älter"
    today = date.today()
    if chat_date == today:
        return "Heute"
    if chat_date == today - timedelta(days=1):
        return "Gestern"
    if chat_date >= today - timedelta(days=7):
        return "Letzte 7 Tage"
    return "Älter"


def _run_sync(config: Config | None) -> tuple[str, bool]:
    if config is None:
        return "Vault-Sync nicht verfügbar: .env-Konfiguration prüfen.", False
    try:
        result = sync_vault(
            vault_path=config.obsidian_vault_path,
            api_key=config.gemini_api_key,
            mock=not bool(config.gemini_api_key),
        )
    except Exception as exc:
        detail = str(exc).splitlines()[0][:180] if str(exc) else ""
        suffix = f": {detail}" if detail else ""
        return f"Vault-Sync fehlgeschlagen ({type(exc).__name__}){suffix}", False
    if result.errors:
        first_error = result.errors[0][:160]
        return (
            f"Sync teilweise abgeschlossen ({result.chunks_embedded} Chunks eingebettet, "
            f"{len(result.errors)} Fehler: {first_error}).",
            False,
        )
    mode = "Mock" if not config.gemini_api_key else "Live"
    return (
        f"{mode}-Sync: {result.total_files} Notizen, "
        f"{result.chunks_embedded} Chunks eingebettet, "
        f"{result.changed_files} geändert, {result.deleted_files} gelöscht.",
        True,
    )


def _render_chat_sidebar(config: Config | None) -> None:
    with st.sidebar:
        st.title("Aqua")
        first, second = st.columns(2)
        if first.button("+ Neuer Chat", use_container_width=True):
            st.session_state.chat = create_chat()
            st.rerun()
        if second.button("Temporär", help="Wird nicht auf der Festplatte gespeichert."):
            st.session_state.chat = create_chat(is_temporary=True)
            st.rerun()

        search = st.text_input("Chats durchsuchen…", key="chat_search")
        chats = [
            chat
            for chat in list_chats()
            if search.casefold() in str(chat.get("title", "")).casefold()
        ]
        groups = ("Heute", "Gestern", "Letzte 7 Tage", "Älter")
        active_id = str(st.session_state.chat.get("id", ""))
        for group in groups:
            grouped_chats = [chat for chat in chats if _date_group(chat) == group]
            if not grouped_chats:
                continue
            st.caption(group)
            for saved_chat in grouped_chats:
                chat_id = str(saved_chat["id"])
                title = str(saved_chat.get("title", "Neuer Chat"))
                action_col, menu_col = st.columns([5, 1])
                if action_col.button(
                    title,
                    key=f"open-chat-{chat_id}",
                    type="primary" if active_id == chat_id else "secondary",
                    use_container_width=True,
                ):
                    st.session_state.chat = load_chat(chat_id)
                    st.rerun()
                with menu_col.popover("⋯", key=f"chat-menu-{chat_id}"):
                    new_title = st.text_input(
                        "Chat umbenennen",
                        value=title,
                        key=f"rename-title-{chat_id}",
                    )
                    if st.button("Umbenennen", key=f"rename-{chat_id}"):
                        try:
                            rename_chat(chat_id, new_title)
                        except (ValueError, FileNotFoundError) as exc:
                            st.error(str(exc))
                        else:
                            st.rerun()
                    if st.button("Chat löschen", key=f"delete-{chat_id}"):
                        if delete_chat(chat_id):
                            if active_id == chat_id:
                                st.session_state.chat = create_chat()
                            st.toast("Chat gelöscht")
                            st.rerun()

        st.divider()
        with st.expander("Einstellungen & Vault", expanded=False):
            st.session_state.focus = st.selectbox(
                "Fokus",
                FOCUS_OPTIONS,
                index=(
                    FOCUS_OPTIONS.index(st.session_state.focus)
                    if st.session_state.focus in FOCUS_OPTIONS
                    else 0
                ),
            )
            model_options = list(
                dict.fromkeys(
                    [
                        config.gemini_model if config else FALLBACK_MODEL,
                        st.session_state.model,
                        FALLBACK_MODEL,
                    ]
                )
            )
            st.session_state.model = st.selectbox(
                "Gemini-Modell",
                model_options,
                index=model_options.index(st.session_state.model),
            )
            st.session_state.allow_auto_inbox_write = st.toggle(
                "Direktes Speichern in Inbox auf Befehl",
                value=st.session_state.allow_auto_inbox_write,
            )
            if not st.session_state.auto_synced:
                if os.environ.get("AQUA_SKIP_AUTO_SYNC") == "1":
                    message = "Auto-Sync für diesen Start deaktiviert."
                    succeeded = True
                else:
                    with st.spinner("Vault wird synchronisiert…"):
                        message, succeeded = _run_sync(config)
                st.session_state.sync_message = message
                st.session_state.auto_synced = True
                st.session_state.sync_succeeded = succeeded
            if st.session_state.sync_message:
                st.caption(st.session_state.sync_message)
            if st.button("Vault jetzt synchronisieren", use_container_width=True):
                with st.spinner("Vault wird synchronisiert…"):
                    message, succeeded = _run_sync(config)
                st.session_state.sync_message = message
                st.session_state.sync_succeeded = succeeded
                if succeeded:
                    st.toast("Vault-Sync abgeschlossen")
                else:
                    st.warning(message)


def _safe_download_path(raw_path: str, config: Config | None) -> Path | None:
    path = Path(raw_path).expanduser().resolve()
    allowed_root = (
        config.export_dir.expanduser().resolve()
        if config is not None
        else (PROJECT_ROOT / "exports").resolve()
    )
    try:
        path.relative_to(allowed_root)
    except ValueError:
        return None
    return path if path.is_file() else None


def _render_sources(message: dict[str, Any], index: int) -> None:
    sources = message.get("sources", [])
    if not isinstance(sources, list) or not sources:
        return
    obsidian = [source for source in sources if source.get("source_type") != "web"]
    web = [source for source in sources if source.get("source_type") == "web"]
    with st.expander(f"Quellen: {len(obsidian)} Obsidian · {len(web)} Web"):
        for source in obsidian:
            filename = source.get("filename", "Unbekannte Notiz")
            path = source.get("path", "")
            heading = source.get("heading_path", "")
            score = source.get("score")
            label = f"{filename} — {path}"
            if heading:
                label += f" · {heading}"
            if score is not None:
                label += f" · Score {float(score):.3f}"
            st.caption(label)
        for source_index, source in enumerate(web):
            url = str(source.get("url", ""))
            if url.startswith(("https://", "http://")):
                st.link_button(
                    str(source.get("title", url)),
                    url,
                    key=f"web-source-{index}-{source_index}",
                )


def _handle_save_action(
    chat: dict[str, Any],
    message_index: int,
    proposal_id: str,
    config: Config | None,
    approved: bool,
) -> None:
    message = chat["messages"][message_index]
    pending = message.get("pending_saves", [])
    proposal = next(
        (
            item
            for item in pending
            if isinstance(item, dict) and item.get("proposal_id") == proposal_id
        ),
        None,
    )
    if proposal is None:
        st.error("Der Speichervorschlag ist nicht mehr verfügbar.")
        return
    if approved:
        if config is None:
            st.error("Vault-Konfiguration fehlt; der Vorschlag bleibt unverändert.")
            return
        try:
            saved_path = execute_note_save(
                proposal,
                confirmed=True,
                vault_path=config.obsidian_vault_path,
            )
        except (OSError, PermissionError, ValueError) as exc:
            st.error(f"Speichern fehlgeschlagen ({type(exc).__name__}).")
            return
        relative_path = str(
            Path(saved_path).resolve().relative_to(
                config.obsidian_vault_path.resolve()
            )
        ).replace("\\", "/")
        try:
            reindex_single_file(
                Path(saved_path),
                config.obsidian_vault_path,
                api_key=config.gemini_api_key,
                mock=not bool(config.gemini_api_key),
            )
            indexed = True
        except Exception as exc:
            indexed = False
            st.warning(
                f"Notiz gespeichert, aber Re-Indexierung fehlgeschlagen "
                f"({type(exc).__name__}); bitte Vault-Sync ausführen."
            )
        message.setdefault("saved_notes", []).append(
            {
                "path": relative_path,
                "filename": Path(saved_path).name,
                "indexed": indexed,
            }
        )
    message["pending_saves"] = [
        item
        for item in pending
        if not isinstance(item, dict) or item.get("proposal_id") != proposal_id
    ]
    save_chat(chat)
    st.rerun()


def _render_message_actions(
    message: dict[str, Any],
    message_index: int,
    chat: dict[str, Any],
    config: Config | None,
) -> None:
    _render_sources(message, message_index)
    for saved_note in message.get("saved_notes", []):
        path = saved_note.get("path", "")
        st.markdown(
            f'<div class="aqua-badge aqua-saved">✓ Direkt gespeichert: {path}</div>',
            unsafe_allow_html=True,
        )

    pending = message.get("pending_saves", [])
    if isinstance(pending, list):
        for proposal_index, proposal in enumerate(pending):
            if not isinstance(proposal, dict):
                continue
            proposal_id = str(proposal.get("proposal_id", proposal_index))
            st.info(
                f"Speichervorschlag: {proposal.get('target_folder', '')}/"
                f"{proposal.get('filename', '')}"
            )
            st.code(str(proposal.get("content", ""))[:1200], language="markdown")
            approve_col, discard_col = st.columns(2)
            if approve_col.button(
                "✓ Ins Vault speichern",
                key=f"approve-save-{message_index}-{proposal_id}",
            ):
                _handle_save_action(
                    chat,
                    message_index,
                    proposal_id,
                    config,
                    approved=True,
                )
            if discard_col.button(
                "✕ Verwerfen",
                key=f"discard-save-{message_index}-{proposal_id}",
            ):
                _handle_save_action(
                    chat,
                    message_index,
                    proposal_id,
                    config,
                    approved=False,
                )

    downloads = message.get("downloads", [])
    if isinstance(downloads, list):
        columns = st.columns(min(len(downloads), 3)) if downloads else []
        for index, download in enumerate(downloads):
            if not isinstance(download, dict):
                continue
            path = _safe_download_path(str(download.get("path", "")), config)
            if path is None:
                st.warning("Ein gespeicherter Download ist nicht verfügbar.")
                continue
            with path.open("rb") as file:
                columns[index % len(columns)].download_button(
                    f"⬇ {download.get('filename', path.name)}",
                    data=file.read(),
                    file_name=path.name,
                    key=f"download-{message_index}-{index}",
                )


def _render_history(chat: dict[str, Any], config: Config | None) -> None:
    for index, message in enumerate(chat.get("messages", [])):
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue
        with st.chat_message(role):
            st.markdown(str(message.get("content", "")))
            for filename in message.get("attachment_names", []):
                st.caption(f"📎 {filename}")
            if role == "assistant":
                _render_message_actions(message, index, chat, config)


def _render_welcome() -> None:
    st.markdown(
        '<div class="aqua-welcome"><h2>Hallo! Womit starten wir heute?</h2>'
        "<p>Dein zweites Gehirn für Notizen, Recherche und Dokumente.</p></div>",
        unsafe_allow_html=True,
    )
    prompts = [
        "Durchsuche mein Second Brain nach einem Thema, das ich gleich nenne.",
        "Notiere das in meiner Obsidian Inbox: ",
        "Erstelle mir ein Anschreiben oder einen Lebenslauf als Word-Datei.",
        "Starte einen Projektplan aus meinen Vault-Notizen.",
    ]
    columns = st.columns(2)
    for index, prompt in enumerate(prompts):
        with columns[index % 2]:
            if st.button(prompt, key=f"quick-prompt-{index}", use_container_width=True):
                st.session_state.queued_prompt = prompt
                st.rerun()


def _process_user_turn(
    prompt: str,
    uploads: list[Any],
    chat: dict[str, Any],
    config: Config | None,
) -> None:
    agent_config = config or _agent_fallback_config()
    mock_mode = config is None or not bool(config.gemini_api_key)
    chat_context = {**chat, "messages": list(chat.get("messages", []))}
    result = None
    error_message = None
    try:
        with st.status("Aqua arbeitet…", expanded=True) as status:
            def on_status(step: str) -> None:
                status.write(step)
                status.update(label=step)

            result = run_agent_turn(
                prompt=prompt,
                chat=chat_context,
                focus=st.session_state.focus,
                model_name=st.session_state.model,
                uploads=uploads,
                config=agent_config,
                allow_auto_inbox_write=st.session_state.allow_auto_inbox_write,
                on_status=on_status,
                mock=mock_mode,
            )
            status.update(label="Fertig", state="complete")
    except Exception as exc:
        error_message = f"Anfrage fehlgeschlagen ({type(exc).__name__})."
        st.error(error_message)

    if result is None:
        result_text = error_message or "Die Anfrage konnte nicht verarbeitet werden."
        result_sources: list[dict[str, Any]] = []
        downloads: list[dict[str, Any]] = []
        pending_saves: list[dict[str, Any]] = []
        saved_notes: list[dict[str, Any]] = []
        attachments_context = ""
    else:
        result_text = result.reply_text
        result_sources = list(result.obsidian_sources) + [
            {"source_type": "web", **source}
            for source in result.web_sources
        ]
        downloads = result.download_files
        pending_saves = result.pending_saves
        saved_notes = result.saved_notes
        attachments_context = result.attachments_context
        if isinstance(chat_context.get("memory"), dict):
            chat["memory"] = chat_context["memory"]

    add_message(
        chat,
        "user",
        prompt,
        attachments_context=attachments_context,
        attachment_names=[str(upload.name) for upload in uploads],
    )
    with st.chat_message("assistant"):
        st.write_stream(stream_final_reply(result_text))
    add_message(
        chat,
        "assistant",
        result_text,
        sources=result_sources,
        downloads=downloads,
        saved_notes=saved_notes,
        pending_saves=pending_saves,
    )
    save_chat(chat)
    st.rerun()


def main() -> None:
    config = _read_config()
    _initialize_state(config)
    apply_styles()

    chat = st.session_state.chat
    is_temporary = bool(chat.get("is_temporary"))
    if is_temporary:
        badge_col, end_col = st.columns([5, 1])
        badge_col.markdown(
            '<div class="aqua-badge">🕶️ Temporärer Chat · wird nicht gespeichert</div>',
            unsafe_allow_html=True,
        )
        if end_col.button("Beenden"):
            st.session_state.chat = create_chat()
            st.rerun()
    else:
        st.title("Aqua")
        st.caption(
            f"{st.session_state.model} · {st.session_state.focus}"
        )

    _render_chat_sidebar(config)
    chat = st.session_state.chat
    if not chat.get("messages"):
        _render_welcome()
    _render_history(chat, config)

    queued_prompt = st.session_state.pop("queued_prompt", None)
    chat_input = st.chat_input(
        "Frage stellen, Datei anhängen oder Befehl eingeben…",
        accept_file=True,
        file_type=UPLOAD_TYPES,
    )
    if queued_prompt is not None:
        _process_user_turn(str(queued_prompt), [], chat, config)
    elif chat_input is not None:
        prompt = str(chat_input.text or "").strip()
        uploads = list(chat_input.files or [])
        if prompt or uploads:
            _process_user_turn(prompt or "Bitte analysiere den Anhang.", uploads, chat, config)


main()
