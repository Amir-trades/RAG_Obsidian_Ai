from __future__ import annotations

from pathlib import Path
import sys

import streamlit as st
from docx import Document

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.chat_storage import (
    add_message,
    create_chat,
    list_chats,
    load_chat,
    save_chat,
)
from src.config import ConfigError, load_config
from src.ingest import (
    detect_vault_changes,
    extract_metadata_for_vault,
    load_sync_state,
    scan_markdown_files,
)
from src.tools import (
    build_generate_content_config,
    build_two_step_configs,
    create_downloadable_file,
    propose_note_save,
)


def initialize_state() -> None:
    if "chat" not in st.session_state:
        st.session_state.chat = create_chat()
    if "used_sources" not in st.session_state:
        st.session_state.used_sources = []
    if "pending_save" not in st.session_state:
        st.session_state.pending_save = None
    if "download_files" not in st.session_state:
        st.session_state.download_files = []
    if "focus" not in st.session_state:
        st.session_state.focus = "Allrounder"
    if "model" not in st.session_state:
        st.session_state.model = "gemini-3.8-flash"


def get_folders_for_focus(focus: str) -> list[str] | None:
    if focus == "System-Engineering & Projekt-Architekt":
        return ["System_Engineering", "Studium", "Projekte", "Prompts"]
    if focus == "Interview- & Reflexions-Coach":
        return ["Inbox", "Projekte"]
    return None


def mock_sync() -> tuple[int, int]:
    st.session_state.auto_synced = True
    try:
        config = load_config()
    except ConfigError:
        return 0, 0
    files = scan_markdown_files(config.obsidian_vault_path)
    metadata = extract_metadata_for_vault(files, config.obsidian_vault_path)
    state = load_sync_state(PROJECT_ROOT / "sync_state.json")
    changed, deleted = detect_vault_changes(
        config.obsidian_vault_path,
        metadata,
        state,
    )
    return len(changed), len(deleted)


def extract_uploaded_text(upload) -> str:
    name = upload.name
    suffix = Path(name).suffix.lower()
    data = upload.getvalue()

    if suffix in {".txt", ".md", ".csv"}:
        return f"\n\n[Anhang {name}]\n" + data.decode("utf-8", errors="replace")
    if suffix == ".docx":
        import io

        document = Document(io.BytesIO(data))
        return "\n\n[Anhang " + name + "]\n" + "\n".join(
            paragraph.text for paragraph in document.paragraphs
        )
    return ""


def generate_mock_reply(prompt: str, focus: str) -> str:
    return (
        f"[Mock-Modus] Fokus: {focus} | Modell: {st.session_state.model}\n\n"
        f"Ich habe deine Anfrage erhalten: {prompt}\n\n"
        "In der echten Konfiguration wird diese Anfrage mit Gemini, "
        "Obsidian-RAG und optionaler Websuche beantwortet."
    )


def generate_gemini_reply(
    prompt: str,
    focus: str,
    uploads: list,
    config,
) -> tuple[str, list[dict[str, object]]]:
    from google import genai
    from src.tools import search_vault

    parts: list[object] = [prompt]
    vault_markers = (
        "aus dem vault",
        "aus dem obsidian",
        "beschreibe mich",
        "mein profil",
        "über mich",
    )
    if any(marker in prompt.casefold() for marker in vault_markers):
        try:
            from src.ingest import scan_markdown_files

            config_vault = load_config().obsidian_vault_path
            markdown_files = scan_markdown_files(config_vault)
            terms = {
                term.casefold()
                for term in prompt.replace("(", " ").replace(")", " ").split()
                if len(term.casefold()) >= 4
                and term.casefold()
                not in {"beschreibe", "person", "vault", "mal", "dies", "mich"}
            }
            matches: list[tuple[Path, str]] = []
            for path in markdown_files:
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                score = sum(text.casefold().count(term) for term in terms)
                filename_hint = path.name.casefold()
                if any(
                    hint in filename_hint
                    for hint in ("profil", "person", "identita", "über-mich", "ueber-mich", "cv")
                ):
                    score += 20
                if "identität" in text.casefold() or "identity" in text.casefold():
                    score += 10
                if score:
                    matches.append((path, text))
            matches.sort(key=lambda item: item[0].name.casefold())
            context_parts = []
            for path, text in matches[:8]:
                relative = path.relative_to(config_vault)
                excerpt = text[:4000].strip()
                context_parts.append(f"[{relative}]\n{excerpt}")

            if context_parts:
                context = "\n\n".join(context_parts)
                parts.append(
                    "Lokaler Obsidian-Kontext:\n"
                    f"{context}\n\n"
                    "Beantworte die Frage ausschließlich auf Basis dieses "
                    "lokalen Kontexts und kennzeichne Unsicherheit."
                )
        except Exception:
            parts.append(
                "Hinweis: Die lokale Obsidian-Suche ist nicht verfügbar."
            )
    for upload in uploads:
        suffix = Path(upload.name).suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".webp", ".pdf"}:
            mime = upload.type or {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
                ".pdf": "application/pdf",
            }.get(suffix, "application/octet-stream")
            from google.genai import types

            parts.append(
                types.Part.from_bytes(
                    data=upload.getvalue(),
                    mime_type=mime,
                )
            )

    for upload in uploads:
        extracted = extract_uploaded_text(upload)
        if extracted:
            parts.append(extracted)

    client = genai.Client(api_key=config.gemini_api_key)
    own_config, web_config = build_two_step_configs()

    live_request_markers = (
        "aktuell",
        "news",
        "nachricht",
        "heute",
        "live",
        "im internet",
        "websuche",
        "neueste",
        "latest",
    )
    selected_config = (
        web_config
        if any(marker in prompt.casefold() for marker in live_request_markers)
        else own_config
    )

    response = client.models.generate_content(
        model=st.session_state.model,
        contents=parts,
        config=selected_config,
    )
    text = response.text
    if not text:
        function_calls: list[tuple[str, dict[str, object]]] = []
        for candidate in getattr(response, "candidates", []) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", []) or []:
                function_call = getattr(part, "function_call", None)
                if function_call is not None:
                    arguments = getattr(function_call, "args", {}) or {}
                    function_calls.append(
                        (str(getattr(function_call, "name", "")), dict(arguments))
                    )

        if function_calls:
            from src.ingest import scan_markdown_files

            query = prompt
            for name, arguments in function_calls:
                if name == "search_vault":
                    query = str(arguments.get("query", prompt))

            config_vault = load_config().obsidian_vault_path
            markdown_files = scan_markdown_files(config_vault)
            terms = {
                term.casefold()
                for term in query.replace("(", " ").replace(")", " ").split()
                if len(term.casefold()) >= 4
                and term.casefold()
                not in {"welche", "welcher", "beschreibe", "person", "vault", "mal"}
            }
            matches: list[tuple[Path, str]] = []
            for path in markdown_files:
                try:
                    file_text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                score = sum(file_text.casefold().count(term) for term in terms)
                if score:
                    matches.append((path, file_text))
            matches.sort(key=lambda item: item[0].name.casefold())

            context_parts = []
            for path, file_text in matches[:8]:
                relative = path.relative_to(config_vault)
                context_parts.append(
                    f"[{relative}]\n{file_text[:4000].strip()}"
                )
            if context_parts:
                from google.genai import types

                follow_up = (
                    f"{prompt}\n\nLokaler Obsidian-Kontext:\n"
                    + "\n\n".join(context_parts)
                    + "\n\nBeantworte die Frage auf Basis dieses lokalen "
                    "Kontexts und kennzeichne Unsicherheit."
                )
                response = client.models.generate_content(
                    model=st.session_state.model,
                    contents=follow_up,
                    config=types.GenerateContentConfig(),
                )
                text = response.text

    if not text:
        from google.genai import types

        retry_contents = parts
        retry_response = client.models.generate_content(
            model=st.session_state.model,
            contents=retry_contents,
            config=types.GenerateContentConfig(),
        )
        text = retry_response.text

    if not text:
        text = (
            "Das Modell hat keine Textantwort erzeugt. Bitte frage erneut oder "
            "wähle ein anderes Gemini-Modell."
        )
    sources = list(st.session_state.used_sources)
    return text, sources


initialize_state()
st.set_page_config(page_title="Second Brain AI", layout="wide")
st.title("Second Brain AI-Architect")

with st.sidebar:
    st.subheader("Vault")
    if st.button("+ Neuer Chat"):
        st.session_state.chat = create_chat()
        st.session_state.used_sources = []
        st.rerun()

    for chat in list_chats():
        label = str(chat.get("title", "Neuer Chat"))
        if st.button(label, key=f"chat-{chat['id']}"):
            st.session_state.chat = load_chat(str(chat["id"]))
            st.session_state.used_sources = list(chat.get("sources", []))
            st.rerun()

    st.subheader("Fokus")
    st.session_state.focus = st.radio(
        "Such-Priorisierung",
        [
            "Allrounder",
            "System-Engineering & Projekt-Architekt",
            "Interview- & Reflexions-Coach",
        ],
        index=[
            "Allrounder",
            "System-Engineering & Projekt-Architekt",
            "Interview- & Reflexions-Coach",
        ].index(st.session_state.focus),
    )

    st.subheader("Gemini-Modell")
    model_options = [
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-4-argon",
        "gemini-4-carbon",
        "gemini-4-hydrogen",
        "gemini-4-neon",
    ]
    st.session_state.model = st.selectbox(
        "Modell für diesen Chat",
        model_options,
        index=model_options.index(st.session_state.model)
        if st.session_state.model in model_options
        else 0,
        help="Standard: Flash-Modell. Stärkere Modelle können mehr kosten.",
    )

    st.subheader("Vault-Synchronisierung")
    if "auto_synced" not in st.session_state:
        changed, deleted = mock_sync()
        st.caption(f"Auto-Sync: {changed} geändert, {deleted} gelöscht")

    if st.button("Vault jetzt synchronisieren"):
        changed, deleted = mock_sync()
        st.success(f"{changed} geändert, {deleted} gelöscht")

chat = st.session_state.chat
for message in chat.get("messages", []):
    role = message["role"]
    if role in {"user", "assistant"}:
        with st.chat_message(role):
            st.markdown(message["content"])

chat_input = st.chat_input(
    "Nachricht oder Datei",
    accept_file=True,
    file_type=[".png", ".jpg", ".jpeg", ".webp", ".pdf", ".txt", ".md", ".csv", ".docx"],
)

if chat_input:
    prompt = chat_input.text or ""
    uploads = chat_input.files or []
    add_message(chat, "user", prompt)
    save_chat(chat)

    try:
        config = load_config()
        mock_mode = not bool(config.gemini_api_key)
    except ConfigError:
        config = None
        mock_mode = True

    if mock_mode:
        reply = generate_mock_reply(prompt, st.session_state.focus)
        sources: list[dict[str, object]] = []
    else:
        try:
            reply, sources = generate_gemini_reply(
                prompt,
                st.session_state.focus,
                uploads,
                config,
            )
        except Exception as exc:
            error_text = str(exc).lower()
            if "resource_exhausted" in error_text or "429" in error_text:
                st.warning(
                    "Gemini-Konto: Das API-Kontingent ist ausgeschöpft oder der "
                    "Tarif erlaubt noch keine Live-Anfragen. Bitte Quota prüfen "
                    "oder Billing aktivieren."
                )
            else:
                st.warning(
                    "Die Live-Anfrage an Gemini ist fehlgeschlagen: "
                    f"{type(exc).__name__}: {str(exc).splitlines()[0][:300]}"
                )
            reply = "[Live-Modus] Die Anfrage konnte nicht verarbeitet werden."
            sources = []

    st.session_state.used_sources = sources
    add_message(chat, "assistant", reply, sources=sources)
    save_chat(chat)
    st.rerun()

if st.session_state.used_sources:
    with st.expander("Genutzte Obsidian-Quellen & Web-Links"):
        for source in st.session_state.used_sources:
            st.write(
                source.get("filename")
                or source.get("title")
                or source.get("url")
                or "Quelle"
            )

if st.session_state.pending_save:
    proposal = st.session_state.pending_save
    if st.button("Bestätigen & ins Vault speichern"):
        try:
            from src.tools import execute_note_save

            execute_note_save(proposal, confirmed=True)
        except Exception as exc:
            st.error(str(exc))
        else:
            st.success("Notiz ins Vault gespeichert.")
            st.session_state.pending_save = None
            st.rerun()

for download_path in st.session_state.download_files:
    path = Path(download_path)
    if path.is_file():
        st.download_button(
            label=f"Datei herunterladen: {path.name}",
            data=path.read_bytes(),
            file_name=path.name,
            key=f"download-{path.name}",
        )
